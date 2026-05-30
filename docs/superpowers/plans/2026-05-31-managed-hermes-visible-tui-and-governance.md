# Managed Hermes Visible-TUI + Governance + Full Review — Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Driven by the VERIFIED recon blueprint (live-tested 2026-05-31). Honors HARD requirements in memory [[hard-req-visible-tui-in-dashboard]]. CORRECTS the earlier headless api_server pivot (which violated "visible TUI in dashboard").

**Goal:** Managed hermes shows its REAL TUI in the dashboard console (hidden OS process, no popup windows), receives dispatches into that visible session, agent self-replies; lazy-started; safe session retention (no loss/merge/split unless asked); plus offline hard-block, lazy autostart with env auto-bind, race guard. Then full path review + tests + docs.

## VERIFIED BLUEPRINT (recon a48ade9, source + live)

Per managed hermes agent, TWO hidden processes + aify's WS client:
1. **Gateway host:** `hermes dashboard --tui --port <P> --host 127.0.0.1 --no-open --skip-build` — the ONLY server of JSON-RPC WS `/api/ws` (in-process `tui_gateway.dispatch`, shared `_sessions`). `--tui` REQUIRED (else `/api/ws` closes 4403). Auth token from dashboard index HTML `__HERMES_SESSION_TOKEN__`. Plain hidden child (`windowsHide`). NOTE: `hermes gateway run` does NOT serve `/api/ws` (api_server HTTP only) — must be `hermes dashboard --tui`.
2. **Visible Ink TUI in the bridge node-pty:** `hermes --tui` with env `HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<P>/api/ws?token=<token>` (attach as thin client, not spawn stdio gateway) + `HERMES_TUI_RESUME=aify-<agentId>` (resume STABLE session; else forges new each launch). ConPTY renders it windowless into the dashboard xterm.
3. **Delivery:** aify opens its OWN WS to the same `ws://127.0.0.1:<P>/api/ws?token=...`; discover the TUI's EPHEMERAL runtime sid via `session.active_list`; `prompt.submit {session_id, text}` (fallback `session.steer` on `4009 busy`). Events route to the TUI's transport (owner) → TUI renders; aify's submit does NOT rebind transport → NO displacement (VERIFIED). Agent self-replies via `comms_send` (aify needs no event capture).
4. **Session stability:** TUI resumes stable DB session (`aify-<agentId>` title/id) — same transcript across restarts. Runtime sid is EPHEMERAL (`uuid4().hex[:8]` per attach) — aify must RE-DISCOVER via `active_list` after each (re)attach, NEVER cache it.

KEEP from prior work: governance (sticky id, mode FSM, mutual-exclusion, reminders), status deliverability, session-handle capture, claim/report loop, `hermes-gateway-protocol.js` (prompt.submit/steer/busy), teardown. REPLACE: the headless delivery — `mcp/stdio/hermes-managed-gateway-session.js` synthesizes a FAKE terminal (no real TUI); and my api_server `hermes-channel.js` chat path. Both → the real-TUI-in-PTY + WS prompt.submit model. The per-agent api_server `hermes gateway run` daemon is replaced by the `hermes dashboard --tui` gateway host.

## Phase 1 — Managed hermes visible-TUI delivery (the core correction)

- T1.1: Recon-confirm the bridge managed-launch path: how `terminal-runtime.js`/`server.js` runs a managed agent's `terminal.command` in node-pty (visible in dashboard); confirm the hermes wrapper can be the PTY command. Document the exact wiring point.
- T1.2: `install.sh` hermes wrapper — managed launch = (a) start hidden `hermes dashboard --tui --port <perAgentPort>` gateway host (windowsHide), wait healthy, grab token; (b) `exec hermes --tui` with HERMES_TUI_GATEWAY_URL + HERMES_TUI_RESUME=aify-<agentId> IN THE BRIDGE PTY (so it renders in dashboard). Per-agent port from `hermes-endpoint.js`. Kill-prior (TUI + gateway host + reap). Register aify-comms MCP into the TUI's hermes config (self-reply). Drop the api_server-only daemon path.
- T1.3: Delivery worker — replace `hermes-channel.js` api_server delivery + `hermes-managed-gateway-session.js` fake-terminal with: claim run → open WS to per-agent gateway → `session.active_list` to find the TUI's runtime sid for `aify-<agentId>` → `prompt.submit` (steer on busy) → mark delivered → agent self-replies. Reuse `hermes-gateway-protocol.js`. TDD vs a fake gateway fixture modeling create/active_list/prompt.submit/steer + event routing.
- T1.4: `hermes-daemon.js`/endpoint — host is now `hermes dashboard --tui` (WS), ensure-up + health (dashboard index/health) + windowsHide + teardown on stop. Adjust probe/health accordingly.
- T1.5: Live verify: spawn a managed hermes agent → dashboard console shows the REAL TUI (no OS window); dispatch → TUI renders the turn live + agent self-replies threaded back; one gateway-host + one TUI, no popups, no proliferation.

## VERIFIED CHANGE-SET (T1.1 recon adca30a, file:line)

**Process model per managed agent (the key decision):** the PTY runs ONLY the visible `hermes --tui`; a SEPARATE hidden background helper per agent (a) spawns `hermes dashboard --tui --port <agentPort> --no-open --skip-build` (windowsHide) + waits healthy + scrapes `__HERMES_SESSION_TOKEN__` from index HTML, (b) runs the dispatch CLAIM loop, (c) opens its own WS to the gateway, `session.active_list`→pick the TUI's runtime sid for title/key `aify-<agentId>`, `prompt.submit` (steer on 4009 busy). Agent self-replies via comms_send.

- **Service path UNCHANGED:** `_ensure_managed_pty_for_dispatch` (api_v2.py:5083-5161) writes a terminal_session with `command=console_command()` = `hermes-aify --aify-agent <id>` (hermes.py:41-45); bridge `runTerminalControlLoop` (server.js:1653-1755) runs it in node-pty (terminal-runtime.js startPty:180-244, ConPTY=windowless) → streams to dashboard. So the wrapper running `hermes --tui` renders in the dashboard console. CONFIRMED.
- **Wrapper managed branch (install.sh bash:1324-1332, PS:1590-1598)** currently `exec node hermes-channel.js` (headless api_server). REPLACE: kill-prior (TUI + gateway host by port) → start the hidden helper (gateway host + delivery loop) → `exec hermes --tui` with `HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<port>/api/ws?token=<tok>` + `HERMES_TUI_RESUME=aify-<agentId>`. Delete the misleading "no per-agent WS / dashboard-tui removed" comments (install.sh:1339-1349,1372-1377). Drop api_server `aify_hermes_ensure_daemon` from managed branch.
- **Delivery worker:** REUSE hermes-channel.js claim/report/teardown loop (runPollCycle/processClaimedRun :198-312, markRunDelivered :165-175, release+teardown :324-409, dispatchContent, bridgeKind:"channel-sidecar"). SWAP only the delivery call (was apiClient.chatStream) → WS active_list+prompt.submit/steer. Re-discover sid each attach, NEVER cache.
- **hermes-gateway-protocol.js:** REUSE buildPromptSubmitFrame(:11)/buildSessionSteerFrame(:20)/buildSessionInterruptFrame(:91)/isSessionBusyError(:142, 4009)/isSessionNotFoundError(:155, 4010)/pickFreshestSessionFromList(:168). ADD `buildSessionActiveListFrame` + `pickSessionForKey(resp, "aify-<id>")` (active_list + title-match don't exist yet — the one gap).
- **Salvage** gateway-host spawn/token/health from hermes-managed-gateway-session.js (:34-77,:129-187) + ADD windowsHide:true (it's missing, :153 — popup bug). Then DELETE its fake-terminal `_pushTerminalFrame` (:119-127,:208-247) + the dead HermesManagedController gateway branch + `AIFY_HERMES_MANAGED_USE_GATEWAY`.
- **windowsHide:** startPty ConPTY already windowless (no change). The helper's `hermes dashboard --tui` child NEEDS windowsHide:true.
- **Teardown:** helper SIGTERM/release → kill its gateway host + itself (reap, like hermes-channel.js teardown). kill-prior reaps orphans on relaunch.
- **Resident branch (install.sh:1336-1351)** still calls api_server daemon — migrate it to the dashboard-host model in the same pass (or it lingers).
- **Obsolete after (Phase 7):** hermes-channel.js api_server-chat path + hermes-apiserver-client.js + the `hermes gateway run` daemon (hermes-daemon.js) IF nothing else uses it; hermes-managed-gateway-session.js fake-terminal.

## Phase 2 — Lazy autostart + env auto-bind

- T2.1: comms_send to an `available` managed agent with NO `managedEnvironmentId` → auto-select first ONLINE env supporting the runtime (like `comms_spawn` env omission), bind, eager-spawn, deliver. If no online env supports it → reject with a clear "no environment available" (not silent). TDD.

## Phase 3 — Offline = explicit disable / hard-block

- T3.1: an explicit operator DISABLE → `offline` state that (a) never auto-starts, (b) refuses dispatches from OTHER agents (preflight hard-block), (c) is operator-reversible (enable). Distinguish from "available" (auto-startable). Dashboard enable/disable toggle. TDD.

## Phase 4 — Registration race guard

- T4.1: on re-register of an agent whose prior instance is STILL LIVE (fresh heartbeat) in the same mode → ERROR (race) with an actionable message; allow supersession only when the prior is stale. Complements Phase-4 (prior plan) cross-mode guard. TDD.

## Phase 5 — Full path review + tests (trace ALL paths)

- T5.1: Enumerate + trace every control path for managed + resident across runtimes: dispatch/chat delivery, reply, interrupt (`comms_run_interrupt` / `/v1/runs/stop` / gateway), kill/stop session (terminal stop, comms_remove_agent, mode switch release + teardown), session resume/retention (no merge/split), status transitions, dashboard console open/input. For EACH: confirm code path + a test (unit or live). Produce a path×status matrix with pass/fail.
- T5.2: Adversarially verify the safety claims: no session duplication on restart, no merge/split without explicit confirm, no orphan processes, no popup windows. Fix any gap found.

## Phase 6 — Docs / skills / dashboard instructions

- T6.1: Update `DECISIONS.md` (the new managed-hermes visible-TUI model; supersede the headless-era notes; record the api_server-pivot reversal + why), `README.md` API/usage if affected.
- T6.2: Update skills `.claude/skills/aify-comms/SKILL.md` + `aify-comms-debug/SKILL.md` AND the `.agents/skills/*` mirrors (status taxonomy: available=lazy-autostart, offline=disabled-hard-block; managed=visible-TUI-in-dashboard; the governance flows; resident vs managed).
- T6.3: Dashboard in-app instructions/help text (managed console = real TUI; enable/disable; resident takeover; session-changed confirm/keep).

## Phase 7 — Cleanup + final report

- T7.1: Remove now-dead code (headless `hermes-managed-gateway-session.js` fake-terminal, obsolete api_server-only daemon path if fully unused — keep what's reused). Full regression (node + python), container rebuild, reinstall integrations. Confirm zero new failures.
- T7.2: Live end-to-end across the matrix; mark tasks; final report to operator.

## Safety constraints (operator, non-negotiable)
- No session loss / duplication / unasked merge or split (sticky identity + stable resume + active_list re-discovery).
- No popup OS windows (windowsHide on all child spawns; TUI only in node-pty rendered to dashboard).
- Visible TUI in dashboard console is a HARD requirement — never trade it away.
- Test EVERY path; docs/skills/dashboard current before "done".
