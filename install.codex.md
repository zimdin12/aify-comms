# Install For Codex

Use aify-comms when you want dashboard-driven coordination for coding agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

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

For dashboard-managed spawns, also connect an environment bridge on the machine that should run Codex. The installer adds the `aify-comms` launcher for this:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://localhost:8800`; the current directory is always an allowed workspace root; extra root arguments are optional. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The installer configures Codex's MCP client; the environment bridge is the long-running host process started with `--environment-bridge`, heartbeats into the dashboard, and claims spawn requests.

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
- If you run the installer from Git Bash on Windows, it installs Bash wrappers plus `.cmd` shims in `%USERPROFILE%\.local\bin`, including `aify-comms.cmd` and `codex-aify.cmd`, and adds that directory to your user `PATH`.
- Open a new PowerShell after install. If `aify-comms.cmd` is still not recognized, run `$env:Path += ";$env:USERPROFILE\.local\bin"` for the current window or launch it directly with `& "$env:USERPROFILE\.local\bin\aify-comms.cmd"`.
- If you install from WSL instead, the wrapper stays WSL-local. That is still the right setup for WSL Codex, but it does not create a native Windows launcher.

Recommended registration from inside `codex-aify`:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", cwd="<native-path-to-project>", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
```

Use a native path for the runtime you are actually running:
- WSL/Linux Codex: `/mnt/...` or other native Linux paths
- native Windows Codex: `C:/...` with forward slashes

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

### If things go wrong

Troubleshooting lives in the **aify-comms-debug** skill (loaded automatically alongside the main skill). It covers:

- `AbsolutePathBuf deserialized without a base path` and the full hard-reset sequence
- Stuck `running` dispatches (orphaned runs) and how to cancel them via the API
- not live-bound when you expected `codex-live`
- live-send rejections, stale bridge claims, and more

If the debug skill isn't loaded in your session, see `.claude/skills/aify-comms-debug/SKILL.md` in this repo.

## WSL Note

- If Codex CLI lives in WSL, run the installer from WSL too.
- That keeps the registered `cwd` and `codex app-server` paths in the same Linux environment.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `comms_register` creates a resident session for messaging/presence and, for Codex, captures the live `thread.id` when available.
- If started with `codex-aify`, resident wakeups use the same WebSocket app-server as the visible TUI and show up as `codex-live`. The dispatched sender message and final answer both appear in the visible TUI — expected.
- `comms_send` is the normal teamwork path and is live-delivery gated. If the target is offline, stale, stopped, already working, already has queued work, or lacks a live wake path, no message is written. Agent-reported blocked/completed states are status notes, not delivery blockers.
- `comms_dispatch` is the explicit tracked-run/debug path. When you dispatch, it still arrives as a sender message and also opens tracked run state with reply handoff by default.
- Explicit threaded replies are preferred for agent-to-agent work, but a reply-dispatch back to the sender also satisfies the handoff. If the comms tool path is blocked or stalls, managed prompts allow final plain text as the fallback handoff; the bridge mirrors it only when no real reply handoff was recorded and best-effort wakes the original sender when it is startable.
- Plain `codex` (not `codex-aify`) falls back to `codex-thread-resume`, which resumes the stored thread through a separate hidden app-server.
- `comms_spawn` creates a persistent environment-backed agent session. Use `comms_envs` first when you need to choose a host/workspace.
- Normal `comms_send` does not append to future queues. Advanced dispatch/run-control APIs may still expose queued runs for already-created work; clear stale queued runs before using chat.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If an environment bridge is killed, managed teammates backed by it become offline/detached and active sessions become lost; chats, identities, spawn specs, and session records remain. Restart the bridge, or assign the teammate to another online environment from **Team**, then recover/restart from **Sessions**. If a resident `codex-aify` wrapper is closed, that resident session is no longer live-wakeable until it is restarted and re-registered.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or environment-backed agents.
- Managed runtime hard timeout is **12 hours** by default (`runtimeConfig.timeoutMs`). Managed Codex also has a quiet-stall watchdog of **30 minutes** without Codex runtime notifications/stderr after the last observed activity (`runtimeConfig.quietTimeoutMs` or `runtimeConfig.silenceTimeoutMs`). Set the quiet timeout to `0` only for agents expected to run very long silent commands.
- If another agent says you are a resident Codex session without a bound session handle, restart Codex and re-register from the live session.

## What This Installs

- The `aify-comms` stdio MCP server for Codex (tool namespace retained for compatibility)
- The aify skill in `$CODEX_HOME/skills/aify-comms`
- Optional unread-message hook notifications via `$CODEX_HOME/hooks.json`
- An `aify-comms` environment bridge launcher in `~/.local/bin`
- A `codex-aify` wrapper in `~/.local/bin`

Current Codex CLI note:
- The installer uses the current `codex mcp add ... --env ...` syntax.
- For hooks, Codex now reads `hooks.json` and requires `features.codex_hooks = true` in `config.toml`.
- The unread hook is installed for `PostToolUse` on `Bash`, which matches the current Codex hooks runtime.
- Re-running the installer removes stale duplicate aify unread-hook entries, even if an older install used a different repo path.
- Resident triggering only works when the bridge talks to the same Codex installation/thread store that created the live session. A Windows desktop session and a WSL CLI session are different stores.
- `codex-aify` avoids the extra hidden-resume hop by pointing both the visible TUI and aify at the same local WebSocket app-server.

## Quick Start

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
comms_inbox(agentId="my-agent", mode="headers")
comms_inbox(agentId="my-agent", messageId="<message id>")
```
