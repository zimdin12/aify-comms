# aify-comms — Codex project notes

This repo is `aify-comms`. The product is a dashboard-driven headless agent control plane: connect Windows/WSL/Linux environment bridges, spawn persistent managed agents into selected workspaces, chat with agents and channels, monitor tracked work, and stop/restart/recover sessions without requiring manual `comms_register` from dashboard-spawned agents.

## Primary Documents

- [README.md](README.md) — repo overview.
- [docs/PRODUCT_BRIEF.md](docs/PRODUCT_BRIEF.md) — goals, non-goals, user stories.
- [docs/ARCHITECTURE_PLAN.md](docs/ARCHITECTURE_PLAN.md) — target architecture and data model.
- [docs/SESSION_MODEL.md](docs/SESSION_MODEL.md) — backed managed-warm sessions and recovery rules.
- [docs/DASHBOARD_SPEC.md](docs/DASHBOARD_SPEC.md) — dashboard UX direction.
- [docs/WEB_APP_DESIGN.md](docs/WEB_APP_DESIGN.md) — web app UX/architecture quality bar.
- [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md) — concise engineering guide for coding agents.
- [docs/PLAN_REVIEW.md](docs/PLAN_REVIEW.md) — pressure-test and risks to keep in mind.
- [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) — historical staged plan plus current status notes.
- [docs/FIRST_CODING_AGENT_TASK.md](docs/FIRST_CODING_AGENT_TASK.md) — historical Slice 1 task; useful for context only.

Compatibility docs and APIs are still present where useful. Treat the dashboard/live-wake docs as authoritative for normal product behavior.

## Current Product Thesis

The user should be able to:

- open the dashboard
- see connected environments such as WSL, Windows, Linux host, Docker, or remote machines
- spawn Claude/Codex/Hermes/OpenCode agents into a chosen environment and workspace
- have spawned agents auto-register with stable identity/session metadata
- have managed-warm agents backed by stored spawn spec, workspace, transcript/memory, runtime handles when available, and recovery policy
- message agents in direct chats, group chats, and channels
- watch run state, handoff state, bridge/session health, and available runtime output; token/cost telemetry should appear only when an adapter exposes it honestly
- stop, restart, or resume agents from the dashboard

Manual registration remains available for debugging and compatibility, but it is not the normal workflow.

## Developing

```bash
git status --short
docker compose up -d --build
curl http://localhost:8800/health
```

Backend changes under `service/`, `mcp/`, and `config/` require a container rebuild or hot-copy/restart during local iteration. Host-side bridge changes under `mcp/stdio/` require restarting the relevant wrapper/bridge process.

## Engineering Constraints

- Keep existing `aify-comms` message/channel/dispatch APIs working while adding lifecycle features.
- Do not create a second message concept. Dispatch/run state attaches to messages.
- Environment bridges are first-class. A service container cannot directly spawn native Windows processes unless a Windows bridge is connected and claims that spawn.
- Spawning must be auditable: every spawned agent needs an environment ID, workspace, runtime, command/profile, process/session handle, lifecycle status, and owner.
- Managed warm is the default teammate mode. Run-once is advanced/internal; resident-visible is for human-open CLI sessions like `codex-aify`, `claude-aify`, and `hermes-aify`. Pi/OpenCode resident registrations are presence/debug metadata only until a real multi-client resident surface exists; triggerable Pi/OpenCode delivery is managed.
- Persistent/backed does not imply CLI-attachable. Use capability flags.
- Prefer adapters over hardcoded CLI assumptions. `claude -p`, `codex exec`, and `opencode run` flags can change; encapsulate them behind runtime adapter modules and tests.
- Dashboard should be usable without reading docs: visible env selector, spawn form, agent list, chat, channels, worker/session controls, and clear run/session evidence.
- Dashboard should be a real web application, not a raw operational table. Use compact primary views plus inspectors/drawers for IDs, logs, JSON, and long text.

## Runtime symmetry

Adding or changing a harness (claude, codex, hermes, pi, opencode, future) follows two
principles. Full rationale + per-runtime detail:
[docs/superpowers/specs/2026-05-30-runtime-symmetry-and-session-governance-design.md](docs/superpowers/specs/2026-05-30-runtime-symmetry-and-session-governance-design.md).

- **P1 — Symmetry is the default.** Every harness realizes the SAME triad and behavior
  falls out. Code paths branch on **capabilities**, never on `if runtime == "x"`. Adding a
  harness = implement the triad. The registry-driven symmetry-guard test
  (`mcp/stdio/tests/adapter-contract-symmetry.test.js`) iterates every registered adapter and
  fails loudly if one omits a contract method — that's what enforces completeness.
- **P2 — Asymmetries are explicit and justified.** Where a runtime genuinely cannot be
  symmetric, express it as a capability flag **and** carry a `// ASYMMETRY(<runtime>): <why>`
  comment at the deviation. No silent special-casing.

### The triad

| Layer | File | Responsibility (symmetric across runtimes) |
|------|------|--------------------------------------------|
| **Adapter** (bridge) | `mcp/stdio/adapters/<rt>.js` | Capability flags (`supportsManaged/Resident/Interrupt/Steering`), `discoverSessionId()`, `sessionIdSource ∈ {pinned, captured, resume}`, `resumeCommand(sessionId)`, diagnostic env. |
| **Controller / delivery** (bridge) | `mcp/stdio/controllers/<rt>-*.js` (+ a `*-channel.js` sidecar for channel runtimes) | Deliver a dispatched run to the agent and surface the reply. Two shapes: **sidecar-channel** (claude, hermes) and **controller-PTY/native** (codex, pi). |
| **Runtime class** (service) | `service/runtimes/<rt>.py` | Server-side execution-mode resolution, status/deliverability inputs, channel-enabled flag, resume-command metadata for the dashboard. |

### Realization matrix (symmetry + documented asymmetries)

| Concern | claude | hermes | codex | pi |
|--------|--------|--------|-------|-----|
| Session id source | `captured` (SessionStart hook) | `captured` (native session id via active-session file, stored as the handle) | `resume` | `resume` |
| Delivery shape | sidecar-channel | gateway-host (WS tui_gateway) | controller (PTY/native) | controller (PTY) |
| Reply author | agent self (`comms_send` + `inReplyTo`) | agent self (`comms_send`) | agent self | agent self |
| Owns its own process | process per agent | `ASYMMETRY(hermes)`: hidden `hermes dashboard` gateway host (no `--tui` — rejected by the subcommand since hermes 0.15.1) + a visible `hermes --tui` PTY resuming the agent's REAL native session (rendered in the dashboard console); its equivalent of one-process-per-agent | process per agent | process per agent |
| Wake mechanism | in-process MCP server-push | `ASYMMETRY(hermes)`: `hermes-managed-host.js` channel-sidecar finds the agent's real session in `session.active_list` (by the stored id / `aify-hermes-session-<agentId>` marker, most-recent fallback) and delivers via WS `prompt.submit` / `session.steer`; agent then self-replies | controller inject | controller inject |
| Can be force-pinned | `ASYMMETRY(claude)`: mints its own id → `captured` not `pinned`; we capture+resume+guard | no — `captured` like claude: hermes uses its OWN native session id (no synthetic `aify-<id>`) | partial (resume id) | partial |

Shrink the asymmetry column over time. What remains for hermes (per-agent daemon;
sidecar-delivered wake) is intrinsic to its architecture and documented above.

### Session lifecycle verbs (minimal, non-duplicate)

The dashboard session lifecycle is a minimal verb set (cleaned 2026-06-03; `13d3821`).
The dead `recover`/`resume` aliases on `POST /sessions/{id}/control` (byte-identical to
`restart`) were removed — only `restart`/`recreate`/`stop`/`cli_takeover` remain there.

| Verb | Meaning |
|------|---------|
| Spawn | Create a fresh managed backing (no resume). |
| Stop | Halt the running backing; keep spec/handle/identity. Reversible via Restart. |
| Restart | Re-spawn and RESUME native context (`resume_policy=native_first`; carries `session_handle`). |
| Reset (fresh context) | Re-spawn discarding native handle/state (`resume_policy=fresh_context`; was "Recreate"). |
| Resume wake | Re-enable wake/dispatch for a stopped RESIDENT agent — `POST /agents/{id}/control` action=`resume` (no spawn; kept separate from session-control). |
| Pause for CLI | Hand session ownership to the terminal; return via Restart. |
| Switch managed/resident | Ownership flip (see matrix below). |
| Set handle | Operator repair of the native resume target. |
| Interrupt / Steer | Run-level control. |
| Remove | Tombstone the identity. |
| Kill bridge / Forget | Environment-level. |

### resident↔managed switch matrix + state model

- **Full-duplex (both modes):** claude-code, codex, hermes. **Managed-only** (resident =
  presence/debug metadata, NOT live-wakeable): pi, opencode — `managed→resident` is
  **rejected** for them (`switch_agent_session_mode` guards on the adapter's
  `supports_resident`; the dashboard hides their "Switch to resident" button). Same
  asymmetry as the runtime-shape note in DECISIONS.md.
- **`resident→managed` carries the native `session_handle`** into the coldstart spawn, so the
  managed worker resumes the same codex thread / hermes gateway / claude transcript instead
  of starting fresh. Per-agent chat always carries over (keyed per agent). Advisory warning
  when binding a handle another live agent already owns.
- **Session display status is DERIVED from live truth** (`_compute_session_display_status`),
  exactly like the agent dot — managed keys on the live `terminal_sessions` row, resident on a
  fresh non-superseded bridge. The denormalized `agent_sessions.status`/`terminal_status` is a
  cache, never the display source; this kills "Stopped/Stale but running". One canonical
  `LIVE_SESSION_STATUSES`, one `_agent_liveness` predicate. See DECISIONS.md / KNOWN_ISSUES.md
  (2026-06-03).

### Canonical status labels

Operator-facing agent status (distinct from session display status above):

| Label | Meaning |
|-------|---------|
| `online` | Live worker, idle (no active turn). |
| `available` | Reachable but NO live worker; auto-starts a worker on the next send. |
| `idle` | An ONLINE worker quiet >5 min (only ever demoted from `online`). |
| `working` | Executing a turn / claimed run (active run or fresh `turn_busy`). |
| `stale` | RESIDENT-ONLY; the resident bridge heartbeat is past its ~150s lease (live-but-expired, NOT an old/sticky label). |
| `offline` | Bound env bridge down, or heartbeat past the ~30min window. |
| `stopped` | Operator-stopped, or set by `resident-lost` on clean close. |

Managed lifecycle: `available` → `working` ⇄ `online` → `idle` (+ stop/offline). Resident
adds `stale` when its bridge lease lapses, and (2026-06-03, `5070c84`) `stopped` on clean
close — the resident bridge POSTs `/agents/{id}/resident-lost` on clean exit so it drops off
`online` in ~1.5s instead of waiting out the ~150s lease (crash closes still self-heal at the
lease). See KNOWN_ISSUES.md / DECISIONS.md (2026-06-03 round 2).

### Adding a new harness

Implement the triad (adapter + controller/delivery + runtime class), advertise honest
capability flags, mark any deviation with `// ASYMMETRY(<rt>): <why>`, and make
`adapter-contract-symmetry.test.js` pass — fix the adapter, not the test.

## Current Implementation Bias

Preserve the existing message/channel/artifact APIs and keep the dashboard as the normal control surface. New work should reduce duplicate concepts, keep environment-backed managed agents as the default teammate path, and add tests around lifecycle edge cases before changing runtime behavior.
