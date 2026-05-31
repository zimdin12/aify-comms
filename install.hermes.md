# Install For Hermes

Use aify-comms when you want dashboard-driven coordination for Hermes Agent:
live direct messages, channels, shared artifacts, active dispatch, managed
agent spawn, browser Console, and environment control.

## Prerequisites

- **Node.js 22 or newer** is required on the runtime that launches `hermes-aify`.
  The Hermes TUI's gateway client uses the global `WebSocket` constructor, which
  is only available in Node 22+. Earlier Node versions surface as
  `gateway exited` in the TUI when launching through `hermes-aify`. Plain
  `hermes` still works on older Node because it spawns a stdio gateway instead
  of attaching to a WebSocket.
  - WSL/Linux: install via [nvm](https://github.com/nvm-sh/nvm) (`nvm install --lts`)
    or NodeSource (`curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs`).
  - Native Windows: install from [nodejs.org](https://nodejs.org/) or via `winget install OpenJS.NodeJS.LTS`.
- **Hermes Agent** itself, installed and on `PATH` as `hermes`.

> **Path style is decided at install time.** `install.sh` detects whether the
> `hermes` it wraps is a Linux binary (WSL/native Linux) or a native Windows
> binary, and bakes the matching path style for the plugin `PYTHONPATH` and the
> MCP `server.js` arg. If you later switch which Hermes `hermes-aify` should wrap
> (e.g. change `AIFY_HERMES_COMMAND`/`HERMES_COMMAND`/`PATH` from Linux Hermes to
> native Windows Hermes or vice versa), **re-run `install.sh --client hermes`**
> so the wrapper and config paths match the new runtime.

## Copy-Paste Install

Install Hermes first:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

Then install aify-comms into Hermes:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client hermes http://192.168.100.10:8800 --with-hook
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client hermes --with-hook
```

Restart Hermes after install.

The installer writes the MCP entry and optional hook to Hermes' active config
home. On native Windows this is often `%LOCALAPPDATA%\\hermes` (for example
`C:\\Users\\Administrator\\AppData\\Local\\hermes\\config.yaml`), not
`~/.hermes`. To confirm the target before or after install:

```bash
hermes config path
hermes mcp list
```

For dashboard-managed spawns, also connect an environment bridge on the machine
that should run Hermes:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms http://192.168.100.10:8800
```

On Linux, macOS, or WSL use `aify-comms`. On native Windows from PowerShell/cmd
use `aify-comms.cmd`. The current directory is always an allowed workspace root;
extra root arguments are optional safety boundaries, not the per-agent project
choice.

If the dashboard says Hermes is unavailable even though `hermes-aify` exists,
check the underlying runtime command from the same Windows user/shell that runs
the bridge:

```powershell
Get-Command hermes
Get-Command hermes-aify.cmd
```

`hermes-aify` is only the aify wrapper; the environment bridge still needs the
real `hermes` executable. If Hermes is installed under another path, set it and
restart the bridge:

```powershell
[Environment]::SetEnvironmentVariable('AIFY_HERMES_COMMAND','C:\path\to\hermes.exe','User')
```

## Auto / bypass flag

`hermes-aify -auto` (also `--auto` or `--yolo`) adds Hermes' `--yolo` flag to the
interactive TUI launch, bypassing all dangerous-command approval prompts
(`HERMES_YOLO_MODE=1`). Without it, `hermes-aify` preserves normal visible
approval behavior. This mirrors `claude-aify -auto`
(`--dangerously-skip-permissions`) and `codex-aify -auto`
(`--dangerously-bypass-approvals-and-sandbox`). The flag is consumed by the
wrapper and applied only to the default chat/TUI launch, not to explicit
passthrough subcommands like `hermes-aify model list`.

## Session-mode flag

`hermes-aify` accepts `--resident` and `--managed`. Precedence: inherited
`AIFY_SESSION_MODE` env wins (bridge-spawned managed PTYs set it to `managed`);
else the flag; else TTY auto-detect via `[ -t 0 ]` — interactive defaults to
`resident`, non-TTY to `managed`. Use the explicit flag only when TTY detection
might be wrong for your shell context.

## Delivery path

Managed hermes uses the visible-TUI model: a hidden `hermes dashboard --tui
--port <P>` gateway host plus a `hermes-managed-host.js run <agent>` delivery
loop (runs as a `channel-sidecar` bridge; discovers the TUI's session via WS
`session.active_list` + `pickSessionForKey('aify-<agentId>')` and delivers via
WS `prompt.submit` / `session.steer`), plus a VISIBLE `hermes --tui --resume
aify-<agentId>` rendered in the dashboard Console via xterm.js. The agent
self-replies via `comms_send`. Session continuity is the deterministic stable
pinned id `aify-<agentId>`.

Half-migration note (honest): the `install.sh` RESIDENT branch still calls
`aify_hermes_ensure_daemon` (the api_server daemon) — a known leftover that has
not been fully removed. Both branches now resume the stable pinned
`aify-<agentId>` session, so continuity is consistent regardless. The retired
managed-delivery pieces (the per-agent `hermes gateway run` api_server daemon
AS the delivery path, `aify.session.bind_transport` / `HermesResidentController`,
api_server `chat` wake, and active-session-FILE discovery) should not be
treated as live.

If wrapper-backed delivery is disabled or unavailable, the bridge can fall
back to native Hermes controllers (`HermesController` /
`HermesManagedGatewaySession`) and synthesized `aify://virtual-rpc/hermes`
terminal output for operator visibility. That fallback does not provide the
same live TUI symmetry as wrapper mode.

`hermes-aify` does NOT require the `--strict-mcp-config` + minimal-MCP
isolation that `claude-aify` needs to work around the Claude Code stdio MCP
race bug.

### Resident dispatch delivery (operator-launched `hermes-aify`)

`hermes-aify` runs the operator's real Ink terminal TUI for `hermes chat`, and it exposes a local gateway the aify-comms bridge can use for live resident dispatch. Session continuity is deterministic: the wrapper pins a stable session id `aify-<agentId>` (exported as `HERMES_TUI_RESUME`) and launches `hermes --tui --resume aify-<agentId>`, so the same session is reused across dispatches instead of being discovered from gateway state. The aify-comms bridge attaches to the same `/api/ws` gateway, discovers the TUI's live session (WS `session.active_list` + `pickSessionForKey('aify-<agentId>')`), and delivers via WS `prompt.submit` (idle) / `session.steer` (mid-run). MCP discovery still runs before the TUI gateway builds its `AIAgent`; this matters because `hermes mcp test aify-comms` runs in a separate CLI process and can succeed while the already-running TUI gateway still has no `mcp_aify_comms_*` tools.

1. The wrapper spawns `hermes dashboard --tui --port <P> --host 127.0.0.1 --no-open --skip-build` as a hidden background child. This sets `_DASHBOARD_EMBEDDED_CHAT_ENABLED=True` in `hermes_cli/web_server.py`, which mounts the `/api/ws` JSON-RPC endpoint at the `tui_gateway/server.py` dispatcher.
2. The wrapper fetches `http://127.0.0.1:<P>/` and parses the ephemeral `__HERMES_SESSION_TOKEN__` from the injected `<script>` tag (`web_server.py:3688`).
3. It exports `HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<P>/api/ws?token=<T>` and `HERMES_TUI_RESUME=aify-<agentId>` in the env passed to `hermes --tui`. The Ink TUI's `gatewayClient.ts:startAttachedGateway` opens a WebSocket to that URL instead of spawning its own stdio sidecar — operator sees their normal terminal TUI experience, resumed on the stable pinned `aify-<agentId>` session. On native Windows, `hermes-aify.cmd` runs a generated PowerShell shim instead of Git Bash so the final `hermes.exe --tui` process keeps the real console TTY.
4. The aify-comms bridge (loaded inside `hermes chat` as an MCP server) ALSO opens a WebSocket to the same `/api/ws` (it reads `AIFY_HERMES_GATEWAY_URL` from env, written into the hermes runtime marker by `server.js`). For inbound aify-comms messages the bridge discovers the live TUI session via WS `session.active_list` + `pickSessionForKey('aify-<agentId>')`, then issues JSON-RPC `prompt.submit` (idle session) or `session.steer` (mid-run insertion, when `prompt.submit` returns code 4009 "session busy") against that session. Because the session id is the deterministic pinned `aify-<agentId>`, no `bind_transport` / `session.most_recent` negotiation is needed. Hermes emits real gateway events as `event` frames such as `message.delta`, `message.complete`, `tool.start`, and `tool.complete`; aify-comms translates those into run output and chat replies.

This is the Hermes equivalent to Claude Code channel delivery for the harness-console feature: the prompt and reply should render in the open `hermes-aify` terminal, while the same streamed events complete the aify-comms run/chat accounting.

Resident Hermes registration must come from the wrapper's MCP bridge. Do not
repair or create resident Hermes agents with raw `POST /api/v1/agents` scripts:
those can write `runtimeConfig.gatewayUrl`, but they cannot create the live
`bridgeInstanceId` heartbeat or the dispatch claim loop. A record in that state
is reported as `stale` and dashboard/chat sends are rejected until you restart
`hermes-aify` and run `comms_register` from the visible session, or switch the
identity back to managed.

Hermes exposes MCP tools with server-prefixed callable names. For the
aify-comms MCP server, use `mcp_aify_comms_comms_register`,
`mcp_aify_comms_comms_agent_info`, and `mcp_aify_comms_comms_send` in Hermes
turns; unprefixed names such as `comms_register` are shorthand used by generic
docs and other clients. `hermes mcp test aify-comms` listing
`comms_register` means the live callable name will be the prefixed Hermes tool
name when that toolset is exposed to the turn.

If a live `hermes-aify` turn can run `hermes mcp test aify-comms` but still
does not expose `mcp_aify_comms_comms_register` /
`mcp_aify_comms_comms_agent_info`, the active TUI gateway has not loaded MCP
tools. On current installs, restart `hermes-aify`; for an already-open gateway,
the gateway `reload.mcp` method (or the wrapper's reload-MCP control if
available) repairs the live registry without direct HTTP registration.

Hermes dashboard turns execute MCP tools inside the dashboard-gateway process,
not the later `hermes chat` child. Current wrappers export the selected
dashboard port before launching that process, and the runtime plugin derives
`AIFY_HERMES_GATEWAY_URL` inside `hermes_cli.web_server` from that port plus
Hermes' own session token. Without that dashboard-side env injection,
`mcp_aify_comms_comms_register` may be callable but still register as
`hermes-missing-handle` because no `runtimeConfig.gatewayUrl` reached
aify-comms.

**Mid-run insertion (`session.steer`)** is a first-class primitive on the hermes side: text lands on the last tool result of the next tool batch and the model sees it on its next iteration. No interrupt, no role-alternation violation.

**Bypass:** set `AIFY_HERMES_SKIP_GATEWAY=1` to fall back to plain `hermes` exec without the dashboard child. Use this if the dashboard probe is breaking your install and you don't need resident bridge-injection.

**Plugin A/B test:** set `AIFY_HERMES_DISABLE_PLUGIN=1` to launch
`hermes-aify` without the aify runtime shim. This is useful for comparing
upstream Hermes behavior after a Hermes update. With the plugin disabled,
resident visible-session binding and the guarded Codex stream fallback are not
provided by aify-comms. The old in-place source edit path is legacy/debug only:
set `AIFY_HERMES_LEGACY_SOURCE_PATCH=1` before running `install.sh --client
hermes` if you explicitly want that behavior.

**Cleanup:** `trap cleanup_aify_dashboard EXIT INT TERM` in the wrapper kills the dashboard child on wrapper exit, so `hermes-aify`'s lifecycle owns the dashboard process. Background dashboard logs go to `$XDG_STATE_HOME/aify-comms/hermes-aify-dashboard-<port>.log` (or `~/.local/state/aify-comms/...` on systems without XDG_STATE_HOME).

**Known limitations.** The dashboard binds to 127.0.0.1 only and uses ephemeral per-process tokens — it's safe to leave running. The `--skip-build` flag relies on hermes having already built the web UI dist once; **`install.sh --client hermes` now pre-builds this automatically** (see "web_dist prebuild" below). If you skip install.sh's prebuild (e.g. install hermes after running install.sh), you can prime it manually with `hermes dashboard --no-open` once.

### web_dist prebuild (added 2026-05-25)

`install.sh --client hermes` detects whether `<hermes-install-root>/hermes_cli/web_dist/index.html` exists and, if not, runs `npm install && npm run build` once in `<hermes-install-root>/web/`. Without this, fresh hermes installs hit the failure described in the section above: `hermes dashboard --skip-build` dies with `✗ --skip-build was passed but no web dist found at: ...`, the wrapper falls through to plain `hermes`, and every resident-channel wake for the session reports `hermes-missing-handle`.

Detection order for the hermes install root:

1. `AIFY_HERMES_INSTALL_ROOT` env (overrides everything; useful when `hermes` is symlinked to a non-canonical location)
2. `hermes config path` parsed up to `/hermes_cli/...` (the canonical Windows path is `~/AppData/Local/hermes/hermes-agent/hermes_cli/config.yaml`, so the install root is `~/AppData/Local/hermes/hermes-agent`)
3. Skip with a log line if neither resolves

The prebuild is idempotent — re-running `install.sh --client hermes` after web_dist exists logs `hermes web_dist already present at ...` and skips. Re-run is required only when hermes itself is upgraded.

### Fallback warning (added 2026-05-25)

When `hermes-aify` cannot start the dashboard gateway (port allocation failure, dashboard probe timeout, missing web_dist, token capture failure) or when `AIFY_HERMES_SKIP_GATEWAY=1` is set, it now prints a multi-line WARNING block before exec-ing plain `hermes`:

```
[hermes-aify] WARNING: AIFY_HERMES_GATEWAY_URL was NOT exported to this hermes session.
[hermes-aify]   Reason: <one of port_alloc_failed / dashboard_unreachable / token_capture_failed / gateway_disabled>
[hermes-aify]   Log:    ~/.local/state/aify-comms/hermes-aify-dashboard-<port>.log
[hermes-aify]   Effect: comms wake/dispatch to this agent will report 'hermes-missing-handle'.
[hermes-aify]   Fix:    re-run install.sh --client hermes to prebuild hermes web_dist, or
[hermes-aify]           inspect the dashboard log above for the underlying error.
```

Without this banner the fallback was silent and operators had no signal that their resident hermes wake-mode would never work. Current fallback still preserves an explicit `hermes-aify --resume <session-id>` by launching plain `hermes --tui --resume <session-id>` when the gateway path cannot start.

### Session continuity (stable pinned id)

Session continuity is deterministic, not discovered. The wrapper pins a stable
session id `aify-<agentId>` and launches `hermes --tui --resume aify-<agentId>`
(exported as `HERMES_TUI_RESUME`). Every dispatch resumes that same id, so the
agent's chat history is stable across wakes without negotiating which session
is "current." The old active-session-FILE discovery
(`HERMES_TUI_ACTIVE_SESSION_FILE` / `AIFY_HERMES_ACTIVE_SESSION_FILE`) and the
`session.most_recent` binding path are retired — they reported historical
Hermes DB state and could bind to a session that could not visibly receive
delivery. The aify-comms bridge now finds the live session via WS
`session.active_list` + `pickSessionForKey('aify-<agentId>')`.

If dispatch says `visible session not found`, the open terminal was started
with an old wrapper, with `AIFY_HERMES_DISABLE_PLUGIN=1`, or before the visible
TUI attached on the pinned id. Re-run `install.sh --client hermes`, restart that
`hermes-aify` terminal, and re-register from inside the same visible session.

## What This Installs

- The shared `aify-comms` local MCP server for Hermes.
- A Hermes MCP config entry in the active Hermes config file (`hermes config path`).
- The resident wrapper `hermes-aify`, which exports `AIFY_COMMS_URL` so shell hooks know which aify service to call and loads `integrations/hermes-aify-plugin` for Hermes runtime compatibility.
- A `pre_llm_call` shell hook (`~/.hermes/agent-hooks/aify-turn-start.sh`) that POSTs `/api/v1/agents/{id}/turn-start` to the aify service before each LLM call. Closest equivalent to claude-aify's `UserPromptSubmit` hook — flips the dashboard to `working` when the operator submits a prompt to hermes-aify. No matching turn-end shell hook exists upstream; the 120s server-side `turn_busy` stale window handles cleanup.
- With `--with-hook`, a non-blocking Hermes `post_tool_call` notification hook (separate from the turn-start hook above; this one is for incoming-message notifications).

Resident Hermes is terminal-first — `hermes-aify` opens an interactive Hermes
TUI for human use. Managed Hermes defaults to the same wrapper shape, but the
environment bridge owns the `hermes-aify` PTY and the dashboard Console renders
that real TUI. Native `HermesController` / ACP fallback remains available when
wrapper-backed delivery is disabled or unavailable.

## Native fallback ACP session (managed dispatches)

**Re-running `install.sh --client hermes` does NOT update an existing `aify-comms` block in `config.yaml`.** The installer's idempotency check (`install_hermes_config` in install.sh) exits early if the `aify-comms:` entry already exists under `mcp_servers:`. After upgrading aify-comms (e.g. to pick up new env-var propagation entries like `AIFY_HERMES_GATEWAY_URL`), you need to either:
- Manually edit `~/.hermes/config.yaml` (or `%LOCALAPPDATA%\hermes\config.yaml`) and add the new `env:` entries under the existing `aify-comms:` block
- OR delete the `aify-comms:` block entirely and rerun `bash install.sh --client hermes` to regenerate it

Current managed Hermes defaults to wrapper-backed `hermes-aify` PTY delivery
(`managed_via_wrapper=["codex","hermes"]`). The bridge owns the wrapper PTY,
the wrapper starts the local dashboard gateway, and the child bridge delivers
through `aify.session.bind_transport` plus `prompt.submit` / `session.steer`.
The rest of this section describes the native controller fallback used only
when wrapper-backed delivery is disabled or unavailable.

On the fallback path, the bridge spawns a single `hermes acp --accept-hooks`
per agentId on first dispatch, runs the ACP handshake (`initialize` →
`session/new`), and reuses the same `sessionId` for every subsequent
`session/prompt`. This gives native conversation continuity and token-level
streaming in a synthesized dashboard terminal, but it does not provide the
same visible TUI symmetry as wrapper-backed delivery.

- Default launcher: `hermes acp --accept-hooks` (looked up on PATH).
- Override: `AIFY_HERMES_ACP_COMMAND="/abs/path/to/hermes acp --accept-hooks"` (quote-aware, so `AIFY_HERMES_ACP_COMMAND='"C:\Program Files\hermes\hermes.exe" acp --accept-hooks'` works for paths-with-spaces)
  (or per-agent via `runtimeConfig.hermesAcpCommand`).
- Idle reaper: 24h by default. Override globally via
  `AIFY_HERMES_IDLE_TIMEOUT_MS` or per-agent via
  `runtimeConfig.hermesIdleTimeoutMs`.
- Handshake-startup window: 45s default; tune via
  `AIFY_HERMES_STARTUP_TIMEOUT_MS` or `runtimeConfig.startupTimeoutMs`.
- Resident hermes is unaffected — the operator-typed hermes still launches
  interactively under PTY.

**Filesystem callbacks are sandboxed to the agent's `cwd`.** When hermes requests `fs/read_text_file` or `fs/write_text_file`, the bridge resolves the path against the registered session `cwd` and refuses anything outside that tree with JSON-RPC error -32602. This prevents a compromised agent from reading `~/.ssh/id_rsa` or writing arbitrary host files through the ACP callback channel. Operators who genuinely need unrestricted access can set `AIFY_HERMES_FS_UNSAFE=1` to disable the containment check (explicit opt-out, not recommended).

**Permission auto-approve uses an allow-list.** The bridge auto-selects only options whose `kind` is `allow_once` or `allow_always`. If hermes presents only escalation-kind options, the bridge returns `outcome.cancelled` instead of picking option[0]. The hook lives in `_handleClientRequest` in `mcp/stdio/hermes-session.js` if you need a different policy.

To verify this fallback child is alive: open the agent's Console after a
managed dispatch whose command is `aify://virtual-rpc/hermes` — status should
go `available → working → available` while the same `hermes acp` PID stays up
between turns (`tasklist | findstr hermes` on Windows, `pgrep -f "hermes acp"`
on POSIX). A second dispatch reuses the same PID. The bridge declines
hermes's `terminal/*` callbacks (no in-bridge sandbox), so configure hermes
itself to use its own sandbox if you need tool-driven child processes.

See [docs/HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md) for the full
integration guide, hooks details, MCP config shape, resident mode, and current
limits.
