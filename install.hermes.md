# Install For Hermes

Use aify-comms when you want dashboard-driven coordination for Hermes Agent:
live direct messages, channels, shared artifacts, active dispatch, managed
agent spawn, browser Console, and environment control.

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

On native Windows from PowerShell/cmd use `aify-comms.cmd`. The current
directory is always an allowed workspace root; extra root arguments are optional
safety boundaries, not the per-agent project choice.

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

## Session-mode flag

`hermes-aify` accepts `--resident` and `--managed`. Precedence: inherited
`AIFY_SESSION_MODE` env wins (bridge-spawned managed PTYs set it to `managed`);
else the flag; else TTY auto-detect via `[ -t 0 ]` — interactive defaults to
`resident`, non-TTY to `managed`. Use the explicit flag only when TTY detection
might be wrong for your shell context.

## Delivery path

Managed-hermes dispatches flow through the bridge's `createHermesController`
native RPC adapter. For each dispatch the bridge spawns
`hermes chat -Q -q "<built prompt>"` — upstream's documented "Programmatic
mode" (`-Q` suppresses the banner/spinner/tool previews so the stdout is
just the agent's reply text). `--yolo` is added by default for managed runs
so unattended approval prompts don't stall the turn. There is no visible
wrapper PTY for managed dispatches under this path.

Operator visibility comes from a synthesized `terminal_session` row tied
to the agent (`command='aify://virtual-rpc/hermes'`). Each dispatch echoes
the request as `> [dashboard] <subject>` plus body lines, a `[hermes]
thinking...` marker while the spawn runs, and the captured reply when it
arrives. The dashboard's Console pane attaches to this synthesized stream
the same way it attaches to managed Pi's `aify://virtual-rpc/pi` virtual
terminal (Phase 2 architecture).

Conversation context across turns is carried in the wire prompt
(`buildUserPrompt` includes recent `conversationContext` from aify-comms)
rather than via `--resume`, because upstream Hermes does not yet support
`-q` combined with session resume. This is the same shape codex uses for
its per-dispatch `createCodexController` path.

Mid-turn steering is not supported (`hermes chat -q` is single-shot);
`comms_run_steer` rejects with a clear message. Send a follow-up dispatch
instead — the next-turn prompt carries the prior context automatically.

The bridge does NOT depend on aify-comms loading as an MCP server inside
the hermes session for delivery. So `hermes-aify` does NOT require the
`--strict-mcp-config` + minimal-MCP isolation that `claude-aify` needs to
work around the Claude Code stdio MCP race bug.

### Resident dispatch delivery (operator-launched `hermes-aify`)

`hermes-aify` runs the operator's real Ink terminal TUI for `hermes chat`, AND it accepts bridge-injected aify-comms messages mid-conversation. The mechanism mirrors `codex-aify`:

1. The wrapper spawns `hermes dashboard --tui --port <P> --host 127.0.0.1 --no-open --skip-build` as a hidden background child. This sets `_DASHBOARD_EMBEDDED_CHAT_ENABLED=True` in `hermes_cli/web_server.py`, which mounts the `/api/ws` JSON-RPC endpoint at the `tui_gateway/server.py` dispatcher.
2. The wrapper fetches `http://127.0.0.1:<P>/` and parses the ephemeral `__HERMES_SESSION_TOKEN__` from the injected `<script>` tag (`web_server.py:3688`).
3. It exports `HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<P>/api/ws?token=<T>` in the env passed to `hermes chat --tui`. The Ink TUI's `gatewayClient.ts:startAttachedGateway` opens a WebSocket to that URL instead of spawning its own stdio sidecar — operator sees their normal terminal TUI experience.
4. The aify-comms bridge (loaded inside `hermes chat` as an MCP server) ALSO opens a WebSocket to the same `/api/ws` (it reads `AIFY_HERMES_GATEWAY_URL` from env, written into the hermes runtime marker by `server.js`). For inbound aify-comms messages the bridge calls JSON-RPC `prompt.submit` (idle session) or `session.steer` (mid-run injection, when `prompt.submit` returns code 4009 "session busy"). `tui_gateway/transport.py::TeeTransport` mirrors dispatcher events back to BOTH attached clients, so the operator's TUI renders the injected user turn AND the model's reply live.

This is the symmetric equivalent of Claude Code's `notifications/claude/channel` delivery and the codex resident `turn/start` path — same wrapper-spawned-daemon + transport-pluggable-TUI + bridge-as-second-client shape. No upstream patches required.

**Mid-run insertion (`session.steer`)** is a first-class primitive on the hermes side: text lands on the last tool result of the next tool batch and the model sees it on its next iteration. No interrupt, no role-alternation violation.

**Bypass:** set `AIFY_HERMES_SKIP_GATEWAY=1` to fall back to plain `hermes` exec without the dashboard child. Use this if the dashboard probe is breaking your install and you don't need resident bridge-injection.

**Cleanup:** `trap cleanup_aify_dashboard EXIT INT TERM` in the wrapper kills the dashboard child on wrapper exit, so `hermes-aify`'s lifecycle owns the dashboard process. Background dashboard logs go to `$XDG_STATE_HOME/aify-comms/hermes-aify-dashboard-<port>.log` (or `~/.local/state/aify-comms/...` on systems without XDG_STATE_HOME).

**Known limitations.** The dashboard binds to 127.0.0.1 only and uses ephemeral per-process tokens — it's safe to leave running. The `--skip-build` flag relies on hermes having already built the web UI dist once; if you never ran `hermes dashboard` before, the first wrapper invocation may need to build it (skip the flag, or run `hermes dashboard --no-open` once to prime).

## What This Installs

- The shared `aify-comms` local MCP server for Hermes.
- A Hermes MCP config entry in the active Hermes config file (`hermes config path`).
- The resident wrapper `hermes-aify`, which exports `AIFY_COMMS_URL` so shell hooks know which aify service to call.
- A `pre_llm_call` shell hook (`~/.hermes/agent-hooks/aify-turn-start.sh`) that POSTs `/api/v1/agents/{id}/turn-start` to the aify service before each LLM call. Closest equivalent to claude-aify's `UserPromptSubmit` hook — flips the dashboard to `working` when the operator submits a prompt to hermes-aify. No matching turn-end shell hook exists upstream; the 120s server-side `turn_busy` stale window handles cleanup.
- With `--with-hook`, a non-blocking Hermes `post_tool_call` notification hook (separate from the turn-start hook above; this one is for incoming-message notifications).

Resident Hermes is terminal-first — `hermes-aify` opens an interactive `hermes
chat` session for human use. Managed Hermes is driven by `createHermesController`
in the bridge, which keeps a long-lived `hermes acp` JSON-RPC child per agent
(see `mcp/stdio/hermes-session.js`) and streams `session/update` notifications
into the dashboard Console — no visible wrapper PTY is needed for delivery.

## Persistent ACP session (managed dispatches)

For managed hermes agents the bridge spawns a single `hermes acp --accept-hooks`
per agentId on first dispatch, runs the ACP handshake (`initialize` →
`session/new`), and reuses the same `sessionId` for every subsequent
`session/prompt`. This gives native conversation continuity and token-level
streaming.

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

To verify the persistent child is alive: open the agent's Console after a
managed dispatch — status should go `available → working → available` while
the same `hermes acp` PID stays up between turns (`tasklist | findstr hermes`
on Windows, `pgrep -f "hermes acp"` on POSIX). A second dispatch reuses the
same PID. The bridge declines hermes's `terminal/*` callbacks (no in-bridge
sandbox), so configure hermes itself to use its own sandbox if you need
tool-driven child processes.

See [docs/HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md) for the full
integration guide, hooks details, MCP config shape, resident mode, and current
limits.
