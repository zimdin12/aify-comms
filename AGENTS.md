# aify-comms — Codex project notes

This repo is `aify-comms`. The product is a dashboard-driven headless agent control plane: run aify-env
on each host, spawn persistent managed agents into selected workspaces, chat with agents and channels,
monitor tracked work, and stop/restart/reset sessions without requiring manual `comms_register` from
dashboard-spawned agents.

## Primary Documents

**Three repos, one stack.** This one owns messaging, dispatch and sessions;
[aify-wrapper](https://github.com/zimdin12/aify-wrapper) owns the launchers and arrives here as a
pinned npm dependency; [aify-env](https://github.com/zimdin12/aify-env) owns processes and terminals on
a host and is the only spawner. Which concern lives where is
[docs/AIFY_ENV_BOUNDARY.md](docs/AIFY_ENV_BOUNDARY.md).

This file stays high-level. Anything with a number in it (suite counts, file sizes, which gate fails
on what, the release recipe) lives in [CLAUDE.md](CLAUDE.md). Read that before changing code; read this
for what the product is trying to be.

- [README.md](README.md) — repo overview, install and day-to-day use.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how it is built: the tiers, the service layering, the message-to-work path, and the layer rules with their gates.
- [docs/TARGET_ARCHITECTURE.md](docs/TARGET_ARCHITECTURE.md) — the shape it is heading for, as the operator specified it.
- [docs/SESSION_MODEL.md](docs/SESSION_MODEL.md) — backed managed-warm sessions and recovery rules.
- [docs/OPERATING_MODES.md](docs/OPERATING_MODES.md) — managed and resident agents, delivery per runtime, runtime settings.
- [docs/README.md](docs/README.md) — the index of `docs/`: which documents describe the system now and which are records of finished work.

## Current Product Thesis

The user should be able to:

- open the dashboard
- see connected environments such as WSL, Windows, Linux host, Docker, or remote machines
- spawn Claude/Codex/Hermes agents into a chosen environment and workspace
- have spawned agents auto-register with stable identity/session metadata
- have managed-warm agents backed by stored spawn spec, workspace, transcript/memory, runtime handles when available, and recovery policy
- message agents in direct chats, group chats, and channels
- watch run state, handoff state, session health, and available runtime output; token/cost telemetry should appear only when an adapter exposes it honestly
- stop, restart, or resume agents from the dashboard

Manual registration remains available for debugging and compatibility, but it is not the normal workflow.

## Developing

```bash
git status --short
bash scripts/stamp.sh && docker compose up -d --build
curl http://localhost:8800/health
aify-comms doctor
```

Changes under `service/`, `mcp/` (except `mcp/stdio/`) and `config/` need a container rebuild. Changes
under `mcp/stdio/` need `install.sh` re-run and the affected agent relaunched. Restarting aify-env is
the operator's action: a second one supersedes the first and reaps its workers.

## Engineering Constraints

- Keep existing `aify-comms` message/channel/dispatch APIs working while adding lifecycle features.
- Do not create a second message concept. Dispatch/run state attaches to messages.
- Environments are first-class. The service container never spawns a process itself: aify-env on the target host claims each spawn request and runs it.
- Spawning must be auditable: every spawned agent needs an environment ID, workspace, runtime, command/profile, process/session handle, lifecycle status, and owner.
- Managed warm is the default teammate mode. Resident is for human-open CLI sessions like `codex-aify`, `claude-aify`, and `hermes-aify`. Pi is deprecated and managed OpenCode is unsupported; their resident registrations are presence/debug metadata only.
- Persistent/backed does not imply CLI-attachable. Use capability flags.
- Prefer adapters over hardcoded CLI assumptions. `claude -p`, `codex exec`, and similar flags can change; encapsulate them behind runtime adapter modules and tests.
- Dashboard should be usable without reading docs: visible env selector, spawn form, agent list, chat, channels, worker/session controls, and clear run/session evidence.
- Dashboard should be a real web application, not a raw operational table. Use compact primary views plus inspectors/drawers for IDs, logs, JSON, and long text.

## Runtime symmetry

Adding or changing a harness follows two principles. Full rationale + per-runtime detail:
[docs/superpowers/specs/2026-05-30-runtime-symmetry-and-session-governance-design.md](docs/superpowers/specs/2026-05-30-runtime-symmetry-and-session-governance-design.md).

- **P1 — Symmetry is the default.** Every harness realizes the SAME triad and behavior
  falls out. Code paths branch on **capabilities**, never on `if runtime == "x"`. Adding a
  harness = implement the triad. The registry-driven symmetry-guard test
  (`mcp/stdio/tests/adapter-contract-symmetry.test.js`) iterates every registered adapter and
  fails loudly if one omits a contract method.
- **P2 — Asymmetries are explicit and justified.** Where a runtime genuinely cannot be
  symmetric, express it as a capability flag **and** carry a `// ASYMMETRY(<runtime>): <why>`
  comment at the deviation. No silent special-casing.

### The triad

| Layer | File | Responsibility (symmetric across runtimes) |
|------|------|--------------------------------------------|
| **Adapter** (bridge) | `mcp/stdio/adapters/<rt>.js` | Capability flags (`supportsManaged/Resident/Interrupt/Steering`), `discoverSessionId()`, `sessionIdSource ∈ {pinned, captured, resume}`, `resumeCommand(sessionId)`, diagnostic env. |
| **Controller / delivery** (bridge) | `mcp/stdio/controllers/<rt>-*.js` (+ a `*-channel.js` sidecar for channel runtimes) | Deliver a dispatched run to the agent and surface the reply. Two shapes: **sidecar-channel** (claude, hermes) and **controller** (codex). |
| **Runtime class** (service) | `service/runtimes/<rt>.py` | Server-side execution-mode resolution, status/deliverability inputs, channel-enabled flag, resume-command metadata for the dashboard. |

### Realization matrix (symmetry + documented asymmetries)

| Concern | claude | hermes | codex |
|--------|--------|--------|-------|
| Session id source | `captured` (SessionStart hook) | `captured` (native session id via active-session file, stored as the handle) | `resume` |
| Delivery shape | sidecar-channel | gateway-host (WS tui_gateway) | controller |
| Reply author | agent self (`comms_send` + `inReplyTo`) | agent self (`comms_send`) | agent self |
| Owns its own process | process per agent | `ASYMMETRY(hermes)`: hidden `hermes dashboard` gateway host (embedded chat via `HERMES_DASHBOARD_TUI=1`, `HERMES_YOLO_MODE=1` so gateway-hosted turns never prompt) + a visible `hermes --tui` PTY resuming the agent's real native session, rendered in the dashboard console | process per agent |
| Wake mechanism | in-process MCP server-push | `ASYMMETRY(hermes)`: `hermes-managed-host.js` finds the real session in `session.active_list`, uses WS `prompt.submit` when idle and native `session.steer` for ordinary busy sends; explicit queue and a 4009 submit race wait for turn-end | controller inject |
| Advertised mid-turn steering | yes | yes — ordinary busy sends use native `session.steer`; explicit queue waits for turn-end | yes |
| Can be force-pinned | `ASYMMETRY(claude)`: mints its own id → `captured` not `pinned`; we capture+resume+guard | no — `captured` like claude: hermes uses its own native session id | partial (resume id) |

Pi (deprecated) follows the codex column. Shrink the asymmetry column over time.

### Session lifecycle verbs

| Verb | Meaning |
|------|---------|
| Spawn | Create a fresh managed backing (no resume). |
| Stop | Halt the running backing; keep spec/handle/identity. Reversible via Restart. |
| Restart | Re-spawn and RESUME native context (`resume_policy=native_first`; carries `session_handle`). |
| Reset (fresh context) | Re-spawn discarding native handle/state (`resume_policy=fresh_context`; the route action is `recreate`). |
| Resume wake | Re-enable wake/dispatch for a stopped RESIDENT agent — `POST /agents/{id}/control` action=`resume` (no spawn). |
| Pause for CLI | Hand session ownership to the terminal (`cli_takeover`); return via Restart. |
| Switch managed/resident | Ownership flip (see below). |
| Edit… → Native session handle | Operator repair of the native resume target. |
| Interrupt / Steer | Run-level control. |
| Remove | Tombstone the identity. |
| Forget environment | Hide an offline environment; identities, chats and records remain. |

`POST /sessions/{id}/control` accepts exactly `restart`, `recreate`, `stop` and `cli_takeover`.

### resident↔managed switch + session display status

- **Full-duplex (both modes):** claude-code, codex, hermes. **Managed-only** (resident =
  presence/debug metadata, not live-wakeable): pi, opencode. `managed→resident` is
  **rejected** for them (`switch_agent_session_mode` guards on the adapter's
  `supports_resident`; the dashboard hides their "Switch to resident" button).
- **`resident→managed` carries the native `session_handle`** into the coldstart spawn, so the
  managed worker resumes the same codex thread / hermes session / claude transcript instead
  of starting fresh. Per-agent chat always carries over (keyed per agent). Binding a handle
  another live agent already owns warns.
- **Session display status is DERIVED from live truth** (`_compute_session_display_status`),
  exactly like the agent dot: managed keys on the live `terminal_sessions` row, resident on a
  fresh non-superseded bridge. The stored `agent_sessions.status`/`terminal_status` is a cache,
  never the display source. One canonical `LIVE_SESSION_STATUSES`, one `_agent_liveness`
  predicate.

### Agent status

The operator-facing status vocabulary, with what each state means and the normal action, is the
table in [.claude/skills/aify-comms/references/operations.md](.claude/skills/aify-comms/references/operations.md)
("Status Meanings"); `VALID_STATUSES` in `service/status_engine.py` is the code's list. Status is
proof-based and never time-decayed.

Status is decided by `derive()`, a pure function of `StatusInputs` and the sole authority.
`working`/`blocked` come from turn events folded into `agent_status_state.in_turn` /
`awaiting_input`, fed for every runtime: the dispatch `/heartbeat turnBusy` field drives a
`turn_start`/`turn_end` status event alongside the resident turn hooks. The only time terms are
dropped-event liveness backstops (e.g. an `in_turn` ceiling to un-latch a lost turn-end), not
status decay. The served status reads from the in-memory `_LIVE_STATE_CACHE`; see CLAUDE.md's
single-worker constraint.

A resident bridge POSTs `/agents/{id}/resident-lost` on clean exit, so it drops to `stopped`
without waiting out the resident lease (a crash is caught when the lease goes silent → `offline`).
A **managed** agent hitting that same endpoint returns to `available` (cold-startable), not
`stopped`.

### Adding a new harness

Implement the triad (adapter + controller/delivery + runtime class), advertise honest
capability flags, mark any deviation with `// ASYMMETRY(<rt>): <why>`, and make
`adapter-contract-symmetry.test.js` pass — fix the adapter, not the test.

## Current Implementation Bias

Preserve the existing message/channel/artifact APIs and keep the dashboard as the normal control surface. New work should reduce duplicate concepts, keep environment-backed managed agents as the default teammate path, and add tests around lifecycle edge cases before changing runtime behavior.
