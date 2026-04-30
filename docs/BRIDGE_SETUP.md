# Environment Bridge Setup

The service container is the control plane. It stores messages, environments, spawn requests, sessions, and dashboard state. It does not directly launch native Windows, WSL, Linux, or remote processes.

An environment bridge is a host-side `mcp/stdio/server.js` process. It heartbeats an environment, advertises workspace roots and runtimes, claims spawn requests for that environment, and runs Codex/Claude/OpenCode work on that host.

Only the `aify-comms` launcher should advertise an environment. Normal Claude/Codex/OpenCode MCP client sessions use the same stdio server for tools, messaging, and resident dispatch, but they do not register themselves as spawn targets. This prevents every open agent tab from appearing as a duplicate environment.

## Quick Model

- Run the service once, usually with Docker Compose.
- Run one bridge per execution environment you want to target from the dashboard.
- The dashboard **Environments** page should show each bridge as `online`.
- Spawned agents can only use workspaces under that bridge's advertised roots.
- The launcher always advertises the directory you run it from. Extra roots are optional.

## Start The Service

```bash
docker compose up -d --build
curl http://localhost:8800/health
```

If another service already owns port `8800`, change the published port in Compose or use an override. Bridges must use the externally reachable service URL, not the container-internal URL.

## Installed Launcher

Running `install.sh` now installs an `aify-comms` launcher into `~/.local/bin`. On native Windows when installed from Git Bash, it also installs `aify-comms.cmd` so PowerShell and `cmd.exe` can launch it.

Installed files:

- Linux/WSL/Git Bash: `~/.local/bin/aify-comms`
- Native Windows PowerShell/cmd after Git Bash install: `%USERPROFILE%\.local\bin\aify-comms.cmd`

Basic usage:

```bash
aify-comms
aify-comms /path/to/extra/root /another/root
aify-comms http://host:8800 /path/to/extra/root
```

If no server URL is passed, the launcher uses `AIFY_SERVER_URL` or falls back to the URL provided during install, then `http://localhost:8800`. The current directory is always included in `AIFY_CWD_ROOTS`; `AIFY_CWD_ROOTS` and extra command-line roots add more allowed workspace boundaries.

The launcher passes `--environment-bridge` to the stdio server. That process argument is what turns the stdio server into a dashboard spawn target. Do not set the legacy `AIFY_ENVIRONMENT_BRIDGE=1` flag for ordinary MCP client sessions unless you intentionally want that process to claim dashboard spawn requests.

Roots are not the project choice for every agent. They are safety boundaries that say "this bridge may launch agents somewhere under here." The exact project folder is selected per spawned agent in the dashboard.

Run this once in each environment you want the dashboard to control:

- native Windows PowerShell/cmd/Git Bash for native Windows agents and `C:/...` workspaces
- WSL for WSL agents and `/mnt/...` or Linux workspaces
- Linux host or remote Linux shell for Linux agents

Leave the process running while you use the dashboard. Stop it with `Ctrl+C`.

The bridge heartbeats every 30 seconds. A graceful `Ctrl+C` marks the environment offline immediately; a hard kill, crash, or machine sleep is inferred from missed heartbeats and normally appears offline within about 90 seconds. The dashboard sorts environments by status and name, not by heartbeat time, so cards should not swap places during normal refresh.

If you start `aify-comms` again for the same environment before killing an older bridge, the newer bridge becomes the current bridge for that environment. Older bridge heartbeats are ignored once the server has seen the newer bridge's `bridgeStartedAt` metadata, and the server queues a stop control for the older bridge. Current bridge builds log the replacement bridge, PID, and cwd before exiting, so a terminal that closes with an "environment was superseded" message is not a runtime crash; another bridge for the same environment became current. The older OS process may still exist if it is hung and no longer polling, but it should not own spawn claims anymore.

If a bridge is superseded immediately after spawning or messaging a managed Claude/Codex agent, update and reinstall the bridge launcher. Older launchers used an inherited `AIFY_ENVIRONMENT_BRIDGE=1` environment variable; managed child MCP servers could inherit it and briefly impersonate the environment bridge from the agent workspace. Current launchers pass `--environment-bridge` only to the real bridge process, and managed child processes strip bridge-only environment variables.

Killing a bridge stops the execution target, not the team identity. Managed teammates that were backed by that environment are marked offline/detached and their active sessions become lost; chats and identity records remain. Restart the bridge, or assign the teammate to another online environment from **Team**, then recover/restart from **Sessions**.

Forgetting an environment hides that execution target from normal dashboard lists. It does not delete agent identities, chats, saved spawn specs, or session records. A forgotten environment can reappear if its bridge starts heartbeating again.

## Linux Or WSL Bridge

Use this when the runtime CLIs and target workspaces live in Linux or WSL.

```bash
cd /path/to/aify-comms
bash install.sh --client codex http://localhost:8800 --with-hook
npm --prefix mcp/stdio install

cd /path/to/workspace-or-workspace-parent
aify-comms
```

For WSL, run this from the WSL distro that owns the runtime CLI and workspace paths. Use Linux paths such as `/mnt/c/Docker/project`, not `C:/Docker/project`. Add extra roots only when you want one bridge command to cover multiple workspace trees:

```bash
aify-comms /mnt/c/Docker /home/you/work
```

If `aify-comms` is not found in WSL, add the install directory to PATH for the current shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
command -v aify-comms
```

## Native Windows Bridge

Use this when the runtime CLIs and target workspaces live in native Windows.

Install from Git Bash so the shell wrappers and `.cmd` shims are created in the Windows user profile:

```bash
cd ~/aify-comms
bash install.sh --client codex http://localhost:8800 --with-hook
```

Then open a new PowerShell window and verify:

```powershell
Get-ChildItem "$env:USERPROFILE\.local\bin\aify-comms.cmd"
Get-Command aify-comms.cmd
```

If PowerShell still cannot find it, the user PATH has not refreshed in that terminal. For the current PowerShell window:

```powershell
$env:Path += ";$env:USERPROFILE\.local\bin"
Get-Command aify-comms.cmd
```

To run the bridge:

```powershell
cd C:\path\to\workspace-or-workspace-parent
aify-comms.cmd
```

If the shim exists but PATH is still broken, run it by full path:

```powershell
& "$env:USERPROFILE\.local\bin\aify-comms.cmd"
```

Use forward-slash paths in agent registration and dashboard workspaces when possible, for example `C:/Docker/project`. The bridge normalizes paths for runtime requests, but Codex is especially strict about Windows path shape.

Add extra roots only when needed:

```powershell
aify-comms.cmd C:\Docker C:\Users\$env:USERNAME\work
```

## Service URL Rules

- Same host Linux/WSL/browser to service: usually `http://localhost:8800`.
- Native Windows bridge to a service running in Windows Docker Desktop: usually `http://localhost:8800`.
- Bridge in a container reaching a host service: often `http://host.docker.internal:8800`.
- Remote machine bridge: use the LAN/VPN URL for the service, for example `http://10.0.0.20:8800`.

If the dashboard does not show the bridge, first verify the bridge can reach:

```bash
curl "$AIFY_SERVER_URL/health"
```

## Root Delimiters

`AIFY_CWD_ROOTS` uses the host OS path-list delimiter:

- Linux/macOS/WSL: colon, for example `/home:/mnt/c/Docker`
- Windows PowerShell/cmd: semicolon, for example `C:\Docker;D:\Work`

Dashboard spawn requests outside these roots are rejected by the service and by the bridge.

## Resident Sessions Versus Headless Bridge

The headless environment bridge is enough for dashboard-managed spawns. Resident visible sessions still need the runtime wrapper when you want the dashboard to wake an already-open CLI:

- Codex: install Codex support, start with `codex-aify`, then register from that session.
- Claude Code: install Claude support, start with `claude-aify`, then register from that session.
- OpenCode: register with a real `sessionHandle` for resident resume, or use managed dashboard spawns.

Stopping a resident from the dashboard disables wake/dispatch in the control plane and interrupts active work when a runtime control path exists. It does not forcibly close a human's terminal window. Managed sessions spawned through the bridge can be stopped/restarted/recovered through their stored spawn spec.

To move an existing resident identity under dashboard-managed control, open **Team -> Manual / Resident CLI Identities**, choose **Edit** or **Actions -> Adopt env**, and assign an online environment, runtime, and workspace. This does not attach the already-open CLI process to an environment. It converts the identity into a managed teammate by creating a spawn spec and a recoverable session record for the selected environment/workspace/runtime. After adoption, close or stop the old resident CLI tab for that same `agentId`, then use **Sessions -> Recover/Restart** to run future work through the environment bridge.

If you are moving a dashboard-managed session into the native CLI temporarily, use **Pause for CLI** first, then run the shown resume command. The dashboard shows the command as a code block; click it to copy. Then call `comms_register(...)` from that same CLI with the same `agentId` and runtime handle. `claude-aify --resume <id>` exports `CLAUDE_SESSION_ID=<id>` for the MCP process, so a normal Claude registration can capture it. Codex should register with `$CODEX_THREAD_ID` and `$AIFY_CODEX_APP_SERVER_URL` when available. That registration updates the saved Claude session ID, Codex thread ID, or OpenCode session ID. When you close the CLI and return to the dashboard, **Recover** or **Restart** should reuse that handle. Fresh native handles should come from a new spawn or explicit **Clear resume state**, not from ordinary adopt/recover/restart.

Claude Code has two different native continuation flags: `--session-id` creates a specific new session, while `--resume <id>` continues an existing transcript. The bridge now checks for the transcript under `.claude/projects/...` and uses `--resume` after the first managed turn, so dashboard messages keep native Claude memory instead of colliding with the already-created session file.

On Windows, current bridge builds terminate the whole managed runtime process tree when a run is interrupted, stopped, timed out, or when the bridge exits. This matters for managed Claude Code because the bridge may launch through `cmd.exe /c claude`; killing only the wrapper process can leave a hidden `claude -p --session-id ...` child behind. If a managed Claude run still reports `Session ID ... is already in use` after the resume check, the Windows bridge searches for a process command line containing the exact locked session ID, excludes interactive `claude-aify` / `--resume` commands, kills that process tree, and retries once. If the session ID is not visible in the process command line, the bridge also checks aify runtime markers for the same workspace and may stop a marked Claude parent only when that parent looks headless (`-p`, `--print`, or `--session-id`). If auto-cleanup still cannot find the owner, search for and stop the stale Claude process manually, then restart the Windows `aify-comms` bridge.

Managed runtimes have a 12-hour hard dispatch timeout by default. Managed Codex also has a 30-minute quiet-stall watchdog. The hard timeout caps total runtime. The quiet watchdog only fires when Codex stops emitting runtime notifications or stderr after the last observed activity, which usually means the app-server/turn path wedged. If the last event is `Started mcpToolCall`, the turn is inside a Codex MCP tool call; normal remote aify-comms HTTP calls have a bounded timeout (`AIFY_HTTP_TIMEOUT_MS`, default 20000ms), and managed Codex config sets `tool_timeout_sec = 25` for the aify-comms MCP server so transient service/transport stalls surface as tool errors instead of wedging the run indefinitely. Managed Codex also disables `comms_listen`; delivered managed runs already have the message in their prompt and should not long-poll. Override per agent with `runtimeConfig.timeoutMs` for the hard limit and `runtimeConfig.quietTimeoutMs` / `runtimeConfig.silenceTimeoutMs` for the quiet window. Set the quiet timeout to `0` only for agents expected to run very long silent commands.

Managed agent-to-agent prompts prefer explicit `comms_send(..., inReplyTo=...)` replies. If the comms tool path is blocked or appears stalled, the prompt tells the runtime to finish with plain text instead; the bridge can mirror that final text as the handoff only when no explicit reply was recorded. Mirrored fallback handoffs are stored in the original sender's inbox and best-effort queued for live delivery to that sender when the sender is startable. If a manager later receives a teammate reply and owes the human a report, it should send `comms_send(to="dashboard", ...)`; dashboard is a store-only human recipient and no runtime is started.

Resident `claude-aify` sessions receive live messages and steer controls through Claude Code Channels. The bridge emits `notifications/claude/channel` into the already-running interactive Claude session, so it can react to external comms while the terminal stays open. This is different from Codex's app-server `turn/steer` call, and different from dashboard-managed headless Claude runs (`claude -p --resume ...`), which have no active-turn API for mid-run injection.

Managed prompts also include a focused team-communication contract: stay on the current ask, verify state/history before asserting it, answer with result/evidence/blocker/next action, and split unrelated topics instead of dragging all recent context into one turn. The injected direct-message context is intentionally compact and should be treated as background, not as a command to continue every old thread.

## Verify

1. Open `http://localhost:8800/api/v1/dashboard`.
2. Go to **Environments**.
3. Confirm the bridge is `online`, has the expected roots, and advertises the runtime you want.
4. Spawn an agent into a workspace under one of those roots.
5. Confirm the agent appears in **Sessions** and **Chat**.

For full removal of the service, wrappers, MCP config, hooks, skills, and data volume, see [UNINSTALL.md](UNINSTALL.md).
