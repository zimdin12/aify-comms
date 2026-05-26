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

Managed Hermes defaults to the wrapper-backed path (`managed_via_wrapper=["codex","hermes"]`): the bridge owns a `hermes-aify` PTY, the wrapper starts a local Hermes dashboard gateway, and the wrapper's in-process aify-comms bridge claims dashboard dispatches with `executionModes=["channel","resident"]`. The browser Console renders the real wrapper TUI. This is the same operator-visible shape as a resident `hermes-aify`, except the bridge owns the PTY.

If wrapper-backed delivery is disabled, the bridge can fall back to native Hermes controllers: a persistent ACP JSON-RPC child or gateway controller, with synthesized terminal output for dashboard visibility. The gateway path gives multi-client visibility; the ACP path is single-client and mirrors `session/update` notifications into a virtual terminal.

Resident Hermes is terminal-first: `hermes-aify` opens an interactive `hermes chat --tui` for the operator. The wrapper spawns a hidden `hermes dashboard --tui` backing in the background, captures its ephemeral session token, exports `HERMES_TUI_GATEWAY_URL` so the Ink TUI attaches via WebSocket instead of spawning its own stdio sidecar, and exports `AIFY_HERMES_GATEWAY_URL` so the aify-comms bridge can attach to the same gateway. For dispatched work, the bridge resumes the durable Hermes session key into its own short gateway sid and uses `prompt.submit` / `session.steer`; delivery evidence is the aify-comms run/chat output, not whether the operator's original TUI redraws the injected turn.

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

`hermes-aify` is a wrapper around the real Hermes Agent executable. The bridge
advertises Hermes as available only when it can resolve `hermes` from its own
PATH, or when `AIFY_HERMES_COMMAND` / `HERMES_COMMAND` points at a real
executable. On Windows, verify from the bridge user:

```powershell
Get-Command hermes
Get-Command hermes-aify.cmd
```

If only `hermes-aify.cmd` exists, set `AIFY_HERMES_COMMAND` to the absolute
Hermes executable path and restart the bridge.

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

Managed dispatch normally starts or reuses the bridge-owned `hermes-aify` wrapper PTY. The wrapper's child bridge submits dashboard prompts through the local Hermes gateway (`session.resume` plus `prompt.submit` / `session.steer`), and dashboard Console renders the wrapper PTY plus dispatched-run synth frames. Native controller fallback may use synthesized `aify://virtual-rpc/hermes` output when wrapper mode is disabled or unavailable.

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
      command: "bash \"$HOME/.hermes/agent-hooks/aify-notify.sh\""
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

- Mid-turn steering is not supported on managed Hermes — `hermes chat -q` is single-shot per upstream's documented programmatic mode. `comms_run_steer` rejects with a clear message; send a follow-up dispatch instead (the next turn's prompt carries the prior context via `buildUserPrompt`).
- Conversation state across managed turns lives in the wire prompt, not in a persistent Hermes session, because upstream doesn't expose `-q` combined with `--resume`. If Hermes upstream adds either a true RPC daemon or session-resume on programmatic mode, the controller will be upgradeable to a persistent shape similar to managed Pi's Phase 2 architecture.
- Resident Hermes uses the real `hermes chat` TUI under `hermes-aify` and supports operator-driven multi-turn interactively. Bridge-dispatched work runs through a resumed gateway sid and may not appear in the operator's original TUI live. The bridge will not auto-discover a Hermes session ID created during a resident TUI session; copy it manually if you want durable resume and use dashboard **Set handle** or `hermes-aify --resume <session-id>`.
