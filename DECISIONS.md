# aify-comms: Design Decisions & Current Limits

Short rationale log for non-obvious choices, plus the current runtime limits. If you're wondering *why* the service behaves a certain way, this file beats guessing from the code.

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

## Container name, repo name

The repo is `zimdin12/aify-comms` and the Docker container is `aify-comms-service`. Earlier versions used `aify-claude`; the rename is cosmetic and GitHub auto-redirects old URLs. If you see `aify-claude` in a log or filesystem path on an older install, it's the same project.
