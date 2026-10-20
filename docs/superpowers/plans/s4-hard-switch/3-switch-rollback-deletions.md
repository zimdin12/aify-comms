# 3. The switch, the rollback, the deletions (revision 2)

## Before the switch

- **SW2, the operational view.** `commsView(records, dispatch, handoffs) -> {status, note, deliverable,
  eligibility, busy, holdsDelivery, runOccupied}` is pure, and it is called by the status push, the status poll, the
  sessions list and every delivery reader (Q4).
  - `Record` gains `busy` from the record's top-level field.
  - Availability, busy and handoff stay separate typed outputs (page 1).
  - The word follows C6 rows 1-6 with Q1 (`unknown`) and Q3.
- **SW-H, message handoff** (page 1). The persisted handoff rows, opened by transport delivery writes and resolved by a
  new turn anchor, an outcome settlement or a lifetime change.
- **Slice 5's obligations, rebased.** Worker readiness, live counts and running-run promotion read the new typed facts.
  - Unknown stays unknown in each of them. The old diagnostic containment (a recording failure read as "no change")
    is not promoted into a fail-open default.
  - Promotion, row 5, is applied inside the view, so it holds on every path.
- **D8 rows 1 and 2 (SW4, comms):**
  - an undefined agent the operator stopped reads `stopped`;
  - an undefined managed agent comms can cold-start reads `available`;
  - each gets a unit witness in the view.
- **B4 (SW4, env).** Trace the producers of `given.screen` and `given.backgroundShells` (agent-state-host.mjs) and
  build only what is missing, under the env-schema rule (every copy; sibling suites with AIFY_ENV_REPO=candidate).
- **Controls by obligation, with new expected answers:**
  - The 17 P7 witnesses and slice 7's deterministic list (P0:403-406: reordered and tied events, a delayed
    prior-lifetime hook, a lost publication, stale, duplicate or conflicting publications, a reused pid, an unknown
    probe, stopped, undefined) keep their obligations.
  - Each expected answer is rewritten for the new owner contract.
  - A test that exists only to pin a withdrawn comms timer ("expired turn", "renewed long turn", the strict-timer
    specimens) is retired with the timer. It does not keep the timer alive.

## The switch (SW5): one revertible commit

- **Every reader on page 1 is rewired** to the view in a single commit.
- **The rollback's inputs stay live until the rollback gate:**
  - the legacy write routes keep applying, so `agent_turn_state`, `agent_status_state` and `agent_hook_order` stay
    current;
  - launchers keep posting turn events to comms as well as aify-env;
  - the legacy readers stay in the tree, unreferenced.
- **Retained code alone is not a rollback.** The rollback test below proves those inputs are still arriving.

## The rollback (SW6)

- **What reverts:** the SW5 commit, followed by a stamp and rebuild. That takes minutes, and nobody relaunches.
- **The rollback test, in SW5's review:**
  1. A turn starts after the cutover, plus a no-reply channel handoff.
  2. The SW5 revert is applied over the same database.
  3. Hold and release, queue and claim, and run ownership must read correctly under the legacy readers, from tables
     the legacy routes kept writing.
- **The interval:** the rollback stays ready until a clean watch period ends. That is the operator's call, with the
  plan default being one working day of normal fleet use. Only then does SW7 run.

## The watch list, for the first hours on the real fleet

Read from comms' routes and the dispatch tables at 15 min, 1 h and 4 h.

| what | healthy reading |
|---|---|
| Hermes gateway agents (comms-senior-dev, pc-manager) | busy while working, idle after; handoffs close on the next turn |
| resident claude panes (B1 launchers) | the word matches the pane; `unknown` only when that host's aify-env is silent |
| agents at a Claude dialog (dev-channels, folder trust) | nothing delivered into the dialog; any open handoff is visible |
| open handoffs | each closes on a new turn, a settlement or a lifetime change; none stays open on an idle, fresh agent |
| queue depth | no non-steer queue held on an agent whose record is idle and fresh and which has no open handoff |
| reminders and stranded failures | none for a run whose handoff is open or whose agent is busy |

## The rollback trigger

Roll back on any one of these:
- a non-steer message delivered while aify-env reports the agent busy, or while another handoff is open;
- a queued run held with a fresh idle record and no open handoff on the next claim pass;
- an agent of a reporting host reading `unknown` past one publication interval;
- a handoff open on a fresh idle agent past its next turn;
- the operator says so.

The run, handoff and record ids that show it are kept as the evidence.

## Deletions (SW7, 0.9.9, after the rollback gate)

In S5's order (status-switch.md step 7):
1. A database backup.
2. Launchers post only to aify-env. Steven relaunches.
3. The legacy turn routes become accept-and-ignore (C7). The rollback inputs end here, which is why this comes after
   the gate.
4. Deleted, each after proof that no live route reaches it:
   - the legacy engine, detectors and tables;
   - the comparison-only items on page 4;
   - the timed formulas retired with the turn timer: the `DeliveryTurns` fold, `strictExpiry`, the stale and
     delivery-turn disjuncts, `busy_consumers`' old law with its 120-second freshness, and the B6c ceiling module.
5. The `agent-state-shadow` doctor row retires. An operational check stays: per host, publisher availability (fresh,
   stale, restored, unavailable), open handoffs, and whether the reload ran at start.
6. The two tables are renamed out of `shadow`, with a migration.

aify-env's unverified-lifetime fallback, and any redesign of it, is aify-env's own question and not a step here.
