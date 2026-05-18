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

## What This Installs

- The shared `aify-comms` local MCP server for Hermes.
- A Hermes MCP config entry in `~/.hermes/config.yaml`.
- The resident wrapper `hermes-aify`.
- With `--with-hook`, a non-blocking Hermes `post_tool_call` notification hook.

Hermes is terminal-first. Managed dashboard Hermes sessions use the shared PTY
path; opening Console attaches to that live terminal, and Messenger delivery is
sent through the managed PTY when Console is open or closed.

See [docs/HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md) for the full
integration guide, hooks details, MCP config shape, resident mode, and current
limits.
