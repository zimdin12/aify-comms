# Hermes Agent Integration

This guide covers the aify-comms integration for NousResearch Hermes Agent.

Primary upstream references:

- Hermes install guide: https://hermes-agent.nousresearch.com/docs/getting-started/installation
- Hermes CLI/TUI guide: https://hermes-agent.nousresearch.com/docs/user-guide/cli
- Hermes MCP guide: https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp
- Hermes event hooks guide: https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks

## What Is Integrated

- Runtime name: `hermes`
- Managed dashboard spawns through environment bridges.
- Browser Console through the shared PTY/xterm path.
- Messenger delivery through the same managed PTY when Console is open or closed.
- Resident wrapper: `hermes-aify`.
- MCP server registration in `~/.hermes/config.yaml`.
- Optional post-tool notification hook using `~/.hermes/agent-hooks/aify-notify.sh`.

Hermes is terminal-first. aify-comms treats it like the other PTY-capable runtimes: the bridge starts the interactive process, dashboard chat sends prompts into that process, and Console attaches to the live terminal stream.

## Install Hermes

Install Hermes first, following the upstream guide.

Linux, macOS, or WSL2:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

Native Windows PowerShell is upstream early beta:

```powershell
irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1 | iex
```

After install, open a fresh shell and verify:

```bash
hermes doctor
hermes --help
```

Configure Hermes providers/models with Hermes's own commands, for example:

```bash
hermes model
hermes config check
```

## Install aify-comms Into Hermes

From the aify-comms repo:

```bash
bash install.sh --client hermes http://192.168.100.10:8800 --with-hook
```

The installer:

- installs/updates the shared `aify-comms` environment bridge launcher
- registers `aify-comms` as a Hermes MCP server in `~/.hermes/config.yaml`
- installs `hermes-aify`
- with `--with-hook`, installs `~/.hermes/agent-hooks/aify-notify.sh` and adds a `post_tool_call` shell hook

Restart any running Hermes terminals and any long-running `aify-comms` bridge after updating.

## Start The Environment Bridge

Start the bridge from a directory that should be allowed as a dashboard workspace root:

```bash
cd /path/to/workspace-or-parent
aify-comms http://192.168.100.10:8800
```

You can advertise additional roots:

```bash
aify-comms http://192.168.100.10:8800 /path/to/extra/root
```

The dashboard can spawn Hermes only in workspaces under the bridge's advertised roots.

## Spawn A Managed Hermes Agent

In the dashboard:

1. Go to **Environments**.
2. Choose an online bridge that advertises `hermes`.
3. Spawn an agent with runtime `hermes`.
4. Pick a workspace under that bridge's roots.
5. Send a normal chat message.

For terminal-capable managed delivery, aify-comms will start a managed PTY and send the message into Hermes. Opening Console attaches to that same PTY instead of changing the agent to `cli-takeover`.

## Resident Hermes

Use resident mode only when you intentionally want a human-open terminal to own the session:

```bash
hermes-aify --aify-agent my-hermes-agent
```

If you know a Hermes session ID:

```bash
hermes-aify --aify-agent my-hermes-agent --resume <session-id>
```

The wrapper sets:

- `AIFY_RUNTIME=hermes`
- `AIFY_AGENT_ID=<agent>`
- `AIFY_SESSION_HANDLE=<session-id>` when provided
- `HERMES_SESSION_ID=<session-id>` when provided

If no session ID is known, the resident session can still register and chat while alive, but it is not resumable through a saved native handle until you set one.

## Hooks

Hermes shell hooks are configured under `hooks:` in `~/.hermes/config.yaml`. With `--with-hook`, aify-comms installs a post-tool hook that checks for unread aify messages after Hermes tool calls:

```yaml
hooks:
  post_tool_call:
    - matcher: ".*"
      command: "~/.hermes/agent-hooks/aify-notify.sh"
      timeout: 3
```

The hook is intentionally non-blocking. It is for notifications and heartbeats, not primary message delivery.

## MCP

Hermes reads MCP servers from `~/.hermes/config.yaml` under `mcp_servers`. The aify installer adds:

```yaml
mcp_servers:
  aify-comms:
    command: "node"
    args:
      - "/path/to/aify-comms/mcp/stdio/server.js"
```

When Hermes is launched through `hermes-aify`, the wrapper exports the server URL so the MCP child connects to the dashboard service.

## Current Limits

- aify-comms does not yet parse Hermes terminal output to discover a newly created session ID automatically.
- If you need durable resume after the PTY exits, copy the Hermes session ID from Hermes and use dashboard **Set handle** or launch with `hermes-aify --resume <session-id>`.
- Hermes active-run steering is not exposed as a separate adapter yet. Messenger input is delivered through the PTY path.
