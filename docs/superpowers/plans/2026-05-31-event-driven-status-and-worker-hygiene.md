# Event-Driven Status + Managed-Worker Hygiene (all harnesses) — Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use `- [ ]` checkboxes. Honors AGENTS.md runtime symmetry — every per-runtime branch is symmetric or carries an `// ASYMMETRY(<rt>): <why>` comment.

**Goal:** Make agent status **event-driven and precise** across claude-code, codex, hermes, pi — driven by each runtime's own turn-lifecycle signals (hooks / RPC events), not 120s staleness timers — and make a managed agent resolve to **exactly one live worker process** (no leaked claude.exe / hermes TUI / MCP children).

**Two tracks, one theme (managed-worker lifecycle):**
- **Track A — Event-driven status:** `working` means *actually running a turn*; idle-owing-a-reply reads `online · awaiting reply`; close → `available`/`offline`. Wire the missing per-runtime signals so transitions are immediate.
- **Track B — Process hygiene:** generalize the claude kill-prior reaper to every managed runtime + reap orphaned MCP children, so the process count matches reality (operator observed 11 leaked hermes TUIs and stale MCP children for 6 managed agents).

**Why now:** operator-observed — managed claude blinked `working` ~2 min after going idle (no `Stop` hook → `turn_busy` cleared only by the 120s staleness timer), and 11 hermes `entry.js` TUIs leaked for a handful of agents (hermes kill-prior reaps the gateway host + delivery loop but not the visible TUI). Claude proliferation already fixed (`3c2b74f`); this generalizes the fix and closes the status-precision gap.

---

## Current coverage (recon 2026-05-31, verified)

| Runtime | turn-start | turn-end | session-close | kill-prior (1 worker) | MCP-child reap |
|---|---|---|---|---|---|
| claude-code | `UserPromptSubmit` (session-capture hook; not a turn-start POST) | **MISSING** (`Stop` hook not installed) | **MISSING** (`SessionEnd` not installed) | ✅ done (`reap-managed-claude.js`, `3c2b74f`) | partial (taskkill /t on reap) |
| codex | hook + RPC `turn/started` | hook + RPC `turn/completed` ✅ | n/a | ⚠️ verify | ⚠️ verify |
| hermes | `pre_llm_call` ✅ | **MISSING** ("no clean upstream turn-end") | n/a | ❌ TUI `entry.js` leaks (11 observed) | ❌ |
| pi | `/turn-start` + RPC | `/turn-end` + RPC `agent_end` ✅ | n/a | ⚠️ verify | ⚠️ verify |

Status engine gap (all runtimes): `_compute_live_status_cache` sets `working` from `channel_pending_reply_run` **with no staleness bound and decoupled from `turn_busy`** → an idle agent owing a reply shows orange `working`.

---

## Canonical status state machine (the contract Track A implements)

```
                 spawn / SessionStart            UserPromptSubmit / turn-start / RPC turn/started
   available ───────────────────────▶ online ───────────────────────────────▶ working
      ▲   ▲                             ▲  │                                      │
      │   │  SessionEnd(logout/idle-    │  │ Stop / turn-end / RPC turn-completed │
      │   │  timeout)                   │  └──────────────────────────────────────┘
      │   └───────────────────────────┘                  │
      │                          delivered+requireReply & turn ended
      │                                                   ▼
      │                                      online · AWAITING-REPLY  (NOT working)
      │                                                   │ reply lands → online
      │                                                   │ Notification(permission_prompt) → blocked
   offline  ◀── env down / stale heartbeat / SessionEnd(logout)
```

- **working** ⇔ `turn_busy` fresh (hook/RPC turn-start, cleared by turn-end) OR a claimed/running dispatch run. Nothing else.
- **awaiting-reply** ⇔ `channel_pending_reply_run` AND NOT working → presented as `online` + an `awaitingReply` badge (reminder loop nudges it; never deferred for being "working").
- **available** ⇔ reachable, no live worker (idle-timeout closed it, or never started).
- **offline** ⇔ env down / stale / `SessionEnd(logout)`.

---

## File structure

| File | Track | Responsibility |
|---|---|---|
| `service/routers/api_v2.py` (`_compute_live_status_cache`) | A | status-split: gate `working` on `turn_busy`/active-run; surface `awaitingReply` separately |
| `service/routers/api_v2.py` (`_run_contract_reminders_once`) | A | don't defer reminders for awaiting-reply-but-idle |
| `mcp/stdio/claude-turn-hook.js` (NEW) | A | claude `Stop`→`/turn-end`, `SessionEnd`→`/session-end`, `Notification`→blocked |
| `install.sh` (claude wrapper hook block) | A | install `Stop` + `SessionEnd` + `Notification` hooks |
| `install.sh` (`install_hermes_turn_hooks`) | A | add a hermes turn-END signal (post_llm_call / process-idle) |
| `mcp/stdio/reap-managed-worker.js` (NEW, generalizes `reap-managed-claude.js`) | B | per-runtime kill-prior by stable per-agent identifier |
| `mcp/stdio/terminal-runtime.js` (`startPty`) | B | call the general reaper for every managed runtime before spawn |
| `install.sh` (codex/hermes/pi wrapper managed branches) | B | wrapper-side kill-prior (mirror claude) |
| `mcp/stdio/server.js` / terminal stop path | B | reap orphaned MCP children when a worker dies; tree-kill on stop |
| `service/routers/api_v2.py` (`_close_idle_virtual_rpc_workers`) | B | ensure idle-timeout close reaps the worker tree (online-not-working X min) |

---

## TRACK A — Event-driven status

### Phase A1 — Status-split (the blink fix, runtime-agnostic; highest leverage)

- T-A1.1: **Failing test** — `service/tests/test_status_taxonomy.py`: an agent with a `delivered`+`require_reply` channel run AND `turn_busy=0`/stale must compute `status="online"` with `awaitingReply=true` + a reason, NOT `working`. A second test: with `turn_busy=1` fresh it IS `working`.
- T-A1.2: In `_compute_live_status_cache`, change the `elif channel_pending_reply_run:` branch (api_v2.py ~3031) so it no longer sets `working` unconditionally. New logic: compute `awaiting_reply = bool(channel_pending_reply_run)`; only the `turn_busy`/active-run branches set `working`; when not working but `awaiting_reply`, status falls through to `online` and the cache carries `awaitingReply=true` + reason `"awaiting reply: <subject>"`.
- T-A1.3: Add `awaitingReply` to `_agent_record_to_dict` / the live-state cache payload.
- T-A1.4: **Reminder deadlock fix** — in `_run_contract_reminders_once`, the preflight (`_preflight_live_send_recipients`) must treat awaiting-reply-but-idle as launchable (it already is, since `working` is no longer set — verify with a test: a reminder fires for an idle agent owing a reply).
- T-A1.5: Run `test_status_taxonomy.py` + `test_api_v2_regressions.py` reminder tests. Commit.

### Phase A2 — Claude precise lifecycle hooks

- T-A2.1: **Test** — `mcp/stdio/tests/claude-turn-hook.test.js`: feed the hook stdin JSON for `Stop` (asserts POST `/agents/<id>/turn-end`), `SessionEnd` with `exit_reason` (logout→offline-ish, resume/clear→keep), `Notification` matcher `permission_prompt` (→blocked signal). Injected HTTP client.
- T-A2.2: Write `mcp/stdio/claude-turn-hook.js`: reads stdin JSON, switches on `hook_event_name`, POSTs the matching endpoint with `AIFY_AGENT_ID`. (Stop→`/turn-end`; SessionEnd→`/session-end`+exit_reason; Notification permission_prompt→`/blocked`, idle_prompt→`/turn-end`.) Reuse the existing httpCall pattern.
- T-A2.3: `install.sh` claude wrapper: add `Stop`, `SessionEnd`, and `Notification` hook entries to the generated hook settings (alongside the existing SessionStart/UserPromptSubmit), invoking `claude-turn-hook.js`. Keep `UserPromptSubmit` also POSTing `/turn-start` (verify it does; if not, add).
- T-A2.4: Service: ensure `/agents/{id}/session-end` exists (or add) — sets `available` (resume/clear) or `offline` (logout); `/blocked` sets a transient blocked flag. (turn-start/turn-end already exist.)
- T-A2.5: Live verify on a managed claude: prompt → `working` immediately; finish → `online` within ~1s (no 120s lag); permission prompt → `blocked`; `/clear` → `available`. Commit.

### Phase A3 — Hermes turn-end + codex/pi parity

- T-A3.1: Add a hermes turn-END signal. Options (recon first): `post_llm_call` hook, or derive turn-end from the managed-host delivery loop when `prompt.submit` completes / the session goes idle. Wire `install_hermes_turn_hooks` to POST `/turn-end`.
- T-A3.2: Verify codex (`turn/completed`→/turn-end) and pi (`agent_end`→/turn-end) already clear `turn_busy` promptly; add tests pinning each runtime's turn-end → `online`.
- T-A3.3: Symmetry-guard test: every launchable runtime has a turn-start AND turn-end signal wired (registry-driven), or an `ASYMMETRY(<rt>)` note. Commit.

---

## TRACK B — Managed-worker process hygiene (the node leak)

### Phase B1 — Generalize the reaper to all runtimes

- T-B1.1: **Test** — `mcp/stdio/tests/reap-managed-worker.test.js`: per runtime, given a stable per-agent identifier, reap sibling worker processes except keepPid. Identifiers: claude `--resume <handle>`; hermes `--resume aify-<agentId>` (TUI entry.js) AND the gateway-host/delivery-loop; codex thread/`--aify-agent`; pi session. Injected list+kill.
- T-B1.2: Generalize `reap-managed-claude.js` → `reap-managed-worker.js` with a per-runtime matcher table (process-name + cmdline pattern). Keep `reap-managed-claude.js` as a thin re-export for back-compat. Hermes matcher must catch `hermes-agent/ui-tui/dist/entry.js` resuming `aify-<agentId>` (the 11-TUI leak).
- T-B1.3: `terminal-runtime.js startPty`: replace the claude-only reap with the general reaper keyed on `runtime` + the command's stable identifier — fires for claude/codex/hermes/pi managed spawns.
- T-B1.4: `install.sh`: add wrapper-side kill-prior to the codex/hermes/pi managed branches (mirror the claude wrapper reap). Hermes managed branch must reap prior TUI `entry.js` for `aify-<agentId>` before launching.
- T-B1.5: Tests green; commit.

### Phase B2 — Orphaned MCP children + tree-kill on stop

- T-B2.1: When a managed worker is stopped/superseded/idle-closed, ensure its **aify-comms MCP child** (`mcp/stdio/server.js` stdio) is reaped too (it's a child of the worker; `taskkill /t` should get it — verify, and add a sweep for orphans whose parent worker is gone).
- T-B2.2: `_close_idle_virtual_rpc_workers` (idle-timeout, `worker_idle_close_minutes` when enabled): confirm it measures **online-not-working** (not just terminal idle) and that closing reaps the worker tree via the reaper. This is the operator's "keep one alive until stopped or idle-timeout" spec.
- T-B2.3: Test: a superseded/idle-closed worker leaves no orphan worker or MCP child. Commit.

### Phase B3 — One-time cleanup + verify

- T-B3.1: Reap the existing 11 leaked hermes TUIs (keep each live agent's current one, identified via binding file like the claude reap). Show the operator the keep-set first.
- T-B3.2: Live verify: process count matches reality (1 bridge + N live agents × {1 worker + 1 MCP child}); no duplicates per agent across a few relaunches.

---

## Phase C — Dashboard + docs

- T-C1: Dashboard: render the `awaitingReply` badge (distinct from orange `working`); surface `wakeMode`. (8800 legacy first; 8801 under its parity contract.)
- T-C2: DECISIONS.md: record the event-driven status model + the general worker-hygiene reaper (supersedes the timer-based `turn_busy` staleness as the primary signal). Update skills' status-taxonomy section.

## Phase D — Regression + deploy

- T-D1: Full python + node suites green. No opencode live tests (operator constraint).
- T-D2: Reinstall wrappers (hooks + reapers) + bridge restart picks up terminal-runtime; no container rebuild unless `_compute_live_status_cache` (service) changed → rebuild for Track A. Live end-to-end across runtimes.

## Safety constraints (operator)
- Visible-TUI in dashboard is HARD; no popup windows (windowsHide on all spawns).
- Exactly one managed worker per agent; reapers keyed on per-agent stable identifiers (never cross-agent).
- Event-driven status is the source of truth; the 120s staleness becomes a fallback only.
- Don't run opencode tests on this host.
