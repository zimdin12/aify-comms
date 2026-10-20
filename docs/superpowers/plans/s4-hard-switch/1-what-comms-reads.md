# 1. What comms reads after the switch (revision 5)

Revision 3 deleted the handoff machinery: the delivery gap is closed by the runtimes themselves (below), not by
comms. Revision 4 replaces the D8 availability exception with today's D8 and the real cold-start sequence.

## The delivery gap needs no comms record

A run can leave claimed/running before aify-env publishes the turn it causes. Today's paths are the channel receipt,
`delivered` or `completed` at delivery (channel-dispatch-receipts.mjs `markDispatchDelivered`); Hermes
`markRunDelivered`; and console input for non-turn runtimes. A second message delivered in that gap is harmless when the
runtime's own delivery path never runs it inside the first turn.

| runtime and mode | what a mid-turn delivery does | evidence |
|---|---|---|
| Claude, resident and managed (channel) | **queues**: "Events queue into the session and are processed in order. If several notifications arrive while Claude is busy, they're delivered together on the next turn." | Claude Code channels reference, "Notification format" (documentation, not executed here) |
| Hermes gateway, resident and managed (`hermes-delivery-run.mjs`) | **refuses or queues**: a non-steer `prompt.submit` to a busy session fails with 4009 "session busy" and the run is marked requeued; a steer is sent only when the gateway reports the session working and lands only with `status: "queued"`; a rejected steer is requeued | hermes-delivery-run.mjs (the 4009 branch calls `markRunRequeued`; the steer result is read for `queued`); `isSessionBusyError` in hermes-gateway-protocol.js |
| Hermes and other native-managed runs (dispatch-loop.mjs) | the run stays `running` from start to outcome, so `runOccupied` covers the whole turn | dispatch-loop.mjs `status: "running"` at start, `completed` at the outcome |
| aify-env | has no delivery path of its own: no `prompt.submit` or `session.steer` anywhere in env lib or bin | grep with a control: the same search finds `turn-event` in lib |
| Codex | not supported for the switch (operator ruling, 2026-10-20) | — |
| Console/PTY insertion | opt-in only (`insert_messages_via_console`, off by default); **excluded from the switch** until its own evidence exists | — |

**What this drops:**
- the handoff row, its opening and resolving events, the anchor comparison and the settlement list;
- all handoff watch and rollback items.

**What the runtime contract does not give.** The channel reference also says a notification written to the channel
is transport, not consumption, and that events are dropped silently when the channel is not loaded or not allowed.
So a `delivered` run never certifies processing; native queueing makes a gap delivery safe, not successful.

**What stays separate:** steer, an explicit "inject now" accepted only by a runtime that queues or refuses it. The
obligation moves from comms to two dependencies: the runtime contract above, and the switch watch.

**If a supported runtime is ever found to run a gap delivery mid-turn,** comms needs message-bound consumption
evidence for that runtime only. That work is not in this plan.

## The facts and the truth table

Availability (per holder: `fresh`, `stale`, `restored`, `unavailable`, `lost`) and busy (a fresh record's top-level
`busy`) stay separate typed facts. `Record` gains `busy` from the validated top-level field, never from `busyIf`,
`clock` or raw evidence.

| holders of the agent on its machine | eligibility | holdsDelivery |
|---|---|---|
| none, and no publisher is unreadable | unknown (except the D8 rows below) | unknown |
| any holder `stale`, `restored`, `unavailable` or `lost`, or any publisher unreadable, or the binding failed | **unknown**, even beside a fresh idle holder | true if a fresh holder says busy, else unknown |
| all holders fresh, none busy | known | **false**, the only known false |
| all holders fresh, any busy (a conflict included) | known | true |

**Every delivery route checks eligibility first.** Unknown withholds before busy, before steerability and before the
queue choice:
- the claim gate, which serves every claimed run, default sends and `queueIfBusy` alike. With console insertion off,
  it is the only delivery point;
- console insertion, if it is enabled later.

The send-time path (`_launch_recipients_for_dispatch`) delivers nothing with console insertion off. It only ensures
backing, so a hold there must never skip that step (next section).

With eligibility known: busy holds non-steer work, and the steer carve-out applies only to a known busy. Not busy
proceeds.

**The word.** Unknown eligibility shows `status: unknown`. The last applied record may be shown beside it, as a
stale last-known note with its application time, never as the current word. Q3's proven-absent wake handle still
reads `offline`.

**The D8 rows, in precedence order:**
1. An operator stop on an undefined agent: `stopped`.
2. A record present: the table above.
3. No record: `unknown` (Q3's proven-absent wake handle reads `offline`). No row makes a no-record agent available.

D8 stays as built: no undefined agent is newly started. `start_refusal` and `insert_spawn_request`
(definition_start.py), the cold start's binding check (dispatch_start.py) and `_ensure_managed_pty_for_dispatch` each
refuse one, and `test_a_defined_agent_starts_from_its_definition.py` holds it. Undefined sessions already running are
a different population: they read by their records.

## A cold start creates backing; delivery stays at the claim gate

The guarantees for a defined managed agent with no live backing (console insertion off), as protocol stages, not
statement order. Today messages.py calls `_launch_recipients_for_dispatch` before `_create_dispatch_runs`; no reorder
is needed, because what matters is that backing is ensured and delivery waits for the claim gate:
1. The send creates the dispatch run carrying the message, `queued` (messages.py, `_create_dispatch_runs`).
2. The send-time path ensures backing. It inserts a D8-guarded cold-start spawn request whose `initial_message` is
   empty (dispatch_start.py), and leaves the run queued for `/dispatch/claim` (dispatch_launch.py). It delivers
   nothing.
3. aify-env claims the spawn request, launches the lifetime and publishes its record.
4. The wrapper's bridge, or the native adapter, boots and polls `/dispatch/claim`.
5. The claim gate reads the truth table. Until comms accepts a publication carrying the new lifetime, eligibility is
   unknown and the claim withholds. On the first pass after a fresh idle record is accepted, the run is claimed.

**The ordering rule:** a send-time hold, unknown or busy, never skips step 2 for an agent with no live backing.
Today's `queueIfBusy` hold `continue`s past backing; after the switch an unknown no-record agent would never be
started, and its run would wait for a record that only a start produces. The backing step is idempotent (a live
claimer or a pending spawn request short-circuits it), so running it under a hold starts nothing twice. An undefined
agent is refused at step 2 and its run is recorded not started, as today.

## Readers

| reader | after the switch |
|---|---|
| send-time path (dispatch_launch.py) | the run is queued; backing is ensured whatever eligibility and busy say; it delivers only through console insertion, which is excluded |
| claim gate (dispatch_claim.py) | unknown: withhold, steer included; busy: hold unless a claimable steerable run exists; false: proceed |
| reminder skip B21 (dispatch_sweeps.py) | **operator decision R3** below |
| stranded guard B26 (dispatch_lifecycle.py) | **operator decision R3** below |
| word, readiness, live counts, promotion | the one view; row 5 promotion everywhere; unknown never reads as available or idle |

## Restart

At start, before the ingest, send and claim routes are served:
- every non-retired publisher and its rows load from the saved tables; `record_json` carries top-level `busy`;
- cursor and data generations stay distinct, and stored application stamps are kept;
- each restored publisher is `restored`.

A restored publisher stays `restored`, so eligibility is unknown, until the publisher sends a **complete snapshot**
that comms accepts. A same-generation changes publication keeps it incomplete, as `Mirror.feed` does today, so it
stays `restored`. Unavailable and lost stay unknown, retired stays absent, and the load refuses to overwrite a key
already present.

## Operator decisions this page needs

Both are requests until adopted: receipt freshness before SW3b, R3 before SW5L.

- **R3, reminders and stranded runs.** aify-env's turn carries no run id, so comms cannot tell which run a busy agent
  is working on.

  | reader | today | proposed |
  |---|---|---|
  | B21 reply-overdue reminder | skipped while a run is claimed or running, or while a fresh turn (120 s) is attributed to ANOTHER run; a turn on THIS run does not stop the reminder | **paused while aify-env says the agent is busy with anything, or eligibility is unknown**; sent when the agent is idle |
  | B26 stranded-reply guard | never fails a run while a turn is open on THIS run; a turn on other work does not protect it | **protects any run while the agent is busy or unknown**; a stranded run is failed only once the agent is idle |

  - The effect: no reminder interrupts an agent mid-work, and a run whose message was really lost is failed later,
    never sooner.
  - The cost: a reminder about run R waits while the agent works on unrelated S.
  - A comms-owned processing relation would keep today's exactness. It needs message-bound receipts from each
    runtime, which is not cheap, so it is not proposed.
- **Receipt freshness, 180 s.** A publisher is `fresh` while its last applied publication is under 180 s old on comms'
  own receipt clock (`age_class`).
  - This times comms hearing from aify-env, never a turn.
  - Nothing guarantees a publication within 180 s: a scheduler delay, an occupied request, a credential, a role or a
    transport failure can each prevent one.
  - Then the agent reads `unknown`, and messages queue until a publication is accepted.
  - A stale record is never read as expired-idle.
