# Known Issues & Concerns — aify-comms

Living list of known limitations, deferred work, and things to watch. Complements [DECISIONS.md](DECISIONS.md) (rationale) and the `aify-comms-debug` skill (troubleshooting). Last reviewed 2026-06-02.

## Status / liveness / worker-hygiene

### Open limitations (not cleanly fixable yet)

- **Resident direct-typed hermes work can show `online` while actually working (#172, residual only).** aify-comms detects a hermes turn via (a) an aify dispatch run, (b) the `pre_llm_call` turn-start hook, or — for MANAGED hermes, as of 2026-06-02 — (c) the delivery loop's gateway-session-idle turn-end event. The **managed** case is now event-driven end-to-end: the loop sets `turn_busy` on turn-start and clears it the moment the gateway session goes idle (`/turn-end`), covering dispatch, autonomous, AND direct-typed turns that run through the managed gateway. The residual is the **resident** (operator-launched `hermes-aify`, no managed delivery loop) direct-typed turn: it still relies on the `pre_llm_call` turn-start hook (no upstream turn-end hook) plus the long `TURN_BUSY_BACKSTOP_SECONDS` (15m) staleness ceiling for cleanup, so a resident-typed turn can briefly read `online` between hook-fire windows. Closing it would need an upstream resident turn-end hook or a resident-side gateway idle watcher. Tracked in task #172 / #171.

> **Note (gateway-liveness, 2026-06-02):** managed-hermes `online` no longer derives from gateway presence at all — it now requires a live delivery-loop CLAIMER lease (see "Resolved 2026-06-02"). The older `gatewayOk = !!gatewayUrl` presence check + reactive/proactive probes (`hermes-gateway-liveness.js`) still backstop the resident `available` capability, but the managed false-`online`-from-presence path is closed.

### Deferred (cost/benefit)

- **The 60s reconcile sweep doesn't push status deltas over WebSocket.** When the periodic self-heal corrects a stale status, dashboards see it on their next poll rather than instantly. Event-driven push (C1) already covers operator-driven transitions; the reconcile loop has no WS handle, so wiring a broadcast there is awkward for modest benefit. Tracked in task #171.

### Watch (revisit only if the symptom recurs)

- **Managed-claude console churn / sidecar self-exit guard misfire.** The channel-sidecar self-exit guard reads `process.ppid` (`ORIGINAL_PPID`) to detect a dead controlling parent (`mcp/stdio/claude-channel.js`). In the managed-claude process tree (`cmd → bash → claude.exe → node`), the immediate parent can be a transient `cmd`/`bash` that exits while `claude.exe` lives — which could make the guard skip liveness beats or self-exit a healthy worker, producing ghost-console reaps + console re-spawn churn. Observed once on sc-claude (2026-06-01), healthy afterward. Do NOT harden preemptively; if console drops become frequent, walk to the real `claude.exe` ancestor (or use a more robust parent signal than the immediate ppid). Tracked in task #173.

## Resolved 2026-06-02 (managed-hermes lifecycle + restart-teardown batch)

Branch `feature/managed-hermes-lifecycle` made managed runtimes bridge-owned with a single lifecycle owner. The mechanism for each:

- **Daemon/delivery-loop split + `hermes.exe` proliferation — RESOLVED.** The delivery loop is now the single supervisor of the managed-hermes triad (gateway host, loop, console PTY): it registers liveness BEFORE bringing the gateway up, port-kills the gateway host on teardown, and self-exits on any terminal condition (410 / dead gateway / release). Combined with the per-agent daemon kill-prior (`8fd3da9`), one process set per agent. No more accumulation across restarts/churn.
- **"Team stranded after restart / runs stuck `claimed`" — RESOLVED.** Restarting `aify-comms` is now a guaranteed **clean slate**: the env bridge tears down all managed sessions it owns on shutdown and boot-sweeps survivors of a crashed predecessor (both scoped to owned agents via `cwdRoots`, never resident/other-env). No dead claimer survives a restart holding a busy agent. The 60s requeue (`a76afb5`) + the new queued-run backstop reaper (`queued_run_backstop_seconds`, 180s) recover/close any in-flight stragglers.
- **"Online but no live worker" / "online but deaf" — RESOLVED.** `online` now means *deliverable*: managed hermes joined the channel-sidecar-delivery gate, so `online` requires a live, non-superseded claimer (not gateway/console presence). An explicit delivery-loop **claimer lease** (acquired when the loop becomes a live claimer, released on clean teardown) is the positive signal; a cleanly-exited loop is immediately non-deliverable. Sends to a deaf target (lease recorded but no longer live) **fail fast** with an actionable reason and write no run. An agent that never recorded a lease stays cold-startable (lazy-autostart preserved).
- **Queued-run pileups (`buffer_full`) — RESOLVED.** The fail-fast deaf-target send stops the pileup at source; the queued-run backstop reaper fails any never-claimed queued run past its window and mirrors the failure to the sender.
- **Managed hermes false-`working` / never-`working` (Bug A/B) — RESOLVED.** Turn-end is event-driven: the loop clears `turn_busy` the moment the gateway session goes idle, so `working` flips off immediately (no 120s wait) and a queued run delivers on the next claim. The second-based windows are demoted to backstops: STATUS uses `TURN_BUSY_BACKSTOP_SECONDS` (15m, dropped-event ceiling); the CLAIM-gate keeps the short `TURN_BUSY_STALE_SECONDS` (120s) so a queued run is never stranded behind a possibly-finished turn for 15m.
- **Channel completed-without-reply strand — MITIGATED.** A managed/channel agent that deferred its `require_reply` reply to a second turn stranded it (idle session not re-woken). The channel wake text now directs a same-turn `comms_send(inReplyTo=...)` reply and warns the session won't be re-woken (`claude-channel.js`).
- **Cruft GC — RESOLVED.** Port/key gateway markers cleared on terminal teardown; dead-PTY console rows reaped (host-reported `process_id` liveness); orphaned `dispatch_runs` for tombstoned agents pruned.

## Design notes carried forward

- **The three compensating carve-outs were evaluated and KEPT (not removed).** Sidecar claim-path self-heal, complementary-pair protection in `_record_bridge_registration`, and idle-resident-accepts-sidecar in `_resident_bridge_is_fresh`. The unconditional liveness beat only refreshes `last_seen` and short-circuits superseded rows — it never un-supersedes a row nor prevents register-time supersession — so each carve-out still does real work (each removal probe broke its behaviour test). See DECISIONS.md. Tracked in task #154.

## Pre-existing backlog (separate from the 2026-06-01 status work)

- **#123** — split `mcp/stdio/runtimes.js` into per-concern modules.
- ~~**#134** — PostToolUse hook isn't refreshing the turn_busy heartbeat (claude).~~ **RESOLVED** (2026-06-02): `mcp/stdio/notify-check.js` re-pulses `{turnBusy:true, turnRuntime:"claude-code"}` on every PostToolUse, and the server invalidates the live-state cache on that write (`0189ab1`), so long multi-tool claude turns stay `working` past the 120s stale window.
- **#136** — codex managed stored `session_handle` goes stale; `no_rollout` on resume.
- **#137** — pi managed Console PTY empty (omp not running in a foreground TTY).
