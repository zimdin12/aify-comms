# aify-comms troubleshooting: Codex (resume, app-server, approvals)

## Contents

- [Codex resident keeps prompting for approval despite the bypass flag](#codex-resident-keeps-prompting-for-approval-despite-the-bypass-flag)
- [Codex: `Invalid request: AbsolutePathBuf deserialized without a base path`](#codex-invalid-request-absolutepathbuf-deserialized-without-a-base-path)
- [Hard reset: Codex dispatches keep failing after update](#hard-reset-codex-dispatches-keep-failing-after-update)
- [Not live-bound when you expected `codex-live`](#not-live-bound-when-you-expected-codex-live)
- [Closed resident Codex still receives dashboard work](#closed-resident-codex-still-receives-dashboard-work)
- [Codex native fallback persistent app-server session](#codex-native-fallback-persistent-app-server-session)

## Codex resident keeps prompting for approval despite the bypass flag

**Symptom.** A resident `codex-aify` launched no-prompt (the default
`--dangerously-bypass-approvals-and-sandbox`) still raises an interactive approval prompt
on certain MCP tool calls and strands the dispatch.

**Cause (operator config, NOT repo code).** A per-tool gate
`[mcp_servers.X.tools.Y] approval_mode = "approve"` in the operator's `~/.codex/config.toml`
is evaluated INDEPENDENTLY of codex's global bypass flag, so the global bypass does not
suppress it.

**Fix.** Set those per-tool gates to `approval_mode = "auto"` — the docs-correct per-tool
"no prompt" value. Note `never` is a valid GLOBAL `approvalPolicy` but is NOT a valid
per-tool `approval_mode`, so don't use it there. **Managed codex is unaffected** — it runs
under a clean generated CODEX_HOME that never inherits the operator's per-tool overrides;
only resident/operator-config codex hits this. (Context: every `*-aify` wrapper already
launches its harness no-prompt by default — claude `--dangerously-skip-permissions`, codex
`--dangerously-bypass-approvals-and-sandbox`, hermes `--yolo`, pi/opencode `--auto-approve`,
managed-codex `approvalPolicy:never` — all behind a uniform `--safe`/`--no-auto` opt-out.)

## Codex: `Invalid request: AbsolutePathBuf deserialized without a base path`

**Symptom.** Dispatches to a Codex agent fail with this Rust error. Dashboard may also show `Codex WebSocket app-server connection closed (1006)`.

**Root cause #1 (Windows, resident, and the one you hit first).** On Windows the bridge's `defaultCodexCommand()` returns `wsl.exe -e codex app-server`, so the legacy launcher-based `codexWorkingPath` transform turns `C:/Docker/project` into `/mnt/c/Docker/project` regardless of whether the bridge will spawn its own Codex or connect to one `codex-aify` already started. When the connection is to a native-Windows Codex (the normal `codex-aify` setup), sending `/mnt/c/...` makes Rust's `Path::is_absolute()` return false — there is no drive-letter prefix — and `AbsolutePathBuf::deserialize` rejects the request at `turn/start`. Fixed in the bridge by `resolveCodexRequestCwdFor` in `mcp/stdio/codex-errors.js`: when `appServerUrl` is set, the transform is skipped and we send `C:/Docker/project` instead. Locked down by `mcp/stdio/tests/codex-cwd-transform.test.js`. Check with `npm test` from `mcp/stdio/`. If the test is absent or fails, the bridge predates the fix — `git pull` and restart `codex-aify`.

**Backend guard (current build).** The server now rejects impossible resident Codex registrations up front: `linux:` / `darwin:` machine IDs cannot register `C:/...` cwds when `appServerUrl` is present, and `win32:` machine IDs cannot register `/mnt/...` cwds. If `comms_register` now fails immediately with `Invalid cwd`, that is the intended fast-fail path; fix the cwd and re-register instead of trying to dispatch through it.

**Root cause #2 (stored rollout).** Codex's `thread/resume` loads the thread's stored rollout from the active `CODEX_HOME` under `sessions/...`. Dashboard-managed Codex uses a managed home (`~/.local/state/aify-comms/managed-codex-home`), while a resident or manually started Codex usually used `~/.codex`. If the saved handle points at a rollout that exists only in the other home, Codex reports `no rollout found for thread id ...`. If a path field in the file cannot be deserialized, or if the rollout/context has grown past Codex's websocket frame limit (`Space limit exceeded: Message too long: ... > 16777216`), the call crashes before the bridge can send anything else. The tell is that the failed run has an **empty `externalThreadId`**: the bridge never got past `thread/resume`.

**Auto-recovery (current build).** When managed Codex gets `no rollout found for thread id`, the bridge first searches the normal Codex homes (`CODEX_HOME`, then `~/.codex`), copies the matching `sessions/.../rollout-*.jsonl` and any `shell_snapshots/...` files into the managed Codex home, and retries `thread/resume` once. This preserves native chat memory when the thread exists but was stored under the resident/default Codex home.

If the rollout is corrupt, oversized, or cannot be found in any Codex home, ordinary restart fails loudly instead of silently discarding memory. Only an explicit Dashboard **Sessions -> Reset (fresh context)** / `fresh_context` request creates a replacement thread. In that explicit mode the bridge:

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

## Not live-bound when you expected `codex-live`

**Symptom.** Right after `comms_register` the agent is not live-bound, or an older API/debug view shows `wakeMode: message-only`, even though you're inside `codex-aify`.

**Causes.**
- Multiple `codex-aify` sessions are open on the same machine — the bridge sees ambiguous live markers and refuses to pick one.
- The wrapper was launched from a different directory than the `cwd` you passed to `comms_register` and auto-discovery can't resolve it.
- The live app-server env var `$AIFY_CODEX_APP_SERVER_URL` was not available inside the session at register time. `$CODEX_THREAD_ID` can be empty on a fresh `codex-aify`; current bridges can discover the live thread through Codex `thread/list`, but older bridges only looked at `$CODEX_THREAD_ID`. Do not fill it from historical rollout files.

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

Add `sessionHandle="$CODEX_THREAD_ID"` only when it is non-empty in that same session, usually after explicit `codex-aify --resume <id>`. If `wakeMode` remains `codex-missing-handle` even though `appServerUrl` is set, the running MCP bridge likely predates the Codex `thread/list.data` parser fix; reinstall/restart `codex-aify` and register again. If neither app-server URL nor thread ID is available, the session predates the current live-wake flow — restart Codex through `codex-aify` and try again.

## Closed resident Codex still receives dashboard work

**Symptom.** Dashboard chat to an agent you previously opened in `codex-aify` fails with `connect ECONNREFUSED 127.0.0.1:<port>`. Chat details still say **Resident live CLI**, even though you closed that visible CLI. The same identity may also have a managed backing and a CLI resume command.

**Cause.** The Codex app-server died when the visible CLI closed, but an orphaned aify-comms stdio bridge process was still heartbeating. The backend saw a fresh resident bridge lease and kept routing to the dead resident `appServerUrl` instead of returning the identity to managed backing.

**Fix (current build).** Resident Codex bridges now probe their app-server before heartbeating or claiming work. If the app-server is unreachable twice in a row, the bridge reports `resident-lost`, stops tracking the resident binding, and the backend immediately returns the identity to its saved managed environment when a spawn spec exists. Superseded/lost bridge heartbeats are ignored, so orphaned MCP child processes cannot keep the identity active.

**Manual recovery on older builds.** Restart the relevant `aify-comms` environment bridge and stop the orphaned stdio process. Then use Dashboard **Sessions -> Restart** on the identity. If needed, inspect with `comms_agent_info(agentId="...")`; healthy fallback should show `sessionMode: managed` and `wakeMode: managed-worker`.

## Codex native fallback persistent app-server session

Managed Codex defaults to a wrapper-backed `codex-aify` PTY. This section applies only when wrapper-backed delivery is disabled/unavailable or when the Console command is `aify://virtual-rpc/codex`: the native fallback keeps a long-lived `codex app-server` child per agent (`mcp/stdio/codex-session.js`). Symptoms specific to this path:

### `codex-aify` exits with `Error: stdin is not a terminal`

**Symptom.** Running `codex-aify` from an interactive WSL terminal exits
immediately with `Error: stdin is not a terminal`.

**Cause.** Old wrappers launched the visible Codex TUI as a Bash background job
and then waited on it. In non-interactive Bash wrappers, async jobs can receive
`/dev/null` as stdin, so Codex sees no terminal even though the operator started
from one. The app-server child should be backgrounded; the visible Codex TUI
must stay foreground.

**Fix.** Re-run `install.sh --client codex` or `redeploy.sh` from the current
repo and restart `codex-aify`. Verify `~/.local/bin/codex-aify` contains
`codex "$@"` in `run_codex_foreground` and does not contain `codex "$@" &`.

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

**Fix.** Either flip the agent to `resumePolicy=fresh_context` (Dashboard → Sessions → Reset (fresh context)) or restore the rollout file in the active CODEX_HOME and retry.
