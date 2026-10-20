# 1. What comms reads after the switch

## `holdsDelivery`: aify-env's busy, and nothing comms times

`holdsDelivery(agent)` is three-valued:
- **true:** any fresh record of the agent says `busy`. Under a conflict, either record's `busy` counts.
- **false:** every fresh record says not busy, and no publisher on the agent's machine is unreadable.
- **unknown:** anything else, meaning no record, a stale or unreadable record, or a lost mirror binding.

`busy` is the record's top-level `busy`, which aify-env already decides: `turnIsBusy` holds a verified lifetime's open
turn with no timer (P-1), and falls back to its own strict limit only for an unverified one (env
lib/turn-events.mjs:96-101, agent-state-host.mjs:155-171).

`runOccupied` stays comms' own fact: a claimed or running row in `dispatch_runs`, which is persisted.

**Deleted from the product path:**
- the stale-record open-turn disjunct and `strictExpiry`, and with them N2 and fold provenance (b)
  (comms_view.py:107-118);
- the delivery-turn disjunct, its `turn_is_still_live` timing and the renewal read (comms_view.py:120-132).

`holds_delivery` keeps one disjunct.

## No delivery turn

Today `agent_turn_state` holds a turn comms delivered. The C6 fold reproduces it in memory (delivery_turns.py:160-170).
Each reader that uses it, and its replacement:

| reader (today) | today's question | after the switch |
|---|---|---|
| send-time queue (dispatch_launch.py:84-95) | `turn_busy_decision(...).holds` | queue when `runOccupied`, queued runs exist, or `holdsDelivery` is not false |
| claim gate (dispatch_claim.py:364-384) | `decision.holds`, then the steer carve-out | hold when `holdsDelivery` is not false, unless a claimable steerable run exists (carve-out unchanged) |
| reminder skip B21 (dispatch_sweeps.py:279-281) | `hasActiveRun`, or a fresh turn on a run other than this one | `hasActiveRun`, or `holdsDelivery` not false |
| stranded guard B26 (dispatch_lifecycle.py:139-145) | the turn is open on THIS run | `holdsDelivery` not false |
| status word, row 5 promotion (records.py `_status_with_dispatch`) | a running run on an idle-like word shows `working` | unchanged rule, applied by the one view |

**The two run-correlated readers change behaviour.** aify-env's turn has no run id: `turn-start` and `turn-end`
carry none (env lib/turn-events.mjs:21-22).
- **Reminder skip:** the agent busy on THIS run's own turn now skips the reminder too. Today's skip-forever deadlock
  (dispatch_sweeps.py:269-278) came from a `turn_busy` flag that never cleared. aify-env's verified turn ends at
  turn-end, so the skip ends with the turn.
- **Stranded guard:** a turn on other work now protects this run. A stranded run is failed after the agent goes idle,
  not while it works on something else. That is a delay, never a loss.
- **Witnesses that change**, by ruling: `test_a_fresh_turn_on_other_work_does_not_protect_this_run` inverts, and
  `test_the_reminder_skips_for_a_running_run_or_a_fresh_turn_on_other_work_and_for_nothing_else` widens. The other 15
  P7 witnesses keep their assertions, with the busy source swapped.
- **The alternative,** for review: aify-env's turn carries the reporter's `runId`. That is a schema change in env,
  comms and wrapper (rule: every copy), and it keeps today's correlation exactly.

## `unknown` means queue

- Send and claim treat unknown like busy: a non-steer run waits, and a steerable run is still injected (the harness
  takes mid-turn input).
- Reminder skip and stranded guard treat unknown like busy: they skip and retry next pass.
- **The word:** an agent with no record, or only stale records, reads `unknown` (grey; Q1 replaces C6 rows 2b and 3).
  Q3 still overrides to `offline` for a resident whose wake handle is proven absent. An aify-env that is not
  reporting is not that proof.

**The one clock left in comms.** `age_class` (partial_status_shadow.py:164-177) calls a publisher fresh while its last
applied publication is under 180 s old on comms' own clock. It measures when comms last heard from aify-env, not how
long a turn has run, and it stays. A later option: a held connection whose loss marks the publisher silent.

## The mirror reload at start (SW1)

**Today:** `mirror = Mirror()` starts empty (partial_status_shadow.py:392). `agent_state_shadow_rows` and
`agent_state_shadow_publishers` are written on every apply (agent_state_shadow.py:280-297) and never read back.

**The rule:** at service start, before the first route is served, each publisher row is rebuilt into the mirror:
- the cursor's data generation, incarnation, publication and `data_applied_at`, with the rows re-parsed through the
  same validation `prepare` applies;
- each reloaded publisher keeps its stored `data_applied_at`, so `age_class` alone decides whether it is fresh;
- a quick restart keeps knowledge, and a long one reads stale, so `unknown` and queue. No reload ever invents
  freshness.

**Edge cases:**
- **Empty tables:** an empty mirror, as today, and every agent `unknown` until its next publication.
- **A corrupt row or unparseable `record_json`:** that publisher loads as `unavailable` with `loss`. Its agents read
  `unknown` and nothing is guessed from the other rows. A whole-table read failure leaves the process quarantined,
  which the B5 receiver already does (`_new_mirror`).
- **A retired publisher** (cursor kind `retired`): not loaded. B5's per-receiver cursor already says it holds nothing.
- **A generation that went backwards:** the cursor stored is the highest applied, and `_order` refuses a later body
  below it (agent_state_shadow.py `_order`). The reload loads the stored cursor as-is and does not re-judge it.
- **Two publishers holding one agent:** loaded as stored. The view's conflict rule decides, as on a live apply.

The tables keep their names through 0.9.8. Renaming them (the `shadow` in the name is now wrong) waits for S5, so the
rollback interval runs on unchanged schema.

**How much the reload saves.** Without it, a comms restart is blind until each host's next publication. That is at
most one publication interval (ASSUMED at 60 s or less), and comms queues meanwhile, so nothing is delivered wrongly.
The reload makes the blind window zero for a quick restart. The earlier statement that a restart "would blind the
switched system" assumed the in-memory delivery turn, which this design drops.
