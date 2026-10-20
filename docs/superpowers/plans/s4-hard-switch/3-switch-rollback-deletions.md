# 3. The switch, the rollback, the deletions (revision 5)

## Before the switch

- **SW2, the operational view.** `commsView(records, dispatch) -> {status, lastKnown, deliverable, eligibility,
  busy, holdsDelivery, runOccupied}` is pure, and it is called by the status push, the status poll, the sessions list
  and every delivery reader (Q4).
  - `Record` gains `busy` from the record's top-level field.
  - Availability and busy stay separate typed outputs, with page 1's truth table.
  - The word follows C6 rows 1-6 with Q1, Q3 and the D8 rows.
- **Slice 5's obligations, rebased.** Worker readiness, live counts and running-run promotion read the new typed facts.
  - Unknown stays unknown in each of them. The old diagnostic containment (a recording failure read as "no change")
    is not promoted into a fail-open default.
  - Promotion, row 5, is applied inside the view, so it holds on every path.
- **D8 (SW4, comms), today's rule kept:**
  - an undefined agent the operator stopped reads `stopped`, and a no-record agent reads `unknown`, each with a unit
    witness in the view;
  - the send-time hold never skips the backing step (page 1's ordering rule), witnessed by the gate's cold-start case;
  - no undefined start is added, and no D8 guard is bypassed to build a fixture.
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

SW5 is built and reviewed on a branch after SW1, SW2 and SW4, and is not landed. That reviewed commit is the
candidate SW3b qualifies. It lands (SW5L) only after SW3b is green under page 2's rule, on the accepted runner, and
decisions 1 and 2 are adopted. Deploying it is SW6, a separate step.

- **Every reader is rewired** to the view in a single commit: page 1's readers plus readiness, live counts and
  promotion. The review receipt lists each call site, so the rewrite can be checked as complete.
- **The rollback's inputs stay live until the rollback gate:**
  - the legacy write routes keep applying, so `agent_turn_state`, `agent_status_state` and `agent_hook_order` stay
    current;
  - launchers keep posting turn events to comms as well as aify-env;
  - the legacy readers stay in the tree, unreferenced.
- **Retained code alone is not a rollback.** The rollback test below proves those inputs are still arriving.

## The population the switch covers (SW6, before deploy)

The switch qualifies Claude (channel, resident and managed) and Hermes (gateway and native-managed) with console
insertion off. A default and a skipped install attest nothing about the live fleet, so before deploy:
- read `insert_messages_via_console` as the deployed service resolves it (`_insert_messages_via_console(settings)`)
  and require it off;
- list the registered agents with their runtimes and modes from comms, and name each one outside the population
  (Codex, or any other runtime);
- each named agent is stopped, or the operator accepts it as unqualified by name.

The reads go with the SW6 receipt. Nothing outside the population is called qualified.

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
| a Claude agent sent a non-steer message while a turn it did not start is already active | the message is processed in a later turn, never inside the active one. Several messages accepted before that next turn may be processed together in it; that is healthy |
| queue depth | no non-steer queue held on an agent whose eligibility is known and whose record is idle |
| reminders and stranded failures | as ruled in R3: none while the agent is busy or unknown |

**Processing evidence** is the session's own record of which turn consumed the message (the transcript), read with
the run events and aify-env's turn boundaries. A `delivered` run, a channel write or a rendered box in the pane is
transport, and is not processing evidence.

## The rollback trigger

Roll back on any one of these:
- a non-steer run claimed while aify-env reports the agent busy;
- **non-steer processing inside a pre-existing active turn**: a non-steer message, sent while a turn was already
  active, consumed inside that same turn. Excluded: a steer the runtime accepted as one (Hermes `status: "queued"`, a
  claimed steerable run), and several messages processed together in the first turn after they were accepted;
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
