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

## What This Installs

- The shared `aify-comms` local MCP server for Hermes.
- A Hermes MCP config entry in the active Hermes config file (`hermes config path`).
- The resident wrapper `hermes-aify`.
- With `--with-hook`, a non-blocking Hermes `post_tool_call` notification hook.

Resident Hermes is terminal-first — `hermes-aify` opens an interactive `hermes
chat` session for human use. Managed Hermes is now driven by `createHermesController`
(per-dispatch `hermes chat -Q -q` with a synthesized terminal feed in the
dashboard Console) and does NOT need a visible wrapper PTY for delivery. See the
"Delivery path" section above and [docs/HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md).

See [docs/HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md) for the full
integration guide, hooks details, MCP config shape, resident mode, and current
limits.
