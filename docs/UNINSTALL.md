# Clean Uninstall

This project installs three separate things:

- the Docker service/dashboard and its data volume
- host-side bridge/wrapper scripts such as `aify-comms`, `codex-aify`, `claude-aify`, `hermes-aify`, `omp-aify`, and `pi-aify`
- MCP client config and skills for Claude Code, Codex, Hermes, OpenCode, or Oh My Pi

Remove only the parts you actually want gone.

## Stop Host Bridges

Linux/WSL:

```bash
pkill -f '/mcp/stdio/server.js' || true
pkill -f 'aify-comms' || true
```

Native Windows PowerShell:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'mcp[\\/]+stdio[\\/]+server\.js|aify-comms' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

If you started a bridge in a dedicated terminal, closing that terminal is also enough.

## Stop Or Remove The Docker Service

Keep data but stop containers:

```bash
docker compose down
```

Remove containers and the service data volume:

```bash
docker compose down -v
```

If `.env` uses the default project values from this repo, the volume name is `aify-comms-data`. Older local installs from the temporary bridge branch may use `aify-agents-bridge-data`. Verify before deleting manually:

```bash
docker volume ls | grep -E 'aify-comms|aify-agents-bridge'
```

Then, if needed:

```bash
docker volume rm aify-comms-data
# or, for older local installs:
docker volume rm aify-agents-bridge-data
```

## Remove Codex Integration

```bash
codex mcp remove aify-comms || true
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/aify-comms"
rm -f "$HOME/.local/bin/aify-comms" "$HOME/.local/bin/codex-aify"
rm -f "$HOME/.local/bin/aify-comms.cmd" "$HOME/.local/bin/codex-aify.cmd"
rm -rf "$HOME/.local/state/aify-comms"
```

If `install.sh --with-hook` was used, also remove the `notify-check.js` hook entry from:

```text
~/.codex/hooks.json
```

The installer enables `codex_hooks = true` globally. Leave that setting if other hooks use it; otherwise remove or set it to `false` in:

```text
~/.codex/config.toml
```

## Remove Claude Code Integration

```bash
claude mcp remove --scope user aify-comms || true
claude mcp remove --scope user aify-comms-channel || true
claude mcp remove --scope local aify-comms || true
claude mcp remove --scope project aify-comms || true
claude mcp remove --scope local aify-comms-channel || true
claude mcp remove --scope project aify-comms-channel || true
rm -rf "$HOME/.claude/skills/aify-comms"
rm -rf "$HOME/.claude/commands/aify-comms"
rm -f "$HOME/.local/bin/aify-comms" "$HOME/.local/bin/claude-aify"
rm -f "$HOME/.local/bin/aify-comms.cmd" "$HOME/.local/bin/claude-aify.cmd"
rm -rf "$HOME/.local/state/aify-comms"
```

If `install.sh --with-hook` was used, remove the `notify-check.js` hook entry from:

```text
~/.claude/settings.json
```

## Remove OpenCode Integration

OpenCode config is JSON. Remove the `mcp.aify-comms` entry from:

```text
${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json
```

Then remove the shared launcher/state if you no longer use it:

```bash
rm -f "$HOME/.local/bin/aify-comms" "$HOME/.local/bin/aify-comms.cmd"
rm -rf "$HOME/.local/state/aify-comms"
```

## Remove Hermes Integration

Hermes config is YAML. Remove the `mcp_servers.aify-comms` entry from:

```text
~/.hermes/config.yaml
```

If `install.sh --with-hook` was used, also remove the `hooks.post_tool_call` entry that points at:

```text
~/.hermes/agent-hooks/aify-notify.sh
```

Then remove the shared launcher/wrapper/state if you no longer use it:

```bash
rm -f "$HOME/.local/bin/aify-comms" "$HOME/.local/bin/hermes-aify"
rm -f "$HOME/.local/bin/aify-comms.cmd" "$HOME/.local/bin/hermes-aify.cmd"
rm -f "$HOME/.hermes/agent-hooks/aify-notify.sh"
rm -rf "$HOME/.local/state/aify-comms"
```

## Remove Oh My Pi Integration

Oh My Pi config is JSON. Remove the `mcpServers.aify-comms` entry from:

```text
~/.omp/agent/mcp.json
```

Then remove the shared launcher/wrapper/state if you no longer use it:

```bash
rm -f "$HOME/.local/bin/aify-comms" "$HOME/.local/bin/omp-aify" "$HOME/.local/bin/pi-aify"
rm -f "$HOME/.local/bin/aify-comms.cmd" "$HOME/.local/bin/omp-aify.cmd" "$HOME/.local/bin/pi-aify.cmd"
rm -rf "$HOME/.local/state/aify-comms"
```

## Native Windows Notes

When installed from Git Bash, wrappers are usually under:

```text
%USERPROFILE%\.local\bin
```

Remove:

```text
aify-comms
aify-comms.cmd
codex-aify
codex-aify.cmd
claude-aify
claude-aify.cmd
hermes-aify
hermes-aify.cmd
omp-aify
omp-aify.cmd
pi-aify
pi-aify.cmd
```

The installer may have added `%USERPROFILE%\.local\bin` to the user `Path`. Remove it from Windows environment variables only if no other tools there are needed.

## Verify Removal

```bash
curl http://192.168.100.10:8800/health
```

This should fail if the service is fully stopped.

Check no bridge is still running:

```bash
pgrep -af '/mcp/stdio/server.js|aify-comms' || true
```

Check the client no longer has the MCP server:

```bash
codex mcp list 2>/dev/null | grep aify-comms || true
claude mcp list 2>/dev/null | grep aify-comms || true
```
