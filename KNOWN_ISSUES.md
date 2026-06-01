# Known Issues & Concerns — aify-comms

Living list of known limitations, deferred work, and things to watch. Complements [DECISIONS.md](DECISIONS.md) (rationale) and the `aify-comms-debug` skill (troubleshooting). Last reviewed 2026-06-01.

## Status / liveness / worker-hygiene

### Open limitations (not cleanly fixable yet)

- **Autonomous / direct-typed hermes work shows `online` while actually working.** aify-comms detects a hermes turn only via (a) an aify dispatch run, or (b) the `pre_llm_call` turn-start hook. Work a hermes agent does autonomously (not driven by an aify dispatch), or direct TUI/gateway input that doesn't trip the hook, produces no `turn_busy` signal — so status reads `online` instead of `working`. The long **managed**-turn case IS fixed (in-flight turn re-pulse, `mcp/stdio/hermes-turn-repulse.js`). Fixing the autonomous case needs new gateway instrumentation (a hermes busy/streaming signal over WS, or a turn-end hook). Tracked in task #172 / #171.

### Deferred (cost/benefit)

- **The 60s reconcile sweep doesn't push status deltas over WebSocket.** When the periodic self-heal corrects a stale status, dashboards see it on their next poll rather than instantly. Event-driven push (C1) already covers operator-driven transitions; the reconcile loop has no WS handle, so wiring a broadcast there is awkward for modest benefit. Tracked in task #171.

### Watch (revisit only if the symptom recurs)

- **Managed-claude console churn / sidecar self-exit guard misfire.** The channel-sidecar self-exit guard reads `process.ppid` (`ORIGINAL_PPID`) to detect a dead controlling parent (`mcp/stdio/claude-channel.js`). In the managed-claude process tree (`cmd → bash → claude.exe → node`), the immediate parent can be a transient `cmd`/`bash` that exits while `claude.exe` lives — which could make the guard skip liveness beats or self-exit a healthy worker, producing ghost-console reaps + console re-spawn churn. Observed once on sc-claude (2026-06-01), healthy afterward. Do NOT harden preemptively; if console drops become frequent, walk to the real `claude.exe` ancestor (or use a more robust parent signal than the immediate ppid). Tracked in task #173.

## Design notes carried forward

- **The three compensating carve-outs were evaluated and KEPT (not removed).** Sidecar claim-path self-heal, complementary-pair protection in `_record_bridge_registration`, and idle-resident-accepts-sidecar in `_resident_bridge_is_fresh`. The unconditional liveness beat only refreshes `last_seen` and short-circuits superseded rows — it never un-supersedes a row nor prevents register-time supersession — so each carve-out still does real work (each removal probe broke its behaviour test). See DECISIONS.md. Tracked in task #154.

## Pre-existing backlog (separate from the 2026-06-01 status work)

- **#123** — split `mcp/stdio/runtimes.js` into per-concern modules.
- **#134** — PostToolUse hook isn't refreshing the turn_busy heartbeat (claude).
- **#136** — codex managed stored `session_handle` goes stale; `no_rollout` on resume.
- **#137** — pi managed Console PTY empty (omp not running in a foreground TTY).
