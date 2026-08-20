# Install For Claude Code

Use aify-comms when you want dashboard-driven coordination for coding agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Copy-Paste Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/.claude/plugins/aify-comms
cd ~/.claude/plugins/aify-comms
bash install.sh --client claude http://localhost:8800 --with-hook
```

You do **not** need anything else. `install.sh` builds `claude-aify` from templates that come from
[zimdin12/aify-wrapper](https://github.com/zimdin12/aify-wrapper), which arrives as a normal npm
dependency of the bridge during the install you just ran. Installing that package yourself points a
coding-agent CLI at some OTHER coordinating service; doing it alongside this gives you a second copy
of the same launcher, not more harnesses.

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/.claude/plugins/aify-comms
cd ~/.claude/plugins/aify-comms
bash install.sh --client claude --with-hook
```

Restart Claude Code after install.

Resident Claude wakeups require a shared aify server URL. In local-only mode, the normal `comms_*` tools still work, but `claude-aify` and resident channel wakeups are intentionally not installed.

For dashboard-managed spawns, also connect an environment bridge on the machine that should run Claude Code. The installer adds the `aify-comms` launcher for this:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

On Linux, macOS, or WSL use `aify-comms`. On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://localhost:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. `aify-comms --help` shows usage and unknown flag-like arguments are rejected instead of becoming roots. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The installer configures Claude's MCP client; the environment bridge is the long-running host process started with `--environment-bridge`, heartbeats into the dashboard, and claims spawn requests.

After every update:

1. Restart Claude Code.
2. Start the live session with `claude-aify`.
3. Re-register from that exact live session.
4. Confirm with `comms_agent_info(agentId="...")`.

For resident-session wakeups, start Claude with:

```bash
claude-aify
```

### Session-mode flag

`claude-aify` accepts `--resident` and `--managed` to declare session mode. Precedence: inherited `AIFY_SESSION_MODE` env wins (bridge-spawned managed PTYs set it to `managed`); else the flag; else TTY auto-detect (`[ -t 0 ]` — interactive defaults to `resident`, non-TTY to `managed`). `claude-aify` always exports `AIFY_CHANNELS_ENABLED=1` so its `mcp/stdio/server.js` child registers with `runtime_config.channelEnabled=true` — that's the precondition for resident-run/interrupt/steer caps to survive `_row_capabilities` strip.

### Session rediscover (added 2026-05-26, Plan 6 B4)

Unlike hermes/codex/pi (which query a live runtime), Claude has no probe endpoint — but its session id maps 1:1 to a JSONL transcript at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. `claude-aify` now validates `CLAUDE_SESSION_ID` against the on-disk transcript: if no `<id>.jsonl` exists anywhere under `~/.claude/projects/`, the env value is stale (prior session GC'd, operator cd'd into a different project, etc.) and the wrapper unsets both `CLAUDE_SESSION_ID` and `CLAUDE_RESUME_ID` so Claude creates a fresh session — the bridge's discover (Plan 4) picks up the truthful id on the first heartbeat (Plan 6 A1). The scan is filename-based, so the Windows-native vs git-bash cwd-encoding mismatch doesn't trip the validator. Failures are non-fatal: a missing transcript triggers a single `[claude-aify] CLAUDE_SESSION_ID '<id>' has no transcript ... clearing` log line and the wrapper continues normally.

### Agent identity is MANDATORY for status — and now self-recovering (2026-07-14)

**Always launch a registered agent with its id** (`claude-aify --aify-agent <agent-id> …`, or `AIFY_AGENT_ID` exported). `AIFY_AGENT_ID` gates EVERY turn-state path — the bridge's turn detector, the `Stop`/`UserPromptSubmit`/`PostToolUse` hooks, and the session-store capture hook. Launched without it, the agent still registers, sends/receives messages and heartbeats perfectly, but its status is structurally broken: only the channel sidecar can touch turn state, and it only ever SETS `working` on an inbound wake — so the agent latches `working` forever, then (once the backstop ages that flag out) reads `online` and can never show `working` again. Nothing errors; it just silently has no working status.

Two guards now make this hard to hit:

- **Handle → agent recovery.** On `claude-aify --resume <session-handle>` with no agent id, the wrapper asks the service which agent owns that handle (authoritative; survives a `/tmp` wipe), falling back to the local session store (`/tmp/aify-claude-session-<agent>.json`). It logs `resolved aify agent '<id>' from session handle '<handle>'` when it recovers. Same design hermes has had since 2026-06-03.
- **Loud refusal to degrade silently.** If the id is still unknown, the wrapper prints `NO AGENT ID: aify turn/status detection is DISABLED for this session (status will latch)`. Anonymous sessions remain legal (a plain claude + comms session is a real use case) — they just aren't silent.

The dashboard's resume/takeover command now carries `--aify-agent` too; it previously did not, which is how identity got dropped in the first place. **A running session cannot be repaired** — re-registering only writes DB rows, and Claude Code's in-app `/resume` picker swaps the conversation inside the same process (same env). Relaunch with `--resume <handle>`; the conversation is preserved.

### Wrapper MCP isolation (opt-in strict-mcp-config)

By default `claude-aify` loads your FULL `~/.claude.json` MCP server list (browsermcp, github, aify-project-graph, etc.) — the installer merges `aify-comms` + `aify-comms-channel` into that list at install time, so they are present without isolation. Setting `AIFY_CLAUDE_STRICT_MCP=1` in the launching shell opts into strict mode: the wrapper then launches Claude with `--strict-mcp-config` and a minimal MCP config containing ONLY `aify-comms` + `aify-comms-channel`, and your other MCP servers are NOT loaded inside that wrapper session (they still work in plain `claude` sessions outside the wrapper).

**Why the escape hatch**: a known Claude Code bug ([#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)) silently fails to initialize MCP servers when many stdio servers compete at startup. `aify-comms-channel` can lose the init race against a large operator config, leaving the channel listener unregistered and every channel-routed dispatch silently dropped despite the bridge reporting `delivered`. When that race bites, set `AIFY_CLAUDE_STRICT_MCP=1` and relaunch — the strict 2-server config restores guaranteed channel wake at the cost of the other MCP servers.

On Git Bash Windows, the wrapper uses `cygpath -m` to convert `/c/Docker/aify-comms` → `C:/Docker/aify-comms` so the MCP server paths are Windows-native (otherwise the MCP child processes fail to start).

### Managed-channel routing

`insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)` (settings, default false) routes dispatches to managed Claude agents via channel events (claimed by `claude-channel.js`, emitted as `<channel source="aify-comms-channel" ...>` MCP notifications) instead of typing into the wrapper PTY. Same protocol resident Claude already uses. Flip via `PUT /api/v1/settings` and roll back instantly if anything regresses.

> **Precondition.** Channel routing still requires a `claude-aify` wrapper PTY to be alive — `claude-channel.js` runs INSIDE that wrapper as an MCP child of Claude and is the actor that claims the dispatch. The bridge spawns the wrapper on managed dispatch (or eagerly with `managed_pty_eager_spawn=true`); a resident `claude-aify --aify-agent <id>` works equivalently. If the env doesn't advertise terminal+claude-code support (check via `Get-Command claude` and `Get-Command claude-aify.cmd` from the bridge's user/shell — set `AIFY_CLAUDE_COMMAND` to the absolute path if missing; reinstall to repair node-pty if the bridge reports `terminal=false`/`pty=false`), the wrapper cannot be spawned, no claim happens, and the dispatch sits in `queued` indefinitely. "Channel route doesn't need a PTY" is wrong — channel route is "PTY exists but delivery goes via MCP notification instead of typing into stdin", not "no PTY at all". For managed claude, a live wrapper PTY ALONE is not enough to report `online`: `online` requires BOTH the live console PTY AND a live, non-superseded channel-sidecar (`claude-channel.js`, the actual claimer). A live PTY with no live sidecar — or a live sidecar with no console ("headless orphan", which is reaped) — reads `available`, not `online`.

That wrapper enables the local aify channel bridge, adds Claude's current development-channel flag automatically, and records the live resident-session binding so `comms_register` can advertise `claude-live` reliably.
If Claude says `server:aify-comms-channel · no MCP server configured with that name`, rerun the installer with a real server URL and restart Claude Code.

The visible resident Claude session now skips permission prompts **by default**:

```bash
claude-aify
```

The wrapper adds `--dangerously-skip-permissions` by default. Pass `--safe` (or `--no-auto`) to opt OUT and keep normal visible CLI permission prompts.

Windows note:
- If you run the installer from Git Bash on Windows, it installs Bash wrappers plus `.cmd` shims in `%USERPROFILE%\.local\bin`, including `aify-comms.cmd` and `claude-aify.cmd`, and adds that directory to your user `PATH`.
- Open a new PowerShell after install. If `aify-comms.cmd` is still not recognized, run `$env:Path += ";$env:USERPROFILE\.local\bin"` for the current window or launch it directly with `& "$env:USERPROFILE\.local\bin\aify-comms.cmd"`.
- The hook/config writer is Git Bash aware. It converts hook script paths for native Windows Node and disables MSYS path rewriting for that step, so `--with-hook` should not require manual `settings.json` edits.
- If you install from WSL instead, the wrapper stays WSL-local. That is still fine for WSL Claude sessions, but it does not create a native Windows launcher.

Important:
- Active dispatch works only when the agent is installed through the local `stdio` MCP server.
- `comms_register` creates a resident session for messaging/presence. When the current Claude process was started with `claude-aify`, that resident session becomes wakeable and steerable through its own local aify channel bridge. This uses Claude Code Channels (`notifications/claude/channel`), not the Codex `turn/steer` API.
- `claude-aify` adds `--dangerously-skip-permissions` by default. Pass `--safe` (or `--no-auto`) to keep normal visible CLI permission behavior.
- `comms_send` is the normal teamwork and reply path. It is live-delivery gated for offline/stopped/no-wake targets; those sends are not stored. Busy steer-capable targets receive ordinary sends as current-run steer. Busy live targets that cannot steer queue/merge as next-turn work. Use `queueIfBusy=true` only when you intentionally want next-turn delivery even if steering is available. Agent-reported blocked/completed states are status notes, not delivery blockers.
- `comms_dispatch` is the explicit tracked-run/debug path. When you dispatch, it still arrives as a sender message and also opens tracked run state with reply handoff by default.
- Every aify-comms message is answered with a `comms_send` tool call: delivered dashboard-managed runs AND resident/live CLI sessions reply with `comms_send(type="response", inReplyTo="<message id>", to="<sender|dashboard>")`. That tool call is the team/chat-visible reply and closes the run; stdout/logs/tool output/run summaries/final plain text are the agent's own working output, not the reply. Treat each message as a small contract. Safety net: the `managed_reply_capture_fallback` setting (default on) auto-mirrors a delivered run's summary when it ends with no explicit reply; set it off for strict comms_send-only delivery — but always send the explicit `comms_send`.
- Keep team messages focused: one ask/result/blocker/status per message. When truth or history matters, check inbox/run/files first and say what was checked. Split unrelated topics instead of carrying them in one thread.
- `comms_spawn` creates a persistent environment-backed agent session. Use `comms_envs` first when you need to choose a host/workspace.
- Normal `comms_send` does not store messages for unreachable targets. Busy live targets may steer or queue/merge; stale queued/running work should still be cleared from Runs/Sessions before using chat.
- Short-lived nested subagents should normally report through their parent/coordinator instead of calling `comms_register(...)`, joining channels, or messaging the wider team directly.
- If an environment bridge is killed, managed agents backed by it become offline/detached and active sessions become lost; chats, identities, spawn specs, and session records remain. Restart the bridge, or assign the agent to another online environment from **Agents**, then restart from **Sessions**. If a resident `claude-aify` wrapper is closed, that resident session is no longer live-wakeable until it is restarted and re-registered.
- **Restarting `aify-comms` is a clean slate for managed sessions.** The environment bridge tears down every managed session it owns on shutdown (console PTYs stopped, runtime process trees / managed-hermes triads reaped), and on the next boot sweeps for any survivors of a crashed predecessor whose owning bridge is no longer live. Both are scoped to the agents this bridge owns and never touch resident sessions or another env's agents. So after a restart there are no orphaned managed processes holding false liveness — managed sessions are re-spawned fresh from their spec, not inherited.
- SSE-only installs can message and inspect, but they cannot host triggerable resident sessions or environment-backed agents, and they cannot launch local work themselves.
- Managed Claude Code model is blank by default, which lets the installed Claude Code runtime choose its default/latest model. Managed Claude Code defaults to `high` effort. Configure global defaults in Dashboard **Settings -> Runtime**. The bridge passes `--model` only when a model override is set, and always passes the configured effort. The normal dashboard does not tune model/effort per agent.
- Managed runtime hard timeout is **12 hours** by default (per-agent override via `runtimeConfig.timeoutMs`). Managed Claude Code adds `--dangerously-skip-permissions` for dashboard-managed unattended runs and uses `--max-turns 50` by default (`runtimeConfig.maxTurns` can override). Managed Codex separately uses Codex's unattended bypass sandbox profile by default (`danger-full-access`, equivalent to `--dangerously-bypass-approvals-and-sandbox`) and has Codex-specific quiet/MCP watchdogs: 30 minutes without Codex runtime notifications (`runtimeConfig.quietTimeoutMs` or `runtimeConfig.silenceTimeoutMs`) and 90 seconds for stuck `mcpToolCall aify-comms` turns (`runtimeConfig.mcpToolTimeoutMs` or `runtimeConfig.commsToolTimeoutMs`; set to `0` only for debugging). Current bridge builds terminate the whole managed runtime process tree on timeout/interrupt/stop so stale child processes do not keep false liveness.
- If another agent says you are not wakeable, the usual fix is: restart with `claude-aify`, then re-register from that exact live session with `runtime="claude-code"`.
- On Windows, always register with forward-slash `cwd` (`C:/path/to/project`). The stdio bridge normalizes automatically, but you must restart `claude-aify` after updating aify-comms for the fix to load.

## Delivery path

Resident `claude-aify` sessions are woken via the **channel** path: `claude-channel.js` runs as an MCP child of Claude (loaded via `--dangerously-load-development-channels server:aify-comms-channel`), polls the service for queued dispatches, and emits each one as a `notifications/claude/channel` event that lands in the live session as `<channel source="aify-comms-channel" ...>`.

By default the wrapper loads the operator's full `~/.claude.json` MCP list (which already contains `aify-comms` + `aify-comms-channel`). Setting `AIFY_CLAUDE_STRICT_MCP=1` opts into `--strict-mcp-config` so only `aify-comms` and `aify-comms-channel` load inside the wrapper session — the escape hatch for the Claude Code stdio MCP init race ([anthropics/claude-code#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)). Operator's other MCP servers always work in plain `claude` sessions outside the wrapper.

**Windows-specific note.** The wrapper emits `http://127.0.0.1:8800` (not `http://localhost:8800`) in the generated MCP env block, and both `claude-channel.js` and `server.js` defensively coerce `http://localhost` to `http://127.0.0.1` at fetch time. Reason: Docker Desktop's IPv6 port forwarding is unreliable on Windows, and `localhost` resolves to IPv6 `::1` first — connections hang silently and no channel dispatches get claimed. The coercion is a no-op on Linux/macOS where loopback is IPv4 by default.

### OpenAI/ChatGPT quota panel needs the `codex` CLI signed in

The dashboard's *OpenAI · ChatGPT (Codex + Hermes)* card reads an OpenAI token from the **codex CLI's**
store (`codex login`). Hermes does not hold one — on a default install its `auth.json` is only a pointer
(`{"active_provider": "openai-codex"}`) that delegates to codex. Without codex installed and signed in,
that one card cannot show live usage; nothing else is affected. `install.sh` prints a `[usage] OK` /
`[usage] WARNING` verdict (it proves the connection, so an expired token is reported too), and
`node ~/.aify-comms/mcp/stdio/usage-preflight.js --json` gives an installing agent a machine-readable
`{ok, code}` where `code` is `ok` / `no-token` / `rejected` / `unreachable`.

## What This Installs

- The `aify-comms` stdio MCP server, registered in Claude user scope (tool namespace retained for compatibility)
- The `aify-comms-channel` MCP server used for resident Claude wakeups, also registered in Claude user scope
- The aify skill in `~/.claude/skills/aify-comms`
- Slash commands in `~/.claude/commands/aify-comms`
- Optional unread-message hook notifications
- A `UserPromptSubmit` hook in `~/.claude/settings.json` that POSTs `/api/v1/agents/{id}/turn-start` on prompt submit. Flips the dashboard to `working` the moment the operator submits a prompt — even when the prompt didn't come through aify-comms's dispatch path (i.e., direct CLI typing). This is the turn-**START** event.
- A `Stop` hook in `~/.claude/settings.json` that signals turn-**END** when the assistant is done. **As of 2026-06-19 it routes through `claude-stop-gate.js`** (in the native bridge dir) instead of a raw `curl`: the managed claude wrapper fires premature/duplicate `Stop` hooks BETWEEN the tool-bursts of one logical turn, which used to clear the turn mid-work and flap the status `working`→`online`→`working`. The gate reads the transcript tail and **suppresses** the `/turn-end` only when the turn is *confirmed still in-flight*; on a real end, an unreadable tail, or ANY error it posts `/turn-end` exactly as before (fail-safe — it can never cause a stuck `working`, and falls back to the raw `curl` if node is unavailable). **These two — `UserPromptSubmit` (start) and `Stop` (end) — are the ONLY turn hooks.** STATUS is pure-event: no timer window; the turn clears on the turn-end event (or the 30-min dropped-event backstop).
- **No `PostToolUse` re-pulse (removed 2026-06-02, pure-event change #4).** Earlier installs wired a second `/turn-start` hook on `PostToolUse` to re-assert `turn_busy` on every tool call so a long turn held `working` past the old short staleness window. With status now pure-event there is no short window to outlast, and re-pulsing on every tool call would defeat the turn-END event (an agent that just finished a tool-using turn would keep re-arming `turn_busy`). The installer wires `UserPromptSubmit` only and actively **removes** any `PostToolUse` `/turn-start` hook a prior install left behind — rerun `install.sh` to pick this up.
- **Hook-independent BIDIRECTIONAL turn-state detector (in the bridge, no settings entry).** The fast-path hooks (`UserPromptSubmit` → `/turn-start`, `Stop` → `/turn-end`) only fire for operator-TYPED turns, and neither is a guaranteed terminator (`Stop` misses on interrupt/ESC, MCP-continuations, a crash, or a failed curl). So the `claude-channel.js`/`server.js` bridge runs a transcript turn-state detector for claude agents (resident AND managed; gated on `AIFY_AGENT_ID` + the `claude-code` adapter + `transcriptTail`, not session mode). It reads the session transcript TAIL structure (`adapters/claude.js` `transcriptTail` → `{lastRole, lastStopReason, pendingToolUse}`) every ~30s and drives `turn_busy` in BOTH directions, edge-triggered and idempotent:
  - **Tail IN-FLIGHT** (last assistant `stop_reason == 'tool_use'` / pending `tool_use`, a trailing user/tool_result, or no terminal `stop_reason`) → POSTs `/turn-start` (**SET** `working`). This is the resident under-report fix: a channel-woken or scheduled-task turn never fires `UserPromptSubmit`, so without this the agent showed NOT working while it was. A long blocking tool call or a Task sub-agent dispatch shows a pending `tool_use` (sub-agents write a separate `subagents/*.jsonl`, so the parent transcript is static) and correctly STAYS `working`.
  - **Tail ENDED** (terminal `stop_reason` ∈ `{end_turn, stop_sequence, max_tokens}`, no pending `tool_use`) → POSTs `/turn-end` (**CLEAR**).
  - **Unreadable/null tail** → no change (never false-sets, never false-clears).
  This detector keys ONLY on transcript process truth (the harness's own `.jsonl`), never on the server's computed status (anti-feedback-loop), so it covers typed, channel-woken, AND scheduled turns. It is the robust **replacement for the removed `PostToolUse` re-pulse** for all turn types. The hooks stay the instant path (typed/managed are instant); this is the hook-independent backstop now covering BOTH directions, at ≤ ~30s latency on the detector path.
- An `aify-comms` environment bridge launcher in `~/.local/bin`
- A `claude-aify` wrapper in `~/.local/bin` that exports `AIFY_COMMS_URL` using the form `${AIFY_COMMS_URL:-<install-time-url>}` — caller env wins, so a bridge-spawned managed PTY can override the install-time default if it needs to talk to a different aify-comms service.

**Installer safety:** If `~/.claude/settings.json` is malformed (operator hand-edit, prior crash, BOM), the installer backs up the existing file to `<path>.aify-bak-<timestamp>` and logs a `WARN` to stderr before rewriting. The pre-2026-05-22 behavior silently overwrote the file with an aify-only fresh copy, losing every operator setting/hook. Same protection now applies to all hook/config files the installer touches.

## Quick Start

```text
comms_register(agentId="my-agent", role="coder", runtime="claude-code")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
comms_inbox(agentId="my-agent", mode="headers")
comms_inbox(agentId="my-agent", messageId="<message id>")
```

## How the install works (and updating)

`install.sh` copies the bridge runtime (`mcp/stdio` + its `node_modules`) into a native folder at `~/.aify-comms` (override with `AIFY_HOME`) and points the wrappers and MCP config at that copy — not at this repo checkout. This keeps bridge startup fast on slow/bind-mounted filesystems. Consequence: after `git pull`, changes under `mcp/stdio/` only take effect once you **re-run `install.sh`** (refreshes the copy) and restart the wrapper/bridge. Updating the runtime CLI itself (e.g. a hermes or claude update) does not require reinstalling aify-comms — the two write disjoint files.
