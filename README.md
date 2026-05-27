# aify-comms

Dashboard-driven communication and control plane for AI coding teams.

`aify-comms` solves the practical problem of running more than one coding agent across Windows, WSL, Linux, and remote machines without losing track of who is live, what they are doing, and how to restart or replace them. The normal workflow is: start the service, run an `aify-comms` bridge in each execution environment, open the dashboard, spawn persistent managed identities into chosen workspaces, then coordinate through chat.

The dashboard is the product surface. Messages are the work interface; runs, sessions, bridges, and handoffs are operational telemetry around those messages.

The intended team behavior is conversational but disciplined: dashboard direct chat is human/operator chat, every message is a small contract, direct agent requests should receive threaded replies, and channel discussion should happen when an agent is named, responsible, asked a question, or has evidence to contribute. Managed turns should not end silently: final text is captured as the current reply, and future work needs a real `comms_send` wake.

## Product Direction

`aify-comms` keeps the original communication core:

- direct messages, channels, inboxes, execution/run audit records, handoffs, and shared artifacts
- host-side bridges for Claude Code, Codex, Hermes, OpenCode, and Oh My Pi
- resident session wakeups and environment-backed managed sessions
- dashboard-backed operational visibility

It now adds a first-class identity/session lifecycle layer:

- connected environment registry: WSL, Windows, Linux, Docker host, remote machine
- spawn from dashboard into any connected environment
- runtime adapters for Claude Code, Codex, Hermes, OpenCode, and Oh My Pi managed/resident execution
- automatic identity/registration for spawned agents
- managed-warm sessions for long-lived agent identities
- portable compact/continue into fresh managed backings when a phase changes or context gets noisy
- Work Loop contracts for overdue replies, self-wakes, missing handoffs, and inbox hygiene
- runtime/session visibility, with token/cost telemetry shown only when runtimes expose it
- real chat UI with DMs, channels, conversation/inbox search, bottom-jump, artifacts, and run/handoff state near the conversation

## Target Mental Model

1. Start the service.
2. Connect one or more environment bridges.
3. Open the dashboard.
4. Click **Spawn Agent**.
5. Pick runtime, environment, workspace, role, and initial instructions. Managed model/effort defaults are global settings.
6. The agent identity, spawn spec, and session backing appear automatically.
7. Talk to it in direct chat or channels, assign work through messages, inspect output, stop/restart/recover it, or compact it into a fresh backing.

Manual `comms_register(...)` is an advanced/debug and resident-CLI path, not the normal dashboard-managed workflow.

## Managed And Resident Modes

Use **managed mode** for the normal persistent team. Start `aify-comms` in each Windows/WSL/Linux environment you want to execute work in, then spawn agents from the dashboard. Managed identities have a saved environment, workspace, runtime, spawn spec, native handle when available, and session history. Runtime adapters choose a dashboard-symmetric managed delivery path: Claude Code, Codex, and Hermes use bridge-owned wrapper PTYs where configured, while Pi and OpenCode use native controller delivery with synthesized Console streams. Browser Console attaches to the backing owner without switching identity modes. If wrapper backing cannot be established for a wrapper-capable runtime, the native managed adapter remains the fallback path. The dashboard can restart, stop, compact, or recreate agents without keeping a separate CLI tab open.

Use **resident mode** when you intentionally open a real runtime terminal and want that visible CLI to receive live messages. Start it with `claude-aify --aify-agent <id>`, `codex-aify --aify-agent <id>`, or `hermes-aify --aify-agent <id>` so the wrapper registers that terminal as the live resident candidate and keeps a fresh bridge heartbeat. Raw `POST /api/v1/agents` metadata registration is not a live resident bridge and will be reported as `stale`; use the wrapper's `comms_register` MCP tool or wrapper auto-registration from the visible session. legacy `omp-aify` / `pi-aify` wrappers are not installed by default; triggerable Pi delivery is managed RPC because OMP is single-client. Ownership does not switch automatically. Use **Sessions -> Actions -> Switch to resident** or the Chat details switch when the visible CLI should own delivery; use **Switch to managed** when dashboard sends should return to the managed backing.

Fresh native handles should come from a new spawn or explicit **Recreate**. Ordinary Restart/Recover/Adopt should preserve the stored handle and fail visibly if the handle is locked or cannot be resumed.

If the dashboard is pointing at the wrong saved native context and you know the correct Claude session ID, Codex thread ID, Hermes session ID, OpenCode session ID, or Pi handle, use **Set handle** from Chat details or Sessions actions. This repairs the saved handle and runtime state without creating a fresh context. Use it only for known-good handles; a wrong value binds the identity to the wrong native memory.

Normal dashboard chat is live-delivery gated for unreachable targets: offline, stale, stopped, or no-wake agents fail visibly and the message is not stored for a future run. Managed delivery is runtime-specific but dashboard-symmetric: supported managed runtimes start or reuse a bridge-owned backing, deliver one bracketed dashboard turn through that runtime's channel/app-server/gateway/RPC/PTY path, and stay `working` until the reply closes the run; if the active terminal output clearly asks for operator input or a decision, the agent is shown as `blocked` instead of healthy working. Claude's development-channel confirmation is operator-controlled by setting, not automatic hidden input. The normal Claude footer/prompt chrome is not enough to mark `blocked`; when Claude returns to an idle prompt after producing visible terminal output but forgets to send an explicit `aify-comms` reply, reconcile closes the run as completed-without-reply so it does not pin `working`. Unthreaded completion-style `info` messages such as `Done`, `Pushed`, or `Fixed` can satisfy the active terminal run during send/reconcile so finished work does not stay open. If wrapper-backed Codex/Hermes or another terminal backing cannot be established, the native managed/steer path remains the fallback. Browser Console is an attachment to the backing runtime, not a separate owner. Stopped/failed Console terminals are cleared as the current session binding and remain historical only, so the dashboard does not keep showing an old terminal buffer as the current Console. Busy live targets receive ordinary sends as current-run steer when supported, or as queued/merged next-turn work when steering is not available; the explicit **Queue** action waits behind real active/queued work, but idle terminal-backed agents still receive normal live delivery instead of orphaned queue rows. Required handoffs are repaired automatically when a terminal run finishes without an explicit reply, dashboard-started managed runs persist final text back into dashboard chat, due reply-contract reminders defer while a target is busy or blocked and retry when that agent returns idle, and stale unowned active runs are reconciled periodically so old status rows do not pin agents as `working`. While a `working` agent's terminal is receiving output, its yellow status dot briefly pulses orange as a live-output hint; this is a visual activity signal, not a separate status.

The **Work Loop** page turns message/run state into operational contracts: who asked, who owns the reply, whether the run is queued/working/overdue/answered, when it was last reminded, and whether old read receipts or handoffs need repair. Requests, reviews, and errors require replies by default; routine `info` is a note unless `requireReply` is explicitly set. Reminder messages are nudges, not new obligations, and by default they repeat until the original contract is answered or operator-closed. Busy targets are deferred and reminded when they return idle instead of injecting more text into an active turn. Operators can close individual or selected contracts as reviewed from Work Loop; chat and run audit history remain available. Work Loop does not replace chat; it makes the implicit obligations in chat visible enough for an autonomous team to keep moving without guessing from raw unread counts.

Reliable compaction in `aify-comms` means creating a fresh managed backing from an editable handoff packet and recent comms context. It is portable across Claude Code, Codex, Hermes, OpenCode, and Oh My Pi, and it defaults to the same agent ID so chats and agent identity remain stable. Native in-place compaction is runtime-adapter dependent; current managed adapters do not expose a verified internal compact API.

Three settings shape the managed-delivery surface. `insert_messages_via_console=false` is the default channel-route mode for managed Claude (`claude-channel.js` claims the dispatch and emits `<channel source="aify-comms-channel" ...>` MCP notifications — same protocol resident Claude already uses). `managed_pty_eager_spawn=true` (paired with `managed_terminal_backing_enabled=true`) proactively launches wrapper PTYs at spawn-request running transition when the runtime uses a terminal backing. **`managed_via_wrapper=["codex","hermes"]` is the default wrapper-backed path for Codex and Hermes**: managed dispatches route through bridge-owned `codex-aify` / `hermes-aify` PTYs, the wrapper's in-process MCP bridge claims via `executionModes=["channel","resident"]`, and delivery uses the wrapper's local app-server or Hermes gateway. Dashboard Console renders the real TUI via xterm.js. Pi is structurally excluded from wrapper mode and uses the persistent native OMP RPC virtual terminal. See [DECISIONS.md](DECISIONS.md) for the architectural rationale.

Managed Pi specifically uses a **persistent** `omp --mode rpc` child per agent (spawned on the first dispatch, reused across subsequent ones, 24-hour idle timeout). Each RPC event is formatted into a synthesized `terminal_session` row marked `command='aify://virtual-rpc/pi'` — the dashboard's Console pane shows it like a real terminal, and operator input typed there round-trips as a new RPC turn through the same persistent child. See [install.pi.md](install.pi.md) and [DECISIONS.md](DECISIONS.md) for the full delivery path.

Every `*-aify` wrapper accepts `--resident` and `--managed` flags to declare session mode explicitly; precedence is inherited `AIFY_SESSION_MODE` env > flag > TTY auto-detect (`[ -t 0 ]`). Bridge-spawned wrappers always inherit `AIFY_SESSION_MODE=managed` from `terminal-env.js`; operator-launched wrappers from a real terminal default to `resident`. `claude-aify` additionally exports `AIFY_CHANNELS_ENABLED=1` so its register call carries `runtime_config.channelEnabled=true` (precondition for resident-run/interrupt/steer caps surviving the server-side strip).

`claude-aify` always launches Claude with `--strict-mcp-config` and a runtime-generated minimal MCP config containing ONLY `aify-comms` + `aify-comms-channel`. The operator's broader `~/.claude.json` MCP server list is NOT loaded inside `claude-aify` wrapper sessions. They still work in plain `claude` sessions outside the wrapper. This isolation works around the known Claude Code stdio MCP race bug ([#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)) — without it, `aify-comms-channel` silently fails to register its channel notification listener and channel-routed dispatches sit queued forever despite `delivered` status. On Windows Git Bash the wrapper uses `cygpath -m` to convert install paths to native format.

## Current State

This repo is the canonical `aify-comms` codebase. The dashboard and environment lifecycle work has been folded back into this product rather than living in a separate bridge fork. Existing message, channel, dispatch, artifact, and MCP APIs should keep working while the dashboard becomes the normal way to manage agents.

Important starting docs:

- [AGENTS.md](AGENTS.md) — coding-agent instructions for this repo.
- [docs/PRODUCT_BRIEF.md](docs/PRODUCT_BRIEF.md) — product goals and non-goals.
- [docs/ARCHITECTURE_PLAN.md](docs/ARCHITECTURE_PLAN.md) — proposed control-plane architecture.
- [docs/SESSION_MODEL.md](docs/SESSION_MODEL.md) — backed warm sessions, native resume, bridge-emulated resume, and CLI attach rules.
- [docs/DASHBOARD_SPEC.md](docs/DASHBOARD_SPEC.md) — first dashboard UX spec.
- [docs/WEB_APP_DESIGN.md](docs/WEB_APP_DESIGN.md) — web application UX/architecture principles.
- [docs/DASHBOARD_REVIEW.md](docs/DASHBOARD_REVIEW.md) — current dashboard critique, semantics, and design rules.
- [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md) — WSL/Linux/Windows bridge setup and launcher semantics.
- [docs/COMMUNICATION_GUIDE.md](docs/COMMUNICATION_GUIDE.md) — focused team messaging rules for agents and managers.
- [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md) — concise engineering guide for future coding agents.
- [docs/UNINSTALL.md](docs/UNINSTALL.md) — clean uninstall for Docker service, data, wrappers, MCP config, hooks, and skills.
- [docs/SKILLS.md](docs/SKILLS.md) — installed Codex/Claude skill inventory and relevance.
- [docs/PLAN_REVIEW.md](docs/PLAN_REVIEW.md) — pressure-test, risks, and product decisions that should not drift.
- [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) — historical staged plan plus current status notes.
- [docs/FIRST_CODING_AGENT_TASK.md](docs/FIRST_CODING_AGENT_TASK.md) — historical Slice 1 task, retained for context.

## Repo layout

| Path | What |
|------|------|
| `service/` | FastAPI backend, SQLite persistence, dashboard HTML, dispatch logic. Rebuild container after changes. |
| `mcp/stdio/` | Host-side MCP bridges (`server.js`, `claude-channel.js`, `runtimes.js`, etc.). Restart the `*-aify` client wrapper after changes. |
| `mcp/stdio/adapters/` | Per-runtime `RuntimeAdapter` classes — session-id capture, resume args, diagnostic env. See `docs/superpowers/specs/2026-05-25-runtime-adapter-design.md`. |
| `mcp/stdio/controllers/` | Per-runtime controllers extracted from `runtimes.js` (Plan 3). 11 files: `base-controller.js` (abstract), one per runtime, plus hermes/codex mode-subclasses for the multi-mode runtimes. Each ≤400 lines. Owns delivery + lifecycle (start/injectMessage/interrupt/steer/terminalSink). |
| `service/runtimes/` | Python mirror of `mcp/stdio/adapters/` — runtime capabilities + Plan 3 console/delivery (per-language adapter packages so server and bridge can each own their concerns). See `docs/superpowers/specs/2026-05-25-runtime-adapter-plan2-capabilities-design.md`. |
| `mcp/sse_server.py` | SSE MCP transport (runs inside the container). Rebuild container after changes. |
| `.claude/skills/aify-comms*/` | Agent-facing usage + debug skills. Mirrored under `.agents/skills/` for Codex. |
| `install.sh` | Client installer. Targets Claude, Codex, and Hermes via `--client`; Pi and OpenCode wrapper installs are intentionally disabled pending focused integration work. |
| `redeploy.sh` | Plan 4 helper. Auto-detects installed `*-aify` wrappers at `~/.local/bin/` and re-runs `install.sh --client X SERVER_URL` for each. Run after pulling new aify-comms changes to refresh wrappers. |

## Setup

```bash
bash setup.sh
docker compose up -d --build
curl http://192.168.100.10:8800/health
curl http://192.168.100.10:8801/health
```

The stable dashboard/API remains on `8800`. A replacement dashboard preview is served separately on `8801` when `docker compose` is up; it reads and writes through the existing `8800` API and leaves the old dashboard functional. Change `.env` only if another service already uses those ports (`SERVICE_PORT` for `8800`, `NEW_DASHBOARD_PORT` for `8801`).

Install the host-side CLI integration on every machine/runtime that should expose `aify-comms`, `codex-aify`, `claude-aify`, or `hermes-aify`. Pick the client you use on that host:

```bash
bash install.sh --client codex http://192.168.100.10:8800 --with-hook
bash install.sh --client claude http://192.168.100.10:8800 --with-hook
bash install.sh --client hermes http://192.168.100.10:8800 --with-hook
# OpenCode wrapper/config install is intentionally disabled until it gets a
# focused integration validation pass.
# Pi/OMP wrapper install is intentionally disabled: managed Pi uses the
# environment bridge plus persistent `omp --mode rpc`, not `omp-aify`.
```

After an update, rerun the relevant install command and restart both the CLI client and any long-running `aify-comms` bridge process so managed spawns and resident sessions load the same code/skills. As a convenience after `git pull`, run `./redeploy.sh` — it auto-detects every `*-aify` wrapper installed at `~/.local/bin/` and re-runs `install.sh --client X` for each, so you don't have to remember which clients are installed on the host.

## Connect Environments

Dashboard spawns require at least one host-side environment bridge. The bridge is the process that actually runs Codex, Claude Code, Hermes, OpenCode, or Oh My Pi on Windows, WSL, Linux, Docker, or a remote machine.

See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md) for Linux/WSL and native Windows bridge commands, `AIFY_CWD_ROOTS` rules, and service URL examples.
See [install.hermes.md](install.hermes.md) for the quick Hermes install path and [docs/HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md) for Hermes-specific MCP, hook, and PTY behavior.

Short version for Linux/WSL:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

Short version for Windows PowerShell:

```powershell
cd C:\path\to\workspace-or-workspace-parent
aify-comms.cmd
```

The service URL defaults to `http://192.168.100.10:8800`. The current directory is always advertised as an allowed workspace root. Extra root arguments are optional safety boundaries, for example `aify-comms /mnt/c/Docker` or `aify-comms.cmd C:\Docker`. The exact project workspace is selected per agent in the dashboard spawn form. Ended sessions and historical failures stay available for debugging, but the dashboard hides them from the normal work queue by default.

Managed runtime defaults are configured from Dashboard **Settings -> Runtime**. Managed model fields are blank by default; blank means Claude Code/Codex use their installed runtime default/latest model. Managed Claude Code and Codex both default to `high` effort/reasoning effort. Hermes and Oh My Pi keep their own runtime defaults unless options are supplied through runtime config. The normal dashboard treats model and effort as global runtime policy, not per-agent tuning.

## Design Rule

Messaging remains the source of truth. A run is a delivery/execution attempt attached to a message, not a separate communication concept.

Managed warm agents are also always backed: the system stores identity, spawn spec, workspace, runtime state, transcript/memory, and recovery policy. Native runtime session handles are used when available; otherwise the bridge emulates continuity from stored transcript and summaries. Operator handle repair updates the saved handle; it is not a context reset.

The container hosts the control plane. Bridges execute. The service must not try to directly launch native Windows/WSL/Linux runtime processes unless a bridge for that environment claims the spawn request.
