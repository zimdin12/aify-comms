# Clean Uninstall

This project installs four separate things:

- the Docker service/dashboard and its data volume
- host-side wrapper scripts such as `aify-comms`, `aify-doctor`, `codex-aify`, `claude-aify` and `hermes-aify` (under `~/.local/bin`)
- the native bridge runtime that `install.sh` copies to `~/.aify-comms` (override base: `AIFY_HOME`)
- MCP client config, hooks and skills for Claude Code, Codex or Hermes

Older installs may also carry OpenCode or Oh My Pi config; the legacy sections below remove it.

Remove only the parts you actually want gone.

## Stop Host Bridges

Stop the agents first: stop managed agents from the dashboard and close resident `*-aify` terminals.
Then stop any leftover bridge by the path of the native bridge copy.

Linux/macOS/WSL:

```bash
pkill -f "${AIFY_HOME:-$HOME/.aify-comms}/mcp/stdio/" || true
```

Native Windows PowerShell:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match '\.aify-comms[\\/]+mcp[\\/]+stdio[\\/]' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Match the bridge path, never the bare string `aify-comms`: every Claude agent's command line contains
it (`claude --dangerously-load-development-channels server:aify-comms-channel`), so that pattern kills
every live agent.

If you started a bridge in a dedicated terminal, closing that terminal is also enough.

## Remove The Native Bridge Runtime

`install.sh` copies the host-side bridge (`mcp/stdio` + `node_modules`) into a
native dotfolder that every wrapper and MCP config points at. Remove it once all
clients are uninstalled:

```bash
rm -rf "${AIFY_HOME:-$HOME/.aify-comms}"
```

## Remove The Verifier, This Service's Registry Entry And Its Stored Key

`install.sh` writes these for every client, so they outlive a per-client uninstall.

```bash
rm -f "$HOME/.local/bin/aify-doctor"      # the verifier; `aify-comms doctor` is the same script
rm -f "$HOME/.local/bin/aify-doctor.cmd"  # Git Bash on Windows only
```

Left behind, that command still runs and points at a bridge you just deleted, so it reports failures
about a service that is gone.

The shared service registry needs an edit rather than a delete:

```bash
# ~/.aify/services.json is SHARED. Remove only the "aify-comms" key.
node -e 'const f=require("os").homedir()+"/.aify/services.json";const fs=require("fs");if(!fs.existsSync(f))process.exit(0);const j=JSON.parse(fs.readFileSync(f,"utf8"));delete (j.services||{})["aify-comms"];fs.writeFileSync(f,JSON.stringify(j,null,2)+"\n")'
```

**Do not delete that file.** Every service on the host keeps its entry there, and launchers read it to
learn which services exist — deleting it uninstalls the others from every launcher's point of view.
Leaving the aify-comms key in place is the milder failure: launchers keep being told this service
exists at an address that no longer answers.

When the service had an API key and aify-env was installed, `install.sh` also stored that key in
aify-env's credential store (`~/.aify/credentials`). Remove it with aify-env's own command:

```bash
aify-env credential remove --service aify-comms
```

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
for s in aify-comms aify-comms-debug aify-comms-install; do
  rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/$s"
done
rm -f "$HOME/.local/bin/codex-aify" "$HOME/.local/bin/codex-aify.cmd"
```

The installer adds turn hooks to `~/.codex/hooks.json` on every Codex install: the `UserPromptSubmit`,
`Stop`, `Interrupt`, `PermissionRequest` and `PostToolUse` entries whose command runs
`agent-state-event.mjs`. With `--with-hook` it also adds a `PostToolUse` entry that runs
`notify-check.js`. Remove those entries and keep any others:

```text
~/.codex/hooks.json
```

It also writes `hooks = true` under `[features]` in `~/.codex/config.toml`, plus a
`[hooks.state.'…']` trust entry for each hook it wrote. Remove the trust entries that name
`hooks.json`'s aify hooks; leave `hooks = true` if other hooks use it, otherwise remove it or set it
to `false`:

```text
~/.codex/config.toml
```

The shared launcher and state go only when you are removing the last client (see below).

## Remove Claude Code Integration

```bash
claude mcp remove --scope user aify-comms || true
claude mcp remove --scope user aify-comms-channel || true
claude mcp remove --scope local aify-comms || true
claude mcp remove --scope project aify-comms || true
claude mcp remove --scope local aify-comms-channel || true
claude mcp remove --scope project aify-comms-channel || true
for s in aify-comms aify-comms-debug aify-comms-install; do
  rm -rf "$HOME/.claude/skills/$s"
done
rm -rf "$HOME/.claude/commands/aify-comms"
rm -f "$HOME/.local/bin/claude-aify" "$HOME/.local/bin/claude-aify.cmd"
```

The installer always adds turn hooks to `~/.claude/settings.json`: the entries whose command runs
`agent-state-event.mjs` or `claude-stop-gate.js` (`UserPromptSubmit`, `Stop`, `StopFailure`,
`PermissionRequest`, `PostToolUse` and `SessionStart` with matcher `compact`). With `--with-hook` it
also adds a `notify-check.js` entry. Remove those entries and keep any others:

```text
~/.claude/settings.json
```

The shared launcher and state go only when you are removing the last client (see below).

## Remove Hermes Integration

Every Hermes path below is under the Hermes config root: the directory that `hermes config path`
prints the `config.yaml` of. On native Windows that is often `%LOCALAPPDATA%\hermes` rather than
`~/.hermes`.

In `config.yaml` (YAML), remove:

- the `mcp_servers.aify-comms` entry;
- `aify-comms` from `plugins.enabled` (or run `hermes plugins disable aify-comms`);
- the hook entries whose command runs `agent-hooks/aify-turn-start.sh`, `aify-turn-end.sh`,
  `aify-blocked.sh` or `aify-unblocked.sh` (under `pre_llm_call`, `on_session_end`,
  `pre_approval_request` and `post_approval_response`);
- with `--with-hook`, the `hooks.post_tool_call` entry that runs `agent-hooks/aify-notify.sh`.

Then remove the files the installer wrote there (set `HERMES_ROOT` to the config root first):

```bash
rm -rf "$HERMES_ROOT/plugins/aify-comms"
rm -f "$HERMES_ROOT/agent-hooks/aify-turn-start.sh" "$HERMES_ROOT/agent-hooks/aify-turn-end.sh"
rm -f "$HERMES_ROOT/agent-hooks/aify-blocked.sh" "$HERMES_ROOT/agent-hooks/aify-unblocked.sh"
rm -f "$HERMES_ROOT/agent-hooks/aify-notify.sh"
for s in aify-comms aify-comms-debug aify-comms-install; do
  rm -rf "${HERMES_HOME:-$HOME/.hermes}/skills/autonomous-ai-agents/$s"
done
rm -f "$HOME/.local/bin/hermes-aify" "$HOME/.local/bin/hermes-aify.cmd"
rm -f "$HOME/.local/bin/hermes-aify.ps1"   # hermes is the only client with a PowerShell launcher
```

The skills go under `HERMES_HOME` (default `~/.hermes`), which is the one path here that does not
follow `hermes config path`.

The shared launcher and state go only when you are removing the last client (see below).

## Remove The Shared Launcher And State (Last Client Only)

`aify-comms` and the state directory are shared by every client. Remove them only when no client is
left:

```bash
rm -f "$HOME/.local/bin/aify-comms" "$HOME/.local/bin/aify-comms.cmd"
rm -rf "$HOME/.local/state/aify-comms"
```

## Legacy Cleanup: OpenCode

OpenCode is not supported and current installers do not configure it. On a host an older installer
set up, remove the `mcp.aify-comms` entry from:

```text
${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json
```

## Legacy Cleanup: Oh My Pi

Pi is deprecated and current installers refuse the Pi wrapper. On a host an older installer set up,
remove the `mcpServers.aify-comms` entry from:

```text
~/.omp/agent/mcp.json
```

and the old wrappers:

```bash
rm -f "$HOME/.local/bin/omp-aify" "$HOME/.local/bin/pi-aify"
rm -f "$HOME/.local/bin/omp-aify.cmd" "$HOME/.local/bin/pi-aify.cmd"
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
aify-doctor
aify-doctor.cmd
codex-aify
codex-aify.cmd
claude-aify
claude-aify.cmd
hermes-aify
hermes-aify.cmd
hermes-aify.ps1
```

and, from an older install, `omp-aify`, `omp-aify.cmd`, `pi-aify` and `pi-aify.cmd`.

The installer may have added `%USERPROFILE%\.local\bin` to the user `Path`. Remove it from Windows environment variables only if no other tools there are needed.

## Verify Removal

```bash
curl http://localhost:8800/health     # or the address you installed against
```

This should fail to connect once the service is stopped. **Use the address you actually used.** This
line said `192.0.2.10` — a documentation-range address that is unroutable from everywhere — so it
failed identically whether the service was stopped or still serving, which is a check that cannot
distinguish the thing it was written to distinguish.

Check no bridge is still running:

```bash
pgrep -af "${AIFY_HOME:-$HOME/.aify-comms}/mcp/stdio/" || true
```

Check the verifier and the registry entry are gone:

```bash
command -v aify-doctor || echo "aify-doctor removed"
node -e 'const f=require("os").homedir()+"/.aify/services.json";const fs=require("fs");if(!fs.existsSync(f)){console.log("no registry on this host")}else{const k=Object.keys(JSON.parse(fs.readFileSync(f,"utf8")).services||{});console.log(k.includes("aify-comms")?"STILL REGISTERED":"aify-comms unregistered; other services kept: "+k.join(", "))}'
```

Check the client no longer has the MCP server:

```bash
codex mcp list 2>/dev/null | grep aify-comms || true
claude mcp list 2>/dev/null | grep aify-comms || true
```
