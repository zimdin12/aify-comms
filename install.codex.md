# Install For Codex

Use aify-comms when you want Slack-like coordination for coding agents: direct messages, channels, shared artifacts, and optional active dispatch.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client codex http://localhost:8800 --with-hook
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client codex --with-hook
```

Restart Codex after install.

After every update:

1. Restart Codex.
2. If you want visible live wakeups, start the session with `codex-aify`.
3. Re-register from that exact live Codex session.
4. Confirm with `comms_agent_info(agentId="...")`.

For the live-wake path, start Codex with:

```bash
codex-aify
```

That wrapper starts a local `codex app-server --listen ws://127.0.0.1:...`, launches the visible TUI with `codex --remote ...`, and records that shared app-server binding locally so aify can usually auto-discover the live thread, register the session as `codex-live`, and send resident turns back into the same visible session path.

Windows note:
- If you run the installer from Git Bash on Windows, it now installs both the Bash wrapper and a `codex-aify.cmd` shim, and it adds `%USERPROFILE%\\.local\\bin` to your user `PATH` so `codex-aify` can be launched from PowerShell or `cmd.exe`.
- If you install from WSL instead, the wrapper stays WSL-local. That is still the right setup for WSL Codex, but it does not create a native Windows launcher.

Recommended registration from inside `codex-aify`:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", cwd="C:/path/to/project", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
```

Fallback order if that does not flip to `codex-live`:

1. Drop `sessionHandle` + `appServerUrl`: `comms_register(..., runtime="codex")`.
2. Re-add `sessionHandle="$CODEX_THREAD_ID"` from the same session.
3. Add back `appServerUrl` when multiple `codex-aify` sessions run on the same machine or the wrapper was launched from a different directory than the `cwd` you registered.

### Windows `cwd` trap

Codex CLI is Rust-based and its path deserializer rejects Windows backslash paths with `Invalid request: AbsolutePathBuf deserialized without a base path`, which kills every dispatched run instantly. Always register with forward slashes:

```text
cwd="C:/Users/you/project"     # correct
cwd="C:\\Users\\you\\project"  # triggers the trap
```

The stdio bridge now normalizes `\` → `/` automatically at dispatch time, but you must **restart `codex-aify` after updating aify-comms** to load the fix. If you still see the error, the bridge is running stale code.

### Orphaned runs

If a dispatched run is stuck in `running` and the owning bridge has died (e.g. `codex-aify` crashed), `comms_run_interrupt` cannot reach it because the bridge is no longer polling for controls. Clear it manually:

```bash
curl -X PATCH http://localhost:8800/api/v1/dispatch/runs/<run_id> \
  -H "Content-Type: application/json" \
  -d '{"status":"cancelled","error":"Bridge died, orphaned run"}'
```

## WSL Note

- If Codex CLI lives in WSL, run the installer from WSL too.
- That keeps the registered `cwd` and `codex app-server` paths in the same Linux environment.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `comms_register` creates a resident session for messaging/presence and, for Codex, captures the live `thread.id` when available.
- If started with `codex-aify`, resident wakeups use the same WebSocket app-server as the visible TUI and show up as `codex-live`. The injected task and final answer both appear in the visible TUI — expected.
- Plain-text output stays local to that session and the dispatch record unless the agent explicitly sends a message.
- Plain `codex` (not `codex-aify`) falls back to `codex-thread-resume`, which resumes the stored thread through a separate hidden app-server.
- `comms_spawn_agent` creates a managed worker for detached/background execution.
- If the target is already busy, later dispatches from the same sender are merged into one pending buffered run that starts after the current run finishes instead of piling up as many separate queued runs. Inbox delivery still happens immediately.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If the owning stdio bridge is closed, queued resident/managed runs wait until that bridge reconnects. If the bridge crashes, see "Orphaned runs" above.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or managed workers.
- Default dispatch timeout is **2 hours** (per-agent override via `runtimeConfig.timeoutMs`).
- If another agent says you are a resident Codex session without a bound session handle, restart Codex and re-register from the live session.

## What This Installs

- The `aify-comms` stdio MCP server for Codex
- The aify skill in `$CODEX_HOME/skills/aify-comms`
- Optional unread-message hook notifications via `$CODEX_HOME/hooks.json`
- A `codex-aify` wrapper in `~/.local/bin`

Current Codex CLI note:
- The installer uses the current `codex mcp add ... --env ...` syntax.
- For hooks, Codex now reads `hooks.json` and requires `features.codex_hooks = true` in `config.toml`.
- The unread hook is installed for `PostToolUse` on `Bash`, which matches the current Codex hooks runtime.
- Resident triggering only works when the bridge talks to the same Codex installation/thread store that created the live session. A Windows desktop session and a WSL CLI session are different stores.
- `codex-aify` avoids the extra hidden-resume hop by pointing both the visible TUI and aify at the same local WebSocket app-server.

## Quick Start

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there", silent=true)
comms_inbox(agentId="my-agent")
```
