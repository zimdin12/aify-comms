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

The launcher exports `AIFY_ENVIRONMENT_BRIDGE=1`. That flag is what turns the stdio server into a dashboard spawn target. Do not set it for ordinary MCP client sessions unless you intentionally want that process to claim dashboard spawn requests.

Roots are not the project choice for every agent. They are safety boundaries that say "this bridge may launch agents somewhere under here." The exact project folder is selected per spawned agent in the dashboard.

Run this once in each environment you want the dashboard to control:

- native Windows PowerShell/cmd/Git Bash for native Windows agents and `C:/...` workspaces
- WSL for WSL agents and `/mnt/...` or Linux workspaces
- Linux host or remote Linux shell for Linux agents

Leave the process running while you use the dashboard. Stop it with `Ctrl+C`.

The bridge heartbeats every 30 seconds. A graceful `Ctrl+C` marks the environment offline immediately; a hard kill, crash, or machine sleep is inferred from missed heartbeats and normally appears offline within about 90 seconds. The dashboard sorts environments by status and name, not by heartbeat time, so cards should not swap places during normal refresh.

If you start `aify-comms` again for the same environment before killing an older bridge, the newer bridge becomes the current bridge for that environment. Older bridge heartbeats are ignored once the server has seen the newer bridge's `bridgeStartedAt` metadata, and the server queues a stop control for the older bridge. Current bridge builds log the replacement bridge, PID, and cwd before exiting, so a terminal that closes with an "environment was superseded" message is not a runtime crash; another bridge for the same environment became current. The older OS process may still exist if it is hung and no longer polling, but it should not own spawn claims anymore.

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

## Verify

1. Open `http://localhost:8800/api/v1/dashboard`.
2. Go to **Environments**.
3. Confirm the bridge is `online`, has the expected roots, and advertises the runtime you want.
4. Spawn an agent into a workspace under one of those roots.
5. Confirm the agent appears in **Sessions** and **Chat**.

For full removal of the service, wrappers, MCP config, hooks, skills, and data volume, see [UNINSTALL.md](UNINSTALL.md).
