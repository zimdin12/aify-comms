# 0.9.8: the hard switch to aify-env's word (2026-10-20, plan revision 2, not built)

This replaces S2-S4 and the C8 gate of `2026-10-18-status-switch.md`. It follows two operator rulings
(DECISIONS.md, 2026-10-20):
- **The gate is a sandbox, not a one-day live comparison.** Then a hard switch, with a rollback kept ready.
- **comms never times turns.** aify-env alone decides `busy`, and comms queues when aify-env is silent.

Revision 2 answers comms-senior-dev's shape input (reviewqueue-219/c8-n2-authority-1792516661010) and supersedes
5e6c6654.

## The design in one paragraph

comms keeps what it owns: messages, runs, delivery, and the handoff of a message to an agent. For "is this agent
busy", it reads the top-level `busy` that aify-env publishes, and keeps no turn timer of its own. Three facts stay
separate: whether aify-env's record is available, what it says about busy, and which handed-off messages the agent
has not yet started a turn on. Unknown availability queues every send and withholds every claim, steer included. A
handoff closes on a new turn, an outcome settlement or a lifetime change, never on time. At start, comms restores the
records it last applied as last-known and stale, never as fresh, so delivery waits for each host's next publication.

## Pages

- [What comms reads](s4-hard-switch/1-what-comms-reads.md): the typed facts, the UNKNOWN predicate, `holdsDelivery`,
  the message handoff, the readers, run correlation, and the restore.
- [The sandbox gate](s4-hard-switch/2-sandbox-gate.md): the stand-in agent and the cases, each with what turns it
  red.
- [Switch, rollback, deletions](s4-hard-switch/3-switch-rollback-deletions.md): kept obligations, rollback inputs,
  watch list, trigger, and SW7.
- [The inventory, reclassified](s4-hard-switch/4-inventory-reclassified.md): all 31 rows of d227a123 under the new
  authority.

## Slices, in order, each shippable alone

| # | slice | repo | depends on |
|---|---|---|---|
| SW1 | Restart restore: last-known and stale, unknown until the next publication, ordered before routes | comms | - |
| SW2 | The operational view: `Record.busy`, typed availability, the UNKNOWN predicate, word and promotion, no timers | comms | - |
| SW-H | Message handoff rows: opened by transport delivery, resolved by a new turn anchor, a settlement or a lifetime change | comms (+ bridge receipts read-only) | SW2's types |
| SW3 | The sandbox gate: a stand-in agent plus cases on the env-against-comms runner | comms (+ env read-only) | green after SW1, SW2, SW-H |
| SW4 | D8 rows 1 and 2; B4 producers traced, and built only if missing | comms, env | - |
| SW5 | The switch: every reader rewired in one revertible commit; controls rebased; the rollback test | comms | SW1-SW4, SW3 green |
| SW6 | Deploy, watch, and roll back on the trigger | comms | SW5 |
| SW7 | Deletions and launchers to aify-env only, after the rollback gate | comms, wrapper | SW6 clean |

SW1, SW2 and SW4 run in parallel. SW-H follows SW2's types.

## Decisions this plan asks for

1. **Run correlation.** Option A (recommended): comms correlates through its own handoff rows, and aify-env carries no
   run id. Option B: aify-env's turn carries the reporter's `runId`, a schema change across all three repos. Handoff
   coverage needs only A's data (`startedAtUs`), and B adds exactness for B21 and B26. Page 1.
2. **The one clock comms keeps:** a publisher is fresh while its last applied publication is under 180 s old on comms'
   own receipt clock (`age_class`). It times comms' receipt, never a turn, and it is the explicit freshness contract.
   Page 1.

## ASSUMED, not checked

- aify-env republishes within 180 s while it runs. `SNAPSHOT_MS` = 60 s sets the snapshot epoch
  (env agent-state-sender.mjs); the send trigger was not read.
- B4's implementation status. The host reads `given.screen` and `given.backgroundShells`, and their producers were
  not traced.
- A scripted stand-in can drive `POST /agents/:id/turn-event` (env protocol.mjs) for a lifetime aify-env launched.
- After the switch, Hermes gateway turns reach aify-env through the wrapper's turn hooks.
- A channel message injected mid-turn starts a new turn rather than being consumed silently. The channels
  documentation says notifications that arrive while Claude is busy "are delivered together on the next turn"; Hermes
  steer behaviour was not checked.
