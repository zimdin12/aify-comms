# 3. The switch, the rollback, the deletions (revision 3)

## Before the switch

- **SW2, the operational view.** `commsView(records, dispatch) -> {status, lastKnown, deliverable, eligibility,
  busy, holdsDelivery, runOccupied}` is pure, and it is called by the status push, the status poll, the sessions list
  and every delivery reader (Q4).
  - `Record` gains `busy` from the record's top-level field.
  - Availability and busy stay separate typed outputs, with page 1's truth table.
  - The word follows C6 rows 1-6 with Q1, Q3 and the D8 precedence.
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

- **Every reader is rewired** to the view in a single commit: page 1's readers plus readiness, live counts and
  promotion. The review receipt lists each call site, so the rewrite can be checked as complete.
- **The rollback's inputs stay live until the rollback gate:**
  - the legacy write routes keep applying, so `agent_turn_state`, `agent_status_state` and `agent_hook_order` stay
    current;
  - launchers keep posting turn events to comms as well as aify-env;
  - the legacy readers stay in the tree, unreferenced.
- **Retained code alone is not a rollback.** The rollback test below proves those inputs are still arriving.

## The rollback (SW6)

- **What reverts:** the SW5 commit, followed by a stamp and rebuild. That takes minutes, and nobody relaunches.
- **The rollback test, in SW5's review:**
  1. A turn starts after the cutover.
  2. The SW5 revert is applied over the same database.
  3. Hold and release, queue and claim, and run ownership must read correctly under the legacy readers, from tables
     the legacy routes kept writing.
- **The interval:** the rollback stays ready until a clean watch period ends. That is the operator's call, with the
  plan default being one working day of normal fleet use. Only then does SW7 run.

## The watch list, for the first hours on the real fleet

Read from comms' routes and the dispatch tables at 15 min, 1 h and 4 h.

| what | healthy reading |
|---|---|
| Hermes gateway agents (comms-senior-dev, pc-manager) | busy while working, idle after; 4009 requeues happen only at a turn boundary and drain on the next pass |
| resident claude panes (B1 launchers) | the word matches the pane; `unknown` only when that host's aify-env is silent |
| agents at a Claude dialog (dev-channels, folder trust) | nothing delivered into the dialog |
| a Claude agent given two messages in one gap | the second is processed in the next turn, never mid-turn (from the pane and the run events) |
| queue depth | no non-steer queue held on an agent whose eligibility is known and whose record is idle |
| reminders and stranded failures | as ruled in R3: none while the agent is busy or unknown |

## The rollback trigger

Roll back on any one of these:
- a non-steer message delivered while aify-env reports the agent busy;
- a message processed inside a turn it did not start (a runtime that does not queue);
- a queued run still held on the first claim pass after comms accepts the agent's fresh idle publication;
- an agent of a reporting host reading `unknown` past one publication interval;
- the operator says so.

The run and record ids that show it are kept as the evidence.

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
   stale, restored, unavailable) and whether the reload ran at start.
6. The two tables are renamed out of `shadow`, with a migration.

aify-env's unverified-lifetime fallback, and any redesign of it, is aify-env's own question and not a step here.
