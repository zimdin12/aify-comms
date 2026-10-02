# aify-env owns agent state and lifecycle

Release 0.9.0 in all three repos, after 0.8.0 merges. The operator's direction, 2026-10-02:

> sends when changes or something like that. basically broadcasts. aify-dashboard might want to know also

> aify-comms turns more into purer comms service

and, for this tag ("do it correctly this time"): hermes gateway management in aify-comms while claude's
process side is in aify-env is bad design; each repo's doctor checks only its own responsibilities; a
message to an available agent auto-starting it means aify-comms decides the wake and asks, and aify-env
starts it.

Built on branches `next/0.9-agent-state` in all three repos (worktrees beside each checkout), branched
from the approved 0.8 heads (comms 3811a66f, env df319ad, wrapper 5f3d3018).

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

## What changes, in one table

| | owns after this tag | reads |
|---|---|---|
| **aify-env** | the host's **observations** of each agent, as typed facts (process lifetime, hook-reported turn, screen); lifecycle of defined agents; forwarding state to herdr | launcher hook events, its own process registry, its screen observer, resident lifetime records |
| **aify-env service plugins** | carrying the facts to their service; applying lifecycle requests | the facts, their service |
| **aify-comms** | messages, channels, dispatch runs and reply contracts, files, the chat dashboard; its **status and delivery laws, unchanged**, now fed by host facts where the host is the observer; the wake decision | facts pushed by each host's aify-env, and its own delivery-side facts |
| **aify-wrapper** | the harness side of one run; hooks report to aify-env | its definition, its instance's aify-env descriptor |
| **aify-dashboard** | its own display of the pushed facts | the push, directly |

**The revision of 2026-10-02 (comms-senior-dev's review of 7ca834ae, R1-R5) changed one thing above all:** aify-env
publishes typed facts, not one display word, and aify-comms keeps both of its laws (status `derive()` and the
delivery gate `_turn_busy_holds_delivery`) exactly, with their inputs relocated. The contracts are in
[the P0 document](2026-10-02-aify-env-owns-agent-state-P0.md), C1-C9; the decisions below say what each is for.

## Decisions

**D1. The host observes, once.** `docs/AIFY_ENV_BOUNDARY.md` says aify-env "must not derive status of its own —
deriving it in two places is how two answers start disagreeing". The reason stands. aify-env becomes the one
**observer** of what only the host can see (hooks, screen, process lifetime); aify-comms remains the one place its
status and delivery decisions are made, from those observations plus its own facts. Recorded in DECISIONS.md as
superseding that line's scope, not its reason.

**D2. Typed facts, not a display word (C1).** Per agent per instance: lifetime, process state and verification,
the turn record with its anchor and renewal, the screen, background work, and `runsWith` for defined agents. What
aify-env does not own (operator disable, config defects, environment reachability, the delivery sidecar, spawn
rows, dispatch runs) is not published.

**D3. Hooks report to their own instance (C4).** Each aify-env instance writes `~/.aify/env/<instance>.json`; the
launcher exports `AIFY_ENV_INSTANCE` and `AIFY_LIFETIME` to the runtime, so each hook addresses the instance that
adopted its resident and carries the lifetime it belongs to. Auth stays loopback with browser refusal.

**D4. A resident is a lifetime record verified against the OS (C4).** One file per lifetime
(`<agent>.<lifetime>.json`), the launcher's own OS pid, verified by creation time against the record's write time
and by the launcher in the command line; unknown never means dead. The adopting instance is the one the record
names; two live lifetimes, or one agent from two instances, is a conflict, never a pick.

**D5. Publication is ordered by the publisher, not by rows (C5).** A durable generation
(`max(persisted + 1, now_ms)`, fsynced), an incarnation id, and one publication counter per incarnation order every
push; snapshots are complete or not sent; removals name the lifetime; the receiver applies only newer, answers 204
or 200 `{applied: false, reason}`; header `x-aify-agent-state-key`. Agreed with the aify-dashboard owner.

**D6 withdrawn.** It proposed releasing delivery when publication went stale. Under C6 aify-comms keeps today's
law, so a lost publication is today's "no new hook" and needs no new rule or operator decision.

**D7. Relocate, compare, then switch; delete almost nothing in 0.9 (C6-C8).** Turn events from hooks and the screen
reach aify-comms through the push and apply through today's ordering and law. The delivery-side reporters
(`dispatch-loop.mjs`, `claude-channel.js`, `hermes-run-reporting.mjs`) stay aify-comms producers until the hermes
gateway tag. The bridge turn detectors stay until the comparison shows a replacement for each. The switch (launchers
stop posting to aify-comms) needs the C8 comparison over status, busy, queue, claim and worker readiness, with
denominators, unobserved classes and deterministic tests for each.

**D8. aify-env owns the lifecycle of defined agents (C9).** Requests carry an idempotency key, the expected
definition revision and lifetime, one open request per agent, and durable results. An **undefined** agent keeps
today's spawn path in 0.9. **For the operator: confirm keeping both paths for one tag.**

**D9. Delete the environment "stop" control.** It has no claimer; aify-env's own stop is the host action.
"Forget" stays (service-local). **For the operator: confirm.**

**D10. One observation, forwarded to herdr by aify-env.** aify-env reports the pane state for managed workers and
for residents whose record names a pane. The launchers' herdr hook retires with the switch.

**D11. Each doctor checks its own tier.** To `aify-env doctor`: env-processes, managed-orphans, env-code-currency,
claude-login, context-window. Staying in `aify-comms doctor`: service, api-exposure, external-keys, client-api-key,
bridge-*, agent-identity, skills-installed, session-handles, spawn-queue, definitions-fresh, usage-openai, and a new
`agent-state` row (the C5 publishers: fresh, stale, behind, conflict). gateway-orphans moves with the hermes host.

**D12. An agent's status is not anyone's to set (found by the C2 census).** `PATCH /agents/{id}` writes any string,
`stopped` included, with no gate, so any key holder can stop any agent (KNOWN_ISSUES). The route keeps
self-report and loses the stop: it accepts only the `comms_status` words and never a status in
`_MANUAL_STATUSES` or `NON_LIVE_AGENT_STATUSES`. Stopping stays the operator-gated stop route. Its own commit and
review, independent of the rest; it changes no reader.

## Phases, each reviewed before the next

| phase | repo | what | proves |
|---|---|---|---|
| P0 | plan | C1-C9 frozen, the C2 ledger included | reviewed before any P2 code |
| P1 | wrapper + comms | ride-along: aify-wrapper 764d961 merged (686d182); comms install.sh gives hermes the session servers (9fe8037d) | in review separately |
| P2 | aify-env | the observer: hook route, per-instance descriptors, lifetime records and verification, the C3 ordering and law ported, the core screen observer, the C5 publisher, herdr forwarding | C3 table test run against the Python statement; C4 controls (reused pid, sibling, wrong instance, delayed prior-lifetime hook, tied events, access denied, old exit beside a new record, adoption with no next hook); C5 controls (old snapshot after a new change, removal and recreation, boots A then B then delayed A, equal and backward clocks, duplicate, partial collection, quiet snapshot) |
| P3 | wrapper | hooks carry lifetime and instance and post to aify-env as well as aify-comms; lifetime records written and removed | hook to aify-env readback across both repos |
| P4 | comms | ingest (C5 receiver rules), `host_agent_state`, the C8 comparison recorded per decision, the `agent-state` doctor row, `agentState` in its registry entry | receiver table test; comparison computed on the same rows for each decision |
| P5 | operator | install and restart; the comparison runs on the real fleet | separately authorized |
| P6 | comms + wrapper | the switch, when C8 admits it: launchers stop posting turn events to aify-comms | the send-time queue and claim witnesses (queueIfBusy, steer and non-steer, priority and control, a renewed long turn, an expired or invalid turn, a stale publication) green before and after |
| P7 | aify-env + comms | lifecycle requests (C9); the environment stop control deleted (D9) | idempotent retry, revision and lifetime races, one open request, durable results |
| P8 | all | doctor moves (D11), docs, 0.9.0 bumps, whole-diff review | every suite in all three repos |

## Not in this tag

- The hermes gateway host (start, teardown, kill-prior, ports, resume markers, the delivery loop's process side),
  and with it the delivery-side reporters and the resident hermes gateway turn detector: the next tag.
  `hermes-delivery-loop.mjs` is one closure that owns both the gateway process and delivery.
- Ending an open turn on a sustained idle screen (C3): a delivery policy change for the operator to decide.
- aify-comms emitting `unknown` (C6): `is_live_agent_status('unknown')` answers live today.
- Usage readers. The duplicated console mirror. Per-agent credentials.

## What would prove this wrong

- A reader in the C2 ledger whose required distinction the relocated inputs cannot supply: found before P2, by the
  ledger, not after the switch.
- A C8 disagreement that is neither a bug nor an approved policy change: the switch does not happen.
- A delivery decision that changes across the switch: the P6 witnesses run on today's code first.
- A resident lost or misattributed across an aify-env restart: the P2 controls in C4.
- A publication applied out of order: the P2 controls in C5, and the receiver's table test in P4.
