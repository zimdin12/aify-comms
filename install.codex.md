# Install For Codex

Use aify-comms when you want dashboard-driven coordination for coding agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Two installs, and you may only need one

aify-comms has a **backend** and a **client** side, and they are separate installs.

| you want | install | how |
|---|---|---|
| **the service** — database, dashboard, the API agents talk to | the container | `./setup.sh` then `docker compose up -d --build` |
| **to run agents on this machine** | `aify-env` + the launchers | `npm install -g aify-env`, then aify-wrapper's `install.sh --all --endpoint <url>` |

A machine may do either, both, or neither. The service can live on another host entirely — point the
client install at its address instead of `localhost`.

**The steps below are the client side**, and they currently also install this repo's own bridge
runtime onto the host. That is being unwound: see
[docs/TARGET_ARCHITECTURE.md](docs/TARGET_ARCHITECTURE.md) for where it lands and what is left.

## Before you install: the service has to be running

The steps below install a CLIENT and point it at a service. Something has to be serving that address,
and on a fresh machine nothing is. Clone once and bring the service up first — the same checkout is
what the client install uses:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
./setup.sh                          # generates .env + config from the examples
docker compose up -d --build        # API on :8800, Dashboard Next on :8801
curl http://localhost:8800/health   # {"status":"healthy", ...} before going further
```

If the service already runs somewhere else, skip this, clone anyway (the installer runs from the
checkout), and use that address below instead of `localhost`. Full setup detail is in
[README.md](README.md).

## Copy-Paste Install

```bash
cd ~/aify-comms    # the checkout from the step above
bash install.sh --client codex http://localhost:8800 --with-hook
```

If you are using local-only mode with no shared server:

```bash
cd ~/aify-comms
bash install.sh --client codex --with-hook
```

Restart Codex after install.

## Confirm it took effect

Every deploy path in this repo can fail silently: no error, everything looks installed, and what you
changed is not what is running. Do not read the absence of an error as success.

```bash
aify-comms doctor          # human-readable; --json for scripts, --strict to exit non-zero
```

On a fresh install `service`, `bridge-installed` and `skills-installed` should all be green. The
launcher's own currency is `aify-wrapper-check`'s question, not this tool's — v0.6 moved it there
rather than keep a second implementation of it. `bridge-running` and `agent-identity` SKIP on Windows — they read `/proc` — so on Windows
`bridge-current` is what tells you a running bridge is on the current build. A check that could not
gather evidence reports `unknown-all` and fails; that is the tool working, not a bug to quieten.

The installer verifies that the copied `node-pty` package can load its native binary and automatically rebuilds it when the package exists but the binary is missing or unloadable. Use `aify-comms doctor --json` after installation; checking only `node_modules/node-pty` is not sufficient proof that managed Console PTYs can start.

For dashboard-managed spawns, also connect an environment bridge on the machine that should run Codex. The installer adds the `aify-comms` launcher for this:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

**One bridge per environment, and starting a second replaces the first.** `aify-comms` IS the
environment bridge: run it where you want agents to run, once. Starting it again supersedes the bridge
already serving that environment, and the older one exits taking its managed workers with it — that is
how a four-second run meant only to check the launcher still worked took down nine agents on
2026-08-11. To verify without starting anything, use `aify-comms --check` (validates node and the
script path, registers nothing) or `aify-comms doctor`.

On Linux, macOS, or WSL use `aify-comms`. On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://localhost:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. `aify-comms --help` shows usage and unknown flag-like arguments are rejected instead of becoming roots. See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md). The installer configures Codex's MCP client; the environment bridge is the long-running host process started with `--environment-bridge`, heartbeats into the dashboard, and claims spawn requests.

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

The visible resident Codex session now bypasses approvals/sandbox prompts **by default**:

```bash
codex-aify
```

The wrapper adds the best permission flag supported by the installed Codex CLI (`--dangerously-bypass-approvals-and-sandbox`) by default. Pass `--safe` (or `--no-auto`) to opt OUT and keep normal visible CLI approval prompts.

### Session-mode flag

`codex-aify` accepts `--resident` and `--managed`. Precedence: inherited `AIFY_SESSION_MODE` env wins (bridge-spawned managed PTYs set it to `managed`); else the flag; else TTY auto-detect via `[ -t 0 ]` — interactive defaults to `resident`, non-TTY to `managed`. Use the explicit flag only when TTY detection might be wrong for your shell context (most operators never need it).

### Agent identity is MANDATORY for status — and now self-recovering (2026-07-28)

**Always launch a registered agent with its id** (`codex-aify --aify-agent <agent-id> …`, or
`AIFY_AGENT_ID` exported) *before* it registers. `AIFY_AGENT_ID` gates EVERY turn-state path: the
bridge's rollout-tail turn detector and the `UserPromptSubmit`/`Stop` hooks all test it. An
environment variable cannot be added to an already-running process, so a session that starts without
it can never acquire it — `comms_register` mid-session writes DB rows and nothing more.

Launched without it, the agent still registers, sends and receives messages and heartbeats fine, but
its status is structurally broken: nothing reports turn-start/turn-end, so the status **latches** and
no live process can clear it. No session handle is bound either, so the dashboard has no
"Continue in CLI" command to offer.

Two guards now make this hard to hit:

- **Identity recovery on a bare `--resume`.** `codex-aify` asks the service which agent owns the
  thread handle (scoped to `runtime="codex"`, so a claude agent that happens to share a handle string
  cannot cross-bind) and adopts that id. This mirrors what `claude-aify` has done since 2026-07-14
  and `hermes-aify` since 2026-06-03; codex was the last wrapper without it, and only the
  HAND-TYPED path was affected — the dashboard's resume command already passed `--aify-agent`.
- **Loud refusal to degrade silently.** If the id is still unknown, the wrapper prints
  `NO AGENT ID for --resume <id>: aify turn/status detection is DISABLED for this session (status
  will latch)`. Anonymous sessions remain legal — they just are not silent. `comms_register` also
  warns when it sees a resident registering from an identity-less session, naming the relaunch
  command.

**A running session cannot be repaired** — relaunch through the wrapper. Verify with
`comms_agent_info(agentId=…)`: a healthy resident has a non-empty `sessionHandle`.

### Session handle binding

Fresh `codex-aify` launches do **not** scan `~/.codex/sessions/` to invent `CODEX_THREAD_ID`. The newest rollout file can be an unrelated historical thread, and binding a fresh visible TUI to that ID makes resident/channel delivery target the wrong session. For fresh launches, `CODEX_THREAD_ID` and `AIFY_SESSION_HANDLE` stay unset until Codex exposes a real current thread.

An explicit `--resume <id>` is authoritative. In that path, `codex-aify` exports `CODEX_THREAD_ID=<id>` and `AIFY_SESSION_HANDLE=<id>` before launching Codex so the inner aify-comms MCP bridge and `codex resume` agree on the same thread. The wrapper still probes `~/.codex/sessions/` only to verify that the requested `--resume` handle exists; if it is stale, the wrapper starts a fresh Codex TUI instead of aborting.

### Delivery path

Managed-codex dispatches default to the wrapper-backed path (`managed_via_wrapper=["codex","hermes"]`): the bridge owns a `codex-aify` PTY, the wrapper starts a local Codex app-server, and the wrapper's in-process aify-comms bridge claims dashboard dispatches with `executionModes=["channel","resident"]`. The browser Console renders that real wrapper TUI. `codex-aify` does NOT require the `--strict-mcp-config` + minimal-MCP isolation that `claude-aify` needs to work around the [Claude Code stdio MCP race bug](https://github.com/anthropics/claude-code/issues/38462). Your codex MCP servers (whatever you have configured in `~/.codex/config.toml` or equivalent) load normally inside `codex-aify`.

If wrapper-backed delivery is disabled for Codex, the bridge falls back to the native `createCodexController` app-server RPC adapter and surfaces a synthesized `terminal_session` (`command='aify://virtual-rpc/codex'`, `runtime_state.virtualTerminal=true`) for Console visibility. That fallback pushes per-event frames such as `▶ turn started`, tool start/finish markers, agentMessage deltas, `■ turn ended`, and `✗ error`.

### Resident dispatch delivery (incoming aify-comms messages)

When another agent sends `comms_send(to="<this-codex-agent>", ...)` (or `comms_dispatch(...)`) while this codex session is running resident under `codex-aify`, the bridge delivers the message by calling JSON-RPC `turn/start` against the per-instance `codex app-server` on the resident's active `threadId` — the same app-server the wrapper launched at startup (see `install_codex_wrapper` in `install.sh`) and that your `codex --remote ws://...` TUI is already connected to. This is the symmetric equivalent of Claude's `notifications/claude/channel` delivery, but uses native codex JSON-RPC primitives (no MCP notification extension required).

**Known limitation — codex issue [#15320](https://github.com/openai/codex/issues/15320):** when an external client posts `turn/start` against a thread that a `--remote` TUI is attached to, the TUI does **not** render the externally-injected user turn live; the thread history fixes up later, but the operator may not see the wake event in the TUI itself. To make resident wake events visible regardless, the bridge also pushes synth-terminal frames into the dashboard Console pane — same frames it pushes for managed dispatches (prompt echo, `▶ turn started`, agent message deltas, `■ turn ended`). Use the dashboard Console at `http://localhost:8800` as the source of truth for resident-codex wake events. If your codex version ships with the community patch for #15320 (or upstream resolution), the `--remote` TUI will render injected turns live and the dashboard Console becomes a redundant second view.

Windows note:
- If you run the installer from Git Bash on Windows, it installs Bash wrappers plus `.cmd` shims in `%USERPROFILE%\.local\bin`, including `aify-comms.cmd` and `codex-aify.cmd`, and adds that directory to your user `PATH`.
- Open a new PowerShell after install. If `aify-comms.cmd` is still not recognized, run `$env:Path += ";$env:USERPROFILE\.local\bin"` for the current window or launch it directly with `& "$env:USERPROFILE\.local\bin\aify-comms.cmd"`.
- If you install from WSL instead, the wrapper stays WSL-local. That is still the right setup for WSL Codex, but it does not create a native Windows launcher.

Recommended registration from inside `codex-aify`:

```text
comms_register(agentId="my-agent", role="coder", runtime="codex", cwd="<native-path-to-project>", appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
```

Add `sessionHandle="$CODEX_THREAD_ID"` only when that variable is non-empty, usually after an explicit `codex-aify --resume <id>` or after the current Codex CLI has exposed a real thread ID.

Use a native path for the runtime you are actually running:
- WSL/Linux Codex: `/mnt/...` or other native Linux paths
- native Windows Codex: `C:/...` with forward slashes

Fallback order if that does not flip to `codex-live`:

1. Drop `sessionHandle` + `appServerUrl`: `comms_register(..., runtime="codex")`.
2. If `$CODEX_THREAD_ID` is non-empty in that same session, re-add `sessionHandle="$CODEX_THREAD_ID"`.
3. Keep `appServerUrl` explicit when multiple `codex-aify` sessions run on the same machine or the wrapper was launched from a different directory than the `cwd` you registered.

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
- If started with `codex-aify`, resident wakeups use the same WebSocket app-server as the visible TUI and show up as `codex-live`. Current upstream Codex may not render externally injected `turn/start` traffic live in the `--remote` TUI (see issue #15320); the dashboard Console is the source of truth for those wake events until upstream renders them in the visible TUI.
- `codex-aify` adds `--dangerously-bypass-approvals-and-sandbox` by default. The wrapper does not use the older `--full-auto` alias. Pass `--safe` (or `--no-auto`) to keep normal visible CLI permission behavior.
- `comms_send` is the normal teamwork and reply path. It is live-delivery gated for offline/stopped/no-wake targets; those sends are not stored. Busy steer-capable targets receive ordinary sends as current-run steer. Busy live targets that cannot steer queue/merge as next-turn work. Use `queueIfBusy=true` only when you intentionally want next-turn delivery even if steering is available. Agent-reported blocked/completed states are status notes, not delivery blockers.
- `comms_dispatch` is the explicit tracked-run/debug path. When you dispatch, it still arrives as a sender message and also opens tracked run state with reply handoff by default.
- Every aify-comms message is answered with a `comms_send` tool call: delivered dashboard-managed runs AND resident/live CLI sessions reply with `comms_send(type="response", inReplyTo="<message id>", to="<sender|dashboard>")`. That tool call is the team/chat-visible reply and closes the run; stdout/logs/tool output/run summaries/final plain text are the agent's own working output, not the reply. Treat each message as a small contract. Safety net: the `managed_reply_capture_fallback` setting (default on) auto-mirrors a delivered run's summary when it ends with no explicit reply; set it off for strict comms_send-only delivery — but always send the explicit `comms_send`.
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

### OpenAI/ChatGPT quota panel needs the `codex` CLI signed in

The dashboard's *OpenAI · ChatGPT (Codex + Hermes)* card reads an OpenAI token from the **codex CLI's**
store (`codex login`). Hermes does not hold one — on a default install its `auth.json` is only a pointer
(`{"active_provider": "openai-codex"}`) that delegates to codex. Without codex installed and signed in,
that one card cannot show live usage; nothing else is affected. `install.sh` prints a `[usage] OK` /
`[usage] WARNING` verdict (it proves the connection, so an expired token is reported too), and
`node ~/.aify-comms/mcp/stdio/usage-preflight.js --json` gives an installing agent a machine-readable
`{ok, code}` where `code` is `ok` / `no-token` / `rejected` / `unreachable`.

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
comms_register(agentId="my-agent", role="coder", runtime="codex", appServerUrl="$AIFY_CODEX_APP_SERVER_URL")
comms_agents()
comms_agent_info(agentId="my-agent")
comms_send(from="my-agent", to="other-agent", type="info", subject="Hello", body="Hi there")
comms_inbox(agentId="my-agent", mode="headers")
comms_inbox(agentId="my-agent", messageId="<message id>")
```

## Native fallback persistent app-server (managed dispatches)

Managed Codex defaults to the wrapper-backed path described above: the bridge owns a `codex-aify` PTY and the wrapper's child bridge delivers through its local app-server. If wrapper-backed delivery is disabled or unavailable, the bridge falls back to a native app-server controller: one `codex app-server` per agentId on first dispatch, reused across turns. Benefits of the fallback: no per-turn spawn cost, native conversation continuity via the cached `threadId`, one PID per agent that the dashboard can surface as synthesized terminal output.

- Default launcher: platform-native (`wsl.exe -e codex app-server` on Windows, `codex app-server` on POSIX). Override with `AIFY_CODEX_COMMAND="/abs/path/to/codex app-server"` if your binary isn't on PATH or you want to point the bridge at a wrapper script. (The fake test fixture uses this same env var.) The override is quote-aware so paths-with-spaces work: `AIFY_CODEX_COMMAND='"C:\Program Files\codex\codex.exe" app-server'`.
- **Fresh-context behavior:** if you trigger a fresh context (Dashboard → Sessions → Reset, which writes a new `sessionHandle`), the next dispatch detects the threadId mismatch against the running CodexSession, tears the session down with a clear error (`threadId hint mismatch`), and the dispatch-after-that spawns a fresh `codex app-server` on the new thread. Operator-visible as one "failed" dispatch in the trail, then normal behavior resumes.
- Idle reaper: 24h default. Override globally via `AIFY_CODEX_IDLE_TIMEOUT_MS` or per-agent via `runtimeConfig.codexIdleTimeoutMs`.
- Handshake timeout: 60s default; tune via `AIFY_CODEX_STARTUP_TIMEOUT_MS` or `runtimeConfig.startupTimeoutMs`.
- The resident path (with a real WebSocket `codexAppServerUrl`) is unchanged — that's already pooled at the app-server process level.

Verify: open the agent's Console after a managed dispatch — `tasklist | findstr codex` (Windows) or `pgrep -f "codex app-server"` (POSIX) should show one PID that survives a second dispatch. The same threadId persists, so the conversation accumulates context turn-over-turn natively (no wire-prompt context-carry needed).

## Codex session storage layout

When `--resume <id>` is explicit, `codex-aify` probes `~/.codex/sessions/` for a saved session matching that handle. Plan 4 (2026-05-25) supports three layouts in priority order:

1. **Flat** — `~/.codex/sessions/<id>.jsonl` (legacy or simple installs)
2. **Dir-per-session** — `~/.codex/sessions/<id>/...` (alternative codex versions)
3. **Date-sharded** — `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-timestamp>-<id>.jsonl` (current codex default — verified 2026-05-25 in WSL `Ubuntu`)

If none match, the wrapper falls through to fresh codex with a clear stderr message instead of crashing. The bridge's `mcp/stdio/controllers/codex-controller.js` mirrors this probe.

If your codex stores sessions elsewhere (e.g. custom `CODEX_HOME`), the wrapper won't auto-detect — file a feature request or set `AIFY_CODEX_SESSIONS_DIR` env (planned future enhancement).

## How the install works (and updating)

`install.sh` copies the bridge runtime (`mcp/stdio` + its `node_modules`) into a native folder at `~/.aify-comms` (override with `AIFY_HOME`) and points the wrappers and MCP config at that copy — not at this repo checkout. This keeps bridge startup fast on slow/bind-mounted filesystems. Consequence: after `git pull`, changes under `mcp/stdio/` only take effect once you **re-run `install.sh`** (refreshes the copy) and restart the wrapper/bridge. Updating the runtime CLI itself (e.g. a hermes or claude update) does not require reinstalling aify-comms — the two write disjoint files.
