# aify-comms: Design Decisions & Current Limits

Short rationale log for non-obvious choices, plus the current runtime limits. If you're wondering *why* the service behaves a certain way, this file beats guessing from the code.

## Resident same-handle carve-out is heartbeat-aware (2026-05-23)

**Decision.** The supersession carve-out that protects same-logical-owner resident re-registers (so an `omp --mode rpc` child doesn't kill its parent's in-flight runs) now ONLY applies when the prior bridge_instance is still heartbeating within the 5-minute stale window. A bridge whose `last_seen` is older than that is a dead process — its row is superseded so the table doesn't accumulate zombie entries across restarts.

**Why.** Operator-reported 2026-05-23: comms-tech-lead had 10+ leaked bridge_instances from May 21–22 claude-aify restarts, all sharing the same `session_handle` and `session_mode='resident'`, none superseded because the pre-fix carve-out unconditionally protected same-handle resident rows regardless of liveness. Each restart wrote a new row but left the old one alive in the DB.

**Why this preserves the original protection.** Legitimate multi-window resident scenarios (two operator shells on the same identity, or an RPC child registering under its parent's session_handle) heartbeat continuously while the processes are alive — their rows stay within the 5-min window, the carve-out still protects them. Only DEAD same-handle rows get superseded.

Test: `test_resident_stale_same_handle_bridge_IS_superseded_by_fresh_reregister`.

## Codex managed dispatch uses persistent `codex app-server` (2026-05-23)

**Decision.** Managed codex dispatches go through a long-lived `codex app-server` child per agent (`mcp/stdio/codex-session.js` — `CodexSession`), mirror of HermesSession and PiSession. On first dispatch the bridge spawns `codex app-server`, runs `initialize` + `initialized`, then `thread/start` (or `thread/resume` if a `threadId` is bound on the agent record). Every subsequent dispatch reuses the same RPC connection and threadId — `turn/start` only. The codex `--app-server` URL path (resident WebSocket) is unchanged; only the spawn-fresh managed path is pooled.

**Why.** Symmetry with pi (persistent `omp --mode rpc`) and hermes (persistent `hermes acp`). The prior managed-codex controller spawned a fresh `codex app-server` per turn, paying ~1–3s of startup cost every dispatch and never giving the operator a "one PID per agent" mental model. After this change all four managed runtimes (pi, hermes, codex, opencode) follow the same UX shape: one persistent process per agentId, native conversation continuity, streaming notifications into the synth terminal, idle-reaper cleanup.

**Why per-agent pool (not per-thread).** The thread is the conversation unit; the codex app-server can host many threads but in our model each agent owns one. Pool key = agentId keeps the dispatcher and pool aligned.

**Why we kept the WS app-server path separate.** Resident codex with a shared `codexAppServerUrl` is already pooled at the app-server process level — the WebSocket is the persistent backing. Adding a CodexSession in front would be redundant and harder to reason about. Routing in `createCodexController`: `executionMode==='managed' && !hasCodexLiveAppServer(config)` → CodexSession; else → legacy controller.

**RPC handler swap, not multiplexer.** `createRpcClient` / `createWebSocketRpcClient` gained `setOnNotification(handler)` so the pooled RPC can bind a fresh per-turn handler. Simpler than a multiplexer and keeps the per-turn state (`activeTurnId`, `finalText`, `activeItems`, quiet-timeout, MCP-tool-stall detection) in `CodexSession._runTurnInner` where it can be reasoned about as a single function.

Tests in `mcp/stdio/tests/codex-session.test.js` + `fixtures/fake-codex-app-server.mjs`.

## Hermes managed dispatch uses persistent `hermes acp` JSON-RPC (2026-05-23)

**Decision.** Managed hermes dispatches go through a long-lived `hermes acp` child per agent (mcp/stdio/hermes-session.js — `HermesSession`), mirroring the `PiSession` pattern. The bridge spawns one `hermes acp --accept-hooks` per `(agentId, machine)`, completes ACP `initialize` + `session/new` once, and reuses the same `sessionId` for every subsequent `session/prompt`. Resident hermes (operator-typed `hermes-aify`) still spawns interactive `hermes` under PTY — only the managed delivery path moved to ACP.

**Why.** Operator quote (2026-05-22): *"I do not want pseudo terminal input because i might write while other agent sends message in and it gets scrambled. We neeed to be able to send in background like with claude code pseudo terminal."* ACP is JSON-RPC stdio with a persistent sessionId — the bridge can stream `session/update` notifications without sharing a PTY with the operator. The prior `hermes chat -q` per-turn spawn could not stream incremental tokens, could not carry native conversation context (`--continue <name>` requires a pre-existing session, see Phase 7 rollback note in mcp/stdio/runtimes.js), and had to embed all prior context in the wire prompt every turn.

**Why per-agent pool (not per-session, not per-machine).** `agentId` is the unit the dispatcher already knows about. Multiple sessions sharing one child would require routing `session/update` notifications by `sessionId`, which is bug surface for negligible memory savings.

**Why we decline `terminal/*` client requests from the agent.** The bridge has no operator-safe sandbox for hermes-spawned child processes. Hermes falls back to its own sandbox if one is configured. Revisit when there's an operator demand.

**Wire format note.** ACP method names are slash-separated (`session/new`, `session/prompt`, `session/update`) but field names are camelCase (`sessionId`, `protocolVersion`, `stopReason`) — confirmed live against Hermes 0.14.0; see `docs/plans/notes/2026-05-23-hermes-acp-spike.md`. The `sessionUpdate` discriminator value is snake_case (`agent_message_chunk`, `agent_thought_chunk`, `tool_call`, ...). Tests live in `mcp/stdio/tests/hermes-acp-protocol.test.js` and `mcp/stdio/tests/hermes-session-acp.test.js` with the `fake-hermes-acp.mjs` stdio fixture.


## Runtime limits

| Capability | Claude Code | Codex | OpenCode |
|------------|-------------|-------|----------|
| Managed workers | yes | yes | yes |
| Resident visible-wake | `claude-live` (via `claude-aify`) | `codex-live` (via `codex-aify`) | not yet |
| Resident background resume | — | `codex-thread-resume` | `opencode-session-resume` |
| Interrupt | yes | yes | yes |
| In-flight steering | resident channel only; managed headless no | yes | no |
| Active dispatch hard timeout | 12 h | 12 h | 12 h |

**One active dispatched run per agent.** Later dispatches from the same sender merge into a buffered pending run (see below).

**SSE clients** can message, inspect runs, and request dispatch — but they cannot host triggerable sessions or be local launchers.

## Wake modes

Every agent registration resolves to one of these wake modes. `comms_agent_info` reports the current one:

| Wake mode | Meaning |
|-----------|---------|
| `claude-live` | Resident Claude session started via `claude-aify`; woken through the local aify channel bridge. |
| `codex-live` | Resident Codex session started via `codex-aify`; woken through the shared local WebSocket app-server that the visible TUI uses. |
| `codex-thread-resume` | Resident Codex session started with plain `codex`; woken by resuming the bound `thread.id` in a separate background app-server. |
| `opencode-session-resume` | Resident OpenCode session with a bound `sessionHandle`; resumed in a background worker. |
| `managed-worker` | Detached managed worker created by dashboard Environment spawn or `comms_spawn`. Not visible in a live user CLI. |
| `message-only` | Legacy/no-live binding. Normal `comms_send` rejects these targets instead of storing future work; older inbox-only records may still display this mode. |
| `claude-needs-channel` | Claude agent is registered but no alive `claude-aify` wrapper exists on this machine. Fix: launch one. |

## Managed workers are persistent identities, not persistent processes

**Decision.** Dashboard Environment spawn and `comms_spawn` create a stable managed-worker registration with saved runtime state, but the underlying Codex/Claude/OpenCode process is launched per dispatch run and torn down when that run finishes, fails, times out, or is interrupted.

**Why.** Keeping a long-lived hidden terminal process around for every worker would be harder to supervise, leak resources across idle periods, and make stale-worker cleanup much messier. The state we actually care about is the resumable conversation handle (`threadId`, `sessionId`, etc.), not the lifetime of a specific shell process.

**Consequence.** A manager can keep a personal stable worker pool (`reviewer-worker`, `tester-worker`, etc.) throughout a project and reuse the same logical sessions between dispatches, but "killing a worker" operationally means either interrupting its active run, clearing its saved runtime state, or removing its registration — not hunting for a permanently running background TUI.

## Stale-run cleanup has a short bridge-replacement grace window

**Decision.** The `/dispatch/claim` endpoint treats an active run owned by a different bridge as stale only after a short grace window. During that window the replacement bridge gets `blockedBy.reason = "active_run_owned_by_previous_bridge"` and does not claim more work. After the window, the server marks the orphaned run failed inline and proceeds to hand out queued work. If the active run is owned by the *same* bridge that's polling, the server still blocks as a bridge-side safety net.

**Why.** The previous behavior had a ~60-line tree of heuristics (superseded-bridge check, timestamp comparison, legacy-unowned detection) that tried to distinguish "genuinely busy" from "stale orphan" based on bridge_instances metadata. These heuristics had timing gaps: if a bridge died and a replacement registered slightly before the dead bridge's last claim, the timestamp comparison failed and the stale run permanently blocked all wake delivery for that agent.

The structural insight that eliminates the old heuristics: the bridge-side gate in `server.js` prevents a live bridge from calling `/dispatch/claim` while it has work in flight. Therefore, if a bridge IS calling claim, it has no local active run. Any DB-level "active" row for that agent owned by a *different* bridge is stale once it survives the bridge-replacement grace window. The grace window avoids the opposite race: a fresh bridge starts polling while the previous bridge is still finishing the run it just claimed.

**Bridge liveness as heartbeat.** As a side effect of every `/dispatch/claim` call, the server now updates `bridge_instances.last_seen`. When a bridge has an active run and skips the claim path, it calls `/agents/{id}/heartbeat` instead. This makes `last_seen` a reliable liveness signal for dashboard display without using it as a gate.

**Failed messages stay in inbox.** When an orphaned stale run is cleaned up after the grace window, the original message that created the dispatch is still in the agent's inbox. The agent can read and act on it via `comms_inbox` even though the tracked dispatch run was marked failed. No message content is lost.

## Steer requests are message-backed and stale-safe

**Decision.** `comms_send(..., steer=true)` still writes the inbox message first. If the target already has a live active run on a steer-capable runtime, the server appends a steer control to that run and records the source inbox message ID. When the bridge later marks the control `completed`, the inbox copy is auto-marked read. If the only active run is owned by a superseded bridge, the server waits through the same bridge-replacement grace window before failing that stale run and falling back to a normal queued dispatch instead of steering into dead state.

**Why.** Steering is advisory work-routing, not a separate message transport. The sender still expects an auditable inbox record. Before this fix, steer results could look like "queued behind active run `<same run id>`", and a steer sent while the DB still pointed at a dead bridge could disappear into a stale control queue. Recording the source message ID and treating superseded active runs as stale before steering eliminates both failure modes.

**Consequence.** A successful live steer no longer leaves an unread inbox copy behind. If the active run was stale, you may see that older run fail with an auto-heal summary while the new message queues normally for the replacement bridge.

## Dispatch buffering (cap 10)

**Decision.** When an agent is already running a dispatch and the same sender tries to queue another, new dispatches are merged into one pending buffered run instead of stacking. The buffer caps at 10 items; past that, new dispatches are rejected with `reason: "buffer_full"` in `notStarted`.

**Why.** Without it, a sender that panic-retries (or a channel that fans out aggressively) can pile up 50+ queued runs on a stuck agent. Those runs all claim to be "queued" but there is nothing the operator can do except cancel them one by one. Merging collapses panic-retries into a single growing envelope with per-item timestamps; the cap prevents unbounded body growth.

**Why per-sender.** Different senders are different conversations; merging across senders would lose the thread. The cap is per (sender, recipient) pair.

**History note.** An earlier implementation briefly merged queued work across all senders for a target. That looked efficient in the queue, but it confused contract ownership and could route handoff replies to the wrong requester. Current code intentionally merges only within one `(sender, recipient)` pair.

**Why 10.** Picked to be high enough that normal bursty workflows never hit it, low enough that a buggy sender can't grow a single run body past ~100 KB.

## Re-register is a full state refresh (except description)

**Decision.** `comms_register` on an existing agent overwrites `sessionHandle`, `runtime_state`, `cwd`, `role`, `runtime`, `machineId`, `runtimeConfig`, and capabilities with whatever the new request contains. The only exception is `description`: omitting it preserves the existing value; passing `""` clears it.

**Why not preserve everything.** Earlier versions preserved `sessionHandle` and `runtime_state` across re-register. That let stale Codex thread IDs survive a fresh `codex-aify` start and broke `thread/resume` with `AbsolutePathBuf` or `no rollout found`. Making re-register authoritative is simpler and matches the user's mental model: "I just re-registered, the record should reflect *this* session".

**Why keep description.** Description is human-facing team context ("I work on the NRD ingest pipeline"). It changes on a slow cadence and should survive the common "kill + restart + re-register" loop. The explicit `""` clear is there for when you genuinely want to reset it.

## Codex requires exact wrapper binding; Claude falls back to any alive wrapper

**Decision.** When resolving the runtime marker for an agent's cwd:
- **Claude Code** falls back to *any* alive `claude-aify` wrapper on the machine if there is no per-cwd marker. Registration succeeds with `claude-live` as long as at least one wrapper is running.
- **Codex** requires an exact match. If there are multiple live markers for different cwds, the bridge refuses to pick one and falls back to `message-only` unless the caller passes explicit `sessionHandle` + `appServerUrl`.

**Why the asymmetry.** Claude's resident-wake path only needs the channel bridge to be loaded into *any* Claude session — it's a process-level wake, not a per-thread one. Codex's resident-wake path binds to a specific `codex app-server` WebSocket URL owned by a specific `codex-aify` wrapper; picking the wrong wrapper means the wake goes to a different Codex session than the one the user registered.

**Clarification: wake delivery is per-agent, not per-machine.** The "any alive wrapper" fallback is about *registration*: whether the agent gets `claude-live` or `claude-needs-channel` as its wake mode. Once registered, each Claude session runs its own `claude-channel.js` instance that polls `/dispatch/claim` for only its own agentId. Multiple Claude agents on the same machine do not cross-talk and do not share a wake binding.

**Practical consequence.** In multi-tab Claude setups on the same machine, everything Just Works — each tab registers a distinct agentId and receives only its own dispatches. In multi-tab Codex setups, you need to register each tab with explicit `sessionHandle="$CODEX_THREAD_ID"` and `appServerUrl="$AIFY_CODEX_APP_SERVER_URL"` from inside that tab.

## Codex path format is chosen from connection type, not from the launcher

**Decision.** The cwd we send in Codex JSON-RPC requests (`turn/start` cwd, `turn/start` sandboxPolicy.writableRoots, `thread/start` cwd) is chosen by `resolveCodexRequestCwdFor` in `mcp/stdio/codex-errors.js`. When `appServerUrl` is set (resident `codex-live` sessions spawned by `codex-aify`), we send a native host path — on Windows that means forward-slash with drive letter (`C:/Docker/project`). When `appServerUrl` is empty (managed workers that the bridge spawns itself via `defaultCodexCommand()`), we defer to the legacy `codexWorkingPath(launcher, cwd)` transform, which applies the WSL translation (`C:/foo` → `/mnt/c/foo`) whenever the launcher is `wsl.exe`.

**Why.** On Windows, `defaultCodexCommand()` returns `wsl.exe -e codex app-server`, so the legacy transform unconditionally produces `/mnt/c/...` paths. That is correct for a WSL-hosted Codex process (Linux `Path::is_absolute()` accepts it) but wrong for a native-Windows Codex (Windows `Path::is_absolute()` requires a drive-letter prefix). `codex-aify` always launches a native Codex on the host OS and publishes its local WebSocket via `AIFY_CODEX_APP_SERVER_URL`, so the bridge was happily sending `/mnt/c/Docker/...` over JSON-RPC to a process that interpreted it as non-absolute. Codex's `AbsolutePathBuf::deserialize` then threw `"AbsolutePathBuf deserialized without a base path"` and every resident dispatch on Windows failed. This was the load-bearing root cause behind the long tail of AbsolutePathBuf reports: corrupt rollouts and the MSYS-PID marker bug were real, but fixing them left the path-format bug still reliably breaking dispatch on the very next run.

**Why the connection type and not the platform.** Linux users running `codex-aify` have no launcher drama (their `defaultCodexCommand()` is `codex`, not `wsl.exe`), and their managed-worker path already does the right thing. Only the interaction of (Windows host) × (codex-aify resident path) × (legacy launcher-derived transform) produced the bug, and the discriminator that cleanly separates the fix case from the pass-through case is whether we connect to an existing app-server vs spawn our own.

**Regression coverage.** `mcp/stdio/tests/codex-cwd-transform.test.js` asserts: resident (appServerUrl set) on Windows produces `C:/...`; managed (no appServerUrl) on Windows keeps the legacy `/mnt/c/...` output; Linux is unchanged; mixed-separator inputs collapse to a single form. `npm test` runs it with the other bridge tests.

## Backend rejects impossible live Codex cwd/machine combinations

**Decision.** `POST /agents` rejects resident Codex registrations that include an `appServerUrl` but pair an obviously wrong cwd format with the reported host family: `linux:` / `darwin:` machine IDs may not register drive-letter cwds like `C:/repo`, and `win32:` machine IDs may not register WSL-style `/mnt/c/repo` cwds.

**Why.** Those records are not just "suboptimal"; they are structurally broken for resident dispatch. A Linux/WSL Codex app-server cannot safely consume a Windows drive-letter cwd, and a native Windows Codex app-server cannot safely consume a `/mnt/...` cwd. Before this guard, the bad record looked healthy until the first dispatch failed deep inside Codex with `AbsolutePathBuf deserialized without a base path`, which was noisy, delayed, and easy to misdiagnose as a queue bug or stale bridge race.

**Scope.** The guard is intentionally narrow. It only applies to resident Codex registrations with a live `appServerUrl`, because that is the case where the backend knows the agent is binding to an existing host-native Codex app-server. Managed workers and non-live registrations keep the old behavior.

## Channel history is canonical-only

**Decision.** Channel read endpoints (`GET /channels`, `GET /channels/{name}`) count and return only canonical channel rows (`to_agent IS NULL`). Per-member inbox fan-out rows are not part of channel history.

**Why.** Channel send writes one canonical row plus one inbox delivery row per recipient. Treating both as channel history duplicated every logical post in the UI and MCP reads, inflated message counts, and made channels look noisy even when delivery worked correctly. Canonical-only reads preserve the actual conversation while leaving inbox fan-out intact for unread counts and wake delivery.

## Corrupt Codex rollouts auto-heal instead of failing forever

**Decision.** When the Codex controller's `thread/resume` call fails with `AbsolutePathBuf deserialized without a base path`, `AbsolutePathBufGuard`, `no rollout found for thread id`, or Codex's websocket frame-limit error (`Space limit exceeded` / `Message too long`), the bridge automatically calls `thread/start` to create a brand-new Codex thread, fires `onSessionHandleChange(newHandle)` to update the cached agent state and the backend's stored `sessionHandle`, and continues the current dispatch against the new thread. This applies to both managed workers and resident sessions. Classification lives in `mcp/stdio/codex-errors.js` (`detectCodexResumeFailure`) so it can be unit-tested without a live Codex.

**Why.** The failure happens inside Codex's app-server while loading the thread's on-disk rollout file; no amount of payload normalization on our side can make Codex accept a rollout it can't deserialize or send one that exceeds its websocket transport frame limit. Before this fix, resident mode threw an actionable error and gave up. In practice the user's Codex process usually kept the poisoned thread ID cached in memory and re-exported it to any child process's `$CODEX_THREAD_ID`, so the next "fresh" re-register passed the same poisoned UUID and the dispatch failed again. The cycle only broke when the user fully killed Codex AND moved the rollout file aside AND relaunched from the right directory AND passed a genuinely new thread ID on re-register — a four-step recipe that rarely landed on the first try.

**Trade-off for resident sessions.** The healed thread is a fresh Codex thread that is *not* the one attached to the user's visible TUI. Dispatched work runs in the background and completes successfully, but the user sees no activity in their interactive Codex session. The alternative — the prior behavior — was that dispatches failed forever with `AbsolutePathBuf` until the user executed the hard-reset sequence perfectly. "Work happens invisibly but reliably" is strictly better than "work fails visibly and reliably", and the user can still run the hard-reset sequence on their own schedule to restore full TUI visibility.

**Regression coverage.** `mcp/stdio/tests/codex-resume-failure.test.js` locks down classification against every error string we have observed from Codex, plus a handful of unrelated errors that must NOT trigger the heal. `npm test` from `mcp/stdio/` runs it along with the other two bridge tests.

## Runtime markers are written by the bridge, not the wrapper

**Decision.** The `claude-code` and `codex` runtime markers under `~/.local/state/aify-comms/runtime-markers/` are written by the long-lived MCP bridge processes (`claude-channel.js` for Claude, `server.js` for Codex when `AIFY_CODEX_APP_SERVER_URL` is set), not by the `claude-aify` / `codex-aify` bash wrappers. The bash wrappers no longer touch markers at all.

**Why.** The wrappers used to write markers via a short-lived `node runtime-markers.js write` CLI call, passing bash `$$` as the `pid` field. On Linux that worked — `$$` is a real long-lived kernel PID. On Git Bash for Windows, `$$` is an MSYS shell PID that does not exist in Windows's process table. The bridge's `isProcessAlive` check uses `process.kill(pid, 0)`, which on Windows only understands real Windows PIDs, so it returned false and `listRuntimeMarkers` auto-deleted the marker on the next read. Every claude-aify/codex-aify session on Windows silently lost its marker within a second, and the resulting fallbacks produced a long tail of "can't find live wake mode" symptoms: `claude-needs-channel` wake mode, Codex auto-discovery binding to stale threads, and every `AbsolutePathBuf` dispatch failure that kept returning even after the cwd normalization fixes landed.

**Consequence.** Marker writing now happens inside a process whose `process.pid` is a real long-lived Windows PID. When the bridge exits, it deletes its own marker; if it crashes, the dead PID is detected on the next read and auto-cleaned. The wrappers are simpler (no marker write, no marker cleanup trap) and can't poison the marker store with unreadable PIDs.

## Bridges self-heal on persistent failures

**Decision.** The stdio bridge retries transient HTTP errors up to 3 times with exponential backoff (250ms → 500ms → 1s), and auto-re-registers an agent from its cached state when either (a) the server returns `404` on `/agents/{id}` or `/dispatch/claim` for that agent, or (b) 4 consecutive claim attempts fail for any reason.

**Why.** The most common "stale bridge needs manual re-registration" symptom has two root causes: a transient network blip that the old code didn't retry, and the server legitimately forgetting about the agent (via `comms_clear`, an operator DELETE, or a DB rotation) with no way for the bridge to notice. The first is handled by retries. The second is handled by treating a 404 as "re-register from what I remember" rather than silently polling a dead `agentId`. Both paths use the `REMOTE_AGENT_STATE` cache that already existed — no new state introduced.

**Retry is method-whitelisted to prevent duplicate side effects.** `GET`, `PATCH`, and `DELETE` are always retried because they are idempotent by design. `POST` is only retried on a narrow whitelist of known-idempotent endpoints: `POST /agents` (INSERT OR REPLACE), `POST /agents/{id}/heartbeat`, and `POST /channels/{name}/join`. Non-idempotent POSTs — `/dispatch`, `/dispatch/claim`, `/dispatch/controls/claim`, `/messages/send`, `/channels/{name}/send` — fail fast on the first transient error and surface the error to the caller. Without this restriction, a connection that drops mid-response after the server has already processed a `/dispatch/claim` would retry and claim a second run, leaving the first one orphaned in `claimed` state.

**Limits.** Auto-re-register only works if the bridge has a cached registration for the agent (i.e. it was registered at least once in this process). If the bridge starts up cold against a server that doesn't know about the agent, there's nothing to re-register from — the caller still has to do the first registration manually. Auto-re-register also cannot recover agents that failed their *first* registration attempt, since no cache entry exists yet.

## Superseded bridges are blocked at claim time

**Decision.** When an agent re-registers, the server marks the old bridge instance as `superseded_by: <new bridge id>`. The `/dispatch/claim` endpoint rejects claims from any superseded bridge with `blockedBy: {reason: "bridge_superseded"}`. For Codex/OpenCode stdio bridges, claim also checks `runtimeState.bridgeInstanceId`; if a stale process keeps polling with an ID that is no longer current, claim returns `blockedBy: {reason: "bridge_not_current"}` before it can consume a queued run.

**Why.** Without this, an old `codex-aify` process that didn't exit cleanly would keep polling, keep claiming fresh runs, and keep failing them with its stale in-memory state — even though the code on disk had been updated and a new bridge was ready to handle the same work. Blocking old bridges at claim time makes re-register a definitive handoff. The `bridge_not_current` guard covers the edge case where the old bridge's row has disappeared or cannot be classified as superseded, but the agent's current runtime state clearly points at a newer bridge.

The old bridge stays alive and keeps polling (that's fine — polling is cheap) but can no longer steal work.

## Notifications fire on `PostToolUse` for `Bash`

**Decision.** The unread-notification hook is installed on Claude and Codex with the `PostToolUse` hook, matcher `Bash`. It's not installed on OpenCode at all.

**Why `Bash` specifically.** Codex's current hooks runtime only fires `PostToolUse` for `Bash`, not for every tool. Using the same matcher on Claude keeps the two runtimes consistent so team-wide guidance applies to both.

**Why not OpenCode.** OpenCode doesn't expose a hook path the notification script can bind to yet.

**Consequence.** If an agent never runs a Bash tool call, it never checks for unread messages from the hook path. Agents should call `comms_inbox` explicitly at natural check-in points (start of a task, between major steps).

## Dashboard actions use function handlers, not interpolated JavaScript

**Decision.** Dynamic dashboard buttons register a JavaScript function and call it by generated action ID instead of interpolating agent IDs, run IDs, subjects, or channel names into inline `onclick` strings.

**Why.** Agent IDs and message subjects can contain characters that are safe as data but unsafe inside a hand-built JavaScript string literal. The previous pattern caused broken buttons such as Follow up and Continue as when a value introduced a quote or unmatched escape. Function-backed actions keep dynamic values as closed-over data and make button behavior independent of display text.

**Consequence.** If an action ID is older than the current in-memory render, the dashboard shows an "Action expired" toast instead of throwing a console syntax error.

## Home is an operations queue, not the audit log

**Decision.** The dashboard Home page highlights live blockers, pending handoff repairs, failed spawns, and failed/cancelled runs, but reviewed historical failures can be dismissed locally from Home. Runs, spawn requests, and event history remain in their dedicated audit views.

**Why.** A control-plane homepage becomes useless if old, already-understood failures permanently look urgent. Operators need a current work queue first, with audit detail one click away.

**Consequence.** Dismissal is a browser-local presentation choice. It does not delete messages, runs, spawn requests, sessions, or artifacts.

## Ended sessions are debug history

**Decision.** Sessions with terminal quiet statuses (`ended`, `completed`, `cancelled`) are hidden from the normal Sessions table by default. The table exposes a **Show ended/debug sessions** toggle for lifecycle investigation.

**Why.** Managed sessions are backing records, and old records are useful when debugging recovery. They should not dominate the day-to-day operator view where the user wants running, starting, failed, or recoverable sessions.

**Consequence.** Session counts still include hidden history where useful, but the primary list stays focused on actionable session state.

## Claude channel bridge completes runs only after delivery succeeds

**Decision.** The `claude-channel.js` bridge claims a dispatch run, attempts delivery to the Claude session via MCP notification, and marks the run as `completed` only after the notification succeeds. If delivery throws, the bridge marks the run as `failed` instead of pretending it completed.

**Why.** The older "leave it running" model was wrong because the bridge cannot observe Claude's progress, so runs hung for hours. But marking the run `completed` before the notification actually fired was also wrong: a failed notification silently dropped work while the server claimed success. "Delivered" is only honest after the notification call returns successfully.

**Consequence.** Dispatch run history for Claude resident sessions still shows `completed` immediately after successful delivery, but failed notification attempts now surface as failed runs instead of false positives. The actual "did Claude do the work" tracking remains the message/reply flow (`comms_send` → `comms_inbox` → `comms_send` back with `inReplyTo`). Interrupt/steer controls for Claude resident sessions are not supported through the dispatch run — use `comms_send` instead.

## Dispatch tracks handoff, with explicit replies preferred

**Decision.** `comms_dispatch` requires a reply handoff by default, and `comms_send(type="request")` does too unless `requireReply=false` is passed. Agents are still expected to send their own explicit `comms_send(..., inReplyTo=...)` reply. A reply-dispatch back to the requester also satisfies the handoff. As a recovery path, a recent unthreaded direct `response`/`review`/`approval`/`error` from the worker to the requester satisfies the latest matching pending handoff for that pair. If a required reply is still missing when the run ends, the bridge mirrors the run result back to the requester as a fallback inbox handoff.

**Why.** Pure run summaries were too easy to miss in real manager/worker loops: work finished, but the requester saw an empty inbox and the lane looked dead until someone manually polled `comms_run_status`. Fully automatic replies were also too blunt because the bridge cannot reliably decide what the agent meant to report. The compromise is: require a real reply for work handoff, prefer an intentional agent-authored message, accept reply-dispatches as real handoffs too, but refuse to let the lane silently stall if that handoff never happens.

**Consequence.** Once a real reply is linked to the run, fallback mirror messages are not generated. If an older fallback mirror already exists and a late real reply is linked later, that mirror is auto-marked read so it stops polluting unread counts. The dashboard's `Pending Handoffs` repair action applies the same fallback mirroring to old terminal runs so stale "done but nobody was told" records can be forced into the requester's inbox.

**Claude resident caveat.** Claude resident notification runs that complete with `Delivered to Claude resident session` are delivery acknowledgements, not proof that Claude finished the task. Those rows are not counted as pending handoffs; the real handoff remains the message/reply flow.

**Unread caveat.** When a dispatch is claimed, the server marks the source inbox message as read for the target because the work was already injected into that runtime. Buffered `Pending updates` runs mark every included `MessageId` read on claim, not only the first message, so delivered batches do not keep resurfacing as unread work.

## Channel messages land in inbox

**Decision.** `comms_channel_send` delivers the message to every member's inbox. There is no separate "channel view" the agent has to poll.

**Why.** Coding agents don't keep long-lived UI windows open on channels. If channel messages lived only in channel history, agents would miss them unless they remembered to poll. Delivering to the inbox means the normal unread-notification flow covers channel traffic automatically.

## Identifier name constraints

**Decision.** Agent IDs, channel names, and shared-artifact names must match `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$` — start with an alphanumeric, then up to 127 more alphanumerics, dots, underscores, or hyphens (max 128 total).

**Why.** These end up in URLs (`/agents/{id}/...`), filesystem paths (shared artifacts), and shell arguments. The strict regex prevents path traversal, URL escaping issues, and shell injection without having to sanitize at every call site.

## Dashboard console is a PTY the bridge owns; the service only relays

**Decision.** The dashboard "Console" runs a real PTY (`node-pty`) on the connected environment's bridge, not in the container. The service stores terminal rows and relays output/input/resize/stop as control records the bridge claims. `agent_sessions.owner_mode` flips to `console` while a console is attached and reverts to `managed` when the terminal reaches a terminal state.

**Why.** Operators need direct interactive CLI access to managed agents (and to bypass `claude -p` subscription locks). A PTY must run where the runtime runs — the host — so the service is deliberately a relay, never a process owner. `node-pty` is the same battle-tested substrate VS Code uses (ConPTY on Windows); the console problems were never the PTY layer, they were the relay/render plumbing.

**Consequence.** Bridge changes under `mcp/stdio/` do not need a container rebuild — they take effect on bridge restart. Console-only runtimes (e.g. Hermes) expose a terminal-delivery controller that rejects bridge active-dispatch claims with an actionable message instead of looking mysteriously "unsupported".

## Terminal output sequence is server-owned, monotonic, and streamed as deltas

**Decision.** The service assigns a strictly monotonic `output_seq` per terminal via a coalescing write queue; the bridge never assigns seq. The dashboard streams each `terminal_output` websocket frame's delta straight into the live xterm keyed by seq, and only falls back to a full render on initial mount.

**Why.** The original console flicker was a full `renderChat()` (DOM rebuild + xterm remount + full-buffer repaint) on every output chunk at ~80/sec. Monotonicity must be guaranteed in the queue, not derived from a request-time DB read — a stale `output_seq` read during an uncommitted flush could regress seq and make the dashboard silently drop fresh output. A per-terminal seq floor enforces this regardless of flush/commit timing.

**Consequence.** Output POST responses intentionally omit the (up to 64KB) buffer — the bridge only needs `outputSeq`/`status`; clients read full scrollback via `GET /terminals/{id}`.

**Broadcast ordering.** The live `terminal_output` websocket frame is emitted by the coalescing queue's flush (post-commit, one ordered batch per flush), NOT per-POST. Per-POST broadcasts ran in concurrent request coroutines, so their order did not match the seq assigned at enqueue; the dashboard's `seq <= lastSeq` dedupe then discarded an out-of-order frame, leaving a hole in the byte stream that desynced the terminal's ANSI state ("scrambled text when working"). Flushes are serialized per terminal, so flush-time broadcast is ordered and gap-free, and the dedupe is now correct (it only guards mount-overlap/replays). This also cuts websocket message volume.

## Coalescing terminal writes + `busy_timeout` keep the single SQLite writer alive

**Decision.** Terminal output is batched through an idle/max-latency coalescing queue before hitting SQLite, every connection sets `PRAGMA busy_timeout` (WAL is persistent at the file level), and OperationalError surfaces as a JSON 503, never an HTML 500.

**Why.** A runaway flickering console produced ~80–94 output POSTs/sec, saturating SQLite's single write lock and starving heartbeat/dispatch/spawn-claim writers — that DOS'd the control plane and produced "database is locked" 500s that the dashboard then failed to parse. Fixing the flicker removed the load source; coalescing + `busy_timeout` + a JSON error contract make the remaining contention graceful.

## One live-state engine is the single source of truth for status

**Decision.** `list_agents`, `get_agent`, and every write endpoint (heartbeat, register, dispatch status) derive agent status from one `_compute_live_status_cache` / live-state engine. A bridge-instance id change alone never marks a *live* session offline; `starting` counts as live; a console terminal reaching an end state falls through to active-run/heartbeat truth rather than flat "offline". An attached console reports `working` only while it is *actively producing output* (terminal `updated_at` within `console_active_seconds`, default 90s); an attached-but-quiet console is `active` (reachable, idle), not `working`. The live terminal status is also mirrored onto `agent_sessions.terminal_status` on every output so it advances past `starting` — otherwise the engine reports a permanent transitioning "working" for an idle console.

**Why.** "Statuses broken" had several causes: bridge restarts rotate the instance id and were collapsing running agents to offline; spawn-in-progress sessions were briefly false-offline; console-owned agents looked offline after a normal stop even though managed fallback was live; and write endpoints disagreed with the dashboard because they used a different heuristic. A single engine removes the disagreement; the bridge-instance/live-session and `starting` rules stop the false-offline cases.

## Pi RPC heals dead sessions once, fails auth fast, never silently hangs

**Decision.** The Pi RPC controller classifies child output: auth/provider/401 failures fail fast (no heal — re-running won't fix credentials); a missing/dead saved session heals once to a fresh session (managed only, guarded against loops); resident mode fails visibly with an actionable clear-handle message. On heal the stale handle is cleared server-side (explicit `sessionHandle:""` + cleared runtime-state), not left to be rediscovered.

**Why.** A mis-routed `--model` once sent Pi to a credential-less provider and it hung ~18 min silently. Auth problems must surface immediately; dead-session problems should self-heal like Codex; resident sessions are operator-owned so the dashboard must not silently mutate them.

## Startup reconcile closes runs nothing will ever close

**Decision.** A bounded startup pass closes `delivered` dispatch runs that are result-linked, or stale with no required reply, or — a require_reply run that is stale **and** has no active owner (no queued/claimed/running run and no live session) to ever produce the reply.

**Why.** Hundreds of `delivered` runs accumulated that no code path would ever finish, inflating "reply pending" handoff metrics and making lanes look alive forever. The orphaned-require_reply case is gated on demonstrable no-owner so a run a live session could still answer is never closed prematurely.

## Wrapper session-mode is declared, not inferred

**Decision.** Every `*-aify` wrapper (`claude-aify`, `codex-aify`, `pi-aify`/`omp-aify`, `hermes-aify`) accepts explicit `--resident` and `--managed` flags. The wrapper reads `AIFY_SESSION_MODE` from its inherited env first; if unset, the flag wins; if neither, the wrapper auto-detects via TTY presence (`[ -t 0 ]`) — interactive launches default to `resident`, non-TTY launches default to `managed`. The wrapper exports `AIFY_SESSION_MODE=resident|managed` for its child `mcp/stdio/server.js`, which puts that mode into the `/agents` register call. `terminal-env.js` (the env builder used by bridge-spawned PTYs) always sets `AIFY_SESSION_MODE=managed`, so the inherited env wins for bridge-spawned wrappers regardless of TTY shape.

**Why.** Earlier, session_mode was guessed from registration context (no session_handle ⇒ managed; with handle ⇒ resident). That inference broke when bridge-spawned PTYs (which run inside a node-pty allocated TTY) auto-detected as `resident` and registered as resident, then collided with the real resident bridge for the same agent. The collision triggered scope-mismatched supersession and killed in-flight runs. Making mode an explicit declaration removes the ambiguity at the wire.

**Why TTY auto-detect as a fallback.** Operator-launched wrappers (the human types `pi-aify --aify-agent ...` in a terminal) almost always want resident. Container/bridge-spawned wrappers want managed. TTY presence is the single Unix-shell signal that distinguishes those cases and works identically on Ubuntu bash, macOS, and Git Bash for Windows.

**Why claude-aify always exports `AIFY_CHANNELS_ENABLED=1`.** claude-aify is the channels-aware Claude wrapper. Server-side, `runtime_config.channelEnabled=true` is the precondition for `_row_capabilities` keeping resident-run/interrupt/steer caps; without it the strip reduces caps to just `managed-run/resume` and preflight rejects live sends. Declaring the channel-enabled flag at register time removes the manual DB patching that earlier sessions needed.

## claude-aify wraps with --strict-mcp-config + minimal MCP config

**Decision.** `claude-aify` always launches Claude with `--strict-mcp-config` and a runtime-generated minimal MCP config containing ONLY `aify-comms` and `aify-comms-channel`. The operator's broader `~/.claude.json` MCP server list is NOT loaded inside the `claude-aify` wrapper session. The minimal MCP config is written to a temp file via `mktemp`; cleaned up on shell exit.

**Why.** Known Claude Code bug ([anthropics/claude-code#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)): when Claude loads many stdio MCP servers simultaneously, the slower ones get stuck in `still connecting` state. `aify-comms-channel` was consistently losing the init race against the 13-server operator config and never finished its initialize handshake — Claude's `notifications/claude/channel` listener wasn't registered, so every channel-routed dispatch was silently dropped despite the bridge reporting `delivered`. Confirmed via `claude -p` diagnostic listing both `aify-comms` and `aify-comms-channel` as "still connecting".

Trade-off: operator's other MCP servers (browsermcp, github, gitlab, etc.) do NOT load inside `claude-aify` sessions. They still work in plain `claude` sessions outside the wrapper. Channel delivery reliability requires this isolation.

Applies to BOTH `claude-aify` modes (resident operator-launched AND managed bridge-spawned). Previous gating on `AIFY_SESSION_MODE=managed` only fixed the bridge-spawned case; operator's own session still hit the bug.

## cygpath -m converts SCRIPT_DIR for the wrapper's MCP config on Git Bash Windows

**Decision.** When generating `claude-aify` on Git Bash Windows, `install.sh` uses `cygpath -m "$SCRIPT_DIR"` to convert the install dir to mixed-mode Windows-native path format (`C:/Docker/aify-comms`) before substituting into the generated wrapper script. The wrapper's minimal MCP config emits this Windows-native path in the MCP server `args`. On Linux/Mac (no cygpath available), `SCRIPT_DIR` is already native and used directly.

**Why.** `install.sh` runs in Git Bash where `$SCRIPT_DIR` is MSYS format (`/c/Docker/aify-comms`). Native-Windows Claude cannot resolve that POSIX path when spawning MCP server children. The MCP servers fail to start even with `--strict-mcp-config` in place. Symptom: wrapper output showed `2 MCP servers failed` and Claude reported `aify-comms is currently disconnected` in conversational replies despite all the other fixes being correct.

## Bridges coerce `http://localhost` to `http://127.0.0.1` before fetching

**Decision.** Both `mcp/stdio/claude-channel.js` and `mcp/stdio/server.js` apply a `coerceLoopbackToIPv4` normalization to `AIFY_SERVER_URL` / `CLAUDE_MCP_SERVER_URL` and to every fallback URL in `SERVER_URLS`. Any `http://localhost[:port][/path]` is rewritten to `http://127.0.0.1[:port][/path]` at the point of use. Wrapper-generated MCP configs also emit `127.0.0.1` directly instead of `localhost`. The coercion is universal, not Windows-gated.

**Why.** Docker Desktop on Windows reports IPv6 port bindings (`docker port` shows both `0.0.0.0:8800` and `[::]:8800`) but its IPv6 port forwarding is unreliable in practice — connections to `::1` hang silently. Windows resolves `localhost` to IPv6 `::1` first, so node's `fetch()` and curl both hit the broken path. Every `/dispatch/claim` poll aborted at the bridge's `HTTP_TIMEOUT_MS` (20s), no run was ever claimed, no `notifications/claude/channel` was ever emitted. Symptom looked identical to a channel-registration bug, a wrong-allowlist bug, or a queue-routing bug — but the actual blocker was network-level. Confirmed live: `curl http://localhost:8800/health` from host timed out at 30s while `curl http://127.0.0.1:8800/health` returned in 30ms.

Coercion lives in the bridges rather than only in the wrapper template because operators can override `AIFY_SERVER_URL` from their shell or `~/.claude/settings.local.json`. A wrapper-only fix would miss those cases; a bridge-level fix protects against any future config that says `localhost`. Linux/macOS resolve `localhost` to IPv4 `127.0.0.1` by default so the coercion is a no-op there.

Hindsight: when channel-routed dispatches sit queued forever, time `curl --max-time 5 http://localhost:8800/health` before chasing channel-registration or allowlist hypotheses. The IPv6/loopback bug shows up first.

## insert_messages_via_console (rename + semantic invert of the old channel-only setting)

**Decision.** A single universal flag controls managed delivery semantics across ALL runtimes:

- `insert_messages_via_console=false` (default, target architecture) — managed dispatch flows through each runtime's proper delivery channel:
  - managed claude → `claude-channel.js` notifications inside the wrapper PTY (channel transport).
  - managed codex / pi / opencode → native RPC adapters (`createCodexController`, `createPiController`, opencode SDK) via `executionModes=["managed"]` /dispatch/claim polling.
  - No PTY-input typing.
- `insert_messages_via_console=true` (legacy / opt-in escape hatch) — bridge writes the dispatch body directly into the wrapper PTY as a bracketed-paste `terminal_control`. Operator-visible Console pop-up. Used as a working baseline when channel/RPC delivery is misconfigured or under investigation.

Earlier name was `claude_managed_channel_only` and gated ONLY the claude split with inverted polarity (channel mode was opt-in true). The rename:
- Names what the flag actually does ("insert messages via console" = type them into the PTY).
- Inverts the polarity so the proper-delivery path is the default and the PTY-input fallback is the explicit opt-in.
- Covers ALL managed runtimes, not just claude. Native managed runtimes (codex/pi/opencode) also route through native RPC instead of PTY-input under the default-false.

**Why.** Operator's framing: "we want to deliver messages via channels and `*-aify` bridges like we have." The PTY-input path was a workaround layered on top of the proper-delivery architecture. Making it opt-in clarifies the intended design and removes the visible "console pops up on first send" UX symptom from the default flow.

**Why a legacy escape hatch instead of removal.** Channel delivery for managed claude currently depends on Claude CLI's `--dangerously-load-development-channels` flag + a per-session menu confirmation. The bridge's reactive auto-confirm (detect prompt text → 2s wait → send `1\r`) fires mechanically but the channel hasn't been confirmed working end-to-end yet (Claude inside the wrapper still rejected the channel after auto-confirm in live testing). Until that's resolved, operators may need the via-console escape hatch as a working baseline.

**Implementation.** Helper renamed `_claude_managed_channel_only` → `_insert_messages_via_console`. All call sites inverted (was-true ↔ was-false). Routing in `send_message`:
- NATIVE_MANAGED runtimes: PTY-input branch gated on `_managed_terminal_backing_enabled AND _insert_messages_via_console`. Default-false falls through to native adapter delivery.
- CHANNEL_MANAGED (claude): PTY-input branch gated on `_insert_messages_via_console`. Default-false leaves the run launchable; `_apply_channel_routing_to_claude_runs` flips `execution_mode='channel'` so claude-channel.js claims it.

Tests: regression suite's `setUp` opts the whole legacy suite into via-console mode so historical contracts (`consoleDeliveries`, terminal-control inputs, idle-prompt closes) still apply. Tests for the new default override.

## Managed Claude routes via channel events, not PTY input

**Decision.** When `claude_managed_channel_only=true` (settings, default false in `DEFAULT_SETTINGS`), dispatches targeting managed claude-code agents are claimed by `claude-channel.js` over the channel transport and emitted to the agent as `<channel source="aify-comms-channel" ...>` MCP notifications instead of being typed into the wrapper PTY. `_apply_channel_only_to_claude_runs` flips `execution_mode='channel'` on those runs at create time; the PTY-routing branch in `send_message` is gated by `not _claude_managed_channel_only(settings)`.

**Why.** Channel delivery is the architecturally-correct path for Claude — same protocol resident Claude already uses, no terminal-output parsing, no bracketed-paste injection, no operator-visible terminal pollution. Channel delivery has worked for >1 month for resident Claude; extending it to managed Claude was the natural unification.

**Why a setting, default off.** Existing managed Claude wrappers were configured assuming PTY delivery. Flipping default-on without opt-in would change delivery semantics under operators' feet. Default-off lets operators flip live, smoke-test, and roll back instantly via `PUT /api/v1/settings`.

## Wrapper-PTY pre-spawn at spawn-request running (managed_pty_eager_spawn)

**Decision.** When `managed_pty_eager_spawn=true` AND `managed_terminal_backing_enabled=true` (both settings, both default false), `update_spawn_request`'s running-transition handler proactively launches the wrapper PTY for the newly-registered managed agent by calling `_ensure_managed_pty_for_dispatch`. The wrapper is alive by the time the first dispatch arrives; subsequent dispatches and manual Start Console clicks reuse the same terminal via `_active_terminal_for_agent` (dispatch path) and the slice-3 reuse check in `start_session_console` (manual path).

**Why.** Without it, the first dispatch to a managed agent spawned the wrapper PTY on demand — an operator-visible "console pops up when I send my first message" symptom across pi/codex/opencode/hermes. With it on, the console pre-exists and the dispatch slots into it. Both directions are regression-pinned (test_managed_pty_eager_spawn_creates_terminal_at_spawn_request_running + ..._default_off_preserves_prior_behavior).

**Why best-effort.** A wrapper-launch failure here does NOT fail the spawn-request running transition. The dispatch path's lazy spawn remains the safety net so the agent is still usable even if the eager launch hits a transient issue.

**Why default off.** Same rationale as channel-only: avoid changing established behavior for current operators. Flip on per-environment when ready.

## Console-start reuses existing live wrapper terminal

**Decision.** `POST /api/v1/sessions/{id}/console/start` checks whether the agent_session already has a `terminal_id` pointing to a `terminal_sessions` row in `{starting, attached, running, active, idle, recovering}` before doing anything else. If so, it returns the existing terminal envelope with `reused:true` and appends a `console_attach_reused_existing` audit event — no new terminal_sessions row, no sibling wrapper PTY.

**Why.** Multiple operator clicks on Start Console (or auto-attach flows that hit the endpoint) used to spawn sibling PTYs even when a wrapper was already running for the agent. Sibling PTYs confused the dashboard ("which one is current?") and wasted host processes. The dispatch path already had the same reuse semantics via `_active_terminal_for_agent`; this brings the manual-start path to parity.

## RPC-child env gate: AIFY_BRIDGE_DISABLED is per-spawn, not global

**Decision.** `runtimeChildEnv` does NOT default `AIFY_BRIDGE_DISABLED` for wrapper children. The pi RPC child spawn (`omp --mode rpc` invoked by `createPiController`) sets `AIFY_BRIDGE_DISABLED=1` + `AIFY_AGENT_ID=""` in its explicit per-call env. Other runtimes' wrapper spawns (claude-aify, codex-aify, hermes-aify, opencode) deliberately get the full aify env so their inner MCP servers function.

**Why.** Pi's `omp --mode rpc` child accidentally launches a nested `mcp/stdio/server.js` that would register as a sibling bridge for the same agent and supersede the resident bridge while its run is in flight. The flag tells server.js to exit cleanly at startup. An earlier attempt set this default in `runtimeChildEnv` and broke claude-code's MCP chain because claude-aify legitimately needs the aify env to function. Per-spawn declaration in `createPiController` is the targeted fix.

## Channel-eligible managed claude bypasses the managed-run cap check

**Decision.** In `_agent_execution_mode`, the "managed-run cap required" check at line 871 is SKIPPED when the runtime is `_CHANNEL_MANAGED_RUNTIMES` AND the agent's `runtime_config.channelEnabled=true`. Dispatch flows through `execution_mode='channel'` instead. The inverse (no `channelEnabled` and no `managed-run`) is still rejected.

**Why.** Managed claude default capabilities deliberately omit `managed-run` because claude has no headless managed-run API — there's no `claude --print` style RPC for arbitrary turns. The actual delivery path for managed claude under `claude_managed_channel_only=true` is the wrapper-PTY's `claude-channel.js` which polls `/dispatch/claim` and emits channel notifications. The cap-check was rejecting these dispatches before the channel branch could fire. Deep-test on `e2e-test-claude` caught it: spawn worked, wrapper PTY attached, but initial-message dispatch was cancelled with `agent capabilities do not include "managed-run"`. The skip lets channel-eligible managed claude dispatches reach `execution_mode='channel'` and get claimed by the in-PTY subscriber.

## Channel-only routing applies at every dispatch-create site

**Decision.** All three `_create_dispatch_runs` call sites that create runs for managed claude — `send_message` line 8425, `update_spawn_request` running-transition line 6029, and auto-mirrored handoff line 4912 — now call `_apply_channel_only_to_claude_runs(...)` after creation. Without the helper at a given site, runs stay `execution_mode='managed'` even when `claude_managed_channel_only=true`, and (because no managed-claude path exists end-to-end) sit queued forever.

**Why.** Initially only `send_message` applied channel-only. Spawn-time initial messages and auto-mirrored handoffs bypassed it. The deep e2e test exposed this — spawned `e2e-test-claude` got an initial-message run with `execution_mode='managed'` that no subscriber could claim. Centralizing the post-create helper at every create site is the durable fix; per-call sites add one line each.

## Same-logical-owner supersession scope

**Decision.** `bridge_instances` supersession is scoped to `(agent_id, machine_id, runtime, session_mode, session_handle)`. A new bridge that re-registers an agent with the SAME tuple supersedes prior bridge instances for that tuple only — it does NOT supersede bridges for the same agent with a different session_mode (resident vs managed) or a different session_handle.

**Why.** Earlier supersession was scoped to `(agent_id, machine_id)` only. That triggered when a managed wrapper PTY registered for an agent whose resident bridge was alive — the managed registration superseded the resident bridge and killed its in-flight runs. Scope narrowing to the full logical-owner tuple lets resident and managed sessions for the same agent coexist when that's the intent (e.g. operator runs claude-aify resident while managed claude-aify PTYs handle dashboard dispatch).

## Managed pi uses persistent RPC + synthesized terminal; resident pi uses watchdog mutex

**Decision.** A managed pi agent's `omp --mode rpc` child is now spawned ONCE per agent and reused across dispatches (lives in `mcp/stdio/pi-session.js`'s pool, indexed by `agentId`). Each `AgentSessionEvent` the child emits is formatted into a human-readable frame and streamed into a virtual `terminal_session` row (status='running', command='aify://virtual-rpc/pi') via the bridge's existing `/terminals/{id}/output` ingest path — so the dashboard sees what the bridge sees. The `--mode rpc` channel has no PTY, but the synthesized stream gives operators the same shape of feedback as the real pi-aify TUI. Resident pi keeps the legacy per-dispatch `createPiControllerLegacy` path. A soft watchdog (`GET /agents/{id}/pi-session-state` + the `omp-aify`/`pi-aify` wrapper) refuses to launch an external omp on the same session-id while the bridge owns it; `omp-aify --standalone` is the operator's override.

**Why.** Three earlier symptoms drove this: (1) every managed-pi dispatch paid the full `omp --resume` startup cost, ~3-8 seconds of wall time before the first token, because the child died at `agent_end`; (2) managed pi was an operator black box — no PTY to watch, no way to see what the agent was doing mid-turn, unlike claude-aify or codex-aify which have visible TUIs; (3) two pi processes on the same OMP session-id (one bridge-driven, one operator-driven) silently corrupt each other's session file, and OMP has no multiplexing per upstream issue [#436](https://github.com/can1357/oh-my-pi/issues/436). Persistent reuse fixes (1); the synthesized terminal fixes (2); the watchdog fixes (3). The synthesized stream is the closest UX we can deliver without an OMP feature shipping — see the architectural narrative in `docs/plans/pi-persistent-rpc.md`.

**Consequence.** A managed-pi agent's `runtime_state` now carries `virtualTerminal: true` + `virtualTerminalId: "vterm_..."`. The dashboard reading those fields knows the terminal is bridge-synthesized rather than a real PTY (no resize semantics, no ANSI control flow from a real shell, and stop tears down the persistent RPC rather than killing a PTY). Local `omp-aify` / `pi-aify` invocations check the watchdog and exit 1 with a clear refusal text when the bridge is currently driving the session — pass `--standalone --resume <other-id>` if you intentionally want a parallel session. The persistent child idle-times out at 24h by default (`AIFY_PI_IDLE_TIMEOUT_MS`) as a leak-safety valve; OMP doesn't currently document upper bounds on per-session resource accumulation, so the timer is conservative.

## Status taxonomy: available / online / working (persistent-worker model)

**Decision.** Replace the overloaded `active` status with a clearer split: `available` (env online, agent registered, no live worker — sending wakes it), `online` (worker alive, idle), `working` (worker handling a turn). The live-status engine no longer emits `active`. The discriminator between `available` and `online` is `agent_sessions.status IN _LIVE_SESSION_STATUSES` for the agent — that's the "worker present" signal. Stale-heartbeat → `offline` for both `online` and `available`; idle-warning fires only for `online`. Stop is a unified `POST /agents/{id}/stop-worker` endpoint that ends live sessions, marks any synthesized virtual terminal_session as stopped, clears `runtime_state.virtualTerminal*` pointers, and zeros `turn_busy` — agent goes online → available cleanly.

**Why.** Operator feedback during live testing on 2026-05-22: `active` meant both "ready, watching for work" (worker idle) and "registered but never spawned." Same dot color, totally different operational state. New taxonomy matches the operator's mental model — `available` is the wake-able-but-cold state, `online` is the ready-to-receive state, `working` is the busy state. Send-to-`available` already works because the existing per-runtime dispatch handlers (PiSession.acquirePiSession, claude-aify wrapper spawn) spawn workers on first dispatch — Phase 2 just unblocked the preflight gate for `available`. Per-runtime persistent worker reuse for codex/opencode (one app-server/SDK session per agent across turns, mirroring PiSession's pool) is scoped in docs/plans/persistent-worker-status-taxonomy.md but not yet implemented — UX-equivalent today since per-dispatch still works.

**Consequence.** Dashboard reads `agent.status` and gets `available`/`online`/`working`/`offline`/`idle`/`blocked`/`stopped`. The manual override `agents.status='stopped'` still wins via `_MANUAL_STATUSES`. Legacy bookmarks/saved filters that compared to `active` no longer match — dashboard UI bake-in. Hermes uses `hermes chat -Q -q` per dispatch (the original Phase 7 `--continue aify-${agentId}` was rolled back 2026-05-22 — upstream Hermes's `--continue` requires the session to already exist and refuses on first dispatch with "No session found"); conversation context is in the wire prompt instead, codex-shape. The synthesized terminal_session row survives across dispatches as the operator-visible feed. Architecture detail in `docs/plans/persistent-worker-status-taxonomy.md`.

## Synthesized terminal coverage extends to codex and opencode

**Decision.** Both codex and opencode managed runs now emit a synthesized `terminal_session` row in the same shape as pi/hermes (`command='aify://virtual-rpc/codex'` or `aify://virtual-rpc/opencode'`, `runtime_state.virtualTerminal=true`). `createCodexController` pushes per-event frames into the sink (`▶ turn started`, `→`/`✓` tool item markers, agentMessage deltas streamed as raw text, `■ turn ended` with token usage, `✗ error` red on failure). `createOpenCodeController` pushes coarser frames (prompt echo, connecting marker, final reply, turn ended) because the opencode SDK doesn't expose granular tool events. Both controllers remain per-dispatch — full persistent-worker pool refactors (CodexSession/OpenCodeSession mirroring `pi-session.js`) stay scoped in `docs/plans/persistent-worker-status-taxonomy.md` as Phases 5/6 deferred.

**Why.** Operator-reported (2026-05-22) "Codex - I wrote to him, he answered, but I didn't see any change in console. not inbound, no outbound." Symmetry expectation: if pi and hermes have synthesized terminal feeds, codex and opencode should too. The intermediate (per-dispatch with synth feed, no pool refactor) closes the operator-visible UX gap without the 3-5 day per-runtime refactor of PiSession-style pooling. The full persistent-worker version is still worth doing later for one-spawn-per-agent efficiency, but the visibility win was the immediate ask.

**Consequence.** Managed codex/opencode dispatches show Console activity end-to-end. The auto-close idle-workers reconciler and the stop-worker endpoint already operate on any `command in VIRTUAL_RPC_COMMAND_SET`, so lifecycle is wired automatically. Codex dispatches that fail (provider missing, executable not found, etc.) push a red `✗ error` frame and the run is marked failed via the bridge-side `.catch` retry (3× exponential backoff) — closes the operator-reported "stuck working" symptom from the same testing pass. Service-side `_close_orphaned_managed_runs` (default 5 min via `active_managed_run_stale_minutes`) catches the edge case where the bridge crashed entirely between claim and failure-PATCH.

## Symmetric turn-start / turn-end hooks across *-aify wrappers

**Decision.** Direct CLI typing (operator typing into `claude-aify` / `codex-aify` / `hermes-aify` WITHOUT going through aify-comms's dispatch path) now flips the agent to `working` on prompt submit and back to `online`/`available` on turn-end, symmetric with channel-route dispatches. New service endpoint `POST /agents/{id}/turn-start` (mirror of the existing `/turn-end`) sets `agent_turn_state.turn_busy=1`. Per-runtime hook installs target whichever event surface each CLI exposes:
- **claude-aify**: `~/.claude/settings.json` → `UserPromptSubmit` + `Stop` hooks (Claude Code's standard hook events).
- **codex-aify**: `~/.codex/hooks.json` → same schema (`UserPromptSubmit` + `Stop`). Inert on codex CLI versions that don't yet recognize those event names.
- **hermes-aify**: `~/.hermes/config.yaml` → shell hook on `pre_llm_call` (no turn-end event exposed by upstream shell hooks; the 120s server-side `turn_busy` stale window handles cleanup).
- pi-aify / opencode-aify: no documented hook surface today, but the wrappers now export `AIFY_COMMS_URL` so future hook surfaces (or operator-written tooling) can call `/turn-start` `/turn-end` without additional setup.

**Why.** Operator-asked 2026-05-22 ("we want stuff to be symmetrical and work in same way"). Before this, only channel-route dispatches set `turn_busy` — direct typing left the dashboard showing `online` while the assistant was actively mid-turn. Symmetry across runtimes was the explicit ask.

**Consequence.** All *-aify wrappers now export `AIFY_COMMS_URL` (was only claude-aify). install.sh's `install_claude_turn_start_hook`, `install_codex_turn_hooks`, and `install_hermes_turn_hooks` wire the hooks. The queue-gate fix (next entry) leans on this signal — without it, `queueIfBusy=true` would still fire prematurely on direct-typing turns.

## Queue gate respects turn_busy

**Decision.** `queueIfBusy=true` defers when ANY of `hasActiveRun`, `queuedRuns > 0`, or `agent_turn_state.turn_busy=1` (fresh, within `TURN_BUSY_STALE_SECONDS`) is true. Previously only the first two were checked.

**Why.** Operator-reported 2026-05-22 — clicking dashboard "Queue" sent the message immediately when the target was mid-turn. Repro: target received an `info` (`require_reply=0`) message; the dispatch_run auto-completed server-side on delivery (delivery-only path); the assistant kept working but `hasActiveRun=False` because no dispatch_run was in `claimed`/`running`. Queue therefore saw "not busy" and dispatched the next message immediately.

**Consequence.** Queue now correctly defers across `require_reply=0` dispatches AND across direct-CLI typing (because the turn-start hooks set `turn_busy`). Authoritative clear paths (Stop hook, reply-landing, orphan reconciler, 120s stale safety) remain unchanged.

## Bridge takeover on virtual rpc terminal_sessions

**Decision.** When a `POST /terminals/{id}/output` arrives with a `bridgeId` that differs from the terminal_session's stored `bridge_id` AND the terminal's `command` is in `VIRTUAL_RPC_COMMAND_SET`, ownership transfers to the new bridge (UPDATE bridge_id) and the write proceeds. Audit event `virtual_rpc_bridge_takeover` records the transition. Real PTY terminals (node-pty spawned by a specific bridge) keep the strict ownership check unchanged.

**Why.** Operator-reported 2026-05-22 — graph-tester-pi's synthesized Console showed "▶ turn started + —" indefinitely while chat kept getting fresh replies. Every dispatch since the bridge restart at 00:24 was claimed by a different bridge UUID (each restart picks a fresh `BRIDGE_INSTANCE_ID`). The strict `bridge_id` check at the output endpoint returned 409 for every new bridge's synth frames → frames dropped → terminal content frozen.

**Consequence.** Synthesized rpc terminals are now portable across bridge processes — the operator's frame stream stays continuous across restarts. Real PTY terminals (where ownership matters because a specific bridge spawned a specific node-pty process) keep their strict check; only the synth-row case relaxes.

**Revive-when-stopped (follow-up, `0a0231a`).** A subtle race surfaced after the initial takeover landed: bridge-supersession cleanup (`_stop_virtual_terminals_for_superseded_bridges`) can mark a synth terminal_session `stopped` while a new bridge is concurrently writing frames to it via `/terminals/{id}/output`. The takeover transferred ownership and the writes succeeded (frames appeared in `terminal_events`), but the row's `status='stopped'` lingered — dashboard kept saying "terminal is not running" even with frames streaming in. Bridge-takeover now ALSO revives the row when `current_status == 'stopped'` (`status='running'`, `stopped_at=NULL`, `error=''`). The arriving POST is hard proof the bridge is actively writing. Audit payload extended with `revived: bool`.

## Orphan-managed-run reaper covers terminal-mode runs too

**Decision.** `_close_orphaned_managed_runs` (the 5-min fast reaper) drops its `dispatch_mode != 'terminal'` exclusion and ALSO catches terminal-mode runs whose `claim_bridge_id` is empty. Same 5-min `active_managed_run_stale_minutes` window. Plus: the reaper now requires positive evidence of no progress (`NOT EXISTS dispatch_events since cutoff`) to avoid false-positive reaping of slow-claim clients.

**Why.** Operator-reported 2026-05-22 — hermes-test's queued run sat behind a terminal-mode running run with empty `claim_bridge_id` for 45+ minutes waiting for the generic 30-min `_discard_unusable_active_run` to catch up. The dispatch_mode!='terminal' exclusion was designed to avoid overlap with the generic reaper but was excluding exactly the case where the queue couldn't make progress. Adding the dispatch_events evidence requirement (code-review C1) hardens against false positives from legitimate slow clients.

**Consequence.** Stuck wrapper-PTY-backed dispatches now clear in 5 min instead of 30, unblocking queued messages. Same `active_managed_run_stale_minutes` setting tunes it. Legitimate in-flight runs that DO emit dispatch_events (the normal case) are untouched.

## Container name, repo name

The repo is `zimdin12/aify-comms` and the Docker container is `aify-comms-service`. Earlier versions used `aify-claude`; the rename is cosmetic and GitHub auto-redirects old URLs. If you see `aify-claude` in a log or filesystem path on an older install, it's the same project.

## Resident codex uses the existing WS app-server channel (no separate codex-channel.js)

**Decision.** Resident-codex dispatch delivery is handled by the existing `createCodexControllerLegacy` path in `mcp/stdio/runtimes.js:2118`. The main bridge claims resident-codex runs through `/dispatch/claim` (server.js:1857 with `executionModes` from `supportedExecutionModes`), `launchRuntimeRun` → `createCodexController` routes resident runs with `runtimeConfig.appServerUrl` set to `createCodexControllerLegacy`, which connects WebSocket to the per-instance `codex app-server` launched by `codex-aify` (install.sh:319-330) and issues `turn/start` on the resident's active thread. We did NOT create a separate `codex-channel.js` mirroring `claude-channel.js`.

**Why.** `claude-channel.js` is a separate process because Anthropic's `notifications/claude/channel` mechanism requires a separate MCP server entry registered via `--dangerously-load-development-channels server:aify-comms-channel`. Codex has no equivalent constraint — its native JSON-RPC `turn/start` against an existing `threadId` is the right primitive and is already used by the legacy controller. A separate process would duplicate the WS client, initialize/initialized handshake, turn lifecycle notification handling, and turn/interrupt support that `createCodexControllerLegacy` already implements, increasing the surface area for divergence bugs. The dispatch loop's `reportTurnBusy` pulse (server.js:1930) and explicit clear (server.js:2057-2065) already give resident codex the same status taxonomy as claude.

**Visibility note (codex #15320).** Externally-injected `turn/start` against a thread that a `codex --remote` TUI is attached to may not visibly render in the TUI live (history fixes up later). Mitigated by also pushing synth-terminal frames for resident dispatches — the dashboard Console pane shows the wake event reliably. The `executionMode === "managed"` gate on `terminalSinkProvider` in `runtimes.js` was lifted on 2026-05-24 so resident dispatches feed the same Console surface that managed dispatches always did.

**Reconsider if.** A future codex version ships a custom MCP notification primitive analogous to `notifications/claude/channel` that requires a separate MCP server entry to subscribe. At that point a real `codex-channel.js` is justified.

## Resident hermes uses `hermes dashboard --tui` as a hidden background gateway

**Decision.** `hermes-aify` (`install.sh:install_hermes_wrapper`) spawns `hermes dashboard --tui --port <free> --host 127.0.0.1 --no-open --skip-build` as a background child, captures the ephemeral `__HERMES_SESSION_TOKEN__` from the dashboard's `/` HTML response (`web_server.py:3688`), then `exec hermes chat --tui` with `HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<port>/api/ws?token=<token>` in env. The Ink TUI attaches via WebSocket to that gateway (`ui-tui/src/gatewayClient.ts:resolveGatewayAttachUrl`) instead of spawning its own stdio sidecar. The aify-comms bridge also attaches to the same `/api/ws` and sends `prompt.submit` (idle session) or `session.steer` (busy session) JSON-RPC frames for inbound aify-comms messages. `TeeTransport` (`tui_gateway/transport.py`) fans dispatcher events out to all attached clients, so the operator's terminal TUI renders the bridge-injected user turn + model reply naturally.

**Why.** Symmetric with the codex resident path (`codex-aify` runs `codex app-server` + `codex --remote`). The Ink TUI is already transport-pluggable; the dashboard's `/api/ws` is the documented multi-client gateway with the right primitives — `prompt.submit` for new turns, `session.steer` for mid-run insertion without interrupt. No upstream changes required; everything is available in hermes 0.14+. Mid-run insertion is a first-class primitive (lands on the next tool result of the running turn), so the operator's "mid-run insertions like claude code" requirement is met natively.

**Why not `hermes acp` or `hermes gateway run`.** `hermes acp` is the bridge's managed path and is single-client by design (single `_conn` per session) — can't be shared with an operator's TUI. `hermes gateway run` is for messaging-platform integrations (Telegram/Discord/etc.), NOT the TUI gateway — the name collision misled initial research.

**Visibility.** Symmetric synth-terminal frames pushed in `createHermesResidentChannelController` so the dashboard Console pane mirrors what the operator sees in their terminal TUI (echoed prompt, `[hermes] connecting...`, streamed reply, `■ turn ended`).

**Bypass.** `AIFY_HERMES_SKIP_GATEWAY=1` falls back to plain `hermes` exec for operators who don't want the dashboard child or have a broken install. Graceful internal fallback to plain hermes if any wrapper step fails (port allocation, dashboard timeout, token parse) — broken gateway never blocks operator-typed hermes.

**Reconsider if.** Upstream ships a dedicated `hermes chat --listen` flag that embeds the WS server in the chat process itself. At that point we drop the dashboard child and use the chat-embedded gateway directly.
