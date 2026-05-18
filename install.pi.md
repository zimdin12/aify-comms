# Install For Oh My Pi

Use aify-comms when you want dashboard-driven coordination for Pi agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Copy-Paste Install

Install Oh My Pi first so the `omp` command is available in the same shell/user that will run the bridge.

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client pi http://192.168.100.10:8800
```

Restart Oh My Pi after install.

For dashboard-managed spawns, also connect an environment bridge on the machine that should run Pi:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://192.168.100.10:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. The installer configures OMP's user MCP file at `~/.omp/agent/mcp.json`, installs the `aify-comms` bridge launcher, and installs resident wrappers: `omp-aify` and its `pi-aify` alias.

## Resident Pi

Use `omp-aify` when a terminal you opened should own the visible Pi session. `pi-aify` is kept as an alias:

```bash
cd /path/to/project
omp-aify --aify-agent my-pi --aify-role coder
```

If you are resuming a known Pi session, pass the resume handle so aify-comms can bind resident dispatch to the same native session:

```bash
omp-aify --aify-agent my-pi --resume <session-id-or-prefix>
```

If no Pi session handle is available, the resident session can still use MCP tools and register for presence, but active resident dispatch will remain unavailable until it is rebound with a handle.

## Managed Pi

Managed Pi agents are spawned from the dashboard or `comms_spawn(..., runtime="pi")`. The bridge uses OMP RPC mode (`omp --mode rpc`) so it can send prompts, capture streamed assistant text, steer active work, and interrupt through the runtime boundary.

Current Pi note:
- Runtime aliases `pi`, `omp`, `oh-my-pi`, and `pi-agent` normalize to `pi`.
- Managed Pi supports persistent managed work, resume handles when OMP exposes them, active-run steer, and interrupt.
- Managed Pi captures streamed `text_delta` output and final assistant text from RPC completion events such as `message_end` / `agent_end`.
- A blank model, or a stored model value of `default`, means no `--model` override; Oh My Pi then uses `~/.omp/agent/config.yml`.
- Dashboard **Settings -> Runtime** can set managed Pi model and effort defaults. Pi effort is passed to OMP as `--thinking` when set.
- When managed OMP later reports a native `sessionId`, aify stores it as the agent/session handle so the dashboard edit field and future resumes can use it.
- Steering sends OMP's native RPC `steer` command into the active Pi run. Use `queueIfBusy=true` when a message should wait for the next turn instead.
- Managed runtime hard timeout is **12 hours** by default (`runtimeConfig.timeoutMs`).
- Set `AIFY_PI_COMMAND` or `PI_COMMAND` before starting `aify-comms` if `omp` is installed somewhere that is not on the bridge process `PATH`.

## Quick Start

```text
comms_register(agentId="my-pi", role="coder", runtime="pi", sessionHandle="$PI_SESSION_ID")
comms_agents()
comms_agent_info(agentId="my-pi")
comms_send(from="my-pi", to="other-agent", type="info", subject="Hello", body="Hi there")
comms_inbox(agentId="my-pi", mode="headers")
comms_inbox(agentId="my-pi", messageId="<message id>")
```
