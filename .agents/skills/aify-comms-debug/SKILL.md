---
name: aify-comms-debug
description: Known aify-comms issues and how to fix them. Check here when a dispatch fails, a wake mode looks wrong, a run is stuck, a bridge seems stale, or Claude/Codex reports a path/channel error. Complements the main aify-comms skill.
---

# aify-comms: Troubleshooting

Use this skill whenever something in aify-comms is not behaving the way the main skill says it should. Each entry lists the **symptom**, the **cause**, and the **fix**.

Before digging in, always call `comms_agent_info(agentId="target")` on the agent in question and read `wakeMode`, `sessionMode`, `machineId`, `sessionHandle`, and `dispatchState`. Most of these fixes are just "something in that record is stale or wrong".

## Codex: `Invalid request: AbsolutePathBuf deserialized without a base path`

**Symptom.** Dispatches to a Codex agent fail with this Rust error. Dashboard may also show `Codex WebSocket app-server connection closed (1006)`.

**Root cause #1 (Windows, resident, and the one you hit first).** On Windows the bridge's `defaultCodexCommand()` returns `wsl.exe -e codex app-server`, so the legacy launcher-based `codexWorkingPath` transform turns `C:/Docker/project` into `/mnt/c/Docker/project` regardless of whether the bridge will spawn its own Codex or connect to one `codex-aify` already started. When the connection is to a native-Windows Codex (the normal `codex-aify` setup), sending `/mnt/c/...` makes Rust's `Path::is_absolute()` return false — there is no drive-letter prefix — and `AbsolutePathBuf::deserialize` rejects the request at `turn/start`. Fixed in the bridge by `resolveCodexRequestCwdFor` in `mcp/stdio/codex-errors.js`: when `appServerUrl` is set, the transform is skipped and we send `C:/Docker/project` instead. Locked down by `mcp/stdio/tests/codex-cwd-transform.test.js`. Check with `npm test` from `mcp/stdio/`. If the test is absent or fails, the bridge predates the fix — `git pull` and restart `codex-aify`.

**Backend guard (current build).** The server now rejects impossible resident Codex registrations up front: `linux:` / `darwin:` machine IDs cannot register `C:/...` cwds when `appServerUrl` is present, and `win32:` machine IDs cannot register `/mnt/...` cwds. If `comms_register` now fails immediately with `Invalid cwd`, that is the intended fast-fail path; fix the cwd and re-register instead of trying to dispatch through it.

**Root cause #2 (stored rollout).** Codex's `thread/resume` loads the thread's stored rollout from `~/.codex/sessions/...`. If a path field in that file cannot be deserialized, or if the rollout/context has grown past Codex's websocket frame limit (`Space limit exceeded: Message too long: ... > 16777216`), the call crashes before the bridge can send anything else. The tell is that the failed run has an **empty `externalThreadId`**: the bridge never got past `thread/resume`. This is the case the auto-heal path (below) is designed for.

**Auto-recovery (shipped).** On current bridge code, **both managed and resident** sessions auto-heal this case. When `thread/resume` fails with `AbsolutePathBuf deserialized`, `AbsolutePathBufGuard`, `no rollout found for thread id`, or Codex's websocket `Space limit exceeded` / `Message too long` error, the bridge:

1. Calls `thread/start` to create a brand-new Codex thread.
2. Fires `onSessionHandleChange(newHandle)`, which updates the cached agent state and POSTs `/agents` so the backend's stored `sessionHandle` points at the healed thread.
3. Continues the current dispatch against the new thread.

You'll see a line in the Codex session's stderr like:

```
[aify] healed sessionHandle for "graph-senior-dev" → <new-uuid> (reason: corrupt_rollout, previous: <old-uuid>)
```

For the websocket frame-limit case the reason is `oversized_rollout`.

**Trade-off for resident sessions.** The healed thread is *not* the one attached to the visible Codex TUI — it's a fresh background thread the Codex app-server knows about but your interactive session cannot see. Dispatched work runs successfully but you lose TUI visibility for that dispatch. The old behavior was "dispatch fails forever with a cryptic error", which is strictly worse. To restore full TUI visibility for future work, do the hard-reset sequence below.

**Check that auto-heal actually ran.** If you still see the raw `Invalid request: AbsolutePathBuf deserialized without a base path` in a dispatched run's error field (without a wrapping `healed sessionHandle` stderr line), then one of these is true:
- The bridge process is still running pre-fix code in memory. Relaunch `codex-aify`.
- The bridge's install dir hasn't been pulled yet. `cd` into it and `git pull`; run `npm test` from `mcp/stdio/` to confirm the classifier matches current error shapes.
- Both sides of the bridge were restarted but the classifier missed a new Codex error string. Send the run ID and I'll extend `detectCodexResumeFailure` in `codex-errors.js`.

**Hard reset (only needed to restore TUI visibility for the affected session).**
1. Kill every `codex-aify` and `codex app-server` process on the machine.
2. Move the poisoned rollout aside so Codex cannot re-offer it.
3. Delete the stale runtime markers.
4. `cd` into the target project directory.
5. Launch a fresh `codex-aify` from there.
6. Re-register with the new `$CODEX_THREAD_ID` from the fresh session.

The full commands are right below.

## Hard reset: Codex dispatches keep failing after update

Use this when a fresh dispatch still produces `AbsolutePathBuf` or other path errors immediately after an `aify-comms` update.

```powershell
# Windows PowerShell
Get-Process node, codex -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -match 'aify-comms|codex' } |
  Stop-Process -Force
Remove-Item "$HOME\.local\state\aify-comms\runtime-markers\codex-*.json" -Force -ErrorAction SilentlyContinue
```

```bash
# Linux / Mac / WSL
pkill -f codex-aify
pkill -f 'codex app-server'
rm -f ~/.local/state/aify-comms/runtime-markers/codex-*.json
```

Then launch a fresh `codex-aify` from the **actual project directory** you want bound, and re-register with explicit live env vars:

```
comms_register(
  agentId="coder",
  role="coder",
  runtime="codex",
  cwd="C:/Users/you/project",
  sessionHandle="$CODEX_THREAD_ID",
  appServerUrl="$AIFY_CODEX_APP_SERVER_URL"
)
```

Verify **before** dispatching:

```
comms_agent_info(agentId="coder")
```

Confirm `wakeMode: codex-live`, a non-empty `sessionHandle`, and the expected `machineId`. If any of those are wrong, the session is still bound to stale state.

Repeat for every Codex agent on the machine.

## Claude: wake mode stuck at `claude-needs-channel`

**Symptom.** `comms_agent_info` reports `wakeMode: claude-needs-channel` even though you launched with `claude-aify`. A previous agent may have worked around it by manually writing a runtime marker with a live `claude.exe` Windows PID — that's the fingerprint of this bug.

**Cause.** For a long time the `claude-aify` bash wrapper wrote the runtime marker itself with `pid=$$`. On Git Bash for Windows, `$$` is the MSYS shell PID, not a Windows process ID. The bridge's `isProcessAlive` check uses `process.kill(pid, 0)`, which on Windows only understands real Windows PIDs, so it returned false and `listRuntimeMarkers` **auto-deleted the marker on the next read**. Every claude-aify session on Windows silently lost its marker within a second and fell through to `claude-needs-channel`. Same root cause made `codex-aify` markers disappear, which is why the Codex auto-discovery path kept falling through to poisoned threads.

**Fix (shipped).** The marker is now written by the long-lived bridge process (`claude-channel.js` for Claude, `server.js` for Codex when `AIFY_CODEX_APP_SERVER_URL` is set) using node's real `process.pid`. The wrappers no longer touch markers. Requires: pull, restart `claude-aify` / `codex-aify`. Check `C:\Users\<you>\.local\state\aify-comms\runtime-markers\` after a fresh launch — the file should persist and its `pid` field should match a live node child of claude/codex.

**Fix (recovery when you hit this).** Make sure one `claude-aify` session is alive, then re-register:

```
comms_register(agentId="my-agent", role="coder", runtime="claude-code", cwd="C:/path/you/are/in")
comms_agent_info(agentId="my-agent")
```

On Windows, the installer creates both a Bash `claude-aify` and a `claude-aify.cmd` shim. From PowerShell / cmd prefer the `.cmd`; from Git Bash either is fine.

## Claude managed run fails: `Session ID ... is already in use`

**Symptom.** A dashboard-managed Claude run fails immediately with an error like `Session ID e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6 is already in use`.

**Cause.** The managed session record held a Claude session ID that another Claude process still owns. This can happen after a crash, stale bridge, duplicate restart, or a recovered session that reused a locked Claude session ID.

**Fix (current build).** Managed Claude runs detect this exact failure and stop instead of silently creating a fresh session. Silent session replacement discards native Claude chat memory, so it is now an explicit operator choice. Close the duplicate Claude process that owns the session, or use Dashboard **Sessions/Team -> Clear resume state** when you intentionally want the next run to start with a fresh backing session. Restart the Windows `aify-comms` bridge after updating so it loads the fixed runtime adapter.

**Resident caveat.** Resident Claude sessions are not silently swapped, because their session ID is the visible CLI binding. If a resident session hits this, close the duplicate Claude tab/process, restart with `claude-aify`, and re-register from the live session.

## Machine ID shows `win32:unknown-host`

**Symptom.** Agent's `machineId` is `win32:unknown-host` instead of the real hostname.

**Cause.** `COMPUTERNAME` / `HOSTNAME` env vars were not propagated into the node process that hosts the bridge. The current build falls back to `os.hostname()` before `unknown-host`.

**Fix.** Restart the bridge (restart your `claude-aify` / `codex-aify` session) and re-register. Cosmetic only — it does not block routing, because dispatches are routed by `agentId` rather than `machineId`.

## Send rejected because target has queued work

**Symptom.** `comms_send` returns `ok: false` with `reason: "agent already has queued work"` or `reason: "agent is working"`.

**Cause.** Normal send is live-delivery gated. It no longer appends chat messages to future runs. This prevents fragile "message sent but nobody actually woke up" behavior.

**Fix.** Pick one of:
- Wait for the in-flight/queued run to finish.
- `comms_run_interrupt(runId=<current active run>)` if the current work should stop.
- Use the dashboard run controls to cancel stale queued work.
- `comms_agent_info(agentId=<target>)` to inspect why the agent is not currently startable.
- If the agent is actively running and steer-capable, use `comms_send(steer=true)` for guidance that belongs in the current run.

## Run stuck `running`, `comms_run_interrupt` has no effect

**Symptom.** A dispatch is marked `running` but nothing is happening. `comms_run_interrupt` returns ok but the run never moves.

**Cause (Codex / managed sessions).** The bridge that owned the run has died (crash, machine sleep, network drop). `comms_run_interrupt` works by enqueueing a control the owning bridge polls for — if the bridge is gone, no one claims the control.

**Cause (Claude resident).** On older bridge code, the channel bridge claimed dispatch runs and left them `running` indefinitely — it had no way to track Claude's progress. On current code, the channel bridge completes runs immediately on delivery, so this failure mode no longer occurs for Claude agents. If you still see it, the bridge is running pre-fix code — `git pull` and restart `claude-aify`.

**Auto-recovery (current build).** When a replacement bridge polls `/dispatch/claim` for the same agent, the server gives a recently claimed run a short grace window before declaring it stale. During that window the replacement bridge sees `blockedBy.reason = "active_run_owned_by_previous_bridge"` and should retry. If the previous bridge does not finish, the server then marks the orphaned run failed automatically and existing queued run-control work may be claimed normally. Normal `comms_send` will not create additional queued work while the target is blocked.

For older dispatch-backed messages, the original inbox message may still exist. For current normal `comms_send`, failed live delivery writes no message row, so retry after the agent is startable.

**Manual fix (if no bridge is polling).** Cancel the run directly through the HTTP API:

```bash
curl -X PATCH http://localhost:8800/api/v1/dispatch/runs/<run_id> \
  -H "Content-Type: application/json" \
  -d '{"status":"cancelled","error":"Bridge died, orphaned run"}'
```

Afterwards, restart `claude-aify` / `codex-aify` to bring a live bridge back online.

## Not live-bound when you expected `codex-live`

**Symptom.** Right after `comms_register` the agent is not live-bound, or an older API/debug view shows `wakeMode: message-only`, even though you're inside `codex-aify`.

**Causes.**
- Multiple `codex-aify` sessions are open on the same machine — the bridge sees ambiguous live markers and refuses to pick one.
- The wrapper was launched from a different directory than the `cwd` you passed to `comms_register` and auto-discovery can't resolve it.
- The live env vars `$CODEX_THREAD_ID` / `$AIFY_CODEX_APP_SERVER_URL` were not available inside the session at register time.

**Fix (deterministic):** re-register from that same live session with explicit binding:

```
comms_register(
  agentId="my-agent",
  role="coder",
  runtime="codex",
  cwd="C:/your/exact/project",
  sessionHandle="$CODEX_THREAD_ID",
  appServerUrl="$AIFY_CODEX_APP_SERVER_URL"
)
comms_agent_info(agentId="my-agent")
```

If only the thread ID is available, pass `sessionHandle` without `appServerUrl`. If neither is available, the session predates the current live-wake flow — restart Codex through `codex-aify` and try again.

## Superseded or stale bridge: claim blocked

**Symptom.** A bridge's dispatch loop logs `blockedBy: {reason: "bridge_superseded"}` or `blockedBy: {reason: "bridge_not_current"}`.

**Cause.** A newer `comms_register` for the same `agentId` on the same machine has replaced this bridge. For Codex/OpenCode, the server also compares the polling bridge ID against the agent's current `runtimeState.bridgeInstanceId`; this catches old processes whose bridge row is gone but whose dispatch loop is still alive.

**Fix.** Shut the superseded bridge down. This is not an error — it's the server protecting the queue. The fresh bridge is the one that should be claiming runs.

## Environment still shows online after bridge stopped

**Symptom.** The Environments page still shows a Windows/WSL/Linux bridge as `online` after you closed the visible terminal, or the same environment cards keep changing order.

**Cause.** Environment presence is heartbeat-based. A graceful `Ctrl+C` on current bridge code sends one final offline heartbeat. A hard kill, crashed terminal, machine sleep, or older bridge build can only be inferred after missed heartbeats. If `lastSeen` is still updating, some process is still posting as that bridge; the dashboard card shows the bridge process PID from heartbeat metadata when available.

**Fix.**
- Pull latest, reinstall, and restart `aify-comms` so the bridge has graceful offline reporting.
- Check for leftover processes with `ps -ef | rg 'aify-comms|mcp/stdio/server.js'` on WSL/Linux, or `Get-Process node | Select-Object Id,Path,CommandLine` on Windows.
- Starting a newer `aify-comms` for the same environment supersedes older bridge heartbeats when both advertise `bridgeStartedAt`; the server also queues a stop control for the older bridge. A fresh bridge ignores stale stop controls that were requested before that bridge started. Old OS processes still need manual cleanup if they are hung and no longer polling, but they should not own spawn claims.
- Use the dashboard **Kill bridge** action while the bridge is online. Managed teammates from that environment become offline/detached; chats and identities remain. Assign them to another online environment from **Team** or restart the bridge, then recover/restart from **Sessions**.
- Use **Forget** only to hide an obsolete execution target. Forgetting keeps agent identities, chats, saved spawn specs, and session records; it no longer deletes managed teammates.
- If a spawn request is marked `running` but the first brief dispatch failed, current server code repairs it to `failed` on the next spawn-request list refresh.

## `aify-comms` exits with `environment ... was superseded`

**Symptom.** A bridge terminal exits shortly after start with a message like `environment windows:host:default was superseded by replacement bridge ..., pid ..., cwd ...`.

**Cause.** Only one bridge is current for a given environment ID such as `windows:HOST:default` or `wsl:HOST:default`. A newer bridge heartbeat for the same environment replaced this process, so the server sent this older bridge a targeted stop control. This is intentional: old bridges must not keep claiming spawns or managed runs after a newer bridge takes ownership.

**Fix.** Keep one `aify-comms` process per environment. If the replacement cwd/pid is not the one you want, stop that replacement process from the Dashboard **Environments -> Kill bridge** action or with the OS process manager, then start `aify-comms` from the directory/root you want to be current. The terminal message names the replacement bridge, PID, and cwd so you can identify it.

If the replacement cwd is an agent workspace and appears immediately after a managed Claude/Codex run starts, the bridge is running an old launcher/runtime that lets child MCP servers inherit `AIFY_ENVIRONMENT_BRIDGE=1`. Pull latest, rerun the installer, and restart the OS bridge. Current launchers mark the real bridge with `--environment-bridge`, and managed child processes strip bridge-only env vars before spawning.

## `comms_send(steer=true)` stayed unread or looked queued behind itself

**Symptom.** A steer message lands in the inbox unread, the tool output says it was queued behind the same run ID, or a steer sent during a bridge replacement seems to disappear.

**Cause.** Older server code treated a steered result like a newly queued run and could target a stale active run that was still owned by a superseded bridge. In that state the source inbox message had no completed steer control to mark it read.

**Fix (current build).** Pull latest and restart the target bridge (`codex-aify` / `claude-aify`) so it is running the steer-tracking fix. Current behavior is:
- if there is a live steer-capable active run, the message becomes a steer control and the inbox copy auto-marks read when the control completes
- if the target cannot accept live delivery, `comms_send` returns a not-sent notice instead of queueing future work
- if the runtime does not support steering, the send follows the normal live-start gate

If you still see the old behavior after update, capture the run ID plus `/api/v1/dispatch/runs/<id>` and `/api/v1/agents/<agent>` output.

## Run summary says `Auto-healed: bridge "<old>" replaced by "<new>"`

**Symptom.** A dispatch run shows an auto-heal summary like `Auto-healed: bridge "old" replaced by "new"` or `Auto-healed before steer...`.

**Cause.** The server saw a new live bridge polling for the agent while the DB still had an active run claimed by an older bridge. If that run was older than the bridge-replacement grace window, the server treated it as orphaned and failed it to unblock the queue.

**Fix.** Usually no repair is needed beyond shutting down the stale bridge and re-registering from the live session. This is a recovery path, not silent data loss for older dispatch-backed messages. If it happens seconds after a reconnect, update and restart the dashboard service: current builds wait briefly before failing another bridge's active run. Current normal sends will fail fast instead of queueing fresh work behind stale state. If this repeats on every dispatch, an old bridge is probably still polling; current builds should block it with `bridge_not_current` before it can claim fresh work.

## Bridge "lost" the agent / has to be re-registered manually

**Symptom.** An agent that used to work stops claiming dispatches. Messages still arrive in its inbox but nothing launches. Manually re-registering the agent makes it work again.

**Cause.** On older builds, the server could not distinguish "agent was intentionally removed" from "agent disappeared because the DB was rotated or cleared accidentally." The bridge's local cache kept polling with the old `agentId`, saw `404`, and auto-re-registered it. Current builds use intentional-remove tombstones: dashboard DELETE / `comms_remove_agent` / `comms_clear(target="agents", agentId=...)` return `410 Gone` to that bridge cache, so the bridge forgets the ID instead of recreating it. Plain `404` still means "server forgot this agent" and may auto-re-register.

**Auto-recovery (current build).** The bridge now:
- Retries transient HTTP errors up to 3 times with exponential backoff (250ms / 500ms / 1000ms) before giving up on any single call.
- Watches for `404` responses on `/agents/{id}` and `/dispatch/claim`. A 404 means the agent is unknown to the server, so the bridge automatically re-registers from its cached agent data.
- Watches for `410` responses on `/agents/{id}` and `/dispatch/claim`. A 410 means the ID was intentionally removed, so the bridge stops tracking it.
- Counts consecutive claim failures per agent. After 4 in a row, the bridge tries an auto-re-register from cache as a last-resort self-heal.

Look for these lines on stderr:

```
[aify] agent "foo" missing from server; auto-re-registering
[aify] auto-re-registered "foo" from cached state
[aify] stopped tracking "foo": server marked it intentionally removed
[aify] 4 consecutive dispatch/claim failures for "foo"; attempting auto-re-register
```

**Fix when auto-recovery fails.** If you see the auto-re-register log followed by `auto-re-register failed for "foo"`, the server itself is unreachable or rejecting the payload. Check:
1. `curl http://localhost:8800/health` — is the server even up?
2. The bridge's cached state may be missing a required field (role, runtime) if the agent was never fully registered in the first place. Manual `comms_register(...)` with complete fields is the definitive recovery.

**Removing one bad ID.** Use:

```
comms_remove_agent(agentId="wrong-id")
```

or:

```
comms_clear(target="agents", agentId="wrong-id")
```

Do not use `comms_clear(target="agents")` unless you intend to remove every agent.

## Re-register seemingly "not taking effect"

**Symptom.** You re-register with new values but `comms_agent_info` still reflects the old ones.

**Cause.** Re-register is a **full state refresh** for session-related fields. If you pass `sessionHandle=""` (empty) or omit it, that's what gets stored — old session handles are cleared. If the result is "wrong", the bridge did what you asked.

Note that `description` is the one exception: omitting it preserves the existing value. Pass `description=""` to clear it explicitly.

**Fix.** Pass every field you care about on the re-register call. For Codex resident triggering, that usually means `cwd`, `sessionHandle`, and `appServerUrl` all explicit.

## Install.sh on Windows / Git Bash

Current installer behavior:

- `--with-hook` is Git Bash aware. It writes native Windows hook paths without MSYS path mangling, so the old `C:\c\Users\...` failure should not require manual `settings.json` or `hooks.json` edits.
- The installer creates Bash wrappers and `.cmd` shims in `%USERPROFILE%\.local\bin`, including `aify-comms.cmd`, `claude-aify.cmd`, and `codex-aify.cmd` when the matching client is installed.
- The `.cmd` shims prepend Git's Unix binary directories when they can find Git, so `sed`/`bash` should be available even when PowerShell only had `C:\Program Files\Git\cmd` on PATH.

If Windows still cannot find `aify-comms.cmd` after install:

```powershell
$env:Path += ";$env:USERPROFILE\.local\bin"
& "$env:USERPROFILE\.local\bin\aify-comms.cmd"
```

If Claude is installed but `claude.cmd` is missing, the wrapper falls back to `claude` when available. Prefer the native Windows Claude Code install when possible, then restart Claude/Codex after reinstalling aify-comms.

## General escalation

If none of the fixes above resolve the issue:

1. Capture the exact symptom (dispatch run ID, agent ID, error text).
2. Hit `curl http://localhost:8800/api/v1/dispatch/runs/<id>` to get the raw run state.
3. Hit `curl http://localhost:8800/api/v1/agents/<id>` for the agent state.
4. Forward those three pieces to whoever is debugging aify-comms. A fresh repro against current code (post-hard-reset) is worth 10× more than a trace against stale state.
