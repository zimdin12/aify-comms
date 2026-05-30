# Runtime Symmetry & Session Governance — Design

**Date:** 2026-05-30
**Owner:** comms-tech-lead (operator Steven)
**Status:** DESIGN — awaiting operator review before planning/implementation
**Supersedes/absorbs:** the hermes-delivery plan (`2026-05-30-hermes-apiserver-delivery.md`) becomes the hermes *delivery mechanism* underneath this governance layer; the gateway-bind recon (`2026-05-30-hermes-0.15.1-gateway-api.md`) and api_server contract (`2026-05-30-hermes-apiserver-contract.md`) remain factual references.

## Problem

Two intertwined problems surfaced repeatedly this session:

1. **Session identity is fragile.** Managed agents *guess/mint* session ids, silently adopt new ones on relaunch, and multiple processes resume the same id (observed: 3 `hermes-aify` wrappers all `--resume`ing one session; claude managed agents adopting the operator's session). This causes **splits** (one logical agent scattered across ids) and **merges/collisions** (distinct agents converging on one id). Status then lies (empty/duplicate handle → wrong availability).
2. **Runtime integrations have drifted out of symmetry.** claude works (channel sidecar), hermes was broken (gateway bind), codex/pi differ again. Each fix is bespoke. Adding a new harness means reverse-engineering the pattern. There is an adapter/controller/runtime triad, but no enforced contract or documented rationale for deviations.

## Principles (the backbone)

**P1 — Runtime symmetry is the default.** Every harness (claude, codex, hermes, pi, opencode, future) realizes the SAME contract through the SAME triad. Adding a harness = implement the triad; behavior falls out. Code paths branch on *capabilities*, not on `if runtime == "x"`.

**P2 — Asymmetries are explicit and justified.** Where a runtime genuinely cannot be symmetric, it is expressed as a capability/flag, and the deviation carries a comment: `// ASYMMETRY(<runtime>): <why>`. No silent special-casing. (`runtimes.js` already does this in spots — we make it the rule.)

**P3 — Session identity is sticky and governed.** An agent has ONE persisted session id. It does not churn. Changing it is rare, explicit, and guarded. Managed↔resident is "who drives," not "new identity."

**P4 — Infra is effortless; identity changes are deliberate.** Plumbing (e.g. the hermes daemon) auto-heals silently. Identity/mode transitions require an explicit operator action and surface warnings. Friction goes where safety matters, not on the daily path.

## The symmetric contract (the triad)

A harness is defined by three cooperating pieces with fixed responsibilities:

| Layer | File | Responsibility (symmetric across runtimes) |
|------|------|--------------------------------------------|
| **Adapter** (bridge) | `mcp/stdio/adapters/<rt>.js` | Capability flags (`supportsManaged/Resident/Interrupt/Steering`), `discoverSessionId()`, `resumeCommand(sessionId)`, `sessionIdSource` (`pinned`\|`captured`\|`resume`), diagnostic env. |
| **Controller / delivery** (bridge) | `mcp/stdio/controllers/<rt>-*.js` (+ `*-channel.js` for sidecar runtimes) | Deliver a dispatched run to the agent and surface the reply. Two shapes: **sidecar-channel** (claude, hermes) and **controller-PTY/native** (codex, pi). |
| **Runtime class** (service) | `service/runtimes/<rt>.py` | Server-side: execution-mode resolution, status/deliverability inputs, channel-enabled flag, resume-command metadata for the dashboard. |

A new harness implements these three; the governance layer and dashboard treat it uniformly.

### Per-runtime realization matrix (symmetry + documented asymmetries)

| Concern | claude | hermes (new) | codex | pi |
|--------|--------|--------------|-------|-----|
| Session id source | `captured` (SessionStart hook, #138) | `pinned` (`aify-<agentId>`, daemon-resident) | `resume` (controller stores/resumes) | `resume` |
| Delivery shape | sidecar-channel (`claude-channel.js`) | **sidecar-channel** (`hermes-channel.js`, NEW — now symmetric with claude) | controller (PTY/native) | controller (PTY) |
| Reply author | agent self (`comms_send` + `inReplyTo`) | **agent self (`comms_send`)** — aify-comms MCP tools loaded into the hermes agent; now symmetric with claude | agent self | agent self |
| Owns its own process/daemon | a process per agent | **a `hermes gateway run` daemon per agent** — `ASYMMETRY(hermes)`: hermes's equivalent of "one process per agent"; hosts the agent's pinned session + the aify-comms MCP tools; auto-ensured, torn down on stop | a process per agent | a process per agent |
| Wake mechanism | MCP server-push notification (in-process sidecar) | `ASYMMETRY(hermes)`: external sidecar delivers the wake via api_server `chat` (hermes's MCP **client** cannot be server-woken like claude's — recon B); the agent then self-replies | controller PTY/native inject | controller PTY inject |
| Can be force-pinned | no — `ASYMMETRY(claude)`: claude mints its own id; we capture+resume+guard | yes (we assign `aify-<agentId>` to its daemon) | partial (resume id) | partial |

The goal: **shrink this table's asymmetry column over time.** hermes moving to a per-agent sidecar-channel with self-reply removes the two biggest asymmetries — delivery shape AND reply author now match claude. What remains (per-agent daemon; sidecar-delivered wake) is intrinsic to hermes's architecture and documented.

### Reply robustness — reminders, not scraping (runtime-agnostic)

The agent always authors its own reply (`comms_send` + `inReplyTo`). When a `require_reply` dispatch run stays **unanswered** past a threshold, a runtime-agnostic **reminder** re-wakes the owing agent with a message that BOTH nudges and **reinforces the pattern** ("you owe a reply to message <id> — answer with `comms_send(..., inReplyTo=<id>)`"). This replaces output-scraping entirely: it is symmetric across runtimes (the reminder rides each runtime's normal wake path), self-correcting, and teaches the contract. Generalizes the existing claude inbox-reminder logic (`notify-check.js`) into a service-level reminder for any runtime's unanswered required runs.

## Session governance model (runtime-agnostic, service-level)

### State

Per agent, persisted: `session_id`, `session_mode ∈ {managed, resident}`, `driver_state ∈ {idle, driving}`, `session_id_source`, `pending_session_id?` (a proposed new id awaiting confirmation).

### FSM — mode + driver

```
              switch→resident (dashboard)
   MANAGED  ───────────────────────────────►  RESIDENT
 (sidecar     ◄───────────────────────────────  (operator TUI/CLI
  drives)         switch→managed (dashboard)      drives; sidecar released)

Invariant: at most ONE driver attached to a session_id at any time.
```

- **Switch managed→resident:** service marks the agent resident, signals the managed sidecar to RELEASE (stop claiming/driving), and the dashboard shows the **resume command** (`adapter.resumeCommand(session_id)`) so the operator takes over the SAME session. For hermes: `hermes --tui --resume aify-<agentId>` against the shared daemon. For claude: `claude-aify --resume <session_id>`.
- **Switch resident→managed:** service marks managed; operator's TUI should exit (or is fenced out); sidecar resumes driving the same id.

### Mutual exclusion (the collision guard)

On registration / driver-attach: if a process tries to drive a `session_id` that is **already being driven** in the **other mode**, REJECT with an explicit, actionable error:

> `aify-comms: agent '<id>' is currently MANAGED. To take it over interactively, switch it to 'resident' in the dashboard first, then run: <resumeCommand>.`

This is what prevents the N-wrappers-on-one-session collision. Same-mode re-attach by the *same* logical agent (e.g. a managed restart) is allowed and supersedes the prior bridge (existing machine_id supersession, now case-insensitive).

### Sticky identity + new-id guard (the split/merge catch)

- Registration/heartbeat does NOT silently overwrite `session_id`.
- **First-id auto-accept:** a brand-new agent with no persisted `session_id` accepts its first reported id (not a "change" — no split/merge risk). The guard fires only on a *change* from an already-persisted id.
- If an agent reports a `session_id` **different** from its persisted one: store it as `pending_session_id`, set status `session-changed` (a distinct, visible state), and **do not switch delivery** until resolved.
- Resolution is an explicit operator action in the dashboard: **Confirm new id** (re-pin to the new id) or **Keep current** (the agent is told to resume the persisted id — for hermes/codex via resume; for claude via `--resume`).
- This makes both **split** (agent drifted onto a fresh id → flagged, not silently accepted) and **merge** (two agents reporting the same id → second is rejected by mutual exclusion) observable and guarded. Rare by design (P3/P4).

### Dashboard surface

- A **managed/resident toggle** per agent (the switch you want).
- When resident (or session-changed), show the exact **resume/takeover command** to copy.
- A **session-changed** badge with Confirm / Keep-current actions.

## Infra: the per-agent hermes daemon (auto-ensure)

`ASYMMETRY(hermes)`: each hermes agent needs its OWN `hermes gateway run` daemon hosting api_server, so that the aify-comms MCP tools loaded into it carry that agent's `AIFY_AGENT_ID` and `comms_send` attributes the reply to the right agent (a single shared daemon could not — one process, one identity). This is hermes's equivalent of claude's "one process per agent." The bridge **auto-ensures** it (`hermes-daemon.js ensureDaemon`, already built; extend to per-agent host/port + key derived from agentId): probe → if down, spawn detached → wait healthy → idempotent; torn down on agent stop. **This is lighter than the prior footprint** (which spawned a gateway *plus* a TUI per launch and leaked on restart); one bounded daemon per active agent + teardown is strictly better. claude/codex/pi have no daemon (documented). Operator never starts it manually (P4).

## Wrapper model (install.sh, hermes branch)

- **Managed launch** (`hermes-aify --aify-agent X`): ensure daemon, run `hermes-channel.js` sidecar ONLY. No `dashboard --tui`, no `--tui` spawn → kills the proliferation.
- **Resident launch** (`hermes-aify`, interactive): ensure daemon, attach a real `hermes --tui` to the shared daemon (`HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:8765`), driving the agent's pinned session. Subject to the mutual-exclusion guard.
- Wrapper kills any prior instance of the same `--aify-agent` before launching (belt-and-suspenders against proliferation).
- DROP `patch_hermes_gateway_visible_bind` (dead). Stop publishing `AIFY_HERMES_GATEWAY_URL` for the delivery path (kept only for the resident TUI attach, clearly commented).

## What already exists (slots underneath)

Built + tested on `feature/session-status-robustness`: `hermes-apiserver-client.js`, `hermes-version.js` (probe+assert), `hermes-channel.js` sidecar, `hermes-session-id.js` (pinned id), `hermes.js` adapter (returns pinned id), `hermes-daemon.js`. The WS-bind controller + dead frames are retired.

**Revisions required by the self-reply decision** (these modules were built for the earlier sidecar-posts-reply model):
- `hermes-channel.js`: stop posting the captured reply. Its job is the WAKE only — claim → ensure daemon/session → deliver the dispatch prompt via api_server `chat` → mark the run *delivered* (leave a `require_reply` run pending; the agent's `comms_send`+`inReplyTo` closes it, exactly like `claude-channel.js`). Still pulses `turn_busy`.
- `hermes-daemon.js`: per-agent host/port + key derived from agentId (not one shared instance).
- install.sh: register the aify-comms MCP server into the hermes daemon's config so the agent has `comms_*` tools (the claude-parity bit that enables self-reply).

These are the hermes *delivery* realization of the symmetric contract; governance + dashboard + install + the cross-runtime reminder/guards are the new work.

## AGENTS.md

Add a "Runtime symmetry" section codifying P1/P2 and the triad contract + matrix, so future agents extend symmetrically and justify any deviation with an `ASYMMETRY(<rt>)` comment. (Mirror to `.agents/` per repo convention.)

## Testing strategy

- Service: governance FSM unit tests (mode switch, mutual-exclusion reject, sticky-id incl. first-id auto-accept, new-id→pending→confirm/keep), the reminder subsystem (unanswered `require_reply` run → reminder re-wake fires once past threshold, stops after the reply lands), status deliverability — runtime-agnostic with each runtime parametrized (symmetry enforced by a shared test matrix).
- Bridge: per-adapter contract test (the existing `test_per_adapter`/`test_runtime_adapter_consistency` extended to assert every adapter implements the full contract — a symmetry guard test that FAILS if a new runtime omits a contract method).
- Live: managed hermes round-trip; managed→resident switch shows correct resume cmd and hands off without collision; new-id warning fires on a drifted id.

## Out of scope / later

- True live co-view of one session by two clients (architecturally limited on hermes 0.15.x — snapshot only).
- codex `no_rollout` (#136), pi PTY (#137) — separate, but should adopt the same governance once landed.
