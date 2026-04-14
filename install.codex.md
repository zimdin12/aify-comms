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

Recommended registration from inside `codex-aify`:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex")
```

If that still reports `message-only` or does not flip to `codex-live`, use the deterministic fallback from that same session:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", sessionHandle="$CODEX_THREAD_ID")
```

## WSL Note

- If Codex CLI lives in WSL, run the installer from WSL too.
- That keeps the registered `cwd` and `codex app-server` paths in the same Linux environment.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `comms_register` creates a resident session for messaging/presence and, for Codex, captures the live `thread.id` when available.
- If the session was started with `codex-aify`, resident Codex wakeups use the same WebSocket app-server as the visible TUI and show up as `codex-live`.
- In `codex-live`, the injected task and the final answer will appear in the visible Codex session. That is expected. Plain-text output stays local to that session and the dispatch record unless the agent explicitly sends a message.
- If `codex-aify` is running but `comms_agent_info(...)` still does not show `codex-live`, re-register once more with `runtime="codex"`. If the live thread still is not auto-detected, pass `sessionHandle="$CODEX_THREAD_ID"` explicitly from that same session.
- If the session was started with plain `codex`, resident Codex still falls back to `codex-thread-resume`, which resumes the stored thread through a separate hidden app-server.
- `comms_spawn_agent` still creates a managed worker for detached/background execution and long-lived worker state.
- If the owning stdio bridge is closed, queued resident/managed runs wait until that bridge reconnects.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or managed workers, and they cannot launch local work themselves.
- If another agent says you are a resident Codex session without a bound session handle, restart Codex and re-register from the live session. That usually means the current registration predates the latest resident-triggering flow or was done from the wrong environment.

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
comms_register(agentId="my-agent", role="coder", runtime="codex")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there", silent=true)
comms_inbox(agentId="my-agent")
```
