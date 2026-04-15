---
name: aify-comms-debug
description: Known aify-comms issues and how to fix them. Check here when a dispatch fails, a wake mode looks wrong, a run is stuck, a bridge seems stale, or Claude/Codex reports a path/channel error. Complements the main aify-comms skill.
trigger: tool_available("comms_register") OR tool_available("comms_send") OR tool_available("comms_inbox")
---

# aify-comms: Troubleshooting

Use this skill whenever something in aify-comms is not behaving the way the main skill says it should. Each entry lists the **symptom**, the **cause**, and the **fix**.

Before digging in, always call `comms_agent_info(agentId="target")` on the agent in question and read `wakeMode`, `sessionMode`, `machineId`, `sessionHandle`, and `dispatchState`. Most of these fixes are just "something in that record is stale or wrong".

## Codex: `Invalid request: AbsolutePathBuf deserialized without a base path`

**Symptom.** Dispatches to a Codex agent fail instantly with this Rust error from Codex CLI.

**Causes (in order of likelihood):**
1. The `cwd` registered for the agent is a Windows backslash path like `C:\Users\you\project`. Codex's Rust path deserializer rejects those.
2. A stale pre-update `codex-aify` process is still running on the machine and polling the dispatch queue. Closing one Codex tab doesn't guarantee the background bridge + app-server children exit. The old bridge keeps claiming fresh dispatches and failing them with its cached (pre-fix) thread binding.
3. A Codex thread created *before* the cwd-normalization fix still has a backslash `cwd` inside Codex's own local thread store, so `thread/resume` fails even after you update aify-comms.

**Fix.**
1. Re-register with forward slashes: `cwd="C:/Users/you/project"`. The bridge auto-normalizes new dispatches, but it cannot rewrite Codex's stored thread state.
2. If you've already updated aify-comms and re-registered and it still fails, a stale bridge is the cause. Run the **Hard reset** below.

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

**Symptom.** `comms_agent_info` reports `wakeMode: claude-needs-channel` even though you launched with `claude-aify`.

**Cause.** In the current build, the bridge falls back to *any* alive `claude-aify` wrapper on the machine when no per-cwd marker exists. If you still see `claude-needs-channel`, no alive `claude-aify` process is running — either the wrapper exited, or Claude was started with plain `claude` instead of `claude-aify`.

**Fix.** Make sure one `claude-aify` session is alive, then re-register:

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
