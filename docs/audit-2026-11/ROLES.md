# Component roles (owner-confirmed) — basis for the re-pass and new issues
| Component | Exists so that | Must not |
|---|---|---|
| aify-wrapper | PLATFORM. Any service's agents are launched with identity + MCP config; reusable by future services | know specific services |
| aify-env | PLATFORM. Host truth: processes, PTYs, spawns, the agent REGISTRY and AGENT STATUS served to all services | carry consumer-specific logic/plugins |
| aify-comms | Message store-and-deliver: owner's comms UI (:8801), history, delivery, statistics, work loop (message->run->reply) | re-derive process/agent state, carry launcher/harness knowledge, grow knowledge features |
| aify-dashboard | Second brain: project knowledge for agents (super-LSP, graph context, queryable KB) + owner<->agent questions/events | control the fleet or own agent status (consumes it) |
| aify-project-graph | Code-structure producer (graph, briefs, code_intel) feeding the knowledge layer | be a competing "orient me" entry point |
| agents-setup | Owner's doctrine, skill selection, one machine bootstrap | hand-restated skill lists, competing installers |
Principles: DRY (rare, justified exceptions); data-driven; on-demand/event-driven, not time-based; smallest change,
no overbuilding; minimal agent context (short descriptions, small tool lists); env/wrapper stay independently
consumable (weighs against monorepo); future transcript storage in dashboard is NOT decided — keep comms thin.
Removal only where counterpart exists AND is used.

OWNER DECISION: NO MONOREPO. Repos stay separate because the stack is built mostly by agents: a repo boundary is
the cheapest way to keep separation of concerns — an agent working in one repo cannot quietly reach into another
component, so contracts stay explicit. Merging even aify-env + aify-wrapper is not the direction. Fix lockstep
coupling with small, versioned, schema'd contracts and contract tests instead.

OWNER DECISIONS (2026-11-03, answered in session):
- FLEET CONTROLS STAY IN aify-dashboard (October decision stands; shipped v1.3). Both the comms :8801 UI and the
  dashboard may offer them. Issues must NOT ask to remove them from the dashboard; only ask that both UIs call the
  same aify-env / comms endpoints (no duplicated fleet-control logic). Dashboard purpose = second brain + events/
  questions + these manual controls.
- EVIDENCE STAYS IN THE REPOS, FENCED: exclude evidence from npm packages, Docker images, dashboard search and
  agent-facing docs/context. Do NOT ask to move evidence out of the repos.
- SERVICE PLUGINS LIVE WITH SERVICES: aify-env loads the plugin a service declares in its registry entry; the
  comms/dashboard plugins move to their own repos over time. Overrides AIFY_ENV_BOUNDARY 2026-09-08 / DECISIONS
  2026-10-20 where they say plugins live in aify-env.
- OWNER'S LANGUAGES: professional work in PHP (Laravel etc.); games in C++, Rust or other performant OOP languages;
  the aify repos themselves can use whatever language fits best. PHP host-vs-container not stated: PHP LSP backend
  must work with phpactor on PATH and degrade cleanly when PHP only exists in project containers.
  aify-project-graph: tree-sitter grammars include php, rust, cpp; code_intel LSP verbs cover only C++/TS/Python
  (no rust-analyzer, no PHP).

OWNER DECISIONS (2026-11-04):
- D2: aify-env's core observes agent screens (screen evidence moves out of the comms plugin). This supersedes the
  2026-09-14 "core stays PTY-only" ruling, because aify-env owns agent status.
- D4: the idle word stays "online"; aify-comms maps aify-env's "idle" to "online". No visible rename.
- Host-wide lease watcher (aify-wrapper#25): one per machine, owned by aify-env.
- Publisher conflict rule for agent status, stated once in aify-env's agent-state contract: only proven silence
  (three of a publisher's own announced reportEveryMs intervals, with a fresh same-machine peer listing the agent)
  makes it yield; freshness alone never overrides a live publisher's conflicting report (conflict/unknown until
  identity evidence settles it); restored data can prove silence but never makes a publisher fresh.
- Skip (planetary-golf): never pays for what the player has not seen.

