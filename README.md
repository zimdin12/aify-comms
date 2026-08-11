# aify-comms

Dashboard-driven communication and control plane for AI coding teams.

`aify-comms` solves the practical problem of running more than one coding agent across Windows, WSL, Linux, and remote machines without losing track of who is live, what they are doing, and how to restart or replace them. The normal workflow is: start the service, run an `aify-comms` bridge in each execution environment, open the dashboard, spawn persistent managed identities into chosen workspaces, then coordinate through chat.

The dashboard is the product surface. Messages are the work interface; runs, sessions, bridges, and handoffs are operational telemetry around those messages.

The intended team behavior is conversational but disciplined: dashboard direct chat is human/operator chat, every message is a small contract, direct agent requests should receive threaded replies, and channel discussion should happen when an agent is named, responsible, asked a question, or has evidence to contribute. Managed turns should not end silently: final text is captured as the current reply, and future work needs a real `comms_send` wake.

## Quick start

```bash
git clone <this repo> && cd aify-comms
./setup.sh                        # generates .env + config from the examples
docker compose up -d --build      # service :8800 (API), Dashboard Next :8801
curl http://localhost:8800/health # {"status":"healthy"}
bash install.sh --client claude http://localhost:8800 --with-hook   # per coding-agent client
aify-comms                        # start an environment bridge in each execution environment
```

Then open `http://localhost:8801`, spawn a managed agent into a workspace, and message it. Legacy `:8800/api/v1/dashboard` bookmarks redirect to Dashboard Next. Details: [Setup](#setup) below and the per-client install guides.

## Agent playbooks — install / update, and how to VERIFY it took effect

If you are an agent doing this work, read this section first. **Every flow here fails silently.** Nothing errors, everything looks installed, and the thing you changed is not the thing that is running:

- the container keeps serving the build from *before* your rebuild;
- `~/.aify-comms` holds your new bridge code, but every **running** wrapper still executes the copy it loaded at boot — so a fix "ships" and changes nothing;
- an agent launched without `--aify-agent` registers and messages perfectly while its status is structurally dead;
- the OpenAI quota panel reads a token from a file nobody has.

So **do not report success from the absence of an error.** Every flow ends the same way:

```bash
aify-comms doctor            # human-readable
aify-comms doctor --json     # {ok, checks:[{id, ok, code, detail, fix}]} — parse this
aify-comms doctor --strict   # exit 1 if anything failed (use in scripts/CI)
```

`aify-comms doctor` proves each claim against the running system (build stamps, process start times, process environments, a live API call). It is installed by `install.sh`. **Done means `ok: true`** — or a check whose `fix` you have deliberately deferred and reported.

| Flow | Do | Then |
|---|---|---|
| **1. Install the client integration** (bridge + wrapper for a runtime) | `bash install.sh --client <claude\|codex\|hermes> http://<service>:8800 --with-hook` | `aify-comms doctor` → `wrappers`, `bridge-installed` green. Restart the client so it loads the new bridge. |
| **2. Install / run the service** (container) | `./setup.sh` (first time), then `bash scripts/stamp.sh && docker compose up -d --build` | `aify-comms doctor` → `service` must read **`build <sha> == repo HEAD`**. `curl :8800/health` alone is NOT enough — a healthy container can be serving last week's code. |
| **3. Update local integrations** (after `git pull`) | `bash install.sh --client <runtime>` — this re-copies `mcp/stdio/` into `~/.aify-comms`. **Editing the checkout does nothing on its own.** | `aify-comms doctor` → `bridge-installed` must equal repo HEAD, **and `bridge-running` must be green**. If it lists agents, they are still executing the old code and must be restarted before your change is real. |
| **4. Update the container** | `git pull && bash scripts/stamp.sh && docker compose up -d --build` | `aify-comms doctor` → `service` == repo HEAD. Skipping `stamp.sh` makes `/version` lie about what is deployed. |

Two rules that cost real hours to learn:

1. **Installing does not reload a running bridge.** A process keeps the code it loaded at startup. After any bridge change, the agents using it must restart — `aify-comms doctor`'s `bridge-running` check tells you exactly which ones haven't. (`--resume <handle>` preserves an agent's conversation, so the restart is cheap.)
2. **Always launch a registered agent with its id** (`--aify-agent <id>`). Without it the agent works in every visible way but has no status at all. `aify-comms doctor`'s `agent-identity` check catches it; a plain, unregistered `claude-aify` session is legitimately id-less and is not flagged.
3. **When a managed worker dies, its own console holds the answer — and an agent can read it** (v0.2). `comms_console_tail(agentId="…")` no longer needs a live console: with the worker gone it returns that worker's last recorded output, marked `NOT LIVE`, with the fatal line first. So the agent that hit the failure can diagnose it instead of asking you to read a terminal. The same data is at `GET /agents/<id>/console` (`live:false, historical:true, failureLine`). This exists because on 2026-08-07 a managed hermes worker died 65s after spawn, the cause (`hermes dashboard … did not become ready`) sat in the database for 2.5 hours, and the requesting agent was told something false while a human read the real error out loud.

The OpenAI quota panel additionally needs the **`codex` CLI signed in** (`codex login`) — hermes holds no OpenAI token of its own, it delegates to codex's store. `install.sh` prints a `[usage] OK` / `[usage] WARNING` verdict, and `aify-comms doctor` re-checks it by actually calling the API (an expired token passes a file check and fails for real).

## Security — read before exposing beyond localhost

By default the service runs **without authentication** (`api_key=""`), with **CORS `*`**, and binds **`0.0.0.0`** — a deliberate LAN-trust posture for a private network. Every mutating endpoint (including typing into live agent consoles) is open to anything that can reach the port, and the compose file mounts host agent credentials (e.g. `~/.claude`) into the container for the runtimes to use. Before running anywhere untrusted:

- set a real `API_KEY` in `.env` (clients send it via `AIFY_API_KEY`),
- bind the published ports to loopback in `docker-compose.yml` (`127.0.0.1:8800:8800`), and
- scope `cors_origins` in `config/service.json` to your dashboard origin.

The full audit notes live in [KNOWN_ISSUES.md](KNOWN_ISSUES.md) (security defaults section).

## Notifications — hearing an agent without watching the dashboard

Two independent halves. Both are **off by default** and neither notifies on fleet chatter: only
messages addressed to you (`to: dashboard`) and channels you have actually joined qualify. That
restraint is the feature — the fleet produced 3,883 messages in 14 days, and a notification per
message would be switched off within the hour. Repeats from the same sender on the same subject
coalesce into one alert per 90 seconds.

**Desktop** — nothing to configure. Open the dashboard, turn notifications on in the UI, grant the
browser permission. Works at `http://localhost:8801` with no TLS (localhost is a secure context);
for a LAN address the browser requires HTTPS, which is what the optional
`docker compose --profile https up -d` proxy is for. A focused tab stays quiet — you are already
looking at it.

**Phone** — one line in `.env`, using [ntfy](https://ntfy.sh):

```bash
AIFY_NTFY_URL=https://ntfy.sh/your-private-topic-name    # then: docker compose up -d
```

Install the ntfy app, subscribe to the same topic, done. No PWA, no service worker, no push
subscriptions — the service makes one outbound POST per alert, on a background worker that is never
on the message-send path (`docs/V0.4_SPEC.md` is the contract, and it exists because the first
version of that sentence was ambiguous enough to permit blocking the fleet on a phone alert).

> **The topic URL is a credential.** Anyone who has it can read every notification you receive and
> publish to it. Keep it in `.env` (gitignored) — never `config/service.json`, which is generated.
> The service never logs it and never returns it from any endpoint; `/health` reports the relay's
> state with no URL in it at all.

Check it is working with `curl -s localhost:8800/health | jq .ntfy` — `enabled`, `workerAlive`,
`queueDepth`, `sent`, `droppedFull`, `sendFailures`, and the last success/failure times. A failed
alert is logged and dropped, never retried: the message it describes is already delivered, and a
retry storm against a third-party host while the fleet is busy would be the worse outcome.

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
7. Talk to it in direct chat or channels, assign work through messages, inspect output, stop/restart/reset it, or compact it into a fresh backing.

Manual `comms_register(...)` is an advanced/debug and resident-CLI path, not the normal dashboard-managed workflow.

## Managed And Resident Modes

Use **managed mode** for the normal persistent team. Start `aify-comms` in each Windows/WSL/Linux environment you want to execute work in, then spawn agents from the dashboard. Managed identities have a saved environment, workspace, runtime, spawn spec, native handle when available, and session history. Runtime adapters choose a dashboard-symmetric managed delivery path: Claude Code, Codex, and Hermes use bridge-owned wrapper PTYs where configured, while Pi and OpenCode use native controller delivery with synthesized Console streams. Browser Console attaches to the backing owner without switching identity modes. If wrapper backing cannot be established for a wrapper-capable runtime, the native managed adapter remains the fallback path. The dashboard can restart, stop, compact, or reset agents without keeping a separate CLI tab open.

Managed sessions are **bridge-owned**: the environment bridge that spawned them owns their worker processes. Two consequences follow. First, **restarting `aify-comms` is a clean slate for obsolete managed workers** — the bridge tears down sessions it still owns and, on the next start, sweeps survivors of a crashed predecessor. The sweep is process-safe: a live resident wrapper protects its process family even if backend ownership metadata is stale, so restarting an environment bridge must not disconnect an operator's resident CLI. Second, **`online` means deliverable, not just present** — a managed agent reads `online` only when it has a live claimer behind it (the delivery loop / channel-sidecar that actually receives work), not merely because its gateway answers or a Console is open. A send to a managed agent whose claimer is gone fails fast with an actionable reason instead of queuing against a worker that will never claim it.

Use **resident mode** when you intentionally open a real runtime terminal and want that visible CLI to receive live messages. Start it with `claude-aify --aify-agent <id>`, `codex-aify --aify-agent <id>`, or `hermes-aify --aify-agent <id>` so the wrapper registers that terminal as the live resident candidate and keeps a fresh bridge heartbeat. Raw `POST /api/v1/agents` metadata registration is not a live resident bridge and will be reported as `offline`; use the wrapper's `comms_register` MCP tool or wrapper auto-registration from the visible session. Legacy `omp-aify` / `pi-aify` wrappers are not installed by default; triggerable Pi delivery is managed RPC because OMP is single-client. Ownership does not switch automatically. Use **Sessions -> Actions -> Switch to resident** or the Chat details switch when the visible CLI should own delivery; use **Switch to managed** when dashboard sends should return to the managed backing.

Fresh native handles should come from a new spawn or explicit **Reset**. Ordinary Restart/Adopt should preserve the stored handle and fail visibly if the handle is locked or cannot be resumed.

If the dashboard is pointing at the wrong saved native context and you know the correct Claude session ID, Codex thread ID, Hermes session ID, OpenCode session ID, or Pi handle, use **Set handle** from Chat details or Sessions actions. This repairs the saved handle and runtime state without creating a fresh context. Use it only for known-good handles; a wrong value binds the identity to the wrong native memory.

Normal dashboard chat is live-delivery gated, but an `available` managed agent (registered, environment online, no live worker yet) is **auto-started on send** — the service cold-starts a bridge-claimed worker and auto-binds the freshest online environment that advertises the runtime, so idle agents don't all have to boot when you open the dashboard. Targets still fail visibly (message not stored for a future run) when they are `offline`, when no online environment can host the runtime, or when they are explicitly **disabled**: an operator **Stop** sets the agent to `stopped` (a hard block that never auto-starts and refuses other agents' sends until **Restart**). Managed delivery is runtime-specific but dashboard-symmetric: supported managed runtimes start or reuse a bridge-owned backing, deliver one bracketed dashboard turn through that runtime's channel/app-server/gateway/RPC/PTY path, and stay `working` until the reply closes the run; if the active terminal output clearly asks for operator input or a decision, the agent is shown as `blocked` instead of healthy working. Managed Claude boot prompts are answered only by the bridge's cursor-verified rules; dashboard terminal input remains raw and no service/dashboard layer injects blind confirmation keys. The normal Claude footer/prompt chrome is not enough to mark `blocked`; when Claude returns to an idle prompt after producing visible terminal output but forgets to send an explicit `aify-comms` reply, reconcile closes the run as completed-without-reply so it does not pin `working`. Unthreaded completion-style `info` messages such as `Done`, `Pushed`, or `Fixed` can satisfy the active terminal run during send/reconcile so finished work does not stay open. If wrapper-backed Codex/Hermes or another terminal backing cannot be established, the native managed-controller path remains the fallback. Browser Console is an attachment to the backing runtime, not a separate owner. Stopped/failed Console terminals are cleared as the current session binding and remain historical only, so the dashboard does not keep showing an old terminal buffer as the current Console. Busy live targets receive ordinary sends as current-run steer when supported, or as queued/merged next-turn work when steering is not available; the explicit **Queue** action waits behind real active/queued work, but idle terminal-backed agents still receive normal live delivery instead of orphaned queue rows. Required handoffs are repaired automatically when a terminal run finishes without an explicit reply, dashboard-started managed runs persist final text back into dashboard chat, due reply-contract reminders defer while a target is busy or blocked and retry when that agent returns online, and stale unowned active runs are reconciled periodically so old status rows do not pin agents as `working`. While a `working` agent's terminal is receiving output, its yellow status dot briefly pulses orange as a live-output hint; this is a visual activity signal, not a separate status.

The **Work Loop** page turns message/run state into operational contracts: who asked, who owns the reply, whether the run is queued/working/overdue/answered, when it was last reminded, and whether old read receipts or handoffs need repair. Requests, reviews, and errors require replies by default; routine `info` is a note unless `requireReply` is explicitly set. An overdue reminder is an informational nudge authored by the **original requester**, addressed to the original target, and linked to the original request; it tells the target to answer that original message with `type="response"` and `inReplyTo=<original-id>`. It does not create a second reply obligation. A linked response routes back to the original requester and closes the original contract, including when its run was still queued. Reminders repeat until that contract is answered or operator-closed. Busy targets are deferred and reminded when they return online instead of injecting more text into an active turn. Operators can close individual or selected contracts as reviewed from Work Loop; chat and run audit history remain available. Work Loop does not replace chat; it makes the implicit obligations in chat visible enough for an autonomous team to keep moving without guessing from raw unread counts.

Reliable compaction in `aify-comms` means creating a fresh managed backing from an editable handoff packet and recent comms context. It is portable across Claude Code, Codex, Hermes, OpenCode, and Oh My Pi, and it defaults to the same agent ID so chats and agent identity remain stable. Native in-place compaction is runtime-adapter dependent; current managed adapters do not expose a verified internal compact API.

Three settings shape the managed-delivery surface. `insert_messages_via_console=false` is the default channel-route mode for managed Claude (`claude-channel.js` claims the dispatch and emits `<channel source="aify-comms-channel" ...>` MCP notifications — same protocol resident Claude already uses). `managed_pty_eager_spawn=true` (paired with `managed_terminal_backing_enabled=true`) proactively launches wrapper PTYs at spawn-request running transition when the runtime uses a terminal backing. **`managed_via_wrapper=["codex","hermes"]` is the default wrapper-backed path for Codex and Hermes**: managed dispatches are backed by bridge-owned `codex-aify` / `hermes-aify` PTYs. Codex is claimed by the wrapper child and delivered through its app-server; Hermes is claimed by the per-agent `hermes-managed-host.js` sidecar and delivered through gateway `prompt.submit`, while the wrapper child is excluded from Hermes claims. Dashboard Console renders the real TUI via xterm.js. Pi is structurally excluded from wrapper mode and uses the persistent native OMP RPC virtual terminal. See [DECISIONS.md](DECISIONS.md) for the architectural rationale.

Managed Pi specifically uses a **persistent** `omp --mode rpc` child per agent (spawned on the first dispatch, reused across subsequent ones, 24-hour idle timeout). Each RPC event is formatted into a synthesized `terminal_session` row marked `command='aify://virtual-rpc/pi'` — the dashboard's Console pane shows it like a real terminal, and operator input typed there round-trips as a new RPC turn through the same persistent child. See [install.pi.md](install.pi.md) and [DECISIONS.md](DECISIONS.md) for the full delivery path.

Every `*-aify` wrapper accepts `--resident` and `--managed` flags to declare session mode explicitly; precedence is inherited `AIFY_SESSION_MODE` env > flag > TTY auto-detect (`[ -t 0 ]`). Bridge-spawned wrappers always inherit `AIFY_SESSION_MODE=managed` from `terminal-env.js`; operator-launched wrappers from a real terminal default to `resident`. `claude-aify` additionally exports `AIFY_CHANNELS_ENABLED=1` so its register call carries `runtime_config.channelEnabled=true` (precondition for resident-run/interrupt/steer caps surviving the server-side strip).

By default `claude-aify` launches Claude with the operator's FULL `~/.claude.json` MCP server list — which already includes `aify-comms` + `aify-comms-channel` because the installer merges them in at install time. Setting `AIFY_CLAUDE_STRICT_MCP=1` in the launching shell opts into strict isolation: the wrapper then launches Claude with `--strict-mcp-config` and a runtime-generated minimal MCP config containing ONLY `aify-comms` + `aify-comms-channel`. That strict mode is the escape hatch for the known Claude Code stdio MCP init race bug ([#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)) — when the race bites, `aify-comms-channel` fails to register its channel notification listener and channel-routed dispatches sit queued forever despite `delivered` status; the strict 2-server config restores guaranteed channel wake at the cost of the operator's other MCP servers. On Windows Git Bash the wrapper uses `cygpath -m` to convert install paths to native format.

## Current State

This repo is the canonical `aify-comms` codebase. The dashboard and environment lifecycle work has been folded back into this product rather than living in a separate bridge fork. Existing message, channel, dispatch, artifact, and MCP APIs should keep working while the dashboard becomes the normal way to manage agents.

Important starting docs:

- [AGENTS.md](AGENTS.md) — coding-agent instructions for this repo.
- [docs/V0.2_PLAN.md](docs/V0.2_PLAN.md) — **the current work queue.** Backlog from the v0.1 release review, with the two open operator decisions at the top.
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
| `install.sh` | Client installer. Targets Claude, Codex, and Hermes via `--client`. Pi and OpenCode client/resident wrapper installs are disabled; their managed runtimes use a shared environment bridge installed by a supported client. |
| `redeploy.sh` | Plan 4 helper. Auto-detects installed `*-aify` wrappers at `~/.local/bin/` and re-runs `install.sh --client X SERVER_URL` for each. Run after pulling new aify-comms changes to refresh wrappers. |

## Setup

```bash
bash setup.sh
docker compose up -d --build
curl http://localhost:8800/health
curl http://localhost:8801/health
```

The API remains on `8800`; Dashboard Next (the only operator UI) is served on `8801` when `docker compose` is up. Requests to the `8800` root redirect to Dashboard Next, which reads and writes through the same `8800` API. Change `.env` only if another service already uses those ports (`SERVICE_PORT` for `8800`, `NEW_DASHBOARD_PORT` for `8801`).

Install the host-side CLI integration on every machine/runtime that should expose `aify-comms`, `codex-aify`, `claude-aify`, or `hermes-aify`. Pick the client you use on that host:

```bash
bash install.sh --client codex http://localhost:8800 --with-hook
bash install.sh --client claude http://localhost:8800 --with-hook
bash install.sh --client hermes http://localhost:8800 --with-hook
# OpenCode wrapper/config install is intentionally disabled until it gets a
# focused integration validation pass.
# Pi/OMP wrapper install is intentionally disabled: managed Pi uses the
# environment bridge plus persistent `omp --mode rpc`, not `omp-aify`.
```

After an update, rerun the relevant install command and restart both the CLI client and any long-running `aify-comms` bridge process so managed spawns and resident sessions load the same code/skills. As a convenience after `git pull`, run `./redeploy.sh` — it auto-detects every `*-aify` wrapper installed at `~/.local/bin/` and re-runs `install.sh --client X` for each, so you don't have to remember which clients are installed on the host.

After updating **Hermes itself**, rerun `bash install.sh --client hermes …` — a hermes update wipes the prebuilt `hermes_cli/web_dist`, and `hermes-aify` cannot serve the gateway/console without it. The installer's one-time `web_dist` prebuild is idempotent (a noop when `web_dist/index.html` already exists), so reinstalling after a hermes upgrade rebuilds the missing bundle.

**Checking your version / "N commits behind".** The dashboard header shows a build badge (`v… · <sha>`); when the checkout is behind `origin/main` it turns into a warning pill (`⚠ N commits behind — run git pull && ./redeploy.sh`). The same data is at `GET /version` (JSON: build SHA + a cached GitHub-compare `behind_by`), and `aify-comms --version` prints the installed host-bridge SHA plus a fresh behind-count. The behind-count is a **warning, not an auto-update** — the container has no `.git` to rebuild itself, so updating is the manual `git pull && ./redeploy.sh` (+ `docker compose up -d --build` for the service). The build SHA is stamped at build time by `scripts/stamp.sh` (the container's `.git` is excluded from the image).

The dashboard opens on the **Chat** page (the default landing surface; your last-visited page is still remembered), with a persistent collapsible sidebar and per-conversation chat analytics (open a direct message, click it again to see message-rate, top peers, and total working time).

## Connect Environments

Dashboard spawns require at least one host-side environment bridge. The bridge is the process that actually runs Codex, Claude Code, Hermes, OpenCode, or Oh My Pi on Windows, WSL, Linux, macOS, Docker, or a remote machine.

See [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md) for Linux/macOS/WSL and native Windows bridge commands, `AIFY_CWD_ROOTS` rules, and service URL examples.
See [install.hermes.md](install.hermes.md) for the quick Hermes install path and [docs/HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md) for Hermes-specific MCP, hook, and PTY behavior.

Short version for Linux/macOS/WSL:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

Short version for Windows PowerShell:

```powershell
cd C:\path\to\workspace-or-workspace-parent
aify-comms.cmd
```

The service URL defaults to `http://localhost:8800`. The current directory is always advertised as an allowed workspace root. Extra root arguments are optional safety boundaries, for example `aify-comms ~/work`, `aify-comms /mnt/c/Docker`, or `aify-comms.cmd C:\Docker`. The exact project workspace is selected per agent in the dashboard spawn form. Ended sessions and historical failures stay available for debugging, but the dashboard hides them from the normal work queue by default.

Managed runtime defaults are configured from Dashboard **Settings -> Runtime**. Managed model fields are blank by default; blank means Claude Code/Codex use their installed runtime default/latest model. Managed Claude Code and Codex both default to `high` effort/reasoning effort. Hermes and Oh My Pi keep their own runtime defaults unless options are supplied through runtime config. Runtime Settings also expose the delivery policy toggles that used to be API-only: manual resident/managed switch visibility, managed terminal backing, eager managed PTY spawn, wrapper-backed runtime list, and legacy console injection. The normal dashboard treats model, effort, and delivery policy as global runtime policy, not per-agent tuning.

## Design Rule

Messaging remains the source of truth. A run is a delivery/execution attempt attached to a message, not a separate communication concept.

Managed warm agents are also always backed: the system stores identity, spawn spec, workspace, runtime state, transcript/memory, and recovery policy. Native runtime session handles are used when available; otherwise the bridge emulates continuity from stored transcript and summaries. Operator handle repair updates the saved handle; it is not a context reset.

The container hosts the control plane. Bridges execute. The service must not try to directly launch native Windows/WSL/Linux runtime processes unless a bridge for that environment claims the spawn request.

## License & contributing

MIT — see [LICENSE](LICENSE). Issues and questions welcome; for larger PRs please open an issue first to discuss direction.
