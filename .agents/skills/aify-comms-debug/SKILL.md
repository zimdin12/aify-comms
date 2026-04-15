---
name: aify-comms-debug
description: Known aify-comms issues and how to fix them. Check here when a dispatch fails, a wake mode looks wrong, a run is stuck, a bridge seems stale, or Claude/Codex reports a path/channel error. Complements the main aify-comms skill.
trigger: tool_available("comms_register") OR tool_available("comms_send") OR tool_available("comms_inbox")
---

# aify-comms: Troubleshooting

Use this skill whenever something in aify-comms is not behaving the way the main skill says it should. Each entry lists the **symptom**, the **cause**, and the **fix**.

Before digging in, always call `comms_agent_info(agentId="target")` on the agent in question and read `wakeMode`, `sessionMode`, `machineId`, `sessionHandle`, and `dispatchState`. Most of these fixes are just "something in that record is stale or wrong".

## Codex: `Invalid request: AbsolutePathBuf deserialized without a base path`

**Symptom.** Dispatches to a Codex agent fail with this Rust error. Dashboard may also show `Codex WebSocket app-server connection closed (1006)`. On a current bridge you will also see a clearer wrapping error that names the specific thread ID and tells you which rollout file to move aside — if you only see the raw Rust line, your bridge is still running pre-fix code and needs to be relaunched.

**Causes (in order of likelihood, once the bridge is current):**
1. **Corrupt on-disk Codex rollout.** The `thread/resume` call loads the thread's stored state from `~/.codex/sessions/...`. If that file has a path field Codex's deserializer cannot load — typically a backslash Windows cwd captured before the wrapper's cwd normalization landed — `thread/resume` crashes before the bridge can send anything else. The key tell is that the failed run has an empty `externalThreadId`: the bridge never got past `thread/resume`. Nothing aify-comms does at dispatch time can fix this; the rollout file has to go.
2. **Stale pre-update `codex-aify` bridge still running in memory.** The code on disk has the fix, but the running Node process loaded the pre-fix module. Node does not hot-reload; the bridge must be killed and relaunched.
3. **A manual `comms_register` passed a raw Windows backslash `cwd`** like `C:\Users\you\project`. The current build normalizes this at registration time, at marker-lookup time, and at dispatch time — but only if the bridge was started from current code.

**Auto-recovery (managed workers only).** Current bridge code catches this error during `thread/resume` for managed workers and falls back to starting a fresh Codex thread automatically. Resident sessions get a clearer actionable error instead, because silently creating a new thread would break the visible-TUI wake guarantee.

**Fix (resident Codex sessions).**
1. Kill every `codex-aify` and `codex app-server` process on the machine (the Hard Reset commands below).
2. Move the poisoned rollout aside so Codex cannot re-offer it:
   ```powershell
   Get-ChildItem "$HOME\.codex\sessions" -Recurse -Filter "*<bad-thread-uuid>*" |
     ForEach-Object { Rename-Item $_.FullName "$($_.FullName).poisoned" }
   ```
3. Delete the stale runtime markers.
4. `cd` into the target project directory.
5. Launch a fresh `codex-aify` from there.
6. Re-register with the **new** `$CODEX_THREAD_ID` from the fresh session — verify it is a different UUID than the one that failed.

The full Hard Reset commands are right below.

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

## Machine ID shows `win32:unknown-host`

**Symptom.** Agent's `machineId` is `win32:unknown-host` instead of the real hostname.

**Cause.** `COMPUTERNAME` / `HOSTNAME` env vars were not propagated into the node process that hosts the bridge. The current build falls back to `os.hostname()` before `unknown-host`.

**Fix.** Restart the bridge (restart your `claude-aify` / `codex-aify` session) and re-register. Cosmetic only — it does not block routing, because dispatches are routed by `agentId` rather than `machineId`.

## Dispatch rejected with `reason: "buffer_full"`

**Symptom.** `comms_send` / `comms_dispatch` returns a `notStarted` entry with `reason: "buffer_full"` and `bufferedCount: 10`.

**Cause.** You (the same `fromAgent`) already have 10 buffered dispatches queued behind an active run for that recipient. The buffer is capped to prevent unbounded pile-up.

**Fix.** Pick one of:
- Wait for the in-flight run to drain. The 10 buffered items all run in order after it.
- `comms_run_interrupt(runId=<current active run>)` if the current work should stop.
- `comms_agent_info(agentId=<target>)` to inspect why it's stuck; address that instead of retrying.
- If you legitimately need a new independent run, use a different `fromAgent` — the cap is per-sender.

## Run stuck `running`, `comms_run_interrupt` has no effect

**Symptom.** A dispatch is marked `running` but nothing is happening. `comms_run_interrupt` returns ok but the run never moves.

**Cause.** The bridge that owned the run has died (crash, machine sleep, network drop). `comms_run_interrupt` works by enqueueing a control the owning bridge polls for — if the bridge is gone, no one claims the control.

**Fix.** Cancel the run directly through the HTTP API:

```bash
curl -X PATCH http://localhost:8800/api/v1/dispatch/runs/<run_id> \
  -H "Content-Type: application/json" \
  -d '{"status":"cancelled","error":"Bridge died, orphaned run"}'
```

Afterwards, investigate why the bridge died and restart `claude-aify` / `codex-aify` as needed.

## `message-only` wake mode when you expected `codex-live`

**Symptom.** Right after `comms_register` you see `wakeMode: message-only` even though you're inside `codex-aify`.

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

If only the thread ID is available, pass `sessionHandle` without `appServerUrl`. If neither is available, the session predates the current resident-triggering flow — restart Codex through `codex-aify` and try again.

## Superseded bridge: claim blocked

**Symptom.** A bridge's dispatch loop logs `blockedBy: {reason: "bridge_superseded"}`.

**Cause.** A newer `comms_register` for the same `agentId` on the same machine has replaced this bridge. The server rejects claims from superseded bridges so they can't steal work from the fresh one.

**Fix.** Shut the superseded bridge down. This is not an error — it's the server protecting the queue. The fresh bridge is the one that should be claiming runs.

## Bridge "lost" the agent / has to be re-registered manually

**Symptom.** An agent that used to work stops claiming dispatches. Messages still arrive in its inbox but nothing launches. Manually re-registering the agent makes it work again.

**Cause.** The server forgot about the agent (cleared via `comms_clear`, deleted by an operator, or the DB was rotated) but the bridge's local cache still thinks it's registered and keeps polling with a dead `agentId`. Alternatively, several consecutive dispatch-claim HTTP calls failed — server restart, network blip, sleep/wake — and the bridge hasn't recovered its state.

**Auto-recovery (current build).** The bridge now:
- Retries transient HTTP errors up to 3 times with exponential backoff (250ms / 500ms / 1000ms) before giving up on any single call.
- Watches for `404` responses on `/agents/{id}` and `/dispatch/claim`. A 404 means the agent is unknown to the server, so the bridge automatically re-registers from its cached agent data.
- Counts consecutive claim failures per agent. After 4 in a row, the bridge tries an auto-re-register from cache as a last-resort self-heal.

Look for these lines on stderr:

```
[aify] agent "foo" missing from server; auto-re-registering
[aify] auto-re-registered "foo" from cached state
[aify] 4 consecutive dispatch/claim failures for "foo"; attempting auto-re-register
```

**Fix when auto-recovery fails.** If you see the auto-re-register log followed by `auto-re-register failed for "foo"`, the server itself is unreachable or rejecting the payload. Check:
1. `curl http://localhost:8800/health` — is the server even up?
2. The bridge's cached state may be missing a required field (role, runtime) if the agent was never fully registered in the first place. Manual `comms_register(...)` with complete fields is the definitive recovery.

## Re-register seemingly "not taking effect"

**Symptom.** You re-register with new values but `comms_agent_info` still reflects the old ones.

**Cause.** Re-register is a **full state refresh** for session-related fields. If you pass `sessionHandle=""` (empty) or omit it, that's what gets stored — old session handles are cleared. If the result is "wrong", the bridge did what you asked.

Note that `description` is the one exception: omitting it preserves the existing value. Pass `description=""` to clear it explicitly.

**Fix.** Pass every field you care about on the re-register call. For Codex resident triggering, that usually means `cwd`, `sessionHandle`, and `appServerUrl` all explicit.

## Install.sh fails on Windows (Git Bash)

Known upstream issues with `install.sh` running under Git Bash:

- **Hook installer crashes** with `Error: ENOENT: no such file or directory, open 'C:\c\Users\...'`. Cause: `$HOME` is an MSYS path like `/c/Users/...` which Node interprets as relative and prepends the current drive. Workaround: run `install.sh` without `--with-hook` and install the hook manually.
- **`.cmd` shim hangs with `sed: command not found`** when launched from `cmd` / PowerShell. Cause: the system PATH only contains `C:\Program Files\Git\cmd`, not `C:\Program Files\Git\usr\bin`. Workaround: prepend Git's unix bin dirs to PATH before calling the shim, or run the Bash `claude-aify` / `codex-aify` wrapper directly from Git Bash.
- **`claude` resolves to npm bash-shim instead of native `claude.exe`.** If both are installed, the bash wrapper picks the npm shim, which triggers the PATH issue above. Workaround: prefer the native Windows build of Claude Code (`%USERPROFILE%\.local\bin\claude.exe`) and add a check in the wrapper.

These are maintainer-level issues, not runtime bugs. Track them in the install.sh improvements issue.

## General escalation

If none of the fixes above resolve the issue:

1. Capture the exact symptom (dispatch run ID, agent ID, error text).
2. Hit `curl http://localhost:8800/api/v1/dispatch/runs/<id>` to get the raw run state.
3. Hit `curl http://localhost:8800/api/v1/agents/<id>` for the agent state.
4. Forward those three pieces to whoever is debugging aify-comms. A fresh repro against current code (post-hard-reset) is worth 10× more than a trace against stale state.
