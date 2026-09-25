# aify-comms troubleshooting: Codex (resume, app-server, approvals)

## Resident Codex keeps prompting for approval despite the bypass flag

`codex-aify` launches with `--dangerously-bypass-approvals-and-sandbox` by default (every wrapper
bypasses by default; `--safe` / `--no-auto` opts out). A per-tool gate
`[mcp_servers.X.tools.Y] approval_mode = "approve"` in the operator's `~/.codex/config.toml` is
evaluated independently of that flag. Set those gates to `approval_mode = "auto"` (`never` is a
global `approvalPolicy` value, not a per-tool one). Managed Codex runs under a generated
`CODEX_HOME` and never inherits these overrides.

## `Invalid request: AbsolutePathBuf deserialized without a base path`

**Cause 1: wrong path form.** A native-Windows Codex needs `C:/...`; `/mnt/c/...` is not absolute to
it. `resolveCodexRequestCwdFor` (`codex-errors.js`) sends the Windows form whenever the bridge
connects to an existing app-server. The service refuses the impossible pairings at registration
(`Invalid cwd`): a `linux:` / `darwin:` machine id with a `C:/` cwd, or `win32:` with `/mnt/`. Fix
the cwd and re-register.

**Cause 2: the stored rollout.** `thread/resume` loads the rollout from the active `CODEX_HOME`.
Managed Codex uses `~/.local/state/aify-comms/managed-codex-home`; a resident usually used
`~/.codex`. The tell is a failed run with an empty `externalThreadId`.

- **Missing rollout (`no rollout found`):** managed Codex imports it from the other homes and
  retries once; the run log says `Resumed imported Codex thread ...`.
- **Corrupt or oversized rollout** (`Message too long ... > 16777216`): restart fails loudly rather
  than dropping memory. Only Dashboard **Sessions -> Reset (fresh context)** starts a new thread.

If the raw error persists, the running bridge predates the classifier (`detectCodexResumeFailure`).
Run `aify-comms doctor`; with `bridge-installed` red, re-run `bash install.sh --client codex`, then
relaunch that agent. To verify the classifier from a checkout:
`node --test tests/codex-cwd-transform.test.js` in `mcp/stdio`.

## Hard reset of one resident Codex agent

Use this when one resident agent keeps failing with path or resume errors after an update, and you
want its visible TUI bound again.

1. Close that agent's `codex-aify` terminal. If its processes survive, stop only that agent's tree:
   find the `codex-aify` process whose command line carries `--aify-agent <id>` and stop it with its
   children. Managed Codex is stopped from the dashboard instead.
2. Move that agent's poisoned rollout aside, and delete its marker in
   `~/.local/state/aify-comms/runtime-markers/` (`codex-<cwd-hash>-<pid>.json`).
3. `cd` into the project and launch `codex-aify --aify-agent <id>`.
4. Re-register from that session with the live app-server:

```
comms_register(agentId="<id>", role="coder", runtime="codex", cwd="C:/your/exact/project",
  appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
comms_agent_info(agentId="<id>")
```

Add `sessionHandle="$CODEX_THREAD_ID"` only when it is non-empty in that same session (after
`codex-aify --resume <id>`); never fill it from historical rollout files. Healthy: `wakeMode:
codex-live`, the expected `machineId`, and a live `runtimeConfig.appServerUrl`.

## Not live-bound when you expected `codex-live`

Causes: several `codex-aify` sessions on one machine (ambiguous markers), a launch directory that
differs from the registered `cwd`, or no `$AIFY_CODEX_APP_SERVER_URL` in the session at register
time. Re-register from that live session as in the hard reset, step 4. `codex-missing-handle` with
`appServerUrl` set means the running bridge is old: relaunch `codex-aify` and register again.

## Closed resident Codex still receives dashboard work (`ECONNREFUSED 127.0.0.1:<port>`)

A resident bridge probes its app-server before heartbeating or claiming, and after repeated
failures reports `resident-lost`, so the identity reads `stopped`; ownership never switches by
itself. To hand it back, use Dashboard **Switch to managed**, then **Restart**. Healthy:
`sessionMode: managed`, `wakeMode: managed-worker`.

## `codex-aify` exits with `Error: stdin is not a terminal`

An old wrapper ran the visible TUI as a background job. Re-run `bash install.sh --client codex`;
`~/.local/bin/codex-aify` should run `codex "$@"` inside `run_codex_foreground`, never `codex "$@" &`.

## Native fallback: persistent app-server session

Used only when wrapper-backed delivery is off, or the Console command is
`aify://virtual-rpc/codex`: a long-lived `codex app-server` child per agent
(`mcp/stdio/codex-session.js`).

- **Stuck at `[codex] working...`:** Stop the worker from the dashboard Console and re-send; the
  bridge starts a fresh app-server and resumes the same thread. A turn with no runtime activity for
  30 minutes fails on its own (`runtimeConfig.quietTimeoutMs`; 12-hour hard limit
  `runtimeConfig.timeoutMs`).
- **`codex handshake timeout`:** run `codex app-server` by hand on that host; a broken login needs
  `codex login`. A custom binary goes in `AIFY_CODEX_COMMAND="/abs/path/to/codex app-server"`.
- **`Codex thread/resume failed for saved thread <id>`:** restore the rollout into the active
  `CODEX_HOME`, or Reset (fresh context).
