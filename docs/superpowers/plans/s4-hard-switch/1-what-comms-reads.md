# 1. What comms reads after the switch (revision 3)

Revision 3 answers REVISE s4-design-1792519039743 and supersedes 7e481315. It deletes the handoff machinery: the
delivery gap is closed by the runtimes themselves (below), not by comms.

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
- the claim gate, which serves every claimed run;
- the send-time path, for default sends as well as `queueIfBusy` (today's check sits only under `queueIfBusy`,
  dispatch_launch.py:66-95);
- console insertion, if it is enabled later.

With eligibility known: busy holds non-steer work, and the steer carve-out applies only to a known busy. Not busy
proceeds.

**The word.** Unknown eligibility shows `status: unknown`. The last applied record may be shown beside it, as a
stale last-known note with its application time, never as the current word. Q3's proven-absent wake handle still
reads `offline`.

**The D8 rows, in precedence order:**
1. An operator stop on an undefined agent: `stopped`.
2. A record present: the table above.
3. No record, an undefined managed agent comms can cold-start: `available`. A send to it is a cold start, where the
   spawn carries the message as today, never a claim-gate delivery. That avoids a queue-before-first-record cycle.
4. Otherwise: `unknown`.

SW4 must trace whether aify-env publishes undefined managed lifetimes (ASSUMED no: the managed population is built
from definitions). If it does not, such an agent reads `unknown` after its start, and that is an operator decision
before SW5.

## Readers

| reader | after the switch |
|---|---|
| send-time path (dispatch_launch.py) | unknown: queue; busy: queue for explicit queue, steer when asked and steer-capable; false: deliver |
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
