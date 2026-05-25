# Install For Codex

Use aify-comms when you want dashboard-driven coordination for coding agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client codex http://192.168.100.10:8800 --with-hook
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

On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://192.168.100.10:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. `aify-comms --help` shows usage and unknown flag-like arguments are rejected instead of becoming roots. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The installer configures Codex's MCP client; the environment bridge is the long-running host process started with `--environment-bridge`, heartbeats into the dashboard, and claims spawn requests.

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

Add `-auto` when you want the visible resident Codex session to bypass approvals/sandbox prompts:

```bash
codex-aify -auto
```

The wrapper removes `-auto` before launching Codex and adds the best permission flag supported by the installed Codex CLI.

### Session-mode flag

`codex-aify` accepts `--resident` and `--managed`. Precedence: inherited `AIFY_SESSION_MODE` env wins (bridge-spawned managed PTYs set it to `managed`); else the flag; else TTY auto-detect via `[ -t 0 ]` — interactive defaults to `resident`, non-TTY to `managed`. Use the explicit flag only when TTY detection might be wrong for your shell context (most operators never need it).

### Delivery path

Managed-codex dispatches flow through the bridge's `createCodexController` native RPC adapter — the bridge connects to the codex app-server (over WebSocket) and drives turns directly. The bridge does NOT need an aify-comms MCP server inside the codex CLI session for delivery to work. This means `codex-aify` does NOT require the `--strict-mcp-config` + minimal-MCP isolation that `claude-aify` needs to work around the [Claude Code stdio MCP race bug](https://github.com/anthropics/claude-code/issues/38462). Your codex MCP servers (whatever you have configured in `~/.codex/config.toml` or equivalent) load normally inside `codex-aify`.

Managed codex also surfaces a synthesized `terminal_session` (`command='aify://virtual-rpc/codex'`, `runtime_state.virtualTerminal=true`) that the dashboard's Console pane attaches to. Each dispatch pushes per-event frames: `▶ turn started`, `→ <itemType>` (tool/command/etc. started, yellow) → `✓ <itemType>` (completed, green), agentMessage deltas as raw text, `■ turn ended` with token usage (`in=N out=M` when the app-server reports them), `✗ error` red on failure. Operator-visible Console activity matches the pi/hermes shape. The controller stays per-dispatch (one app-server connection per turn) — the full persistent-worker pool refactor is Phase 5 of `docs/plans/persistent-worker-status-taxonomy.md`, deferred.

### Resident dispatch delivery (incoming aify-comms messages)

When another agent sends `comms_send(to="<this-codex-agent>", ...)` (or `comms_dispatch(...)`) while this codex session is running resident under `codex-aify`, the bridge delivers the message by calling JSON-RPC `turn/start` against the per-instance `codex app-server` on the resident's active `threadId` — the same app-server the wrapper launched at startup (install.sh:319-330) and that your `codex --remote ws://...` TUI is already connected to. This is the symmetric equivalent of Claude's `notifications/claude/channel` delivery, but uses native codex JSON-RPC primitives (no MCP notification extension required).

**Known limitation — codex issue [#15320](https://github.com/openai/codex/issues/15320):** when an external client posts `turn/start` against a thread that a `--remote` TUI is attached to, the TUI does **not** render the externally-injected user turn live; the thread history fixes up later, but the operator may not see the wake event in the TUI itself. To make resident wake events visible regardless, the bridge also pushes synth-terminal frames into the dashboard Console pane — same frames it pushes for managed dispatches (prompt echo, `▶ turn started`, agent message deltas, `■ turn ended`). Use the dashboard Console at `http://localhost:8800` as the source of truth for resident-codex wake events. If your codex version ships with the community patch for #15320 (or upstream resolution), the `--remote` TUI will render injected turns live and the dashboard Console becomes a redundant second view.

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
- `codex-aify -auto` adds `--dangerously-bypass-approvals-and-sandbox`. The wrapper does not use the older `--full-auto` alias. Without `-auto`, `codex-aify` preserves normal visible CLI permission behavior.
- `comms_send` is the normal teamwork and reply path. It is live-delivery gated for offline/stale/stopped/no-wake targets; those sends are not stored. Busy steer-capable targets receive ordinary sends as current-run steer. Busy live targets that cannot steer queue/merge as next-turn work. Use `queueIfBusy=true` only when you intentionally want next-turn delivery even if steering is available. Agent-reported blocked/completed states are status notes, not delivery blockers.
- `comms_dispatch` is the explicit tracked-run/debug path. When you dispatch, it still arrives as a sender message and also opens tracked run state with reply handoff by default.
- Delivered dashboard-managed runs should answer the current message in final plain text. The bridge captures and stores/threads that final answer into chat. Treat each message as a small contract and do not rely on stdout/logs/tool output/run summaries as the team-visible answer. Use `comms_send(...)` from managed runs only for separate out-of-band/proactive messages or to schedule the next owner/self-wake; resident/live CLI sessions should still reply to inbox messages with `comms_send(type="response", inReplyTo=...)`.
- Keep team messages focused: one ask/result/blocker/status per message. When truth or history matters, check inbox/run/files first and say what was checked. Split unrelated topics instead of carrying them in one thread.
- Plain `codex` (not `codex-aify`) falls back to `codex-thread-resume`, which resumes the stored thread through a separate hidden app-server.
- `comms_spawn` creates a persistent environment-backed agent session. Use `comms_envs` first when you need to choose a host/workspace.
- Normal `comms_send` does not store messages for unreachable targets. Busy live targets may steer or queue/merge; stale queued/running work should still be cleared from Runs/Sessions before using chat.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If an environment bridge is killed, managed agents backed by it become offline/detached and active sessions become lost; chats, identities, spawn specs, and session records remain. Restart the bridge, or assign the agent to another online environment from **Agents**, then restart from **Sessions**. If a resident `codex-aify` wrapper is closed, that resident session is no longer live-wakeable until it is restarted and re-registered.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or environment-backed agents.
- Managed Codex model is blank by default, which lets the installed Codex runtime choose its default/latest model. Managed Codex defaults to `high` reasoning effort. Configure global defaults in Dashboard **Settings -> Runtime**. The normal dashboard does not tune model/effort per agent. Repo fallback lives in `mcp/stdio/runtimes.js` (`managedCodexConfigText`) and `mcp/stdio/controllers/codex-controller.js` (Plan 3 RuntimeAdapter — controller class extracted from the previous `createCodexController` factory).
- Managed runtime hard timeout is **12 hours** by default (`runtimeConfig.timeoutMs`). Managed Codex uses Codex's unattended bypass sandbox profile by default (`danger-full-access`, equivalent to `--dangerously-bypass-approvals-and-sandbox`) so managed agents can call MCP tools without hidden approval cancellation; set `runtimeConfig.sandboxMode="workspace-write"` only for permission debugging. Managed Codex also has a quiet-stall watchdog of **30 minutes** without Codex runtime notifications/stderr after the last observed activity (`runtimeConfig.quietTimeoutMs` or `runtimeConfig.silenceTimeoutMs`). A narrower aify-comms MCP tool-call watchdog fails stuck `mcpToolCall aify-comms` turns after **90 seconds** by default (`runtimeConfig.mcpToolTimeoutMs` or `runtimeConfig.commsToolTimeoutMs`; set to `0` only for debugging). Current WSL/Linux bridge builds terminate the whole managed Codex process tree on timeout/interrupt/stop so orphan MCP tool servers do not keep stale state alive. Set the quiet timeout to `0` only for agents expected to run very long silent commands.
- If another agent says you are a resident Codex session without a bound session handle, restart Codex and re-register from the live session.

## What This Installs

- The `aify-comms` stdio MCP server for Codex (tool namespace retained for compatibility)
- The aify skill in `$CODEX_HOME/skills/aify-comms`
- Optional unread-message hook notifications via `$CODEX_HOME/hooks.json`
- `UserPromptSubmit` + `Stop` hooks in `$CODEX_HOME/hooks.json` that POST `/api/v1/agents/{id}/turn-start` and `/turn-end` to the aify service. Symmetric with claude-aify's hooks — direct CLI typing flips status to `working`, end-of-turn flips it back. Codex's hooks.json schema accepts these events; inert on CLI versions that don't yet recognize them.
- An `aify-comms` environment bridge launcher in `~/.local/bin`
- A `codex-aify` wrapper in `~/.local/bin` that exports `AIFY_COMMS_URL` so the turn hooks know which aify service to call

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

## Persistent app-server (managed dispatches)

For managed codex agents (no `appServerUrl` set), the bridge spawns one `codex app-server` per agentId on first dispatch and reuses it across turns. Mirror of the persistent ACP path that managed hermes uses. Benefits: no per-turn spawn cost, native conversation continuity via the cached `threadId`, one PID per agent that the dashboard can surface.

- Default launcher: platform-native (`wsl.exe -e codex app-server` on Windows, `codex app-server` on POSIX). Override with `AIFY_CODEX_COMMAND="/abs/path/to/codex app-server"` if your binary isn't on PATH or you want to point the bridge at a wrapper script. (The fake test fixture uses this same env var.) The override is quote-aware so paths-with-spaces work: `AIFY_CODEX_COMMAND='"C:\Program Files\codex\codex.exe" app-server'`.
- **Fresh-context behavior:** if you trigger a fresh context (Dashboard → Sessions → Recreate, which writes a new `sessionHandle`), the next dispatch detects the threadId mismatch against the running CodexSession, tears the session down with a clear error (`threadId hint mismatch`), and the dispatch-after-that spawns a fresh `codex app-server` on the new thread. Operator-visible as one "failed" dispatch in the trail, then normal behavior resumes.
- Idle reaper: 24h default. Override globally via `AIFY_CODEX_IDLE_TIMEOUT_MS` or per-agent via `runtimeConfig.codexIdleTimeoutMs`.
- Handshake timeout: 60s default; tune via `AIFY_CODEX_STARTUP_TIMEOUT_MS` or `runtimeConfig.startupTimeoutMs`.
- The resident path (with a real WebSocket `codexAppServerUrl`) is unchanged — that's already pooled at the app-server process level.

Verify: open the agent's Console after a managed dispatch — `tasklist | findstr codex` (Windows) or `pgrep -f "codex app-server"` (POSIX) should show one PID that survives a second dispatch. The same threadId persists, so the conversation accumulates context turn-over-turn natively (no wire-prompt context-carry needed).

## Codex session storage layout

`codex-aify` probes `~/.codex/sessions/` for a saved session matching `--resume <id>`. Plan 4 (2026-05-25) supports three layouts in priority order:

1. **Flat** — `~/.codex/sessions/<id>.jsonl` (legacy or simple installs)
2. **Dir-per-session** — `~/.codex/sessions/<id>/...` (alternative codex versions)
3. **Date-sharded** — `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-timestamp>-<id>.jsonl` (current codex default — verified 2026-05-25 in WSL `Ubuntu`)

If none match, the wrapper falls through to fresh codex with a clear stderr message instead of crashing. The bridge's `mcp/stdio/controllers/codex-controller.js` mirrors this probe.

If your codex stores sessions elsewhere (e.g. custom `CODEX_HOME`), the wrapper won't auto-detect — file a feature request or set `AIFY_CODEX_SESSIONS_DIR` env (planned future enhancement).
