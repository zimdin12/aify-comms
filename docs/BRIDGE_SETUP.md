# Environment Bridge Setup

The service container is the control plane. It stores messages, environments, spawn requests, sessions, and dashboard state. It does not directly launch native Windows, WSL, Linux, macOS, or remote processes.

An environment bridge is a host-side `mcp/stdio/server.js` process. It heartbeats an environment, advertises workspace roots and runtimes, claims spawn requests for that environment, and runs Codex/Claude/OpenCode/Pi work on that host.

Only the `aify-comms` launcher should advertise an environment. Normal Claude/Codex/Hermes/OpenCode/Pi MCP client sessions use the same stdio server for tools, messaging, and resident dispatch, but they do not register themselves as spawn targets. This prevents every open agent tab from appearing as a duplicate environment.

## Quick Model

- Run the service once, usually with Docker Compose.
- Run one bridge per execution environment you want to target from the dashboard.
- The dashboard **Environments** page should show each bridge as `online`.
- Spawned agents can only use workspaces under that bridge's advertised roots.
- The launcher always advertises the directory you run it from. Extra roots are optional.

## Start The Service

```bash
docker compose up -d --build
curl http://192.168.100.10:8800/health
```

If another service already owns port `8800`, change the published port in Compose or use an override. Bridges must use the externally reachable service URL, not the container-internal URL.

## Installed Launcher

Running `install.sh` now installs an `aify-comms` launcher into `~/.local/bin`. On native Windows when installed from Git Bash, it also installs `aify-comms.cmd` so PowerShell and `cmd.exe` can launch it.

Installed files:

- Linux/macOS/WSL/Git Bash: `~/.local/bin/aify-comms`
- Native Windows PowerShell/cmd after Git Bash install: `%USERPROFILE%\.local\bin\aify-comms.cmd`

Basic usage:

```bash
aify-comms
aify-comms --help
aify-comms /path/to/extra/root /another/root
aify-comms http://host:8800 /path/to/extra/root
```

If no server URL is passed, the launcher uses `AIFY_SERVER_URL` or falls back to the URL provided during install, then `http://192.168.100.10:8800`. The current directory is always included in `AIFY_CWD_ROOTS`; `AIFY_CWD_ROOTS` and extra command-line roots add more allowed workspace boundaries. Unknown option-looking arguments fail fast instead of becoming roots, and the service also ignores flag-like roots from stale launchers.

The launcher passes `--environment-bridge` to the stdio server. That process argument is what turns the stdio server into a dashboard spawn target. Do not set the legacy `AIFY_ENVIRONMENT_BRIDGE=1` flag for ordinary MCP client sessions unless you intentionally want that process to claim dashboard spawn requests.

Roots are not the project choice for every agent. They are safety boundaries that say "this bridge may launch agents somewhere under here." The exact project folder is selected per spawned agent in the dashboard.

Run this once in each environment you want the dashboard to control:

- native Windows PowerShell/cmd/Git Bash for native Windows agents and `C:/...` workspaces
- WSL for WSL agents and `/mnt/...` or Linux workspaces
- Linux/macOS host or remote shell for Unix agents

Leave the process running while you use the dashboard. Stop it with `Ctrl+C`.

The bridge heartbeats every 30 seconds. This is now an unconditional liveness beat — it fires regardless of activity, so an idle-but-alive agent stays `online` rather than decaying as it goes quiet (the beat stops immediately if the bridge's controlling parent process dies, so an orphan does not fake liveness). A graceful `Ctrl+C` marks the environment offline immediately; a hard kill, crash, or machine sleep is inferred from missed heartbeats and normally appears offline within about 90 seconds. The dashboard sorts environments by status and name, not by heartbeat time, so cards should not swap places during normal refresh.

If you start `aify-comms` again for the same environment before killing an older bridge, the newer bridge becomes the current bridge for that environment. Older bridge heartbeats are ignored once the server has seen the newer bridge's `bridgeStartedAt` metadata, and the server queues a stop control for the older bridge. Current bridge builds log the replacement bridge, PID, and cwd before exiting, so a terminal that closes with an "environment was superseded" message is not a runtime crash; another bridge for the same environment became current. The older OS process may still exist if it is hung and no longer polling, but it should not own spawn claims anymore.

If a bridge is superseded immediately after spawning or messaging a managed runtime agent, update and reinstall the bridge launcher. Older launchers used an inherited `AIFY_ENVIRONMENT_BRIDGE=1` environment variable; managed child MCP servers could inherit it and briefly impersonate the environment bridge from the agent workspace. Current launchers pass `--environment-bridge` only to the real bridge process, and managed child processes strip bridge-only environment variables.

Killing a bridge stops the execution target, not the agent identity. Managed agents that were backed by that environment are marked offline/detached and their active sessions become lost; chats and identity records remain. Restart the bridge, or assign the agent to another online environment from **Sessions -> Identity Directory**, then restart from **Sessions**.

Forgetting an environment hides that execution target from normal dashboard lists. It does not delete agent identities, chats, saved spawn specs, or session records. A forgotten environment can reappear if its bridge starts heartbeating again.

## Linux, macOS, Or WSL Bridge

Use this when the runtime CLIs and target workspaces live in Linux, macOS, or WSL.

```bash
cd /path/to/aify-comms
bash install.sh --client codex http://192.168.100.10:8800 --with-hook
npm --prefix mcp/stdio install
npm --prefix mcp/stdio rebuild node-pty

cd /path/to/workspace-or-workspace-parent
aify-comms
```

If the dashboard says the WSL/Linux bridge has no PTY/terminal support, verify
and repair the native PTY module in that same checkout:

```bash
node -e "import('./mcp/stdio/terminal-runtime.js').then(m=>console.log(m.bridgeTerminalSupported()))"
npm --prefix mcp/stdio rebuild node-pty
```

Restart the `aify-comms` bridge after the rebuild. A bridge can advertise Codex
as available while Console is still disabled if `node-pty` cannot load.

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
bash install.sh --client codex http://192.168.100.10:8800 --with-hook
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

- Same host Linux/macOS/WSL/browser to service: usually `http://192.168.100.10:8800`.
- Native Windows bridge to a service running in Windows Docker Desktop: usually `http://192.168.100.10:8800`.
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

## Resident Sessions Versus Managed Bridge

The environment bridge is enough for dashboard-managed spawns. Resident visible sessions still need the runtime wrapper when you want the dashboard to wake an already-open CLI:

- Codex: install Codex support, start with `codex-aify`, then register from that session or pass `--aify-agent <agentId>` for automatic resident registration.
- Claude Code: install Claude support, start with `claude-aify`, then register from that session or pass `--aify-agent <agentId>` for automatic resident registration.
- OpenCode: use managed dashboard spawns for triggerable delivery. Manual resident registration is presence/debug metadata only until a real multi-client resident surface is wired.
- Oh My Pi: install Pi support and use managed dashboard spawns for triggerable delivery. `omp-aify` / `pi-aify` can register presence or a standalone operator session, but OMP is single-client and does not support live resident injection into an open TUI.

Choose the mode intentionally:

- **Managed mode:** keep `aify-comms` running as the host bridge, spawn agents from the dashboard, and let the dashboard own lifecycle. This is the normal persistent team mode.
- **Resident mode:** open `claude-aify`, `codex-aify`, or `hermes-aify` yourself when you want a visible terminal to own live delivery temporarily. Bind it with `--aify-agent <agentId>` or call `comms_register(...)` from inside the session. Pi and OpenCode are presence-only resident surfaces today; triggerable Pi/OpenCode messages go through managed runtime controllers.

Stopping a resident from the dashboard disables wake/dispatch in the control plane and, when the live resident bridge is still polling, asks that bridge to terminate its host CLI/app process. If the resident bridge is already gone, stop is only a control-plane state change. Managed sessions spawned through the bridge can be stopped or restarted through their stored spawn spec; Recreate is the explicit fresh-context reset.

System shape:

```text
Dashboard/service (:8800)
  |  chats, agents, sessions, runs, artifacts
  v
aify-comms environment bridge
  |  claims managed spawn/run work for one host/runtime environment
  v
Runtime adapter
  |  Claude Code / Codex / Hermes / OpenCode / Oh My Pi in a selected workspace
  v
Agent session
```

### Moving Between Managed And Resident

Resident -> managed:

1. Open **Sessions -> Identity Directory**.
2. Choose **Edit** or **Actions -> Adopt env**.
3. Assign an online environment, runtime, and workspace.
4. Use **Sessions -> Actions -> Switch to managed** or the Chat details switch when dashboard sends should use the managed backing.
5. Close the old resident CLI tab, or use dashboard **Stop wake** / session **Stop** if you want the resident host process terminated.

This creates or updates managed backing for the same agent identity. It does not invent a new native handle. Closing the CLI does not switch ownership by itself; stale resident sends fail visibly until the operator switches to managed or restarts the resident wrapper.

Current resident Codex bridges also verify that their live app-server is reachable before heartbeating or claiming work. If a visible `codex-aify` CLI closes but an MCP child process is orphaned, the backend marks that resident bridge lost and leaves the identity resident/stopped until the operator switches to managed.

Managed -> resident CLI:

1. Open **Sessions** for the agent.
2. Run the shown native resume command with the same identity, or pass it directly to the wrapper:
   - `claude-aify --aify-agent <agentId> --resume <session-id>`
   - `codex-aify --aify-agent <agentId> resume --include-non-interactive <thread-id>`
   - `hermes-aify --aify-agent <agentId> --resume <session-id>`
   - `omp-aify --aify-agent <agentId> --resume <session-id>` for Pi presence/standalone only; use managed mode for triggerable Pi delivery.
3. Use **Sessions -> Actions -> Switch to resident** or the Chat details switch when the visible CLI should own delivery. If you do not pass `--aify-agent`, call `comms_register(...)` from that same CLI with the same `agentId` and runtime handle so the dashboard has a resident candidate. Do not switch Pi identities to resident expecting dashboard injection; switch Pi back to managed for triggerable delivery.
4. Do the direct terminal work.
5. Use **Switch to managed** when dashboard control should return. Close the CLI when done, or use **Stop wake** / session **Stop** to ask the resident process to terminate.

`claude-aify --resume <id>` exports `CLAUDE_SESSION_ID=<id>` for the MCP process, so auto-register and normal `comms_register` can capture it. `codex-aify` exposes its live app-server to the MCP process and auto-discovery binds the current thread when available. `hermes-aify --resume <id>` exports `HERMES_SESSION_ID=<id>`. `omp-aify --resume <id>` and `pi-aify --resume <id>` export `PI_SESSION_ID=<id>` for presence/standalone metadata, while triggerable Pi delivery remains managed RPC. Registration updates the saved Claude session ID, Codex thread ID, Hermes session ID, OpenCode session ID, or Pi session handle. Fresh native handles should come from a new spawn or explicit **Recreate**, not from ordinary adopt/restart.

If you know the correct native ID and only need to repair the saved handle, use Dashboard **Chat details -> Runtime Session -> Set handle** or **Sessions -> Actions -> Set handle**. This updates the identity, runtime state, and latest session record without creating a fresh context. Use it only for known-good handles; a wrong value binds the identity to the wrong native memory.

Ownership transfer is manual and turn-boundary guarded. If a resident CLI registers while an identity is managed, the backend records a `manualResidentCandidate` but keeps the current managed owner. The operator switches with **Switch to resident/managed**; active runs block the switch unless forced. If dashboard sends to a stale resident that has managed backing, the send fails visibly instead of silently retargeting work.

Claude Code has two different native continuation flags: `--session-id` creates a specific new session, while `--resume <id>` continues an existing transcript. The bridge now checks for the transcript under `.claude/projects/...` and uses `--resume` after the first managed turn, so dashboard messages keep native Claude memory instead of colliding with the already-created session file. `claude-aify` consumes explicit `--resume` / `--session-id` args, validates the transcript, and only forwards the resume flag to Claude when the transcript exists; stale saved handles fall back to a fresh session so the child bridge can rediscover and repair the current handle instead of exiting.

Current bridge builds terminate the whole managed runtime process tree when a run is interrupted, stopped, timed out, or when the bridge exits. On WSL/Linux this prevents orphan Codex/Hermes/OpenCode/Pi MCP child processes from keeping stale tool state alive after the parent runtime process is gone. On Windows this also matters for interactive PTY-backed Claude Code. Older managed Claude print-mode runs could leave hidden `claude -p --session-id ...` children behind; current dashboard-managed Claude work no longer uses `claude -p`, but the stale-process cleanup remains for old data and upgrade recovery. If auto-cleanup still cannot find a stale native owner, search for and stop the stale process manually, then restart the Windows `aify-comms` bridge.

Managed runtime defaults are explicit, symmetric, and global. Managed Claude Code and Codex model fields are blank by default; blank means the installed CLI/runtime chooses its own default/latest model. Both runtimes default to `high` effort/reasoning effort. Managed Claude Code uses `--max-turns 50` by default (`runtimeConfig.maxTurns` can override per identity). Managed Pi has optional model/effort defaults; blank or `default` model means no explicit `--model` override, and Pi effort is passed to OMP as `--thinking` when set. Configure operator defaults in Dashboard **Settings -> Runtime**. The normal spawn and identity-edit flows do not tune model/effort per agent. Bridge-spawned `claude-aify` receives model and effort through managed wrapper env and passes them as `--model` / `--effort` at wrapper launch; existing running PTYs must be restarted after changing the policy. Codex uses the managed `CODEX_HOME` plus explicit turn effort values, and only sends thread/turn model values when the global model override is set.

Managed runtimes have a 12-hour hard dispatch timeout by default. Managed Codex uses Codex's unattended bypass sandbox profile by default (`danger-full-access` in app-server terms, equivalent to `codex --dangerously-bypass-approvals-and-sandbox`) because `approvalPolicy=never` plus `workspace-write` can still cancel or wedge MCP calls non-interactively. `codex-aify` uses the same Codex flag by default for resident and wrapper-backed sessions, passing it to both the local app-server and visible remote TUI; launch with `codex-aify --safe` only when deliberately debugging permission behavior in a trusted test session. Use `runtimeConfig.sandboxMode="workspace-write"` only when deliberately debugging managed permission behavior.

Managed Codex also has a 30-minute quiet-stall watchdog. The hard timeout caps total runtime. The quiet watchdog only fires when Codex stops emitting runtime notifications or stderr after the last observed activity, which usually means the app-server/turn path wedged. If the last event is `Started mcpToolCall`, the turn is inside a Codex MCP tool call; normal remote aify-comms HTTP calls have a bounded timeout (`AIFY_HTTP_TIMEOUT_MS`, default 20000ms), managed Codex config sets `tool_timeout_sec = 25` for the aify-comms MCP server, and the bridge has a narrower 90-second watchdog for stuck `mcpToolCall aify-comms` items. That fast watchdog emits `mcp_tool_stalled`, fails the run cleanly, terminates the managed runtime process tree, fails stale controls, and mirrors a required handoff back to the sender. `comms_listen` is deprecated compatibility/debug long-polling and managed Codex disables it; delivered managed runs already have the message in their prompt and should not long-poll. Override per agent with `runtimeConfig.timeoutMs` for the hard limit, `runtimeConfig.quietTimeoutMs` / `runtimeConfig.silenceTimeoutMs` for the quiet window, and `runtimeConfig.mcpToolTimeoutMs` / `runtimeConfig.commsToolTimeoutMs` for the aify-comms MCP tool-call window. Set the quiet timeout to `0` only for agents expected to run very long silent commands; set the MCP tool-call timeout to `0` only while debugging the MCP transport itself.

The reply contract is uniform across managed delivered runs and resident/live sessions: answer every aify-comms message (including dashboard-origin) with `comms_send(type="response", inReplyTo="<message id>", to="<sender|dashboard>")`. That tool call is the chat/team-visible reply and closes the run; final plain text and the run summary are the agent's own working output, not the delivered reply. The `managed_reply_capture_fallback` setting controls the backstop when a delivered run ends with no explicit reply: `true` (default) auto-mirrors the run summary into chat as a safety net; `false` (strict) leaves the run reply-owed so a missing reply is surfaced. Agents should send the explicit `comms_send` regardless.

Managed runs should not call `comms_register`. Dashboard-managed identities are registered by the environment bridge and stored spawn/session records. Current MCP builds reject `comms_register` when `AIFY_MANAGED_DISPATCH=1` so a managed identity cannot accidentally downgrade itself into a resident/manual identity while handling a dashboard message.

Resident `claude-aify` sessions receive live messages and steer controls through Claude Code Channels. The bridge emits `notifications/claude/channel` into the already-running interactive Claude session, so it can react to external comms while the terminal stays open. Delivery-only runs are marked as "Delivered to Claude resident session; awaiting explicit reply" until the resident agent sends a real `comms_send(... inReplyTo=...)` response; that delivery marker is run telemetry, not a teammate-visible answer. Dashboard-managed Claude Code starts or reuses a bridge-owned interactive `claude-aify` PTY, leaves development-channel auto-confirm off unless the operator enables it, and keeps the run active/`working` only while it has live terminal backing and is waiting for the reply. Browser Console attaches to that PTY, and Stop Console tears it down without switching identity to `cli-takeover`. Managed Hermes and Codex default to wrapper-backed PTYs, but their wrapper child bridge delivers through Hermes gateway / Codex app-server APIs rather than raw PTY input. Pi still has native OMP RPC `steer` support where available.

Managed prompts also include a focused team-communication contract: stay on the current ask, treat each message as a small contract, verify state/history before asserting it, answer with result/evidence/blocker/next action, and split unrelated topics instead of dragging all recent context into one turn. A message contract should make clear who owns it, what action or answer is expected, what evidence/result satisfies it, and whether a reply or follow-up wake is owed. Managed turns should not end silently: stdout, logs, tool output, and run summaries are operational telemetry, not the team-visible answer. Each turn should close with a final reply to the triggering sender, separate `comms_send(...)` updates for other owners/dashboard, or a self-scheduled wake when the same agent owns later work. When an agent asks teammates for parallel work, it should name the expected reply target and completion condition so follow-up replies wake the right owner. The injected direct-message context is intentionally compact and should be treated as background, not as a command to continue every old thread.

Turn lifecycle is explicit, but it is not lockstep. Agents may exchange messages mid-turn, run independent lanes in parallel, and continue bounded work inside the current turn. Final plain text is only the reply to the current message; it does not schedule future work. If a managed agent says `Next action: ...` but does not send another message, the loop stops after that turn. For autonomous project work, agents must create the next wake before finishing when more work should happen later: send the next owner a `comms_send(...)`, or self-schedule with `comms_send(to="<own-agent-id>", type="request", queueIfBusy=true, subject="Continue: ...", body="...")` when they know their own next bounded chunk. They should stop for human confirmation only when the docs/team cannot answer a real decision; otherwise they ask the responsible teammate or continue from the plan.

## Verify

1. Open `http://192.168.100.10:8800/api/v1/dashboard`.
2. Go to **Environments**.
3. Confirm the bridge is `online`, has the expected roots, and advertises the runtime you want.
4. Spawn an agent into a workspace under one of those roots.
5. Confirm the agent appears in **Sessions** and **Chat**.

For full removal of the service, wrappers, MCP config, hooks, skills, and data volume, see [UNINSTALL.md](UNINSTALL.md).
