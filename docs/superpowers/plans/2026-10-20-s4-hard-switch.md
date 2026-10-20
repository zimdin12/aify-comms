# 0.9.8: the hard switch to aify-env's word (2026-10-20, plan revision 3, not built)

This replaces S2-S4 and the C8 gate of `2026-10-18-status-switch.md`. It follows two operator rulings
(DECISIONS.md, 2026-10-20):
- **The gate is a sandbox, not a one-day live comparison.** Then a hard switch with a rollback kept ready.
- **comms never times turns.** aify-env alone decides `busy`; comms queues when aify-env is silent.

Revision 3 answers REVISE s4-design-1792519039743 and supersedes 7e481315.

## The design in one paragraph

comms reads the top-level `busy` that aify-env publishes, and keeps no turn timer and no delivery-turn record. The gap
between delivering a message and aify-env seeing the turn needs no comms record. Claude's channel queues mid-turn
messages for the next turn, and Hermes' gateway refuses a busy submit, after which comms requeues the run (page 1
evidence). Availability and busy are separate facts:
- Eligibility is known only when every holder of the agent is fresh.
- Unknown withholds every delivery route before busy and before steer.
- After a restart, restored records stay unknown until a complete snapshot is accepted.

## Pages

- [What comms reads](s4-hard-switch/1-what-comms-reads.md): the runtime evidence, the truth table, the readers, the
  restart, and the operator decisions.
- [The sandbox gate](s4-hard-switch/2-sandbox-gate.md): frozen cases with RED mutations that reach the predicate.
- [Switch, rollback, deletions](s4-hard-switch/3-switch-rollback-deletions.md).
- [The inventory, reclassified](s4-hard-switch/4-inventory-reclassified.md): all 31 rows of d227a123.

## Slices, in order

| # | slice | repo | depends on |
|---|---|---|---|
| SW1 | Restart restore: `restored` until an accepted complete snapshot, loaded before the routes are served | comms | - |
| SW2 | The operational view: `Record.busy`, the truth table, eligibility-first readers, the word with a last-known note, D8 precedence, and the no-timer unit witness | comms | - |
| SW3 | The sandbox gate (page 2) | comms (+ env read-only) | green after SW1, SW2 |
| SW4 | D8 rows; trace B4's producers and whether aify-env publishes undefined managed lifetimes, building only what is missing | comms, env | - |
| SW5 | The switch: every reader rewired in one revertible commit (page 1 plus readiness, live counts, promotion); controls rebased; the rollback test | comms | SW1-SW4, SW3 green, R3 ruled |
| SW6 | Deploy, watch, roll back on the trigger | comms | SW5 |
| SW7 | Deletions and launchers to aify-env only, after the rollback gate | comms, wrapper | SW6 clean |

## Operator decisions (page 1 has the exact before and after)

1. **R3:** reply-overdue reminders pause while aify-env says the agent is busy with anything, and the stranded guard
   protects any run while the agent is busy.
2. **Receipt freshness, 180 s,** on comms' own clock. A missed publication reads `unknown`, and messages queue.
3. **Only if SW4 finds aify-env publishes no undefined managed lifetimes:** such an agent reads `unknown` after its
   cold start.

## ASSUMED, not checked

- Claude's channel queueing, from the documentation, not executed here. The switch watch observes it.
- Hermes `steer` landing only with `status: "queued"` means the gateway queues it. The code reads that status; gateway
  internals were not read.
- B4's producers, and whether aify-env publishes undefined managed lifetimes (SW4 traces both).
- A scripted stand-in can drive `POST /agents/:id/turn-event` for a lifetime aify-env launched.
