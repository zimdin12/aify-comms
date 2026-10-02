# aify-env owns the agents' state and lifecycle; aify-comms owns messaging

Release 0.9.0 in all three repos. Built on branches `next/0.9-agent-state` (worktrees beside each checkout), from
the v0.8.0 tags (comms 3811a66f, env df319ad, wrapper 5f3d3018).

## The operator's rulings this plan follows

Given in the comms-tech-lead session on 2026-10-02. They are quoted here so the review is against them, not against
a summary.

- Direction, unprompted: "sends when changes or something like that. basically broadcasts. aify-dashboard might
  want to know also"; "aify-comms turns more into purer comms service"; "hermes gateway management in aify-comms
  while claude's process side is in aify-env" is bad design; each repo's doctor checks only its own
  responsibilities.
- On the shape, unprompted: "0.9 design should be really separation of concerns one. so aify-comms should keep
  what make sense to keep in comms side, env is environment, that handles agents and processes. ... plugins are ok.
  good way to implement some other responsibilities ... lets keep good architecture, mb even interfaces".
- Answer to an explicit question, "Adopt this 0.9 target: aify-env owns agent state (one derivation) plus lifecycle
  and processes; aify-comms owns messaging and the delivery decision, and deletes its status engine after a
  side-by-side comparison?": **"Yes, this target (Recommended)"**.
- Answer to "How should the git watcher's folder grant work?": **"Agent workspaces + list (Recommended)"** (built:
  aify-env `1d30a1f`).
- "you can tag. we can always update tag if some issues come up."
- **Not explicitly answered, so they are my defaults with no objection, not confirmations:** D8 (undefined agents keep
  today's spawn path for one tag), D9 (delete the environment stop control), D12 (gate the status route).

The previous revision (eda03a3d) kept aify-comms' status laws as the destination. Under the ruling that is only a
stage on the way, so this revision restates the end state. It keeps what comms-senior-dev's review of 7ca834ae
(R1-R5) produced: the typed facts, the reader ledger, lifetime identity, publication order, and the lifecycle
semantics.

## The problem, measured

Read from source on 2026-10-02 at those heads (four independent maps, the load-bearing claims re-checked
by hand).

**Turn state has four producers and two tables in aify-comms, and a third copy in herdr.**

| producer | sends | to |
|---|---|---|
| harness hooks installed by comms `install.sh` (`agent-state-event.mjs`) | turn-start, turn-end, blocked, unblocked | comms `/agents/{id}/turn-start`, `/turn-end`, `/status-event` |
| bridge detectors (`claude-turn-detector-state.mjs`, the codex rollout detector, `hermes-gateway-turn-detector.js`) | the same, with a bridgeId | the same routes |
| managed `turnBusy` heartbeats (`dispatch-loop.mjs`, `hermes-run-reporting.mjs`, `claude-channel.js`) | busy / not busy | `/agents/{id}/heartbeat` |
| aify-env's comms plugin, screen observer (`observeScreen`) | working, idle, blocked, shell | `/terminals/{id}/output` `activity`, read by `host_activity_for` as a 75 s override, managed only |
| the launchers' herdr hook (`aify-herdr-state.sh`) | working, idle, blocked | herdr, directly |

Delivery reads `agent_turn_state` (`_turn_busy_holds_delivery`); the status engine reads
`agent_status_state` (`derive()` via `_gather_status_inputs` and `_compute_live_status_cache`). The same
routes write both, and they can disagree. Process liveness is inferred by aify-comms from terminal rows,
channel-sidecar beats, wrapper-child beats and resident bridge freshness, while aify-env owns the actual
processes (`ProcessRegistry`) and reports only `heldTerminals`.

**aify-dashboard needs an aify-comms connection only for status** (and undefined agents' model/effort).

**Lifecycle is decided in aify-comms.** It builds spawn specs and decides start, stop, restart and
cold-start (`dispatch_start`, `session_control`, `session_ops`, `agent_stop_resume`); aify-env claims and
executes. aify-env never starts an agent from its own definition and has no start, stop or restart verb
(`bin/aify-env-agents.mjs`: list, show, set, remove, import, unlock, recover).

**The environment "stop" control has no claimer.** `control_environment` writes `environment_controls`
rows and `POST /environments/controls/claim` serves them, but nothing in aify-env or the bridge calls
that route (searched 2026-10-02 for the literal and for built `/environments/` paths; the terminal and
dispatch claimers found as the positive control). Stopping an environment changes the service's rows and
nothing on the host.

**aify-env has no route a hook can report to.** Its API (`ROUTES`, `lib/protocol.mjs`) serves processes,
startable/importable agents, start and herdr-space, on 127.0.0.1 with browser-origin refusal and no key.


## The end state

| | owns | does not own |
|---|---|---|
| **aify-env core** | agents (definitions), their processes and lifetimes, their lifecycle (start, stop, restart), and **their state**: one pure derivation from what the host observes (hooks, screen, process lifetime), including whether a turn is busy; the grants (`watchRoots`, workspaces); the generic state feed every opted-in service receives | any service's protocol |
| **aify-env plugins** | one per service, each implementing the plugin interface (D13): the translation between the core and that service's protocol (aify-comms: heartbeat, console streaming, definition push, lifecycle requests; aify-dashboard: git facts) | state, policy |
| **aify-comms** | messages, channels, dispatch runs, reply contracts, files, chat; **delivery**: putting a message into a session, and deciding **when** to deliver from aify-env's busy and its own run in flight; the messaging notes on an agent (reply owed, run running, no delivery path) | whether an agent is alive or working; processes |
| **aify-wrapper** | the launcher: hooks to aify-env, a lifetime record for a resident | state |
| **aify-dashboard** | projects, tasks, docs; shows aify-env's state | — |

aify-comms' agent badge becomes aify-env's state word, with a messaging note beside it. Its status engine, turn
tables, hook routes and bridge turn detectors are deleted after the comparison (D7). The hermes gateway host is a
process, so it moves to aify-env in the tag after this one.

## Decisions

The contracts are in [the P0 document](2026-10-02-aify-env-owns-agent-state-P0.md), C1-C10.

**D1. One derivation of state, in aify-env (C1, C3).** A pure `deriveAgentState` over host observations only. Its
vocabulary is `working`, `blocked`, `idle`, `shell`, `starting`, `available`, `offline`, `stopped`,
`misconfigured`, `unknown`. aify-comms shows `idle` as `online`. `docs/AIFY_ENV_BOUNDARY.md`'s "must not derive
status of its own" is superseded, and its reason is kept: the derivation is moved, never duplicated, and aify-comms'
copy is deleted.

**D2. aify-comms decides delivery from aify-env's busy (C6).** Hold delivery while aify-env says the turn is busy, or
while aify-comms' own delivered run is in flight. Both halves exist today, merged into one table. The badge is
aify-env's word, with two exceptions, both aify-comms' own facts (C6): a run it is delivering shows as `working` (today's
promotion), and an undefined agent the operator stopped shows as `stopped`. Every other delivery fact is a note and the
`deliverable` flag, never a different word.

**D3. Hooks report to their own aify-env instance (C4).** Each instance writes `~/.aify/env/<instance>.json`. The
launcher exports `AIFY_ENV_INSTANCE` and `AIFY_LIFETIME`, so a hook addresses the instance that adopted its agent
and names the lifetime it belongs to.

**D4. A resident is a lifetime record verified against the OS (C4).** One file per lifetime. The launcher's own OS
pid is accepted only if its creation time is at or before the record's write time, and that exact creation time is
then pinned. Unknown never means dead.

**D5. Publication is ordered by the publisher (C5).** A durable generation, an incarnation id, and one publication
counter across all pushes; complete snapshots or none; removals by lifetime; header `x-aify-agent-state-key`.
Agreed with the aify-dashboard owner.

**D6. The turn law moves to aify-env, ported (C3).** `turn_is_still_live` keeps its parameters: 1800 s strict from
the turn's start. One renewal question goes to the operator as **P-1**: may a hook event from the agent's
**verified** current lifetime renew its turn? Today a hook-owned turn is strict, but a resident claude's long turn
holds past 30 minutes, because the transcript detector renews it through a heartbeating bridge, and that detector is
deleted here. aify-env computes both answers, and the comparison shows what each would change before the operator
picks.

**D7. Compare, classify, approve, switch, delete (C7, C8).** aify-comms computes its current answer and aify-env's,
on the same evaluation, for every decision a reader makes: status, busy, queue, claim, worker readiness and
eligibility. Each difference is a **bug** (fixed in aify-env), an **approved policy change** (each one confirmed by
the operator) or **unresolved** (blocks the switch). Deterministic tests cover the classes the fleet never shows.
Then the readers switch, and C7's list is deleted.

**D8. aify-env owns the lifecycle of defined agents (C9).** Requests carry an idempotency key, the expected
revision and lifetime, one open request per agent, and durable results. An undefined agent keeps today's spawn path
for one tag (a default, not confirmed).

**D9. Delete the environment "stop" control.** It has no claimer (a default, not confirmed).

**D10. aify-env forwards the state to herdr.** It covers managed panes, and residents whose record names a pane. The
launchers' herdr hook retires with the switch.

**D11. Each doctor checks its own tier.** To `aify-env doctor`: env-processes, managed-orphans, env-code-currency,
claude-login, context-window, and an agent-state row (publication, lifetimes, conflicts). Staying in
`aify-comms doctor`: service, api-exposure, external-keys, client-api-key, bridge-*, agent-identity,
skills-installed, session-handles, spawn-queue, definitions-fresh, usage-openai, and an `agent-state` row for the
publishers it receives. gateway-orphans moves with the hermes host.

**D12. An agent's status is not anyone's to set.** `PATCH /agents/{id}` accepts only the `comms_status` words and
never a manual or non-live status. Its own commit (a default, not confirmed). After the switch the route sets only
the agent's own note.

**D13. Plugins implement a documented interface (C10).** JSDoc typedefs for the core's ports (processes, agents,
state feed, credentials, grants, log) and for the plugin (`start`, `stop`, `state`, optional capabilities), plus one
conformance test that every factory's product must pass. The core never names a service. The state feed is the
core's own protocol, so a service opts in without a plugin.

## Phases, each reviewed before the next

| phase | repo | what | proves |
|---|---|---|---|
| P0 | plan | C1-C10 frozen, with the C2 ledger | reviewed before any code |
| P1 | wrapper + comms | hermes session servers | approved (wrapper 686d182, comms 9fe8037d) |
| P2 | aify-env | the plugin interface and its conformance test (C10); the state model and the turn law (C1, C3); hooks, per-instance descriptors, lifetime records (C4); the publisher (C5); herdr forwarding | C3 case tables; the C4 and C5 controls (listed there); conformance of the comms plugin and the dashboard plugin |
| P3 | wrapper | hooks carry lifetime and instance, and post to aify-env as well as aify-comms; lifetime records | hook to aify-env readback |
| P4 | comms | ingest (C5 receiver, its own transaction); the C8 comparison for every decision; the `agent-state` doctor row | the receiver table; old and new computed on the same evaluation |
| P5 | operator | install and restart; the comparison runs on the real fleet | separately authorized |
| P6 | all | every difference classified; each policy change, P-1 included, confirmed by the operator; the bugs fixed in aify-env | zero unresolved |
| P7 | comms + wrapper | the switch: readers take aify-env's word, busy and readiness (C6); launchers post only to aify-env | the send-time queue and claim witnesses, green before and after |
| P8 | comms | the deletions (C7) | the C2 discriminator: no reader of a deleted input remains |
| P9 | aify-env + comms | lifecycle requests (C9); D9; D12 | C9's races and idempotence |
| P10 | all | doctor moves (D11), docs, 0.9.0, the whole-diff review | every suite in all three repos |

P6 depends on the operator's confirmations and on what the fleet shows. If it is not done when everything else is,
0.9 ships P0-P5 and P9 with aify-comms still deciding, and the switch and deletions (P7, P8) become 0.10. That is
the operator's call at that point.

## Not in this tag

- The hermes gateway host (start, teardown, kill-prior, ports, resume markers, and the process side of the delivery
  loop): the next tag. Until then its delivery reporters stay aify-comms facts ("a run I delivered is in flight").
- Usage readers. The duplicated console mirror. Per-agent credentials.

## What would prove this wrong

- A reader in the C2 ledger whose distinction aify-env's state and aify-comms' messaging facts cannot supply
  together: found in P0, not after the switch.
- A difference in C8 that is neither a bug nor an approved policy change: the switch does not happen.
- A delivery decision that changes across the switch without an approval: the P7 witnesses run on today's code first.
- A resident lost or misattributed across an aify-env restart, or a reused pid accepted: the C4 controls.
- A publication applied out of order: the C5 controls.
