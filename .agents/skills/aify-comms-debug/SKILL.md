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
- Resident Hermes/Claude/Codex says live but `comms_send` reports stale bridge
- Hermes fails immediately with `'NoneType' object is not iterable`
- Agent shows `online`/`ready` but the Console/worker is gone
- Dispatch: send rejected, run stuck `running`, superseded bridge, orphaned runs
- Environment presence, re-register semantics, install.sh on Windows
- Dashboard console-mode: DB lock storm, console flicker, broken statuses, parsing error, env-not-found, open-terminal (see "Dashboard console-mode" section)
- General escalation

## Resident send rejected: `resident bridge is stale`

**Symptom.** `comms_agent_info` or the dashboard shows a resident Hermes,
Claude, or Codex agent with live-looking metadata (`wakeMode: hermes-live`,
`claude-live`, or `codex-live`), but sending returns `ok: false`,
`recipientStatus: stale`, and a reason like `resident bridge is stale; switch
to managed or restart the resident wrapper`. In Hermes, the open terminal does
not receive the prompt.

**Cause.** The agent record was updated without a current wrapper bridge. The
common bad workaround is a raw Node/curl `POST /api/v1/agents` that passes
`runtimeConfig.gatewayUrl` or a session handle. That writes metadata, but it
does not start the MCP stdio bridge inside the visible `*-aify` wrapper, does
not create `runtimeState.bridgeInstanceId`, and does not heartbeat
`bridge_instances`. Current servers mark this as `stale` and refuse live
delivery instead of forking hidden work.

**Fix.** Restart the exact visible wrapper that should own delivery, then
register from inside that same session with the MCP tool:

```
comms_register(agentId="target", role="tester", runtime="hermes")
comms_agent_info(agentId="target")
```

For Claude/Codex use the matching runtime and handle fields documented in the
main skill. Prefer launching with `--aify-agent <id>` so the wrapper's MCP
child auto-registers with its real bridge id. Do not repair this by posting to
`/api/v1/agents` manually; use dashboard **Switch to managed** if the open
resident terminal should not own delivery.

## Hermes fails immediately with `'NoneType' object is not iterable`

**Symptom.** A freshly restarted `hermes-aify` terminal reaches the Hermes TUI,
but an ordinary prompt such as "read aify-comms skills again and re-register"
fails before tools run:

```
API call failed: TypeError
Provider: openai-codex
Error: 'NoneType' object is not iterable
```

The wrapper may already be healthy: process list shows
`hermes.exe chat --tui --resume <id>` and
`~/.local/state/aify-comms/hermes-aify-active-session-<port>.json` exists.

**Cause.** This is a Hermes/OpenAI SDK Responses streaming edge case, not an
aify registration error. The SDK can raise a local `TypeError` while consuming
the `openai-codex` stream before Hermes gets to call MCP tools. Current
`install.sh --client hermes` patches Hermes `agent/codex_runtime.py` so this
exact SDK failure falls back to Hermes's lower-level `create(stream=True)`
path.

**Fix.** Pull/update aify-comms, run the Hermes client install/redeploy, then
restart the affected `hermes-aify` terminals so the Python process imports the
patched module. If the same error repeats after restart, check the active
dashboard log at:

```
~/.local/state/aify-comms/hermes-aify-dashboard-<port>.log
```

and verify the installed Hermes file contains:

```
Responses stream hit SDK NoneType iterable bug
```

## Agent shows `online` or `ready`, but no live worker exists

**Symptom.** Dashboard or `comms_agent_info` reports a managed Codex/Hermes
agent as `online` or `ready`, but its Console is gone, sends do not visibly
land in a real worker, or the session only has an old `vterm_*`/historical
terminal row.

**Cause.** Older service builds cached `agent_live_state.status` using
heartbeat freshness, not live worker presence. A wrapper PTY could exit while
another heartbeat kept the cache row fresh, so the UI kept showing
`online`/`ready`. A related bug invalidated the corrected writeback
immediately, and ready/registration changes could leave future-dated cache
rows in place.

**Fix.** Update and restart/rebuild the service. Current builds downgrade
managed wrapper-backed agents with no live `terminal_sessions` row to
`available`, persist that downgrade, invalidate live-state cache on
`PATCH /agents/{id}/ready`, and invalidate cache on registration. After
updating, restart the affected environment bridge or wrapper so a real worker
can re-register and recreate the backing terminal.

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
6. Re-register with `appServerUrl="$AIFY_CODEX_APP_SERVER_URL"` from the fresh session; add `sessionHandle="$CODEX_THREAD_ID"` only if that variable is non-empty.

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
  appServerUrl="$AIFY_CODEX_APP_SERVER_URL"
)
```

Add `sessionHandle="$CODEX_THREAD_ID"` only when `CODEX_THREAD_ID` is non-empty in this same session, usually after `codex-aify --resume <id>`.

Verify **before** dispatching:

```
comms_agent_info(agentId="coder")
```

Confirm `wakeMode: codex-live`, the expected `machineId`, and either an explicitly resumed `sessionHandle` or a live `runtimeConfig.appServerUrl`. If any of those are wrong, the session is still bound to stale state.

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

Prefer the dashboard resume command or `claude-aify --aify-agent <agentId> --resume <session-id>` when opening a managed Claude session directly. The wrapper auto-registers a resident candidate, but ownership does not move automatically. Use dashboard **Switch to resident** when the visible CLI should own delivery, and **Switch to managed** when dashboard sends should return to the managed backing. **Pause for CLI** remains an explicit safety control when you want dashboard sends to fail fast while the terminal owns the session.

After opening the native CLI, re-register from that same session with the same `agentId`. That is how the dashboard learns the current native handle. If the agent forgets CLI conversation after returning to dashboard, check whether the session's stored handle changed or was recreated during adopt/restart. Current code should preserve handles across same-runtime adopt/recover/restart; a new handle should only appear after a new spawn or explicit **Recreate**.

**Dashboard handle repair.** If you know the correct native Claude session ID / Codex thread ID / OpenCode or Pi handle, use Dashboard **Chat details -> Runtime Session -> Set handle** or **Sessions -> Actions -> Set handle**. This updates the identity's saved `sessionHandle`, runtime state (`sessionId` or `threadId`), and latest session record without creating a fresh context. Use it only when you know the handle belongs to the intended transcript/thread; a wrong handle binds the identity to the wrong native memory.

**Resident caveat.** Resident Claude sessions are not silently swapped, because their session ID is the visible CLI binding. If a resident session hits this, close the duplicate Claude tab/process, restart with `claude-aify`, and re-register from the live session.

## Managed Oh My Pi / OMP reply is `(no output)`

**Symptom.** A dashboard-managed OMP (`runtime="pi"`) run reaches `agent_end`, but the dashboard stores `(no output)` as the human-visible reply.

**Cause.** Older OMP RPC adapters only captured streaming `text_delta` events. OMP can also provide the final assistant text on completion events such as `message_end`, `turn_end`, or `agent_end`.

**Fix.** Pull current `aify-comms` and restart the affected `aify-comms` / `omp-aify` bridge process (`pi-aify` is an alias). Current builds capture streamed deltas and final completion-event text before deciding that a managed run produced no reply. Verify the bridge checkout with `npm test` from `mcp/stdio/`.

## Managed Oh My Pi / OMP fails: `Session ... is in another project`

**Symptom.** A managed Pi run fails immediately with an OMP error like `Session "..." is in another project (C:\tmp)`.

**Cause.** The saved OMP/Pi `sessionHandle` belongs to a different project directory than the workspace where the bridge is trying to resume it. This can happen after workspace changes, resident-to-managed lease expiry, or an old session record being reused across projects.

**Fix.** Current bridge builds treat this as a stale managed handle: they clear the saved Pi handle and retry once with a fresh managed session. Resident Pi sessions still fail loudly because auto-swapping a visible CLI session would hide native memory changes. Pull current `aify-comms`, restart the affected bridge, and retry. If it still fails, use Dashboard **Sessions -> Actions -> Recreate** for that Pi agent.

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
- The live app-server env var `$AIFY_CODEX_APP_SERVER_URL` was not available inside the session at register time. `$CODEX_THREAD_ID` can be empty on a fresh `codex-aify`; do not fill it from historical rollout files.

**Fix (deterministic):** re-register from that same live session with explicit binding:

```
comms_register(
  agentId="my-agent",
  role="coder",
  runtime="codex",
  cwd="C:/your/exact/project",
  appServerUrl="$AIFY_CODEX_APP_SERVER_URL"
)
comms_agent_info(agentId="my-agent")
```

Add `sessionHandle="$CODEX_THREAD_ID"` only when it is non-empty in that same session, usually after explicit `codex-aify --resume <id>`. If neither app-server URL nor thread ID is available, the session predates the current live-wake flow — restart Codex through `codex-aify` and try again.

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

**"Environment does not advertise terminal support for claude-code" + dispatch sits queued forever.**

**Symptom.** Dashboard chat to a managed claude agent. The dispatch_run row stays `status='queued'`, `execution_mode='channel'`, no controls recorded. Clicking Start Console for the same agent shows the literal error *"Environment <id> does not advertise terminal support for claude-code"* with a `terminalRuntimes` list that omits `claude-code`.

**Cause.** Channel-route delivery (the `insert_messages_via_console=false` default) is NOT "no PTY at all" — it's "wrapper PTY exists, but delivery flows through MCP notifications instead of typing into stdin". `claude-channel.js` runs INSIDE a `claude-aify` wrapper as an MCP child of Claude and is the actor that claims the channel dispatch and emits the `<channel source="aify-comms-channel" ...>` event. If the bridge can't spawn a `claude-aify` wrapper PTY for the agent, the channel dispatch has nothing to claim it → `queued` forever. The bridge advertises `claude-code` in `terminalRuntimes` only when it can resolve a real `claude` executable; the dashboard's per-runtime support check uses that list. So the two symptoms have the same root cause: the bridge can't find `claude`.

**Fix.** From the same user/shell that runs `aify-comms` on the bridge host:
```powershell
Get-Command claude
Get-Command claude-aify.cmd
```
If either is missing, set `AIFY_CLAUDE_COMMAND` to the absolute path of the real `claude` binary BEFORE starting the bridge (system-wide PATH leaks between WSL and Windows make this common). Then restart `aify-comms`. Re-check `/api/v1/environments` — `terminalRuntimes` should now include `claude-code`. Re-dispatch; the queued run should claim within the dispatch-poll cycle (~3s) and the channel notification land in the wrapper.

**Workaround if you can't fix the bridge host right now.** Launch a resident `claude-aify --aify-agent <id>` on any machine where claude resolves; the resident wrapper claims the channel dispatch directly (same machine isn't required for channel route — the wrapper's `claude-channel.js` polls the service over HTTP).

**Broken agent statuses (everything "active", idle consoles shown "working", live Claude shown "active", old stopped terminal shown as current Console, or live agents shown "offline").** Cause: status was derived in multiple places that disagreed, and stale terminal/session bindings survived after bridge or runtime exits. Fix: all status flows through one live-state engine (`_compute_live_status_cache`/`_refresh_agent_live_state`); a bridge-id mismatch only forces offline when the session is not live and has no active run; `starting` counts as a live session; stopped/failed Console terminals are cleared as current session bindings and remain historical only. Current builds classify `working` from a real active run or a fresh bridge-reported `turnBusy` heartbeat, not from attached console bytes or stale delivered runs. Managed Claude PTY turns stay as running active runs until the reply closes them; if their terminal tail clearly asks for operator input or a decision, the agent is `blocked` instead of healthy `working`, but the normal Claude prompt/footer chrome alone is not blocked. Completion-style unthreaded `info` messages can close active terminal runs during send/reconcile, and Claude PTY runs that visibly return to an idle prompt after output are completed-without-reply instead of pinning `working`. Stale unowned active runs are reconciled periodically, and recent overdue reply-contract reminders are sent by the periodic service loop; busy or blocked targets are deferred by the automatic reminder pass and retried after the agent returns idle. An attached-but-runless console is reachable/`active`, not `working`. While a working agent's terminal receives output, its yellow dot briefly pulses orange as a live-output hint, not a separate status. If an idle agent still shows `working` or statuses look wrong, the container or host bridge predates these fixes — rebuild the service and restart the host bridge.

**Dashboard Next normal chat sends queue instead of delivering live.** Cause: stale dashboard HTML/JS had the `Queue if busy` composer checkbox checked by default, so every normal Send posted `queueIfBusy=true`. Fix shipped: the checkbox is opt-in. Unchecked Send mirrors normal `comms_send`; checked Send intentionally waits behind active/queued work. If normal sends still create queued-only rows, rebuild the service and hard-refresh the dashboard.

**Dashboard Next shows an old managed xterm after switching the identity to resident.** Cause: the UI treated any cached terminal id as current, even when the agent's `sessionMode` was `resident` or the terminal row was stopping/stopped/failed. Fix shipped: the Session Console selector only uses managed xterm/cache when the identity is not `resident` and the terminal status is live. Resident agents show their resident attach surface or an explicit unavailable state; switch back to managed before expecting the managed PTY to receive dashboard-typed turns.

**`Dashboard parsing error` / `Unexpected token <`.** Cause: a non-JSON error body (proxy 502, gateway, unwrapped 5xx) was fed to `response.json()`. Fix: `apiFetch` degrades any non-JSON body to a structured `{ok:false,error}` toast. Persisting means stale dashboard HTML — rebuild.

**Continue/Compact says `environment does not exist`, no dropdowns, Regenerate does nothing.** Cause: free-text environment/runtime inputs and a Regenerate that rebuilt from the stale original session. Fix: Environment and Runtime are dropdowns scoped to live environments (source env kept as a flagged option if offline), workspace has a datalist, and Regenerate rebuilds from the current form selections. Stale dashboard HTML means rebuild.

**Open terminal for a managed/Pi agent: `session does not exist`.** Cause: the dashboard held a client-cached session id that went stale after a rebuild/re-register, so `/sessions/{id}/console/start` 404'd before any bridge code ran. Fix: console start refreshes sessions and retries once against the freshly resolved session; the bridge separately heals dead Pi/Hermes handles (see Pi sections above).

**Pi managed run hangs forever on missing/expired auth.** Cause: the Pi RPC adapter waited silently when Oh My Pi could not authenticate. Fix: Pi RPC classifies auth/provider failures and startup silence and fails fast with an actionable message (run `omp` manually in that environment to re-auth); dead saved Pi session IDs heal to a fresh session and the stale server `sessionHandle` is cleared via `PATCH /agents/{id}/session-handle`. Resident Pi does not auto-heal — it fails with a clear "clear the saved handle / start fresh" message by design.

**Operational note: never rebuild while service files are mid-edit.** The Docker image COPYs the working tree, not git HEAD. Running `docker compose up -d --build` while `service/` has an uncommitted syntax error bakes a broken image and the container crash-loops on `SyntaxError`. Before any rebuild: AST-check (`python -c "import ast; ast.parse(open('service/routers/api_v2.py').read())"`), run `python -m unittest service.tests.test_api_v2_regressions`, and commit. Recover by rebuilding from a known-green commit.

## Send to resident Claude rejected as "no live wake path"

**Symptom.** `comms_send(...)` to a resident claude-code agent returns "Message was not sent because one or more recipients cannot start live work now" even though the wrapper is running and `comms_agent_info` shows the agent online with a recent `last_seen`.

**Cause.** The agent's `runtime_config.channelEnabled` is not `true`, so `_row_capabilities` at `api_v2.py` strips `resident-run`, `interrupt`, and `steer` from the capabilities at every read. Preflight then sees `["managed-run","resume"]` and concludes there's no live wake path. This used to require manual DB patches every time a claude-aify session re-registered.

**Fix.** Restart the claude-aify wrapper from a current install — the post-`1585bc9` claude-aify exports `AIFY_CHANNELS_ENABLED=1`, and the post-`c676f2b` `mcp/stdio/server.js` includes `runtime_config.channelEnabled=true` in the `/agents` register call. If you can't restart immediately, patch the live agent:

```bash
docker exec aify-comms-service python -c "
import sqlite3, glob, os, json
db=sorted(glob.glob('/data/*.db'),key=os.path.getmtime)[-1]
c=sqlite3.connect(db)
c.execute(\"UPDATE agents SET runtime_config=? WHERE id=?\", (json.dumps({'channelEnabled': True}), 'YOUR-AGENT-ID'))
c.commit()
"
```

## In-flight run cancelled with "bridge X is not the current agent bridge Y"

**Symptom.** A managed run was actively producing output, then the bridge log shows `Active run owner bridge X is not the current agent bridge Y` and the run is marked cancelled mid-turn. The agent re-registered (often because a nested RPC child or sibling wrapper PTY started up) and the new bridge_instance superseded the active one.

**Cause.** `bridge_instances` supersession used to be scoped to `(agent_id, machine_id)` only. A sibling registration with a different `session_mode` or `session_handle` triggered supersession against the unrelated live bridge. The most common trigger was a bridge-spawned wrapper PTY (e.g. pi-aify hosting `omp --mode rpc`) that auto-detected its TTY and registered as resident, then collided with the real resident bridge.

**Fix.** Already fixed in the post-`4dbb2e2` `_record_bridge_registration` helper (supersession narrowed to the full `(agent_id, machine_id, runtime, session_mode, session_handle)` tuple) + `terminal-env.js` declaring `AIFY_SESSION_MODE=managed` for bridge-spawned PTYs. If you still see it, check the agent's bridge_instances rows — different `session_mode` or `session_handle` between them means the new code is doing the right thing; the legacy supersession was likely cleared on a prior run.

## Dashboard chat spawns a wrapper console on every send

**Symptom.** Sending to a managed pi/codex/opencode/hermes agent from the dashboard spawns a new wrapper PTY each time. Operator sees the console pop up; the wrapper exits after the dispatch; the next send pops another console.

**Cause.** With `managed_terminal_backing_enabled=true` and `managed_pty_eager_spawn=false` (the default), each dispatch lazily creates a wrapper PTY via `_ensure_managed_pty_for_dispatch`. The wrapper (e.g. `omp --mode rpc`) is ephemeral per dispatch.

**Fix (architectural).** Flip `managed_pty_eager_spawn=true`. The wrapper PTY launches proactively at spawn-request running transition; subsequent dispatches reuse it via `_active_terminal_for_agent` (dispatch path) and the slice-3 reuse check in `start_session_console` (manual Start Console path). `PUT /api/v1/settings {"managed_pty_eager_spawn": true}`. Roll back by flipping false.

**Fix (claude-specific).** For managed claude-code, flip `insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)` instead — dispatches route via channel events (claimed by `claude-channel.js`) and no PTY is involved at all. Same protocol resident Claude already uses.

## Each keystroke in the dashboard Console submits as a command

**Symptom.** Operator types into the dashboard Console; every individual letter behaves like a separate Enter — the wrapper sees `c`, then `cd`, then `cd<space>`, etc. as distinct submissions.

**Cause.** The bridge's `terminal-input` control handler used to auto-append `\r` to every input body. Combined with the dashboard sending keystrokes individually, that meant each letter arrived as a submitted line.

**Fix.** Already fixed in commit `c1a1da1` — bridge does raw passthrough now (`TERMINAL_MANAGER.input(terminalId, rawBody)` with no auto-`\r`). The dashboard sends `\r` explicitly when the operator presses Enter. If you still see this, restart the bridge (the change is in `mcp/stdio/server.js` and loads at bridge start).

## Pi-aify wrapper exits mid-turn / "terminal failed before reply"

**Symptom.** Dashboard chat to a managed pi agent starts the wrapper PTY, the run goes to `running`, then `term_*` status flips to `stopped` and the run fails with "Terminal failed before an explicit reply was recorded".

**Cause.** Most common: `omp --mode rpc` child accidentally launched a nested `mcp/stdio/server.js` (because `pi-aify` exports the full aify env) that registered as a sibling bridge for the same agent. The nested bridge's registration superseded the parent bridge, the parent's RPC child died, and the wrapper exited.

**Fix.** Already fixed in commit `59c66ff` — `PiController` (`mcp/stdio/controllers/pi-controller.js`, originally `createPiController` factory before the Plan 3 extraction) spawns the pi RPC child with an explicit per-call env `{AIFY_BRIDGE_DISABLED:"1", AIFY_AGENT_ID:""}` so the nested `mcp/stdio/server.js` exits at startup. The fix is per-spawn, not global, so other wrapper children (claude-aify, codex-aify) keep their full aify env. If you still see this, verify the bridge log shows `AIFY_BRIDGE_DISABLED=1 exit at startup` from the omp RPC child.

## Console opens a second time for an already-running wrapper

**Symptom.** Operator clicks Start Console (or the dashboard auto-attaches) on an agent that already has a live wrapper PTY. A new sibling `terminal_sessions` row is created and a second wrapper PTY spawns instead of attaching to the existing one.

**Cause.** Pre-`fd00c85`, `start_session_console` always created a fresh terminal_session, even when the agent_session already had a live `terminal_id` in `{starting, attached, running, active, idle, recovering}`.

**Fix.** Already fixed in `fd00c85` — the endpoint now checks the existing terminal_id first and returns `{reused:true, terminal:{...}}` without spawning a sibling. Audit event `console_attach_reused_existing` confirms it in the audit log. If you still see it, container needs rebuild to pick up the api_v2.py change.

## Managed claude dispatch cancelled with "capabilities do not include managed-run"

**Symptom.** Send to a managed claude-code agent with `insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)`. The dispatch_event row reads `agent capabilities do not include "managed-run"` and the run is cancelled before delivery. The agent's capabilities show `["resume", "interrupt", "spawn"]` (no `managed-run`) and `runtime_config.channelEnabled=true`.

**Cause.** Default capabilities for managed claude omit `managed-run` by design (claude has no headless managed-run API). Pre-`a4498a6`, `_agent_execution_mode` rejected dispatches on the missing cap before the channel branch could fire.

**Fix.** Already fixed in `a4498a6` — the cap-check is skipped when runtime is `_CHANNEL_MANAGED_RUNTIMES` AND `runtime_config.channelEnabled=true`. Container needs rebuild to pick up the api_v2.py change. If you're on a build that pre-dates this fix and can't rebuild immediately, you can patch the live agent: `UPDATE agents SET capabilities=json_insert(capabilities,'$[#]','managed-run') WHERE id='YOUR-AGENT-ID'` — the dispatch will then enter the channel branch correctly.

## Spawn-time initial message to managed claude sits queued forever

**Symptom.** `comms_spawn(runtime="claude-code")` registers the agent + spawns wrapper PTY successfully, but the spawn's `initialMessage` dispatch_run has `status='queued'` and `execution_mode='managed'` — never claimed by `claude-channel.js`. With `insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)`, the run should have `execution_mode='channel'`.

**Cause.** Pre-`a4498a6`, `update_spawn_request`'s running-transition handler called `_create_dispatch_runs(...)` to create the initial-message run, but did NOT call `_apply_channel_only_to_claude_runs(...)` afterward. The run stayed `execution_mode='managed'` even with the channel-only setting on. The same gap existed in the auto-mirrored handoff path at line 4912.

**Fix.** Already fixed in `a4498a6` — both call sites now apply channel-only post-create. For runs created BEFORE the fix that are stuck queued, you can manually flip: `UPDATE dispatch_runs SET execution_mode='channel' WHERE id='YOUR-RUN-ID' AND status='queued'` — `claude-channel.js` will pick it up on the next poll cycle (~5s).

## Channel-routed claude dispatches stay queued forever (resident or managed)

**Symptom.** Send to a claude-code agent (resident OR managed). `dispatch_runs.status` stays `queued`, `execution_mode='channel'`. No `claimed` event, no delivery. The bridge appears alive (heartbeats), `aify-comms` MCP tools work for OTHER tasks, but channel dispatches sit forever.

**Cause.** Known Claude Code bug ([anthropics/claude-code#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)): when Claude loads many stdio MCP servers simultaneously, the slower ones get stuck in `still connecting` state. With a typical operator `~/.claude.json` (10+ servers including browsermcp, claude.ai connectors, etc.), `aify-comms-channel` loses the init race. Claude never registers the `notifications/claude/channel` listener, so the bridge's `mcp.notification()` calls are silently dropped — even though the MCP server itself is running and the channel-bridge reports `delivered`.

Verify by running `claude -p "list MCP servers"` from a plain shell — if `aify-comms-channel` shows under "still connecting" (instead of "connected"), the race is biting.

**Fix.** Already applied in `06289e0` — `claude-aify` always uses `--strict-mcp-config` + a temp minimal MCP config with only `aify-comms` + `aify-comms-channel`. Other operator MCP servers do NOT load inside the wrapper but still work in plain `claude` sessions. Run `install.sh --client claude` to regenerate the wrapper, then restart `claude-aify`.

If you're on Windows Git Bash and the regenerated wrapper still fails (`2 MCP servers failed`, `aify-comms is currently disconnected`), the wrapper's MCP config paths may be MSYS-style. The wrapper uses `cygpath -m "$SCRIPT_DIR"` to convert to Windows-native paths. If cygpath isn't available in your Git Bash, install it (`pacman -S cygwin-tools` or update Git for Windows).

## Channel/resident dispatches silently fail on Windows — bridge can't reach localhost:8800

**Symptom.** Send to a resident or channel-routed claude-code agent. The dispatch_runs row stays `status='queued'` forever, `claim_bridge_id=''`. The channel-bridge child process is alive (verify with `Get-CimInstance Win32_Process | Where-Object CommandLine -match claude-channel.js`), `agent_turn_state.turn_busy=0`, the wrapper has the right flags (`--dangerously-load-development-channels`), and `~/.claude/projects/*/[session].jsonl | grep -c notifications/claude/channel` returns **0**. Nothing about the bridge looks wrong — but no claim ever happens and no `<channel source="aify-comms-channel">` event reaches the operator's session.

Smoke test that confirms the bug:
```bash
curl --max-time 5 http://localhost:8800/health                # times out
curl --max-time 5 http://127.0.0.1:8800/health                # returns immediately
node -e 'fetch("http://localhost:8800/health",{signal:AbortSignal.timeout(5000)}).then(r=>r.text()).then(console.log).catch(e=>console.log("ERR",e.message))'  # ERR aborted
node -e 'fetch("http://127.0.0.1:8800/health",{signal:AbortSignal.timeout(5000)}).then(r=>r.text()).then(console.log).catch(e=>console.log("ERR",e.message))'  # OK
```

**Cause.** Docker Desktop on Windows reports IPv6 port bindings (`docker port aify-comms-service` shows both `0.0.0.0:8800` and `[::]:8800`), but its IPv6 port forwarding to the container is unreliable — connections to `::1` hang silently. On Windows, `localhost` resolves to IPv6 `::1` first, so node's `fetch()` and curl both hit the broken IPv6 path. The channel bridge's `/dispatch/claim` poll aborts at `HTTP_TIMEOUT_MS` (20s) every cycle, no run is ever claimed, no `notifications/claude/channel` is ever emitted, and the symptom looks identical to a missing channel-server registration or a queue-routing bug. The same applies to managed runs going through `server.js` HTTP, and to anything else the wrappers do over `http://localhost:8800`.

**Fix (shipped, commit `71f2576`).** `claude-channel.js` and `mcp/stdio/server.js` now coerce `http://localhost` URLs to `http://127.0.0.1` before fetching. Defensive — works regardless of what env vars or wrappers pass. No-op on Linux/macOS (same loopback address). The wrapper template still uses `127.0.0.1` directly in the generated MCP config so operators on Linux don't notice anything; the bridge-level fix protects against custom `AIFY_SERVER_URL=http://localhost:...` configs and stale wrappers that predate the install regeneration. Run `install.sh --client claude` and restart `claude-aify` after pulling — verify with the smoke test above.

**Manual quick-fix while you wait to update.** Set `AIFY_SERVER_URL` / `CLAUDE_MCP_SERVER_URL` to `http://127.0.0.1:8800` in the wrapper's MCP env block (or `~/.claude/settings.local.json`) before launching the wrapper.

## Managed pi: synthesized terminal stream vs. real PTY

**Symptom.** Operator opens the Console pane for a managed pi agent and sees a `command='aify://virtual-rpc/pi'` row with output that looks like `[pi rpc ready]`, `[turn started]`, `[tool] bash ...`, the assistant's streamed text, and `[turn ended]` rather than a real shell prompt. There is no input cursor in the traditional shell sense, but typing into the console DOES work — the operator's line is echoed back as `> ...` and the agent runs a new turn.

**Cause (not a bug).** Phase 2 swapped per-dispatch `omp --mode rpc` spawn for a persistent child per agent, and surfaces the child's `AgentSessionEvent` stream as a synthesized terminal row. The `runtime_state.virtualTerminal=true` flag on the agent marks this as a bridge-driven feed, not a PTY — there is no shell. Operator input typed in the dashboard buffers until `\r`/`\n` and dispatches a new RPC turn through the persistent child. See DECISIONS.md "Managed pi uses persistent RPC + synthesized terminal stream" and `docs/plans/pi-persistent-rpc.md`.

**What to expect.**
- One `terminal_sessions` row per agent for the lifetime of the persistent child (default idle timeout 24h via `AIFY_PI_IDLE_TIMEOUT_MS`).
- No resize semantics (the synthesized stream has no PTY dimensions).
- Stopping from the dashboard tears down the persistent RPC child + the virtual terminal row. Next dispatch respawns.
- Real PTY managed pi (the old `terminal_sessions` rows with `command='pi-aify --aify-agent ...'`) no longer exists for managed dispatches under the persistent RPC path. If you see one, it's a stale leftover from a pre-Phase-2 deployment — clear it the same way as below.

**Cleanup of legacy real-PTY rows (only relevant if upgrading from a pre-persistent-RPC build).**
```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
db = sorted(glob.glob('/data/*.db'))[-1]
c = sqlite3.connect(db)
c.execute(\"UPDATE terminal_sessions SET status='stopped', error='superseded_by_virtual_rpc' WHERE agent_id='YOUR-AGENT-ID' AND command != 'aify://virtual-rpc/pi' AND status IN ('attached','running','starting')\")
c.execute(\"UPDATE agent_sessions SET terminal_id='', terminal_status='' WHERE agent_id='YOUR-AGENT-ID'\")
c.commit()
"
```

## `omp-aify` / `pi-aify` refuses to start: "currently driven by aify-comms"

**Symptom.** Operator runs `omp-aify --aify-agent X` (or `pi-aify ...`) and the wrapper prints:

> Agent 'X' is currently driven by aify-comms (visible in dashboard terminal). Stop it from the dashboard or use `omp-aify --standalone --aify-agent X` to launch a parallel session on a different session-id.

…and exits 1.

**Cause (not a bug).** Phase 4 watchdog. The bridge's persistent `omp --mode rpc` child currently holds this agent's session-id; an external omp on the same handle would corrupt the session file (OMP's RPC channel has no multiplexing, upstream [#436](https://github.com/can1357/oh-my-pi/issues/436)). The wrapper queries `GET /agents/{id}/pi-session-state` before exec'ing omp.

**Choices.**
- Stop the bridge session from the dashboard (Console pane → Stop). Then re-run the wrapper.
- Pass `--standalone` AND a different `--resume <other-handle>`. The bridge keeps driving its session-id; you get a parallel omp on a separate handle. They will not contend.
- If `AIFY_COMMS_URL` is missing, the curl times out, or the runtime isn't pi, the check fails open and the wrapper proceeds normally — so this only fires when the bridge actually claims ownership.

**Quick check from the host:**
```bash
curl -sS http://localhost:8800/api/v1/agents/YOUR-AGENT-ID/pi-session-state | python -m json.tool
# {"ok": true, "bridgeOwned": true|false, "virtualTerminalId": "vterm_..."}
```

## Codex native fallback persistent app-server session

Managed Codex defaults to a wrapper-backed `codex-aify` PTY. This section applies only when wrapper-backed delivery is disabled/unavailable or when the Console command is `aify://virtual-rpc/codex`: the native fallback keeps a long-lived `codex app-server` child per agent (`mcp/stdio/codex-session.js`). Symptoms specific to this path:

### Dispatch sits at `[codex] working...` forever

The codex app-server is alive but the turn never completes. Cause: codex stalled mid-turn (provider hang, sandbox-policy block, etc.).

**Fix.** From the dashboard, Stop the agent's persistent worker (Console Stop) and re-dispatch — the bridge will spawn a fresh `codex app-server`, re-initialize, and `thread/resume` the same threadId to recover the conversation. The synthesized terminal row survives.

### `codex handshake timeout (60000ms)`

The bridge spawned `codex app-server` but never got an `initialize` response within 60s.

- Confirm codex runs by hand from the same host: `codex app-server` should not error.
- If using a custom binary path: set `AIFY_CODEX_COMMAND="/abs/path/to/codex app-server"` and restart the wrapper (claude-aify/codex-aify) so it reloads env.
- Common cause: the codex CLI is itself in a broken auth state — `codex doctor` or re-login may be needed.

### `Codex thread/resume failed for saved thread <id>`

The bridge tried to resume a previously-saved threadId but codex says no rollout exists. CodexSession does the same heal logic as the legacy controller: tries to import the rollout from other CODEX_HOME dirs, then (only if `resumePolicy='fresh_context'`) starts a fresh thread. The conservative default is to fail loudly — see DECISIONS.md.

**Fix.** Either flip the agent to `resumePolicy=fresh_context` (Dashboard → Sessions → Recreate) or restore the rollout file in the active CODEX_HOME and retry.

## Hermes native fallback ACP persistent session

Managed Hermes defaults to a wrapper-backed `hermes-aify` PTY that delivers through the visible-session gateway bind path. This section applies only when wrapper-backed delivery is disabled/unavailable or when the Console command is `aify://virtual-rpc/hermes`: the native fallback keeps a long-lived `hermes acp --accept-hooks` child per agent (`mcp/stdio/hermes-session.js`). Some symptoms specific to this path:

### Dispatch sits at `[hermes] thinking...` forever

`hermes acp` is alive but the prompt never resolves. Cause: hermes deadlocked inside the agent loop (provider stall, hook race, etc.), or a `session/request_permission` callback is being mishandled.

**Fix.** From the dashboard, Stop the agent's persistent worker (terminal Stop) and re-dispatch — the bridge will spawn a fresh `hermes acp`, run `initialize` + `session/new` again, and continue. The synthesized terminal row survives across the restart.

### `hermes acp handshake timeout (45000ms)`

The bridge spawned `hermes acp` but never got an `initialize` response within 45s.

- Confirm `hermes acp --check` exits 0 from the host (`pwsh: hermes acp --check`). If it prompts about shell-hook approval, your install is missing the `--accept-hooks` flag — the bridge passes it by default, but a custom `AIFY_HERMES_ACP_COMMAND` may have dropped it. Re-set: `AIFY_HERMES_ACP_COMMAND="hermes acp --accept-hooks"`.
- Check stderr tail in the dispatch_event for the run — the handshake-timeout error includes the last 200 chars of hermes's stderr. Common culprits: provider credentials missing, `~/.hermes/.env` not loaded, hooks-approval prompt blocking startup.

### Bridge declines hermes's terminal/* callbacks

If hermes asks the bridge to spawn a child process (`terminal/create`, etc.), the bridge replies with method-not-found by design (no in-bridge sandbox). Hermes should fall back to its own sandbox. If hermes errors out instead, configure hermes itself with a sandbox provider — the bridge will not host tool subprocesses.

## Hermes-aify wrapper fell through to plain hermes (Plan 5 Section A)

**Symptom.** A `hermes-aify` resident agent reports `wakeMode='hermes-missing-handle'`. From the operator's hermes shell, `echo $AIFY_HERMES_GATEWAY_URL` prints empty. Dispatches to the agent never wake it; the wrapper appears to have launched ok.

**Detection.**

```bash
ls -la ~/.local/state/aify-comms/hermes-aify-dashboard-*.log
# Look for files ~240 bytes, recently modified
cat ~/.local/state/aify-comms/hermes-aify-dashboard-*.log | head -5
# Expect: "✗ --skip-build was passed but no web dist found at: .../hermes_cli/web_dist"
```

If the log shows that line, the wrapper's `hermes dashboard --tui --skip-build` probe died immediately, `wait_for_http` timed out, and the wrapper silently `exec`'d plain `hermes` without exporting the gateway URL. The MCP child then registered with no gateway env.

**Fix.** Re-run `./install.sh --client hermes` — current installs prebuild `hermes_cli/web_dist` once (commit `5057383`). Then restart the wrapper. Current wrappers also print a visible WARNING when this fallback path triggers (commit `7a544e8`), so subsequent occurrences won't be silent.

## Queued managed run never claimed (Plan 5 Section B)

**Symptom.** A managed codex / hermes / pi agent (e.g. graph-senior-dev, hermes-test, pi-aify managed) is registered, the bridge is alive and heartbeating, the wrapper PTY is running — but `comms_send` messages stay queued indefinitely. No claim event, no controls recorded.

**Detection.** Query the service DB for the agent's dispatch runs:

```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
db = sorted(glob.glob('/data/*.db'))[-1]
c = sqlite3.connect(db)
for r in c.execute(\"SELECT id, runtime, status, execution_mode, claim_bridge_id, created_at FROM dispatch_runs WHERE agent_id='YOUR-AGENT-ID' ORDER BY created_at DESC LIMIT 5\"):
    print(r)
"
```

If you see rows with `status='queued'`, `execution_mode='channel'`, and `claim_bridge_id=''` more than a few seconds old, this is the Plan 5 Section B gap: the server routed the run to channel-mode but the bridge whitelist (`_CHANNEL_CLAIM_RUNTIMES` in `api_v2.py`) didn't include that runtime, so its bridge can't claim.

**Fix.**
1. Confirm Plan 5 is deployed — grep the container for `_CHANNEL_CLAIM_RUNTIMES`:
   ```bash
   docker exec aify-comms-service grep -n "_CHANNEL_CLAIM_RUNTIMES" /app/service/routers/api_v2.py
   ```
   Expect a line defining the set as `_CHANNEL_MANAGED_RUNTIMES | {"codex", "hermes", "pi"}`. Missing → rebuild the service.
2. Check that the affected runtime is in `managed_via_wrapper`:
   ```bash
   curl -s http://localhost:8800/api/v1/settings | python -m json.tool | grep -A3 managed_via_wrapper
   ```
   Should list `"codex"` and `"hermes"` (current default). Pi is intentionally excluded from wrapper mode and uses managed RPC.
3. If wrappers are older than commits `3bcbac2` / `0beab57`, run `./redeploy.sh` to refresh installed `*-aify` wrappers and restart any host bridges. Re-dispatch — the queued run should claim within one poll cycle (~3s).

## Agent shows online without a console (Plan 5 Section C)

**Symptom.** Dashboard shows a managed agent as `online`. Clicking through to the agent never loads a Console widget; no live terminal_session attaches. The wrapper PTY exited some time ago, but the agent never downgraded.

**Detection.** Compare cached status against actual worker presence:

```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
db = sorted(glob.glob('/data/*.db'))[-1]
c = sqlite3.connect(db)
aid = 'YOUR-AGENT-ID'
live = c.execute('SELECT status, updated_at, refresh_after FROM agent_live_state WHERE agent_id=?', (aid,)).fetchone()
terms = c.execute(\"SELECT id, status FROM terminal_sessions WHERE agent_id=? AND status NOT IN ('stopped','failed','exited')\", (aid,)).fetchall()
print('live:', live)
print('active terms:', terms)
"
```

If `live[0]=='online'` AND `active terms` is empty, that's the Plan 5 Section C bug — `agent_live_state` cached `online` and `refresh_after` was keyed off heartbeat freshness rather than worker presence, so a sibling/operator heartbeat kept the lie alive.

**Fix.** Rebuild the container so `_enforce_live_worker_gate` (added at `api_v2.py:352` in commits `b58142e` + `f38f57d`) is loaded. On the next `GET /api/v1/agents` or `/agents/{id}` read, the gate validates the live worker and downgrades to `available`; a cache writeback ensures subsequent reads stay consistent. No manual DB patch is needed once Plan 5 is in.

## Hermes dispatch completes but open console does not move

**Symptom.** `hermes` resident or wrapper-backed runs show `prompt.submit` and may even complete in aify-comms, but the open `hermes-aify` terminal does not show the incoming message or reply. Older events may mention `session.resume`, `session.create`, `session id corrected`, or short `mem-*` gateway ids.

**Cause.** The bridge was forking a fresh in-memory Hermes sid over a second WebSocket. That can complete backend accounting, but it is not the operator-visible TUI session. The harness-console contract requires the active visible sid.

**Fix.** Current installs patch Hermes `tui_gateway/server.py` with `aify.session.bind_transport` and Hermes `hermes_cli/main.py` so the TUI preserves the wrapper-provided active-session file. Re-run `./install.sh --client hermes`, restart every open `hermes-aify`, then re-register from inside the visible terminal. A healthy run event says `visible session bound: <key> -> <sid>` before `prompt.submit`; if the saved handle was stale but the wrapper gateway has exactly one active visible session, you may first see `visible session key corrected: <old> -> <new>`. If the bind method is missing, or no active visible session can be selected, current bridges fail visibly and refuse hidden `session.resume` / `session.create` fallback.

If two Hermes resident agents run in the same cwd, they must still register
with different `runtimeConfig.gatewayUrl` values. Current bridges prefer the
current MCP process env over cwd runtime markers for Hermes. If both agents
show the same gateway URL, the bridge is old or one registration happened from
the wrong terminal; update/restart both wrappers and re-register each from its
own visible TUI.

## Stale session handle causing prompt.submit failures (Plan 6 A)

**Symptom.** Dispatch fails at delivery time with `prompt.submit failed: session not found` (hermes) or analogous "session not found" / GC'd-rollout warnings on codex / pi / claude. Bridges look alive, heartbeating, and the dispatch row reports `delivered` — but the runtime rejects the handle. `agents.session_handle` matches a session that no longer exists in the runtime.

**Detection.** Compare the stored handle against the runtime's actual current session id.

1. Get the stored handle from the server:
   ```bash
   curl -s http://localhost:8800/api/v1/agents/YOUR-AGENT-ID | python -m json.tool | grep -E '"sessionHandle"|"runtime"'
   ```

2. Get the runtime's actual current session id, per runtime:

   - **hermes**: do **not** use gateway `session.most_recent` as the current visible session. It can be historical DB state. Prefer the wrapper active-session file (`$AIFY_HERMES_ACTIVE_SESSION_FILE`) or `comms_agent_info`; only an explicit `hermes-aify --resume <id>` should seed `HERMES_SESSION_ID` before launch. If `AIFY_HERMES_ACTIVE_SESSION_FILE` is set in the operator's shell:
     ```bash
     cat "$AIFY_HERMES_ACTIVE_SESSION_FILE"
     ```
   - **codex**: for a fresh `codex-aify`, do **not** scan `~/.codex/sessions`; the newest rollout may be an unrelated historical thread. Use `$CODEX_THREAD_ID` only if this exact session exported it, usually after `codex-aify --resume <id>`.
   - **pi**: `~/.omp/agent/sessions/<project-key>/...`, OR ask the bridge directly:
     ```bash
     curl -s http://localhost:8800/api/v1/agents/YOUR-AGENT-ID/pi-session-state | python -m json.tool
     ```
   - **claude**: list transcripts the operator's session might have created:
     ```bash
     ls -t ~/.claude/projects/*/*.jsonl | head -5
     ```
     If the stored `sessionHandle` (an `<id>.jsonl` basename) doesn't appear, it's stale.

3. If the runtime's truthful id differs from `agents.session_handle`, this is the Plan 6 A gap.

**Fix.**
- With Plan 6 A1 in place (`mcp/stdio/session-handle-heartbeat.js:25-30`, commit `3167423`) the bridge auto-corrects within one heartbeat tick (~60s). Wait 60s and re-check the stored handle — it should now match the runtime.
- With Plan 6 A2 in place (`mcp/stdio/server.js` `computeInitialSessionHandle`, commit `edbc374`) the FIRST register call also uses discover over env — fresh agents are correct on first dispatch.
- If you're running pre-Plan-6 bridge code (verify with `git log mcp/stdio/session-handle-heartbeat.js | head -3` showing the Plan 6 A1 commit), pull + restart the wrapper.
- One-shot manual recovery without waiting for the heartbeat: re-register the agent with an empty `sessionHandle` and let the bridge's discover fill it:
  ```
  comms_register(agentId="YOUR-AGENT-ID", role="...", runtime="...", cwd="...", sessionHandle="")
  ```
  OR unset the runtime's session env var in your shell before relaunching the wrapper:
  ```bash
  unset HERMES_SESSION_ID    # hermes
  unset CODEX_THREAD_ID      # codex
  unset PI_SESSION_ID        # pi
  unset CLAUDE_SESSION_ID    # claude
  hermes-aify --aify-agent YOUR-AGENT-ID   # or codex-aify / pi-aify / claude-aify
  ```
  Current `codex-aify` and `hermes-aify` deliberately do not rediscover from historical runtime state on fresh launch; explicit `--resume <id>` is the only wrapper-side handle export. For Hermes, a fresh visible session becomes wakeable after the TUI writes the active-session file and the live bridge registers/heartbeats it.

## Manual mode-switch unavailable in dashboard

**Symptom.** Operator wants to flip an agent from `resident` to `managed` (or back) without killing the wrapper, but no switch button is visible in the dashboard's Details panel or Sessions rail. Chip-style "Switch to managed" / "Switch to resident" controls described in Plan 6 C are documented but don't render on screen.

**Current behavior.** The switch is no longer gated by `manual_session_mode`. It should be visible for agents whose `sessionMode` is `resident` or `managed` in Chat details and Sessions actions.

**Detection.** Confirm the agent exposes a switchable mode:

```bash
curl -s http://localhost:8800/api/v1/agents/AGENT_ID | python -m json.tool | grep sessionMode
```

If the value is not `resident` or `managed`, no switch is rendered. If it is switchable and still missing, the dashboard assets are stale or the browser has cached old JS.

**Fix.** Rebuild/redeploy the service (`docker compose up -d --build`) and hard-refresh the browser. Clicking the switch calls `PATCH /api/v1/agents/{id}/session-mode {mode}`; the response updates `sessionMode`, launch mode, capabilities, runtime state, and any side-effect terminal state.

## Known follow-up: wrapper-backed channel claim can race

**Symptom.** A wrapper-backed Codex/Hermes dispatch is routed through `executionModes=["channel","resident"]`, but the main bridge claims the run before the bridge-spawned wrapper child is fully registered. The dashboard may show the run as claimed/running while the visible wrapper terminal never receives the message.

**Cause.** The same bridge process owns environment polling and wrapper-child startup. The correct long-term fix is a stricter claim boundary so wrapper-backed channel runs are claimable only by the wrapper child that has the runtime-local gateway/app-server context, or by an explicit fallback path after the wrapper child times out.

**Current mitigation.** Current builds make Dashboard Next sends normal live sends by default and expose resident/managed mode manually, but this race is still a deeper lifecycle item. If you hit it, capture the run id, target agent id, `claim_bridge_id`, and bridge logs before restarting; the stale queued/running cleanup should unblock later sends, but it does not prove the visible terminal saw the turn.

## General escalation

If none of the fixes above resolve the issue:

1. Capture the exact symptom (dispatch run ID, agent ID, error text).
2. Hit `curl http://localhost:8800/api/v1/dispatch/runs/<id>` to get the raw run state.
3. Hit `curl http://localhost:8800/api/v1/agents/<id>` for the agent state.
4. Forward those three pieces to whoever is debugging aify-comms. A fresh repro against current code (post-hard-reset) is worth 10× more than a trace against stale state.
