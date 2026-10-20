# 0.9.8: the hard switch to aify-env's word (2026-10-20, plan revision 5, not built)

This replaces S2-S4 and the C8 gate of `2026-10-18-status-switch.md`. It follows two operator rulings
(DECISIONS.md, 2026-10-20):
- **The gate is a sandbox, not a one-day live comparison.** Then a hard switch with a rollback kept ready.
- **comms never times turns.** aify-env alone decides `busy`; comms queues when aify-env is silent.

Revision 4 answers REVISE s4-design-1792521656338 and supersedes f466c492:
- D8 stays as built: no undefined start, and no availability exception. A cold start creates backing; delivery stays
  at the claim gate (page 1).
- The watch and the rollback trigger judge non-steer processing inside a turn already active, with steering and
  next-turn grouping excluded (page 3).
- The gate states its decisions, its green rule, a no-timer witness that holds receipt age fixed, and the population
  it covers.

Revision 5 answers REVISE s4-design-1792524066212 (qualification order only). Building the gate, building the
candidate and qualifying it are separate stages: the candidate is built and reviewed on a branch, qualified as an
exact pinned pair, and a green result gates its landing, never its construction.

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
| SW2 | The operational view: `Record.busy`, the truth table, eligibility-first readers, the word with a last-known note, the D8 rows, and the no-timer unit witness | comms | - |
| SW3a | Build the sandbox harness: the stand-in, the frozen cases and their REDs (page 2) | comms (+ env read-only) | - |
| SW4 | The D8 stop row and the cold-start ordering (backing is never skipped by a hold); trace B4's producers, building only what is missing | comms, env | - |
| SW5 | Build the candidate: every reader rewired in one revertible commit (page 1 plus readiness, live counts, promotion); controls rebased; the rollback test. Reviewed on a branch, **not landed** | comms | SW1, SW2, SW4 |
| SW3b | Qualify: the three fixed attempts against the exact pinned pair (the SW5 candidate commit, the aify-env commit) | comms (+ env read-only) | SW3a, SW5 reviewed, decision 2 adopted, the runner accepted |
| SW5L | Land the SW5 candidate, the exact qualified commit | comms | SW3b green, decisions 1 and 2 adopted |
| SW6 | Bind the population, deploy, watch, roll back on the trigger | comms | SW5L |
| SW7 | Deletions and launchers to aify-env only, after the rollback gate | comms, wrapper | SW6 clean |

## Operator decisions (page 1 has the exact before and after)

1. **R3:** reply-overdue reminders pause while aify-env says the agent is busy with anything, and the stranded guard
   protects any run while the agent is busy.
2. **Receipt freshness, 180 s,** on comms' own clock. A missed publication reads `unknown`, and messages queue.

Both are requests until Steven adopts them. Each must be adopted before the first stage it governs: decision 2
before SW3b (the gate's paused case uses it), decision 1 before SW5L. Construction depends on neither: SW1, SW2,
SW3a, SW4 and the SW5 candidate proceed before them, building each rule as proposed. The D8 rows need no decision: they keep today's rule.

## ASSUMED, not checked

- Claude's channel queueing, from the documentation, not executed here. The switch watch observes it. A channel write
  is transport, not acknowledgement, and a channel that is not loaded or not allowed drops events silently.
- Hermes `steer` landing only with `status: "queued"` means the gateway queues it. The code reads that status; gateway
  internals were not read.
- B4's producers (SW4 traces them).
- aify-env publishes a record for a defined managed lifetime it launched. The sandbox's cold-start case exercises it.
- A scripted stand-in can drive `POST /agents/:id/turn-event` for a lifetime aify-env launched.
