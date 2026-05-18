---
name: aify-comms-debug
description: Known aify-comms issues and how to fix them. Check here when a dispatch fails, a wake mode looks wrong, a run is stuck, a bridge seems stale, or Claude/Codex reports a path/channel error. Complements the main aify-comms skill.
---

# aify-comms: Troubleshooting

Use this skill whenever something in aify-comms is not behaving the way the main skill says it should. Each entry lists the **symptom**, the **cause**, and the **fix**.

Before digging in, always call `comms_agent_info(agentId="target")` on the agent in question and read `wakeMode`, `sessionMode`, `machineId`, `sessionHandle`, and `dispatchState`. Most of these fixes are just "something in that record is stale or wrong".

## Contents

- Codex `AbsolutePathBuf` / `thread/resume` failures, hard reset
- Claude wake-mode and `Session ID already in use`
- Oh My Pi / OMP: `(no output)`, wrong-provider API key, auth fail-fast, dead-handle heal
- Spawn/workspace path errors, `ENOENT`, machine ID
- Dispatch: send rejected, run stuck `running`, superseded bridge, orphaned runs
- Environment presence, re-register semantics, install.sh on Windows
- Dashboard console-mode: DB lock storm, console flicker, broken statuses, parsing error, env-not-found, open-terminal (see "Dashboard console-mode" section)
- General escalation

## Codex: `Invalid request: AbsolutePathBuf deserialized without a base path`

**Symptom.** Dispatches to a Codex agent fail with this Rust error. Dashboard may also show `Codex WebSocket app-server connection closed (1006)`.

**Root cause #1 (Windows, resident, and the one you hit first).** On Windows the bridge's `defaultCodexCommand()` returns `wsl.exe -e codex app-server`, so the legacy launcher-based `codexWorkingPath` transform turns `C:/Docker/project` into `/mnt/c/Docker/project` regardless of whether the bridge will spawn its own Codex or connect to one `codex-aify` already started. When the connection is to a native-Windows Codex (the normal `codex-aify` setup), sending `/mnt/c/...` makes Rust's `Path::is_absolute()` return false — there is no drive-letter prefix — and `AbsolutePathBuf::deserialize` rejects the request at `turn/start`. Fixed in the bridge by `resolveCodexRequestCwdFor` in `mcp/stdio/codex-errors.js`: when `appServerUrl` is set, the transform is skipped and we send `C:/Docker/project` instead. Locked down by `mcp/stdio/tests/codex-cwd-transform.test.js`. Check with `npm test` from `mcp/stdio/`. If the test is absent or fails, the bridge predates the fix — `git pull` and restart `codex-aify`.

**Backend guard (current build).** The server now rejects impossible resident Codex registrations up front: `linux:` / `darwin:` machine IDs cannot register `C:/...` cwds when `appServerUrl` is present, and `win32:` machine IDs cannot register `/mnt/...` cwds. If `comms_register` now fails immediately with `Invalid cwd`, that is the intended fast-fail path; fix the cwd and re-register instead of trying to dispatch through it.

**Root cause #2 (stored rollout).** Codex's `thread/resume` loads the thread's stored rollout from the active `CODEX_HOME` under `sessions/...`. Dashboard-managed Codex uses a managed home (`~/.local/state/aify-comms/managed-codex-home`), while a resident or manually started Codex usually used `~/.codex`. If the saved handle points at a rollout that exists only in the other home, Codex reports `no rollout found for thread id ...`. If a path field in the file cannot be deserialized, or if the rollout/context has grown past Codex's websocket frame limit (`Space limit exceeded: Message too long: ... > 16777216`), the call crashes before the bridge can send anything else. The tell is that the failed run has an **empty `externalThreadId`**: the bridge never got past `thread/resume`.

**Auto-recovery (current build).** When managed Codex gets `no rollout found for thread id`, the bridge first searches the normal Codex homes (`CODEX_HOME`, then `~/.codex`), copies the matching `sessions/.../rollout-*.jsonl` and any `shell_snapshots/...` files into the managed Codex home, and retries `thread/resume` once. This preserves native chat memory when the thread exists but was stored under the resident/default Codex home.

If the rollout is corrupt, oversized, or cannot be found in any Codex home, ordinary recover/restart fails loudly instead of silently discarding memory. Only an explicit Dashboard **Sessions -> Recreate** / `fresh_context` request creates a replacement thread. In that explicit mode the bridge:

1. Calls `thread/start` to create a brand-new Codex thread.
2. Fires `onSessionHandleChange(newHandle)`, which updates the cached agent state and POSTs `/agents` so the backend's stored `sessionHandle` points at the healed thread.
3. Continues the current dispatch against the new thread.

You'll see a line in the Codex session's stderr like:

```
[aify] healed sessionHandle for "graph-senior-dev" → <new-uuid> (reason: corrupt_rollout, previous: <old-uuid>)
```

For the websocket frame-limit case the reason is `oversized_rollout`. For managed `no_rollout` imports, the run log instead shows `Imported Codex rollout ...; retrying thread/resume` followed by `Resumed imported Codex thread ...`.

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

**Fix (shipped).** The marker is now written by the long-lived bridge process (`claude-channel.js` for Claude, `server.js` for Codex when `AIFY_CODEX_APP_SERVER_URL` is set) using node's real `process.pid`. Claude markers also include the parent Claude process PID, so a plain Claude tab cannot accidentally bind through another tab's channel. The wrappers no longer touch markers. Requires: pull, restart the affected wrapper (`claude-aify`, `codex-aify`, `omp-aify`, or `pi-aify`). Check `C:\Users\<you>\.local\state\aify-comms\runtime-markers\` after a fresh launch — the file should persist and its `pid` field should match a live node child of the runtime.

**Fix (recovery when you hit this).** Start the same Claude session through `claude-aify`, then re-register from that session:

```
comms_register(agentId="my-agent", role="coder", runtime="claude-code", cwd="C:/path/you/are/in")
comms_agent_info(agentId="my-agent")
```

On Windows, the installer creates both a Bash `claude-aify` and a `claude-aify.cmd` shim. From PowerShell / cmd prefer the `.cmd`; from Git Bash either is fine.

## Claude managed run fails: `Session ID ... is already in use`

**Symptom.** A dashboard-managed Claude run fails immediately with an error like `Session ID e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6 is already in use`.

**Cause.** For old data/old bridge builds, the common managed-run cause was using Claude Code's `--session-id` flag for a session that already had a transcript file; `--session-id` is for creating a specific new session, while `--resume <id>` continues an existing one. A less common Windows cause is a stale headless Claude process that still owns the backing session after a crash or duplicate restart. Current dashboard-managed Claude work is PTY/channel backed and no longer uses `claude -p`.

**Fix (current build).** Managed Claude runs detect this exact failure and stop instead of silently creating a fresh session. Silent session replacement discards native Claude chat memory, so it is now an explicit operator choice. Close the duplicate Claude process that owns the session, or use Dashboard **Sessions -> Actions -> Recreate** when you intentionally want the next run to start with a fresh backing session. Restart the Windows `aify-comms` bridge after updating so it loads the fixed runtime adapter.

**Resume behavior.** Current bridge builds start interactive Claude Code through the managed PTY/channel path and pass `--resume <session-id>` when a saved handle exists. Pull latest, rerun the installer, and restart the Windows `aify-comms` bridge so it loads that path.

**Hidden-process caveat.** The duplicate owner may still be an old headless managed `claude -p` child, not a visible CLI tab. Older bridge builds on Windows launched Claude through `cmd.exe /c`; killing or superseding the bridge could kill `cmd.exe` without killing the Claude child, leaving a stale process behind. Current bridge code terminates the whole process tree on timeout, stop, interrupt, and bridge shutdown. When a managed Claude run still hits the error after the transcript/resume check, the Windows bridge first looks for a process command line containing the exact locked session ID, excludes interactive `claude-aify` / `--resume` commands, kills that process tree, and retries once. If the session ID is not visible in the process command line, it also checks aify runtime markers for the same workspace and stops a marked Claude parent only when that parent looks headless (`-p`, `--print`, or `--session-id`).

If the automatic cleanup cannot find a matching headless process, remove the stale Windows Claude process manually. From an elevated PowerShell:

```powershell
Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -match 'b67ab2d1-a121-43d2-9c63-5ad0a2883e72' -and
    $_.CommandLine -notmatch 'claude-aify' -and
    $_.CommandLine -notmatch '(^|\s)--resume(\s|=)'
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Replace the session ID with the one from the run error. If that finds nothing, list likely hidden Claude owners for the workspace with:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'claude|claude-channel|aify-comms' } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine |
  Format-List
```

Then restart the Windows `aify-comms` bridge and recover/restart the dashboard session. Use **Recreate** only when you accept losing that native Claude memory.

**Visibility caveat.** Dashboard-managed Claude Code is now a managed `claude-aify` PTY backing. Browser Console can attach to that PTY, and a separate native CLI can still be opened with the dashboard's copyable resume command (`claude-aify --resume <session-id>`) after the backing has recorded a resume ID.

If you want the resumed CLI to match managed-agent permissions, use `--dangerously-skip-permissions`. Do not use `--permanently-skip-permissions`; Claude Code rejects it as an unknown option.

Prefer the dashboard resume command or `claude-aify --aify-agent <agentId> --resume <session-id>` when opening a managed Claude session directly. The wrapper auto-registers the resident owner. If a managed run is active, takeover is deferred until that turn ends; after closing the CLI, dashboard sends can return to the saved managed backing once the resident lease expires. **Pause for CLI** remains an explicit safety control when you want dashboard sends to fail fast while the terminal owns the session.

After opening the native CLI, re-register from that same session with the same `agentId`. That is how the dashboard learns the current native handle. If the agent forgets CLI conversation after returning to dashboard, check whether the session's stored handle changed or was recreated during adopt/restart. Current code should preserve handles across same-runtime adopt/recover/restart; a new handle should only appear after a new spawn or explicit **Recreate**.

**Dashboard handle repair.** If you know the correct native Claude session ID / Codex thread ID / OpenCode or Pi handle, use Dashboard **Chat details -> Runtime Session -> Set handle** or **Sessions -> Actions -> Set handle**. This updates the identity's saved `sessionHandle`, runtime state (`sessionId` or `threadId`), and latest session record without creating a fresh context. Use it only when you know the handle belongs to the intended transcript/thread; a wrong handle binds the identity to the wrong native memory.

**Resident caveat.** Resident Claude sessions are not silently swapped, because their session ID is the visible CLI binding. If a resident session hits this, close the duplicate Claude tab/process, restart with `claude-aify`, and re-register from the live session.

## Managed Oh My Pi / OMP reply is `(no output)`

**Symptom.** A dashboard-managed OMP (`runtime="pi"`) run reaches `agent_end`, but the dashboard stores `(no output)` as the human-visible reply.

**Cause.** Older OMP RPC adapters only captured streaming `text_delta` events. OMP can also provide the final assistant text on completion events such as `message_end`, `turn_end`, or `agent_end`.

**Fix.** Pull current `aify-comms` and restart the affected `aify-comms` / `omp-aify` bridge process (`pi-aify` is an alias). Current builds capture streamed deltas and final completion-event text before deciding that a managed run produced no reply. Verify the bridge checkout with `npm test` from `mcp/stdio/`.

## Managed Oh My Pi / OMP fails with Cursor API key when model is `default`

**Symptom.** A managed OMP run is cancelled before a chat reply and reports `No API key found for cursor`, even though `~/.omp/agent/agent.db` exists.

**Cause.** Older adapters treated stored `model: "default"` as a concrete model and launched `omp --mode rpc --model default`. OMP resolves that literal model name through the Cursor provider, which requires Cursor credentials.

**Fix.** Current OMP runtime handling treats blank model values and case-insensitive `default` as no explicit override, so the bridge launches `omp --mode rpc` and lets OMP use `~/.omp/agent/config.yml`. Pull current `aify-comms`, restart the host-side bridge/wrapper, and retry the managed run.

## Managed spawned agent workspace is stored as `\home\dev\...`

**Symptom.** A Linux/WSL managed spawn shows a workspace like `\home\dev\projects\repo` instead of `/home/dev/projects/repo`.

**Cause.** Older service builds normalized slash style for root validation but persisted the original requested workspace string into spawn/session records.

**Fix.** Current service builds normalize workspace paths for the selected environment before persisting spawn requests and runtime specs. Non-Windows environments store POSIX slashes; Windows environments keep Windows path style. Update/restart the service, then retry or repair the affected spawn/session workspace.

## Claude/Pi managed run fails: `spawn "/path/to/claude-or-omp" ENOENT`

**Symptom.** A managed Claude Code or Oh My Pi run fails before the agent replies with an error like `spawn "/home/dev/.local/bin/claude" ENOENT` or `spawn "/home/dev/.local/bin/omp" ENOENT`, even though the diagnostic says `command -v` resolved the launcher.

**Cause.** Node reports the same `spawn <command> ENOENT` shape when either the command cannot be executed **or the requested runtime cwd/workspace does not exist on that bridge host**. This is common after moving between Windows, WSL, Linux, or another PC: the launcher path may be valid, but the saved agent workspace belongs to a different environment or a root that is not mounted there. Other real launcher causes are still possible: missing execute bit, stale symlink, a script with a broken shebang interpreter, or an ELF/native binary whose loader is missing.

**Fix (current build).** Runtime launches preflight the cwd before `spawn()`. A bad workspace now fails as `Workspace "... " does not exist on this bridge host` / `not a directory` / `not readable`, instead of blaming `claude` or `omp`. Update and restart the host-side bridge/wrapper so this diagnostic is loaded.

If the cwd is valid and the error still says the launcher cannot execute, verify the launcher on the same host/user as the bridge:

```bash
ls -l /home/dev/.local/bin/claude /home/dev/.local/bin/omp
readlink -f /home/dev/.local/bin/claude /home/dev/.local/bin/omp
head -1 /home/dev/.local/bin/claude /home/dev/.local/bin/omp
command -v node bun claude omp
```

Set `AIFY_CLAUDE_COMMAND` or `AIFY_PI_COMMAND` only when you know the absolute path points at a real executable for that host. If the problem is a workspace mismatch, repair the agent/session workspace or spawn/adopt it in the environment that owns that path; do not paper over it with a launcher override.

**If the error still shows an old `bridge build=` after an update.** That is not Node's module cache. The build tag is computed from the git checkout that the currently running bridge process loaded. If it says `bridge build=231c607...` after the repo has newer commits, the active process was not restarted or is running from a different checkout. On the affected host, inspect the PID from the error:

```bash
ps -fp <pid>
readlink -f /proc/<pid>/cwd
tr '\0' '\n' < /proc/<pid>/environ | grep -E '^(AIFY|PATH|HOME)='
cd /home/dev/aify-comms && git rev-parse --short HEAD
```

Then stop every old bridge/wrapper for that host and start a fresh bridge from the intended checkout:

```bash
pkill -f 'mcp/stdio/server.js'
pkill -f 'aify-comms'
pkill -f 'claude-aify'
pkill -f 'omp-aify'
cd /home/dev/aify-comms
git pull
bash install.sh --client codex http://192.168.100.10:8800 --with-hook
bash install.sh --client claude http://192.168.100.10:8800 --with-hook
bash install.sh --client pi http://192.168.100.10:8800
aify-comms /path/to/workspace-root
```

The next dashboard failure/success diagnostic should report the new build tag. If it does not, the dashboard is still talking to another bridge process or another checkout.

## Machine ID shows `win32:unknown-host`

**Symptom.** Agent's `machineId` is `win32:unknown-host` instead of the real hostname.

**Cause.** `COMPUTERNAME` / `HOSTNAME` env vars were not propagated into the node process that hosts the bridge. The current build falls back to `os.hostname()` before `unknown-host`.

**Fix.** Restart the bridge or wrapper session and re-register. Cosmetic only — it does not block routing, because dispatches are routed by `agentId` rather than `machineId`.

## Send rejected because target has queued work

**Symptom.** `comms_send` returns `ok: false` with `reason: "agent already has queued work"` or `reason: "agent is working"`.

**Cause.** Normal send is live-delivery gated for unreachable agents. Current builds still allow busy live agents: steer-capable targets receive a steer control, and busy non-steer targets queue/merge as next-turn work. Seeing this error usually means the target is not actually live-startable, the bridge is stale, the target is paused/stopped/offline, or the queue is blocked by stale state.

**Fix.** Pick one of:
- Wait for the in-flight/queued run to finish.
- `comms_run_interrupt(runId=<current active run>)` if the current work should stop.
- Use the dashboard run controls to cancel stale queued work.
- `comms_agent_info(agentId=<target>)` to inspect why the agent is not currently startable.
- If the agent is actively running, ordinary `comms_send(...)` should steer when supported or queue/merge as the next-turn fallback. Set `steer=true` only when you need to be explicit; use `queueIfBusy=true` only to force next-turn delivery. When `queueIfBusy=true`, any `steer` value is intentionally ignored.

## Run stuck `running`, `comms_run_interrupt` has no effect

**Symptom.** A dispatch is marked `running` but nothing is happening. `comms_run_interrupt` returns ok but the run never moves.

**Cause (Codex / managed sessions).** Either the bridge that owned the run has died (crash, machine sleep, network drop), or Codex accepted `turn/start` and then stopped emitting runtime notifications. `comms_run_interrupt` works by enqueueing a control the owning bridge polls for — if the bridge is gone, no one claims the control.

If the last event is `Started mcpToolCall`, do not assume it is WSL-specific. The Codex turn is inside a tool call. A normal `comms_send` / `comms_inbox` call should return quickly. The deprecated `comms_listen` long-poll can intentionally wait and should not be used in managed runs; older builds let mistaken listen calls wedge the run until the outer quiet watchdog fired. Current MCP builds apply a bounded timeout to ordinary remote HTTP tool calls (`AIFY_HTTP_TIMEOUT_MS`, default 20000ms), so the model can see the tool error and use the prompt's plain-text fallback handoff instead of sitting forever.

Current managed Codex config also sets `tool_timeout_sec = 25`, `disabled_tools = ["comms_listen"]`, and `AIFY_MANAGED_DISPATCH=1` for its aify-comms MCP server. Managed Codex runs now use Codex's unattended bypass sandbox profile by default; `approvalPolicy=never` plus `workspace-write` can still cause non-interactive MCP calls to be cancelled or wedge without a normal tool result. The bridge also copies the bundled `aify-comms` and `aify-comms-debug` skills into the managed Codex home. Current WSL/Linux builds kill the whole managed runtime process tree on timeout/interrupt/stop; older builds killed only the parent app-server and could leave orphan MCP children behind. If a managed Codex run still sits at `Started mcpToolCall aify-comms` for minutes after updating, or says those skills are missing, the bridge process is stale or the managed CODEX_HOME/app-server is still running old settings; pull/rebuild/restart the WSL bridge so it regenerates the managed Codex config, skills, and launch policy.

**Codex quiet watchdog (current build).** Managed runtimes have a long hard timeout for genuinely long work, and managed Codex also treats a turn as stalled if no Codex runtime notification or stderr line is seen for the quiet timeout window after the last activity. Defaults are 12 hours hard timeout and 30 minutes quiet timeout. A narrower aify-comms MCP watchdog fails stuck `mcpToolCall aify-comms` items after 90 seconds by default because comms tool calls should either return quickly or fail clearly. Change per agent with `runtimeConfig.timeoutMs`, `runtimeConfig.quietTimeoutMs` / `runtimeConfig.silenceTimeoutMs`, and `runtimeConfig.mcpToolTimeoutMs` / `runtimeConfig.commsToolTimeoutMs`; set quiet timeout to `0` only for agents expected to run very long silent commands, and set MCP tool timeout to `0` only while debugging the MCP transport. The run is failed cleanly, the managed runtime process tree is terminated, stale controls are failed, and a required handoff is mirrored back to the sender.

**Bad root artifacts.** Older launchers could accidentally treat `--help` or another flag-like argument as an advertised workspace root. Current launchers make `aify-comms --help` print usage and reject unknown options, and the service ignores flag-like roots from stale heartbeats. If an environment still shows a root like `--help`, restart the bridge after reinstalling, or edit/reset the environment roots in the dashboard.

**Cause (Claude channel).** On older bridge code, the channel bridge claimed run records and left them `running` indefinitely after delivery — it had no way to track Claude's progress. On current code, the channel bridge marks those runs `delivered` / awaiting explicit reply, so delivery telemetry does not hold the agent in `working`. If you still see it, the service or host bridge is running pre-fix code — rebuild the service and restart the affected `claude-aify` / managed Claude backing.

**Auto-recovery (current build).** When a replacement bridge polls `/dispatch/claim` for the same agent, the server gives a recently claimed run a short grace window before declaring it stale. During that window the replacement bridge sees `blockedBy.reason = "active_run_owned_by_previous_bridge"` and should retry. If the previous bridge does not finish, the server then marks the orphaned run failed automatically and existing queued run-control work may be claimed normally. Normal `comms_send` will not create additional queued work while the target is blocked.

For older dispatch-backed messages, the original inbox message may still exist. For current normal `comms_send`, failed live delivery writes no message row, so retry after the agent is startable.

**Manual fix (if no bridge is polling or the current run predates the watchdog).** Cancel the run directly through the HTTP API:

```bash
curl -X PATCH http://localhost:8800/api/v1/dispatch/runs/<run_id> \
  -H "Content-Type: application/json" \
  -d '{"status":"cancelled","error":"Bridge died, orphaned run"}'
```

Afterwards, restart the affected wrapper to bring a live bridge back online.

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

## Closed resident Codex still receives dashboard work

**Symptom.** Dashboard chat to an agent you previously opened in `codex-aify` fails with `connect ECONNREFUSED 127.0.0.1:<port>`. Chat details still say **Resident live CLI**, even though you closed that visible CLI. The same identity may also have a managed backing and a CLI resume command.

**Cause.** The Codex app-server died when the visible CLI closed, but an orphaned aify-comms stdio bridge process was still heartbeating. The backend saw a fresh resident bridge lease and kept routing to the dead resident `appServerUrl` instead of returning the identity to managed backing.

**Fix (current build).** Resident Codex bridges now probe their app-server before heartbeating or claiming work. If the app-server is unreachable twice in a row, the bridge reports `resident-lost`, stops tracking the resident binding, and the backend immediately returns the identity to its saved managed environment when a spawn spec exists. Superseded/lost bridge heartbeats are ignored, so orphaned MCP child processes cannot keep the identity active.

**Manual recovery on older builds.** Restart the relevant `aify-comms` environment bridge and stop the orphaned stdio process. Then use Dashboard **Sessions -> Restart** or **Recover** on the identity. If needed, inspect with `comms_agent_info(agentId="...")`; healthy fallback should show `sessionMode: managed` and `wakeMode: managed-worker`.

## Superseded or stale bridge: claim blocked

**Symptom.** A bridge's dispatch loop logs `blockedBy: {reason: "bridge_superseded"}` or `blockedBy: {reason: "bridge_not_current"}`.

**Cause.** A newer `comms_register` for the same `agentId` on the same machine has replaced this bridge. For Codex/OpenCode/Pi, the server also compares the polling bridge ID against the agent's current `runtimeState.bridgeInstanceId`; this catches old processes whose bridge row is gone but whose dispatch loop is still alive.

**Fix.** Shut the superseded bridge down. This is not an error — it's the server protecting the queue. The fresh bridge is the one that should be claiming runs.

## Environment still shows online after bridge stopped

**Symptom.** The Environments page still shows a Windows/WSL/Linux bridge as `online` after you closed the visible terminal, or the same environment cards keep changing order.

**Cause.** Environment presence is heartbeat-based. A graceful `Ctrl+C` on current bridge code sends one final offline heartbeat. A hard kill, crashed terminal, machine sleep, or older bridge build can only be inferred after missed heartbeats. If `lastSeen` is still updating, some process is still posting as that bridge; the dashboard card shows the bridge process PID from heartbeat metadata when available.

**Fix.**
- Pull latest, reinstall, and restart `aify-comms` so the bridge has graceful offline reporting.
- Check for leftover processes with `ps -ef | rg 'aify-comms|mcp/stdio/server.js'` on WSL/Linux, or `Get-Process node | Select-Object Id,Path,CommandLine` on Windows.
- Starting a newer `aify-comms` for the same environment supersedes older bridge heartbeats when both advertise `bridgeStartedAt`; the server also queues a stop control for the older bridge. A fresh bridge ignores stale stop controls that were requested before that bridge started. Old OS processes still need manual cleanup if they are hung and no longer polling, but they should not own spawn claims.
- Use the dashboard **Kill bridge** action while the bridge is online. Managed agents from that environment become offline/detached; chats and identities remain. Assign them to another online environment from **Sessions -> Identity Directory** or restart the bridge, then recover/restart from **Sessions**.
- Use **Forget** only to hide an obsolete execution target. Forgetting keeps agent identities, chats, saved spawn specs, and session records; it no longer deletes managed identities.
- If a spawn request is marked `running` but the first brief dispatch failed, current server code repairs it to `failed` on the next spawn-request list refresh.

## `aify-comms` exits with `environment ... was superseded`

**Symptom.** A bridge terminal exits shortly after start with a message like `environment windows:host:default was superseded by replacement bridge ..., pid ..., cwd ...`.

**Cause.** Only one bridge is current for a given environment ID such as `windows:HOST:default` or `wsl:HOST:default`. A newer bridge heartbeat for the same environment replaced this process, so the server sent this older bridge a targeted stop control. This is intentional: old bridges must not keep claiming spawns or managed runs after a newer bridge takes ownership.

**Fix.** Keep one `aify-comms` process per environment. If the replacement cwd/pid is not the one you want, stop that replacement process from the Dashboard **Environments -> Kill bridge** action or with the OS process manager, then start `aify-comms` from the directory/root you want to be current. The terminal message names the replacement bridge, PID, and cwd so you can identify it.

If the replacement cwd is an agent workspace and appears immediately after a managed runtime run starts, the bridge is running an old launcher/runtime that lets child MCP servers inherit `AIFY_ENVIRONMENT_BRIDGE=1`. Pull latest, rerun the installer, and restart the OS bridge. Current launchers mark the real bridge with `--environment-bridge`, and managed child processes strip bridge-only env vars before spawning.

## `comms_send(steer=true)` stayed unread or looked queued behind itself

**Symptom.** A steer message lands in the inbox unread, the tool output says it was queued behind the same run ID, or a steer sent during a bridge replacement seems to disappear.

**Cause.** Older server code treated a steered result like a newly queued run and could target a stale active run that was still owned by a superseded bridge. In that state the source inbox message had no completed steer control to mark it read.

**Fix (current build).** Pull latest and restart the target bridge (`codex-aify` / `claude-aify`) so it is running the steer-tracking fix. Current behavior is:
- if there is a live steer-capable active run, the message becomes a steer control and the inbox copy auto-marks read when the control completes
- if the target is busy but not steer-capable, the message queues or merges as next-turn work
- resident `claude-aify` steering is channel-based: the channel bridge emits a `notifications/claude/channel` event into the live Claude session; this is not the same mechanism as Codex `turn/steer`
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

**`terminal control claim failed: transient HTTP error against http://localhost:8800: fetch failed`.** One or two of these during `docker compose up -d --build`, bridge restart, or service restart are expected: the bridge poll hit the API while the TCP connection was being reset, and current code retries on the next poll. If it repeats while `/health` is healthy, the bridge is probably still using an old `localhost`-only URL from another shell/checkout. Restart the host bridge from the latest checkout and prefer `http://192.168.100.10:8800` for Windows/WSL installs; current bridge builds also try fallback URLs for stale `localhost` configs.

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

**Fix.** Pass every field you care about on the re-register call. For Codex resident triggering, that usually means `cwd`, `sessionHandle`, and `appServerUrl` all explicit. If you only need to repair a known saved native ID, prefer Dashboard **Set handle** so you do not accidentally refresh unrelated identity fields.

## Install.sh on Windows / Git Bash

Current installer behavior:

- `--with-hook` is Git Bash aware. It writes native Windows hook paths without MSYS path mangling, so the old `C:\c\Users\...` failure should not require manual `settings.json` or `hooks.json` edits.
- The installer creates Bash wrappers and `.cmd` shims in `%USERPROFILE%\.local\bin`, including `aify-comms.cmd`, `claude-aify.cmd`, `codex-aify.cmd`, `omp-aify.cmd`, and `pi-aify.cmd` when the matching client is installed.
- `codex-aify` does not force auto permissions by default. Use `codex-aify -auto` to request auto mode; current wrappers pass `--dangerously-bypass-approvals-and-sandbox`. They do not use the older `--full-auto` flag.
- `claude-aify` also preserves normal permissions by default. Use `claude-aify -auto` to add `--dangerously-skip-permissions`.
- The `.cmd` shims prepend Git's Unix binary directories when they can find Git, so `sed`/`bash` should be available even when PowerShell only had `C:\Program Files\Git\cmd` on PATH.

If Windows still cannot find `aify-comms.cmd` after install:

```powershell
$env:Path += ";$env:USERPROFILE\.local\bin"
& "$env:USERPROFILE\.local\bin\aify-comms.cmd"
```

If Claude is installed but `claude.cmd` is missing, the wrapper falls back to `claude` when available. Prefer the native Windows Claude Code install when possible, then restart Claude/Codex after reinstalling aify-comms.

If Hermes shows unavailable while `hermes-aify.cmd` exists, check the underlying runtime separately. The wrapper is not the Hermes executable. The environment bridge advertises Hermes only when `hermes` resolves from the bridge process PATH, or when `AIFY_HERMES_COMMAND` / `HERMES_COMMAND` points at the real executable. From PowerShell, run `Get-Command hermes` and `Get-Command hermes-aify.cmd`; if only the wrapper is found, set `AIFY_HERMES_COMMAND` to the absolute Hermes executable path and restart the Windows bridge.

## Dashboard console-mode: lock storm, flicker, statuses, parsing, env-not-found

This cluster was hardened on the `feature/dashboard-console-mode` branch. All fixes are in current builds; symptoms below mean the running container or host bridge predates them — rebuild the service (`docker compose up -d --build`) and/or restart the host bridge.

**`Database temporarily unavailable: database is locked` (503 in dashboard).** Cause: a runaway/flickering console terminal POSTs output many times per second; SQLite's single writer is starved, so heartbeat/dispatch/spawn-claim writers time out. Fix shipped: `service/db.py` sets `PRAGMA busy_timeout`, `synchronous=NORMAL` per connection (WAL is set once at init — it is a persistent file-level setting); `api_v2.py` has a coalescing terminal-output write queue and returns a JSON 503 instead of an HTML 500. If you still see HTML 500s or crashes, the container predates the fix — rebuild.

**Console text scrambled / flickering / "can't see what's happening".** Causes + fixes (all in current builds; symptom means stale container/bridge — rebuild): (1) the dashboard rebuilt the whole console DOM per `terminal_output` frame — fixed by streaming each delta into the live xterm, deduped/ordered by monotonic `outputSeq`, skipping full refresh for non-visible terminals; (2) the live broadcast was per-POST and reordered vs seq under concurrency, so the `seq <= lastSeq` dedupe dropped a frame → ANSI desync → scrambled — fixed by emitting one ordered, coalesced, post-commit broadcast from the write-queue flush (flushes are serialized per terminal); (3) the default xterm DOM renderer janks under heavy output — current builds load the WebGL renderer with DOM fallback. Contract: the service is the sole source of `outputSeq` (bridge sends none); any new output path must route through `TERMINAL_OUTPUT_WRITES` so the sequence stays monotonic and the single ordered broadcast is preserved.

**Environment does not advertise terminal support / WSL Codex Console is unavailable.** First check `/api/v1/environments`: if the runtime is available but the environment says `terminal=false` / `pty=false`, this is not a Codex problem. The bridge cannot load `node-pty`, so Console is disabled for all runtimes on that host. In that same WSL/Linux checkout, run `node -e "import('./mcp/stdio/terminal-runtime.js').then(m=>console.log(m.bridgeTerminalSupported()))"`. If it prints `false` or `node-pty` reports a missing `pty.node`, run `npm --prefix mcp/stdio rebuild node-pty`, then restart the `aify-comms` environment bridge. Current bridge heartbeats leave `terminalRuntimes` empty when PTY support is unavailable so the UI does not imply per-runtime support.

**Broken agent statuses (everything "active", idle consoles shown "working", live Claude shown "active", or live agents shown "offline").** Cause: status was derived in multiple places that disagreed, and a bridge-instance id change marked live sessions offline. Fix: all status flows through one live-state engine (`_compute_live_status_cache`/`_refresh_agent_live_state`); a bridge-id mismatch only forces offline when the session is not live and has no active run; `starting` counts as a live session; a console-owned session whose terminal reached an end state falls back to managed instead of flat offline. Current builds classify `working` from a real active run or a fresh bridge-reported `turnBusy` heartbeat, not from attached console bytes or stale delivered runs. Managed Claude PTY turns stay as running active runs until the reply closes them; stale unowned active runs are reconciled periodically. An attached-but-runless console is reachable/`active`, not `working`. If an idle agent still shows `working` or statuses look wrong, the container or host bridge predates these fixes — rebuild the service and restart the host bridge.

**`Dashboard parsing error` / `Unexpected token <`.** Cause: a non-JSON error body (proxy 502, gateway, unwrapped 5xx) was fed to `response.json()`. Fix: `apiFetch` degrades any non-JSON body to a structured `{ok:false,error}` toast. Persisting means stale dashboard HTML — rebuild.

**Continue/Compact says `environment does not exist`, no dropdowns, Regenerate does nothing.** Cause: free-text environment/runtime inputs and a Regenerate that rebuilt from the stale original session. Fix: Environment and Runtime are dropdowns scoped to live environments (source env kept as a flagged option if offline), workspace has a datalist, and Regenerate rebuilds from the current form selections. Stale dashboard HTML means rebuild.

**Open terminal for a managed/Pi agent: `session does not exist`.** Cause: the dashboard held a client-cached session id that went stale after a rebuild/re-register, so `/sessions/{id}/console/start` 404'd before any bridge code ran. Fix: console start refreshes sessions and retries once against the freshly resolved session; the bridge separately heals dead Pi/Hermes handles (see Pi sections above).

**Pi managed run hangs forever on missing/expired auth.** Cause: the Pi RPC adapter waited silently when Oh My Pi could not authenticate. Fix: Pi RPC classifies auth/provider failures and startup silence and fails fast with an actionable message (run `omp` manually in that environment to re-auth); dead saved Pi session IDs heal to a fresh session and the stale server `sessionHandle` is cleared via `PATCH /agents/{id}/session-handle`. Resident Pi does not auto-heal — it fails with a clear "clear the saved handle / start fresh" message by design.

**Operational note: never rebuild while service files are mid-edit.** The Docker image COPYs the working tree, not git HEAD. Running `docker compose up -d --build` while `service/` has an uncommitted syntax error bakes a broken image and the container crash-loops on `SyntaxError`. Before any rebuild: AST-check (`python -c "import ast; ast.parse(open('service/routers/api_v2.py').read())"`), run `python -m unittest service.tests.test_api_v2_regressions`, and commit. Recover by rebuilding from a known-green commit.

## General escalation

If none of the fixes above resolve the issue:

1. Capture the exact symptom (dispatch run ID, agent ID, error text).
2. Hit `curl http://localhost:8800/api/v1/dispatch/runs/<id>` to get the raw run state.
3. Hit `curl http://localhost:8800/api/v1/agents/<id>` for the agent state.
4. Forward those three pieces to whoever is debugging aify-comms. A fresh repro against current code (post-hard-reset) is worth 10× more than a trace against stale state.
