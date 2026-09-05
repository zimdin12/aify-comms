# Hermes Agent Integration

This guide covers the aify-comms integration for NousResearch Hermes Agent.
See [HERMES_AIFY_PLUGIN.md](HERMES_AIFY_PLUGIN.md) for the runtime shim loaded
by `hermes-aify`.

## Current Model (2026-07-25)

Hermes delivery is the **visible-TUI gateway-host model** with a
**native-session-id** scheme:

- **Both managed and resident** Hermes run a HIDDEN per-agent gateway host
  (`hermes dashboard ... --no-open --skip-build`, with
  `HERMES_DASHBOARD_TUI=1` enabling `/api/ws` and `windowsHide:true` — no popup window) plus a background **delivery loop**
  (`mcp/stdio/hermes-managed-host.js run <agent>`). The loop is a standalone
  `channel-sidecar` bridge: it claims dispatch runs over HTTP by `agentId`,
  opens its own WebSocket to that gateway host, uses `prompt.submit` while idle and native `session.steer` while busy, and requeues rejected or racing busy delivery without falling through to interrupting submit.
- The visible Ink TUI in the bridge/wrapper attaches to the **same** gateway
  host via `HERMES_TUI_GATEWAY_URL`, so injected prompts and the agent's reply
  render in the operator-visible console. Visible-TUI-in-dashboard is a hard
  requirement (no headless delivery, no popup windows).
- **Native session id:** the wrapper resumes the agent's REAL Hermes session
  id; the synthetic `aify-<agentId>` session and its pre-seed/rename dance are
  retired. The delivery loop targets the real id (read from an agent-keyed
  marker, with a most-recent-live-session fallback), and `resolve-session`
  (in `hermes-managed-host.js`) converges the resume id against the gateway's
  live `session.active_list` at launch.
- The loop authors no reply (wake-only, symmetric with `claude-channel.js`):
  the in-session Hermes agent has the `comms_*` tools and self-replies with
  `comms_send(... inReplyTo=...)`, which closes the run.

Retired on current Hermes (do not treat as live delivery paths): the
`hermes-channel.js` api_server / per-agent daemon sidecar, the
`HermesResidentController` `aify.session.bind_transport` delivery frame, and the
legacy in-place Hermes source patch. The Hermes plugin still registers
`aify.session.bind_transport` and `aify.session.render_notice` on the gateway
defensively, and `render_notice` plus a `TeeTransport` event-fan are part of the
current gateway path so a sidecar submit does not steal the visible TUI's
stream.

Primary upstream references:

- Hermes install guide: https://hermes-agent.nousresearch.com/docs/getting-started/installation
- Hermes CLI/TUI guide: https://hermes-agent.nousresearch.com/docs/user-guide/cli
- Hermes MCP guide: https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp
- Hermes event hooks guide: https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks

## What Is Integrated

- Runtime name: `hermes`
- Managed dashboard spawns through environment bridges.
- Browser Console attaches to the wrapper TUI as a second gateway client.
- Messenger delivery through the per-agent gateway-host delivery loop, whether the Console is open or closed.
- Resident wrapper: `hermes-aify`.
- MCP server registration in `~/.hermes/config.yaml`.
- Optional post-tool notification hook using `~/.hermes/agent-hooks/aify-notify.sh`.

Managed Hermes is wrapper-backed by default (`managed_via_wrapper=["codex","hermes"]`): the bridge owns a `hermes-aify` PTY whose visible TUI attaches to the per-agent gateway host. Delivery is owned by the per-agent `hermes-managed-host.js run <agent>` loop (a `channel-sidecar` bridge), **not** by the wrapper PTY's in-process bridge — the server excludes Hermes from wrapper-child channel/resident claims (`wrapperChildExecutionModes`) so the wrapper child cannot race the loop and fabricate a reply. The browser Console renders the real wrapper TUI.

A native `HermesManagedController` (ACP JSON-RPC child, or gateway-backed when `AIFY_HERMES_MANAGED_USE_GATEWAY=1`) still exists for the non-channel managed path and as a debug/fallback surface. It is not the preferred operator-visible harness console.

Resident Hermes is terminal-first: `hermes-aify` opens an interactive `hermes --tui` for the operator. The wrapper ensures the hidden per-agent `hermes dashboard` gateway host (`HERMES_DASHBOARD_TUI=1`) (via `hermes-managed-host.js ensure-host`), then exports `HERMES_TUI_GATEWAY_URL` so the Ink TUI attaches to that gateway by WebSocket instead of spawning its own `tui_gateway`, and exports `AIFY_HERMES_GATEWAY_URL` (agent-keyed marker) so the aify-comms delivery loop attaches to the same gateway. On native Windows, `hermes-aify` runs a generated PowerShell (`.ps1`) wrapper instead of routing the TUI through Git Bash; this keeps `process.stdin.isTTY` true for Hermes' Node TUI. Current installs load `integrations/hermes-aify-plugin` through `PYTHONPATH` instead of editing Hermes source in place. That runtime shim registers `aify.session.render_notice` (and, defensively, `aify.session.bind_transport`) on the gateway, applies a `TeeTransport` guard so a sidecar `prompt.submit` does not steal the visible TUI's stream, preserves the wrapper-owned active-session file, and applies the guarded Codex stream compatibility fix while the Hermes process is running. The delivery loop targets the agent's REAL session id resolved from `session.active_list`, uses `prompt.submit` while idle and native `session.steer` while busy, and requeues rejected or racing busy delivery without interrupting the active turn. Fresh launches do not bind a synthetic `aify-<agentId>` session or inherited shell handles; `resolve-session` converges the resume id against the gateway's live session list, and only explicit `--resume` seeds a specific saved handle. Dispatch must render in the open `hermes-aify` console.

The shim also recovers from an OpenAI SDK `TypeError: 'NoneType' object is not
iterable` seen with the `openai-codex` provider. That failure happens before
MCP tools run, so it can look like registration broke even when the wrapper and
gateway are healthy. The shim falls back to Hermes's lower-level
`create(stream=True)` path for that exact SDK stream failure, then rebuilds
`response.output` from already-delivered `response.output_item.done` events
when ChatGPT Codex sends a terminal `response.completed` frame with
`output: null`. Restart `hermes-aify` after updating so the running Python
process imports the shim.

Live resident delivery also requires the wrapper's MCP bridge heartbeat. A raw
HTTP `POST /api/v1/agents` can update Hermes metadata, but it cannot create the
`bridgeInstanceId` heartbeat or dispatch claim loop that makes the visible TUI
wakeable. Use `hermes-aify --aify-agent <id>` or run `comms_register` from
inside the visible `hermes-aify` session; otherwise the server reports the
resident identity as `offline` and refuses dashboard/chat sends.

Resident registration also creates or refreshes a dashboard `agent_sessions`
row tied to the current environment bridge. That row is what lets Sessions and
Chat details show the resident identity as a concrete running session even
though the native `hermes-aify` terminal remains the primary console. If two
resident Hermes agents can exchange boxed `aify-comms message`
notices in their native terminals but the dashboard has no resident session row,
rebuild the service and restart/re-register through the current wrapper; old
builds only updated the agent identity.

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
bash install.sh --client hermes http://192.0.2.10:8800 --with-hook
```

The installer:

- installs/updates the shared `aify-comms` environment bridge launcher
- registers `aify-comms` as a Hermes MCP server in `~/.hermes/config.yaml`
- installs `hermes-aify`
- loads the durable Hermes shim from `integrations/hermes-aify-plugin`
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

To A/B test upstream Hermes without the aify runtime shim:

```bash
AIFY_HERMES_DISABLE_PLUGIN=1 hermes-aify
```

That disables the gateway `render_notice`/`TeeTransport` shim and the Codex
stream guard for that launch. The old in-place Hermes source patch path remains
available only for debugging:

```bash
AIFY_HERMES_LEGACY_SOURCE_PATCH=1 bash install.sh --client hermes http://192.0.2.10:8800
```

## Start The Environment Tier

**`aify-comms` starts nothing.** It is a verifier -- `doctor`, `--check`, `--version`,
`--help` -- and anything else exits 2 naming aify-env. Until v0.6.1 the words below started an
environment bridge that SUPERSEDED the one already serving the host, so its managed workers
were reaped; that took a whole fleet down twice. The host tier is `aify-env` now.

```bash
cd /path/to/workspace-or-parent
aify-env                       # serves this host: processes, PTYs, spawn claims
```

**Starting it is the operator's action, not an agent's**, and for the same reason as above:
a second instance supersedes the first and reaps its workers. To find out whether one is
already running, ASK rather than start one:

```bash
aify-env doctor                # answers without starting anything
```

The dashboard can spawn Hermes only in workspaces under the roots this host advertises.

## Spawn A Managed Hermes Agent

In the dashboard:

1. Go to **Environments**.
2. Choose an online bridge that advertises `hermes`.
3. Spawn an agent with runtime `hermes`.
4. Pick a workspace under that bridge's roots.
5. Send a normal chat message.

Managed dispatch starts or reuses the bridge-owned `hermes-aify` wrapper PTY, whose visible TUI attaches to the per-agent gateway host. The service queues those turns as channel-mode work, but they are claimed by the per-agent `hermes-managed-host.js run <agent>` delivery loop (a `channel-sidecar`), not by the wrapper PTY's child bridge — the server excludes Hermes from `wrapperChildExecutionModes` so the wrapper child cannot win the claim and fabricate a reply. The delivery loop uses `prompt.submit` while idle or native `session.steer` while busy against the agent's REAL session id resolved from `session.active_list`; rejected or racing busy delivery requeues without interrupting the active turn, and dashboard Console renders the wrapper PTY. On the first dispatch after a cold launch the loop may briefly find no attached session (the visible TUI is still resuming into the gateway); it requeues the run so the next poll delivers, and only fails with an actionable "no visible TUI attached" message after a bounded number of consecutive empty polls. The native `HermesManagedController` is the fallback surface when channel delivery is unavailable.

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
- `AIFY_SESSION_HANDLE=<session-id>` only for explicit `--resume` / `--session-id`
- `HERMES_SESSION_ID=<session-id>` only for explicit `--resume` / `--session-id`
- `AIFY_HERMES_ACTIVE_SESSION_FILE=<path>` and `HERMES_TUI_ACTIVE_SESSION_FILE=<path>` so the current visible TUI session can be discovered after launch

If no session ID is known, register without `sessionHandle`; the live gateway
and active-session file are authoritative for wake. Do not fill the handle from
`session.most_recent`, because that may be a historical Hermes DB session rather
than the terminal you are looking at.

If `comms_agent_info` shows `wakeMode: hermes-live` but `status: offline`, the
stored gateway metadata exists but the live wrapper bridge is missing or
expired. Restart the visible `hermes-aify` session and register through the MCP
tool from that same terminal; do not use a raw `/api/v1/agents` script as a
substitute.

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

Hermes reads MCP servers from `~/.hermes/config.yaml` under `mcp_servers`. The installer copies the bridge to a self-contained native directory (`~/.aify-comms` by default) for fast cold loads, and points the MCP entry at that copy:

```yaml
mcp_servers:
  aify-comms:
    command: "node"
    args:
      - "<HOME>/.aify-comms/mcp/stdio/server.js"
```

When Hermes is launched through `hermes-aify`, the wrapper exports the server URL so the MCP child connects to the dashboard service.

## Current Limits

- Managed and resident Hermes support non-interrupting mid-turn insertion through native `session.steer`. Explicitly queued work waits for turn-end; rejected or racing busy delivery requeues instead of falling through to interrupting `prompt.submit`. The native ACP/controller fallback has a narrower surface and may require a follow-up dispatch instead.
- Conversation state for the default path lives in the visible Hermes TUI session, reached by the delivery loop through `session.active_list` plus `prompt.submit` while idle or `session.steer` while busy against the agent's real session id. The native ACP/controller fallback keeps its own controller session or synthesized context and is a debug/fallback surface, not the preferred harness-console path.
- Resident Hermes uses the real `hermes --tui` under `hermes-aify` and supports operator-driven multi-turn interactively. The per-agent delivery loop submits into the active visible TUI session, so dispatched work must render in the operator's open console. Fresh wrapper launches avoid binding a synthetic `aify-<agentId>` session; `resolve-session` converges the resume id against the gateway's live session list, and the active-session file or explicit `--resume` is otherwise the source of truth. A stale saved handle falls back to the gateway's most-recent live session (and is re-persisted to the agent-keyed marker). If no visible session ever attaches to the gateway, dispatch fails loudly with an actionable message instead of resuming or creating a hidden session.
