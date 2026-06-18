# Next session — turnkey plan (prepped 2026-06-18, pre-compaction)

> **PROGRESS 2026-06-18 (post-compaction execution session):** Part 1 status fix SHIPPED (cf6ef25) —
> but via a SLOW RE-PROBE (not the audit's fresh-output gate, which had a catch-22; see the status memory).
> Staged via install.sh; needs an env-bridge restart + live-validation to close #224. Part 2 HIGH ports
> SHIPPED + browser-verified: composer Type/Priority/Subject (cfecedb, #230), Identity Directory +
> Spawn-requests table (5511c3a, #231). UX SHIPPED: chat-as-hero default-collapse + status legend
> (f3da47d). UX#4/#5/#6 assessed already-good (prior polish). STILL OPEN (#232): UX#7 button audit + the
> MED endpoint-button ports below. All pushed (HEAD f3da47d).



> Operator said: **"lets do both"** = (1) fix the bridge spinner-lease so statuses are 100% correct, AND (2) keep grinding the new-dashboard UX/feature list. Then: verify holistically, port the missing old-dashboard features that make sense, and report.

Two detailed source docs back this plan — read them first:
- **Status:** `docs/superpowers/plans/2026-06-18-status-holistic-audit.md` (per-harness×mode verdicts + the residual root cause + the exact bridge fix).
- **Dashboard parity:** `docs/superpowers/plans/2026-06-18-dashboard-feature-parity.md` (full old-vs-new feature inventory + prioritized port shortlist).

Current HEAD: `a93e54e` (all status/dashboard fixes pushed). Service is single-worker uvicorn (HARD constraint — in-memory `_LIVE_STATE_CACHE`). DB locks RESOLVED (see [[db-lock-write-serialization]]).

---

## PART 1 — Status: fix the one residual (claude/managed long tool-free generation)

**Verdict from the audit: 5 of 6 harness×mode cells are CORRECT today** (codex×both, hermes×both, claude/resident). The single operator-visible gap is **claude / managed during a LONG tool-free generation** (>20s, no tool call, dashboard console closed): it can briefly flip `working → online` while still working.

**Root cause (pinned, not guessed):** the bridge console keepalive's idle-grace gate latches on a STALE `consoleClass==="idle"` reading and then PAUSES the SIGWINCH nudge — but that nudge is the only thing that could force claude to re-emit and prove it's actually working. Self-reinforcing dead state → the 20s console-working lease expires → `derive()` drops to `online`. Full trace in the audit doc (`terminal-runtime.js:585-607`, `_kaIdleTicks`, `consoleKeepaliveIdleGraceTicks=30`).

### Task 1.1 (load-bearing): evidence-based idle-grace gate
- File: `mcp/stdio/terminal-runtime.js` `_armConsoleKeepalive` (~585-607).
- Stamp `st._lastOutputAt` in `_handleOutput`.
- In the keepalive tick: increment `_kaIdleTicks` only when `consoleClass==="idle"` **AND new output arrived since the previous tick** (i.e. the idle reading is FRESH, provoked by this SIGWINCH). If a SIGWINCH tick yields NO new output, do NOT count it toward the idle streak (treat the stale class as unknown / keep nudging).
- Effect: a genuinely idle console still re-emits its idle footer each SIGWINCH → still accrues idle ticks → still eventually pauses (zero churn at rest). A working-but-quiet console whose stale class happens to be `idle` will NOT accrue ticks → keepalive keeps nudging → next footer re-classifies to `working` → lease refreshes → status stays `working`.
- Lower-effort equivalent: reset `_kaIdleTicks` whenever a SIGWINCH yields no output within one tick.

### Task 1.2 (defense-in-depth): pulse on `unknown` when a turn is known in-flight
- File: `mcp/stdio/server.js` `decideConsolePulse` (~887-913).
- When the bridge KNOWS a turn is in flight (it set turn_busy=true and hasn't posted an authoritative end), treat `consoleClass==="unknown"` as a lease refresh too. Keep strict `working`-only when no turn is known (never manufactures working at rest).

### DO NOT re-add the raw terminal-output "working" signal
It conflates streaming the FINAL reply with active work (broke `test_attached_console_without_active_run_reports_active_not_working` + 11 others). Keep the fix tied to spinner classification / known-turn state.

### Verify + deploy
- `node --check mcp/stdio/terminal-runtime.js && node --check mcp/stdio/server.js`.
- Bridge change → **rerun `install.sh` (copies mcp/stdio + node_modules into ~/.aify-comms/) AND restart `claude-aify`** — wrapper restart alone is NOT enough; no container rebuild.
- Live-validate: a managed claude doing a long tool-free generation with the dashboard Console CLOSED must stay `working` the whole time (watch sc-claude / sc-architect on the dashboard, or sample `comms_agent_info`).
- Close task **#224** when this lands (the grace was the stopgap; this is the real fix).

---

## PART 2 — New dashboard: port the real feature regressions + UX polish

From the parity audit, almost every gap is a missing UI affordance for an endpoint that **already exists** — low effort to re-expose. Do these in priority order; browser-verify each against the old dashboard (chrome-devtools MCP) before claiming done.

### HIGH — genuine capability loss
1. **Chat composer: Type + Priority + Subject fields.** The clearest functional regression. New chat can't send review/approval/error types, can't mark urgent, can't set a task subject. Old: `dashboard.html` composer ~:1402+ (`chat-type`, `chat-priority`, `chat-subject-input`). Add to `service/new_dashboard/index.html` composer + wire in `chat.js` send payload (`type`, `priority`, `subject`). The send endpoint already accepts them.
2. **Identity Directory** (modal). Only place to audit roles / resident bindings and clean offline-CLI rows. Old: `dashboard.html:3837`.
3. **Spawn-requests queue/history table.** Failed/queued spawns have nowhere to surface in the new dashboard. Old: `renderSpawnRequests()` `dashboard.html:7376`. Add to the Environments page.

### MEDIUM — endpoints exist, just no button
4. Work Loop hygiene buttons: **Repair Delivered Reads** (`:4501`), **Repair Handoffs** (`:1242`), **Preview Reminders / dry-run** (`:1240`) — add to Diagnostics page.
5. **CLI takeover + resume command** on a session (`:4846`, `:4740`).
6. **Environment edit/reset workspace roots + copy start command** (`:5070`, `:4699`).
7. **Chat Peek mode** (`:1341`) and a **dedicated global message search** panel (`:5777`).

### LOW — nice-to-have
Compaction-history viewer, follow-up/open-by-ID/image-paste, per-issue mute/dismiss + inbox-hygiene, restore idle/stale/deleted chips & "oldest" sort, Settings reset button. Skip unless time permits.

### UX/UI polish list (operator explicitly requested this list)
1. **Default-collapse the Needs-Attention strip** (or slim it to a one-line banner) so chat is the hero on landing.
2. **Status legend/tooltip** — hovering a dot shows the state meaning + the reason.
3. **More conversation-pane height** (strip + rail currently compete for vertical space).
4. **Chat rail density** — tighten DM rows; refine favorite-star + unread badge.
5. **Composer layout** — tidy the expects-reply / queue-if-busy / attach row + Send placement (especially after adding Type/Priority/Subject in HIGH-1).
6. **Message bubbles** — clearer sent-vs-received, timestamps, woke/stored badges.
7. **Consistent button sizing/spacing** across Sessions/Environments/Diagnostics (reuse the chat chip system).

---

## PART 3 — close-out
- Run the full suite (`docker exec aify-comms-service python -m pytest -q` + node `--check`s + `chat.test.mjs`); keep green.
- Container rebuild only if `service/` changed (`bash scripts/stamp.sh && docker compose up -d --build`).
- Push. Update DECISIONS.md / KNOWN_ISSUES.md / skills (.claude + .agents mirrors byte-identical) if behavior changed.
- Report to the operator with what shipped + an honest residual list.

## Operator constraints (carry forward)
- Single-worker uvicorn (in-memory cache correctness). Never `--workers >1` without Redis/sticky.
- NEVER run/execute opencode (GPU-tanking local LLM).
- No secrets in commits. Commit before container rebuild.
- Root-cause, not band-aids; don't over-claim "done" — verify first.
- Commit messages end: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
