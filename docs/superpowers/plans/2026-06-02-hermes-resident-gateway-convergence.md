# Hermes Resident → Gateway-Host Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make aify-comms messages sent to a **resident** Hermes agent render in that agent's **visible operator terminal**, by routing the resident launch through the same gateway-host + delivery-loop model the managed launch already uses.

**Architecture:** Hermes has two unrelated "gateways": the `tui_gateway` JSON-RPC WebSocket (`/api/ws`, mounted by `hermes dashboard --tui`) which *renders in an attached TUI*, and the `gateway run`/`api_server` REST server which *runs turns and persists to state.db but renders nothing in any TUI*. The managed branch attaches the visible TUI to a hidden `tui_gateway` host and runs a background delivery loop that does `session.active_list` → `pickSessionForKey("aify-<id>")` → `prompt.submit` against the visible TUI's ephemeral sid — so messages render. The resident branch instead used the REST `api_server` daemon and started **no** delivery loop, so nothing rendered. The fix converges resident onto the managed gateway-host path; the service already emits claimable `execution_mode='resident'` runs and the loop already claims `["channel","resident"]`, so **no service change is required**.

**Tech Stack:** Bash (the `install.sh`-generated `~/.local/bin/hermes-aify` wrapper, written via heredoc — note `\$` escaping inside the heredoc), Node MCP bridges under `mcp/stdio/`, Hermes v0.15.1.

---

## Root-cause evidence (grounded in code)

| Fact | Location |
|------|----------|
| REST `api_server` chat path does NOT emit to `tui_gateway` WS clients (renders nothing in a TUI) | research: `gateway/platforms/api_server.py` `_emit` writes SSE only; no `tui_gateway` import |
| Managed branch brings up `tui_gateway` host + delivery loop + exports `AIFY_HERMES_GATEWAY_URL`/`HERMES_TUI_GATEWAY_URL` | `install.sh:1486-1558` |
| Resident branch uses REST `api_server` daemon, NO loop, NO gateway-URL export | `install.sh:1570-1585` |
| Delivery loop claims BOTH channel and resident runs | `mcp/stdio/hermes-managed-host.js:1083` (`executionModes: ["channel","resident"]`) |
| Loop delivers via active_list → pickSessionForKey → prompt.submit (renders in visible TUI) | `mcp/stdio/hermes-managed-host.js:15-17, 760-777` |
| Service emits `execution_mode='resident'` for resident hermes with live ws:// gatewayUrl | `service/routers/api_v2.py:1718-1752` |
| Registered sessionMode comes from `AIFY_SESSION_MODE`, exported BEFORE either launch branch (so broadening the launch guard does not mislabel residents) | `install.sh:1270`; `mcp/stdio/server.js:421,1099` |

---

## File Structure

- **Modify:** `install.sh` — the Hermes wrapper heredoc only. Two edits: (1) broaden the gateway-host launch guard so it fires for resident launches too; (2) delete the now-unreachable REST-daemon resident branch. No new files.
- No service, bridge, or container changes. The container was already rebuilt and is healthy; `config.yaml` already points at `http://localhost:8800`.

---

### Task 1: Broaden the gateway-host launch guard to serve resident + managed

**Files:**
- Modify: `install.sh:1475-1486` (the `MANAGED launch` comment block + guard line)

- [ ] **Step 1: Replace the header comment + guard**

Replace the block that currently reads (lines 1475-1486):

```bash
# MANAGED launch (visible-TUI model, Plan 2026-05-31): \`--aify-agent\` present
# AND session-mode resolved to managed (bridge-spawned in the dashboard PTY).
#   1. kill-prior: reap a stale delivery loop + gateway host for this agent.
#   2. ensure-host: bring up the HIDDEN per-agent \`hermes dashboard --tui\`
#      gateway host (windowsHide) and learn its {port,token,wsUrl}.
#   3. start the background delivery loop (detached, survives the exec below): it
#      claims dispatch runs and prompt.submits them into the TUI's session.
#   4. exec \`hermes --tui\` IN THIS PTY, attached to the gateway host and
#      resuming the STABLE session \`aify-<agentId>\` — the REAL TUI renders
#      windowless in the dashboard console. The in-session agent self-replies via
#      comms_send (wake-only; symmetric with claude).
if [ -n "\$HERMES_AIFY_AGENT_ID" ] && [ "\$HERMES_AIFY_SESSION_MODE" = "managed" ] && [ \${#HERMES_ARGS[@]} -eq 0 ]; then
```

with (drops the `= "managed"` condition; both modes now use this path):

```bash
# GATEWAY-HOST launch (visible-TUI model) — serves BOTH managed and resident
# agent-id launches (convergence 2026-06-02). A normal \`hermes --tui\` spawns its
# tui_gateway over STDIO, which no external WS injector can reach, so an injected
# aify message can never render in it. The ONLY topology that renders in a visible
# TUI is: one shared \`hermes dashboard --tui\` gateway host that BOTH the visible
# TUI attaches to AND the delivery loop injects into (prompt.submit against the
# visible TUI's discovered sid). The former resident path used the REST api_server
# daemon (renders nothing) + started no delivery loop — that was the resident
# "nothing arrives in my terminal" bug.
#   1. kill-prior: reap a stale delivery loop + gateway host for this agent.
#   2. ensure-host: bring up the HIDDEN per-agent \`hermes dashboard --tui\`
#      gateway host (windowsHide) and learn its {port,token,wsUrl}.
#   3. start the background delivery loop (detached, survives the exec below): it
#      claims channel/resident dispatch runs and prompt.submits them into the
#      visible TUI's session.
#   4. exec \`hermes --tui\` IN THIS PTY, attached to the gateway host and
#      resuming the STABLE session \`aify-<agentId>\`. For a managed launch the PTY
#      is the dashboard console (windowless); for a resident launch the PTY is the
#      operator's own terminal (visible). Same exec, same delivery — the only
#      difference is who owns the PTY. The agent self-replies via comms_send.
# The agent still REGISTERS with its resolved sessionMode (AIFY_SESSION_MODE,
# exported at line ~1270 before this branch), so residents stay resident and
# managed stays managed — only the LAUNCH mechanism is now shared.
if [ -n "\$HERMES_AIFY_AGENT_ID" ] && [ \${#HERMES_ARGS[@]} -eq 0 ]; then
```

- [ ] **Step 2: Verify the heredoc still parses**

Run: `bash -n install.sh`
Expected: no output (exit 0). A `\$` escaping mistake inside the heredoc shows here.

---

### Task 2: Delete the now-unreachable REST-daemon resident branch

**Files:**
- Modify: `install.sh:1561-1585` (the `RESIDENT/interactive launch` comment + `if` block)

**Why:** After Task 1 the broadened guard catches every `--aify-agent` launch and ends in `exec` (process-replacing) / `exit`, so this block is dead code. Leaving it is misleading and keeps the broken REST path one edit away from returning.

- [ ] **Step 1: Replace the dead block with a pointer comment**

Replace lines 1561-1585 (from the `# RESIDENT/interactive launch with an agent id:` comment through the closing `fi` of that `if` block, ending at `exit \$_hermes_rc` / `fi`):

```bash
# RESIDENT/interactive launch with an agent id: attach an operator TUI to THIS
# agent's pinned session. \`--resume <pinned session>\` resumes the SAME stable
# DB session (\`aify-<agentId>\`) the managed model drives, so the operator sees
# one continuous transcript.
# TODO(managed-hermes visible-TUI, Phase 1 follow-up): migrate this resident path
# off the api_server \`hermes gateway run\` daemon onto the same hidden
# \`hermes dashboard --tui\` gateway-host model the managed branch now uses (so it
# attaches via HERMES_TUI_GATEWAY_URL too). For now it keeps using
# aify_hermes_ensure_daemon so resident launch is NOT broken by this change.
if [ -n "\$HERMES_AIFY_AGENT_ID" ] && [ \${#HERMES_ARGS[@]} -eq 0 ]; then
  aify_hermes_ensure_daemon "\$HERMES_AIFY_AGENT_ID"
  AIFY_HERMES_PINNED_SESSION="aify-\$(printf '%s' "\$HERMES_AIFY_AGENT_ID" | tr -c 'a-zA-Z0-9_-' '-' | sed -E 's/^-+|-+\$//g')"
  # Daemon teardown leak fix (fix/hermes-leak P3): the resident path starts a
  # per-agent api_server daemon (aify_hermes_ensure_daemon) but historically
  # bare-\`exec\`d the TUI, replacing the shell so the daemon was never stopped —
  # a resident TUI that exits leaked its \`hermes gateway run\` daemon. Run the
  # TUI as a child (no exec) and stop the daemon on exit via a trap, so the
  # daemon stop (killByPort + tracked-pid + clearGatewayMarkers) always runs.
  trap 'node "\$AIFY_HERMES_DAEMON_CLI" stop "\$HERMES_AIFY_AGENT_ID" >/dev/null 2>&1 || true' EXIT
  "\$HERMES_RUNTIME_COMMAND" --tui "\${HERMES_PERMISSION_FLAGS[@]}" --resume "\$AIFY_HERMES_PINNED_SESSION"
  _hermes_rc=\$?
  trap - EXIT
  node "\$AIFY_HERMES_DAEMON_CLI" stop "\$HERMES_AIFY_AGENT_ID" >/dev/null 2>&1 || true
  exit \$_hermes_rc
fi
```

with:

```bash
# RESIDENT agent-id launch: handled by the unified GATEWAY-HOST branch above
# (convergence 2026-06-02). The former REST api_server-daemon resident path was
# removed — it rendered nothing in the visible TUI (api_server chat does not emit
# to tui_gateway WS clients) and started no delivery loop, so injected aify
# messages never appeared in the operator's terminal. \`aify_hermes_ensure_daemon\`
# / \`AIFY_HERMES_DAEMON_CLI\` remain defined above for any explicit fallback use
# but are no longer the resident default.
```

- [ ] **Step 2: Verify the heredoc still parses**

Run: `bash -n install.sh`
Expected: no output (exit 0).

- [ ] **Step 3: Commit the wrapper change**

```bash
git add install.sh docs/superpowers/plans/2026-06-02-hermes-resident-gateway-convergence.md
git commit -m "fix(hermes): resident launch uses gateway-host model so injected messages render in the visible TUI

Converge the resident hermes launch onto the same hidden tui_gateway host +
background delivery loop the managed branch uses. The old resident path used the
REST api_server daemon (which never emits to tui_gateway WS clients) and started
no delivery loop, so aify-comms messages ran the turn server-side but never
rendered in the operator's terminal. The service already emits claimable
execution_mode=resident runs and the loop already claims [channel,resident], so
this is a wrapper-only fix. Registration mode is unchanged (AIFY_SESSION_MODE is
exported before the launch branch), so residents stay resident.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Install the fixed wrapper and verify it statically

**Files:** none (operational)

- [ ] **Step 1: Install the hermes integration (single-line command — avoid the multi-line shell-snapshot tangle)**

Run: `bash /…/aify-claude/install.sh --client hermes --server-url http://localhost:8800`
Expected: completes, rewrites `~/.local/bin/hermes-aify`. Watch for the FATAL gateway-host line (should not appear).

- [ ] **Step 2: Assert the generated wrapper has the broadened guard and no REST-daemon resident branch**

Run: `grep -n 'hermes dashboard\|ensure-host\|managed-host.js run\|AIFY_HERMES_GATEWAY_URL' ~/.local/bin/hermes-aify | head`
Expected: shows the gateway-host `ensure-host`, the `managed-host.js run` loop, and the `AIFY_HERMES_GATEWAY_URL` export reachable for an agent-id launch.

Run: `grep -c 'aify_hermes_ensure_daemon "\$HERMES_AIFY_AGENT_ID"' ~/.local/bin/hermes-aify` — note: in the installed wrapper the `\$` is resolved, so grep for `aify_hermes_ensure_daemon "$HERMES_AIFY_AGENT_ID"`.
Expected: 0 reachable call sites in a launch branch (the function definition may remain).

---

### Task 4: Live two-resident round-trip (handoff — only the operator can run this)

**Files:** none (live test)

- [ ] **Step 1: Launch resident-1 in a terminal**

Run: `hermes-aify --aify-agent hermes-resident-1`
Expected: a visible Hermes TUI; the wrapper prints `managed gateway host ready: {...}`.

- [ ] **Step 2: Launch resident-2 in a second terminal**

Run: `hermes-aify --aify-agent hermes-resident-2`
Expected: same.

- [ ] **Step 3: From resident-1, send to resident-2**

In resident-1's TUI, have the agent call `comms_send` to `hermes-resident-2`.
Expected: the incoming message **renders in resident-2's visible terminal** (the bug symptom — "nothing arrives" — is gone), and resident-2's `comms_send` reply renders back in resident-1.

- [ ] **Step 4: Confirm gateway binding is the visible TUI**

Run: `curl -s http://localhost:8800/api/v1/agents | python3 -c "import sys,json; d=json.load(sys.stdin)['agents']; [print(k, json.loads(d[k].get('runtimeConfig','{}') or '{}').get('gatewayUrl')) for k in d if 'resident' in k]"`
Expected: each resident's `gatewayUrl` is a `ws://…/api/ws?token=…` pointing at the live gateway host (not a stale 9xxx orphan).

---

## Self-Review

**1. Spec coverage:** The reported symptom ("wrote to ci-senior-dev, he started working, nothing arrived in resident terminal") is the resident REST/no-loop path → Tasks 1+2 replace it with the rendering gateway-host path. "Why can't re-register fix it" is answered upstream of this plan (re-register re-reads the wrapper-frozen env; relaunch with the fixed wrapper is what binds correctly) — Task 3 installs that fixed wrapper. No service change needed (evidence table).

**2. Placeholder scan:** No TBD/TODO-as-work/"handle edge cases". The retained `aify_hermes_ensure_daemon` is explicitly noted as no-longer-default, not a placeholder.

**3. Type/name consistency:** Guard variables (`HERMES_AIFY_AGENT_ID`, `HERMES_ARGS`, `AIFY_HERMES_PINNED_SESSION`, `HERMES_TUI_WS_URL`) match the managed branch they now share. `AIFY_SESSION_MODE` (registration) vs `HERMES_AIFY_SESSION_MODE` (wrapper-local) distinction is preserved.

## Risk / follow-ups (not blocking)

- The shared branch hard-fails (`exit 1`) if `ensure-host` can't bring up the gateway host — same as managed today. If operators want a degraded plain-TUI fallback for resident, add it after live validation.
- If two terminals launch the same agent, `aify_hermes_kill_prior` reaps the older one (one-owner-per-agent) — existing managed behavior, now applies to resident too.
