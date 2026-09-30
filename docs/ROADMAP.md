# Roadmap

What is being finished now, what comes next and in which order, and older goals kept to pick from.
Rewritten 2026-09-30. The previous roadmap (written 2026-08-10, last current at v0.5.4) is kept as a
record in [history/ROADMAP-2026-08.md](history/ROADMAP-2026-08.md); what shipped is in the tags and
their messages.

The rule from that roadmap still holds: anything that cannot name the artifact it retires does not get
scheduled. Structural work is judged on whether it makes the next fix cheaper.

## Now: finish v0.7.7 (aify-env v0.7.2)

The TUI and dashboard release, plus the fixes found once it ran on this host:

- starting an agent right after stopping it no longer answers 409;
- a long refusal in the terminal UI is wrapped and keeps its cause;
- the "out" dot no longer wears the working colour;
- an attached pane is drawn over whatever it showed before (the scrambled claude pane);
- a managed agent can be kept out of herdr (drawer button, Tab in aify-env's start list).

Done when: comms-senior-dev approves the pinned range, the tags are moved to the reviewed commits,
the service is rebuilt, aify-env is restarted, and the two live checks pass: a hidden agent starts
with no herdr space, and a re-attached claude pane matches aify-env's screen in herdr's own grid.

## Next: aify-env owns the agents

Plan it properly once v0.7.7 is installed. The operator's shape, 2026-09-30:

- **aify-wrapper** wraps harnesses: it is the agent side, one per harness (claude, codex, hermes).
- **aify-env** registers those harnesses and manages the agents: which exist, on which harness, in
  which project, and how they are launched.
- **Services** (aify-comms, aify-dashboard) get their agents from aify-env instead of owning the list.
  aify-env has a plugin per service that switches on when the service is detected.
- **aify-wrapper can also talk to a service directly**, so an agent still starts when aify-env is down.

Leaning, to be decided in the plan:

- Agent definitions are files in `~/.aify` (one per agent), not a database: desired state that
  changes when the operator changes it, readable without a daemon, diffable in git. One writer per
  file, atomic writes, a version field, as `~/.aify/services.json` already does.
- Live and historical state (sessions, messages, runs, status) stays in the services.
- The per-agent herdr-space setting moves into the definition file.

Open questions the plan must answer: whether agents run on more than one machine (the other PC,
WSL), and if so how per-host definitions become one fleet view without two copies drifting; and the
migration path from aify-comms' `agents` and `spawn_specs` tables.

## After that: harness-swappable session transcripts (experimental)

Open one conversation in claude, codex or hermes. Native session files are private to each harness
(claude JSONL, codex rollouts, hermes SQLite), tool calls differ, and signed thinking cannot move
between providers, so faking another harness's native session is declined: it would break on every
harness update.

What exists: "Continue as…" in the agent drawer starts a fresh session, optionally on another runtime,
with a brief that points at the old transcript and the last N messages. What is missing: the old
transcript is in the old harness's raw format. The plan is a reader per harness in aify-wrapper that
writes one neutral, readable transcript, and the brief points at that.

## Older goals

Not done, not scheduled; kept so they can be picked later. Each was checked against the code on
2026-09-30 and names the old document it came from (`history/ROADMAP-2026-08.md`,
`superpowers/plans/2026-08-30-v0.6.1-roadmap.md`, `V0.2_ROADMAP.md`, `TARGET_ARCHITECTURE.md`,
`history/IMPLEMENTATION_ROADMAP.md`). Items already in `KNOWN_ISSUES.md` are not repeated.

**Dashboard and console**
- Console typing and resize go over the WebSocket instead of HTTP POSTs (v0.6.1); the socket still
  ignores client messages (`service/main.py`).
- Terminal output goes only to the tab showing that terminal, and more than one console can be open
  (v0.6.1).
- The remaining plain relative times tick like the rest (v0.6.1: 12 of 14 sites were left).
- Uploading a shared file cannot be submitted twice (v0.6.1); the spawn form already has the guard.
- The WebGL renderer is skipped on narrow screens (V0.2 WS-4.2).
- The dashboard says why its socket closed (V0.2 WS-4.3).

**Tools and the SSE transport**
- The five tools missing over SSE: `agent_info`, `contracts`, `status`, `describe`, `unsend`
  (TARGET_ARCHITECTURE; audit finding 2).
- The other SSE gaps from that audit: a dead worker's recorded console, send nonces, binary shares.
- Remove agent, delete session and clear record who asked (v0.6.1); only restart does.
- A sender can opt out of message merging (v0.6.1).
- A queued run can be interrupted, not only an active one (v0.6.1); unsending is the workaround.
- Loop safety beyond the queue cap: a per-sender rate limit and dropping identical repeats (v0.6.1;
  IMPLEMENTATION slice 8).
- A one-shot "tell me when it is idle" (v0.6.1).
- @mention fan-out in channels (IMPLEMENTATION slice 8).

**Security**
- aify-env has no inbound authentication beyond the loopback bind and the browser guard (v0.6.1 F5).
- The WebSocket auth test checks close code 1008, not any disconnect (v0.6.1 F7).
- Unattended approval is one named setting across codex and hermes, and whether a resident codex
  auto-approves is decided (V0.2 A2).

**Architecture and tooling**
- The verifier moves off the host PATH (TARGET_ARCHITECTURE #2).
- Launcher state reaches the service (TARGET_ARCHITECTURE #3).
- The Linux-only `bridge-running` doctor check is retired (V0.2 WS-3).
- The dormant containers subsystem (`service/containers/`, about 1,067 lines) is kept or deleted:
  the operator's decision (V0.2 WS-10).
- `_compute_live_status_cache` (457 lines) gets its own plan before it is split (2026-08 roadmap).
- The dead delivery-receipt prefix checks in `api_core/dispatch_state.py` go (2026-08 roadmap).
- `_resolve_live_console_terminal` agrees with itself about `stopping` (V0.2 N3).
- Launching an unknown agent id says it created a new agent (V0.2); it registers silently now.
- The thread-closure pilot (`docs/pilots/2026-08-09-thread-closure-label.md`) needs the operator
  to adopt it.
- aify-env's orphan test waits on a condition, not fixed sleeps (v0.6.1).

**Not established either way** (look before scheduling)
- Whether console input reported as sent can still go unacted (V0.2 WS-2).
- Whether the compaction dialog can still stall a resume (V0.2 A1b).
- Whether the single terminal-output write lock still limits live consoles (v0.6.1).
- Whether a service's `keyEnv` still binds nothing now that aify-env reads it (v0.6.1 F6).
- Whether the drawer's channel-selection race is still reachable (V0.2_PLAN R4b).
