# The delivery turn after a boot: closed by C6's own bound (2026-10-20, proposal)

A knowledge-rule change to the shadow fold (`service/api_core/delivery_turns.py`). It needs the dev's review and then
the operator's decision before any code (D-UNKNOWN decision `bootstrap-knowledge-contract-question`).

## What the measurement found

Cohort w20-w21 (comms 972cb300, env 9b68e0a1, 12.58 h, `complete`, read 2026-10-20T10:25:34Z, sha ec9372f9):

| field `totals.<variant>/claim` | P-1 | strict |
|---|---|---|
| `unavailable:delivery-turn` | 9,838 | 14,805 |
| origin never-known, no reporter since boot (`never-known:null:no` + `never-known:closed:no`) | 8,317 | 8,317 |
| origin never-known, a reporter since boot (`:yes`, incl. strict's `never-known:open:yes` 4,967) | 17 | 4,984 |
| origin ambiguous-closed | 1,504 | 1,504 |

84.5% of P-1's unknown busy answers are agents the fold has heard nothing from since the service booted: no reporter
statement, and an env lineage that is null (7,143) or closed and quiet (1,174).

## Why they stay unknown

The fold starts empty at boot (`DeliveryTurns.__init__`) and C6 closes a delivery turn only on evidence of an end:
an env `ended` (`env_effect`, delivery_turns.py:151) or a matching reporter clear (`reporter_clear`, :113). A first
sight or a quiet republication leaves it `NOTHING_KNOWN` (:149-157), and a null clock writes nothing (`_consume`, :277).
An idle agent produces neither end, so its turn is unknown for the life of the process. The unknown is not about the
agent; it is the turn that may have been open before the boot, which the in-memory fold cannot see.

## The rule, derived from C6 rather than added to it

C6 judges an open delivery turn with `turn_is_still_live` (comms_view.py:104) under `TURN_BUSY_BACKSTOP_SECONDS` =
1800 (liveness.py:171): live only while `now - seen <= 1800`, where `seen` is the start or, renewable, the later of
start and last touch (turn_liveness_policy.py:55-74). Only two writes move those anchors: a reporter busy (touch,
:107-110) and an env `advanced` on an open turn (touch, :154).

So for a turn that could only have been opened before boot B, and whose anchors no write since B could have moved,
both anchors are at most B. At any `at >= B + 1800 + NOW_RESOLUTION_SECONDS`, C6's own law says that turn is not live,
whatever it was. The fold may then read it as closed.

**Rule.** An agent's delivery turn whose origin is never-known, and whose every write since B was an env `first` or
`quiet` consumption (a null clock writes nothing, so it qualifies), reads `CLOSED` at the first read or write at or after
`B + 1800 + NOW_RESOLUTION_SECONDS`. B is the service clock at fold construction. A reporter statement, an env
`advanced`, `started`, `ended`, `ambiguous-closed` or a withhold for that agent makes it ineligible for good. Those
keep today's paths. `lose_everything` at L moves B to L for every never-known key, since the publication it could not
read may have touched any of them.

A reporter busy that arrives after the bound then finds the turn closed and records `startedAt` = now (:109), which
also resolves strict's `never-known:open:yes` when the first busy comes after the bound.

## Not in scope

- `ambiguous-closed` (1,504, 15%): a closed lineage whose clock moved, unblock or late end. A separate question.
- Withheld and lost origins. The same proof covers `lost` with B = L; I propose it only if the dev wants the symmetry.
- The end state persists the delivery turn (it replaces `agent_turn_state`'s run half, P0 C6/C7), so it has no
  bootstrap gap. This rule makes the shadow agree with it once the first 1800 s are over. Those 1800 s stay a warm-up
  interval, which the gate never stitches into the qualifying day (status-switch plan step 4).
- Wall clock, as `turn_is_still_live` uses. Clock steps are the fold-provenance slice's (C1).

## Witnesses, one per obligation

1. No write since B: `NOTHING_KNOWN` at B+1800, `CLOSED` at B+1800+resolution. RED if the rule is removed or the
   bound is off by the resolution.
2. An env `advanced` since B: never closed by the bound. RED if eligibility ignores advances.
3. A reporter busy before the bound: start unknown; after it: start = at. RED if expiry is applied to a reporter key.
4. `lose_everything` at L > B: not closed at B+1800, closed at L+1800+resolution. RED if a loss does not move B.
5. The D-UNKNOWN report counts bootstrap closures as their own origin (`bootstrap-expired`), so they stay visible in
   the comparison instead of disappearing into `not-eligible`.

## What would prove this wrong (pre-registered)

In the first full window opened 1800 s or more after a comms boot, never-known's share of P-1/claim
`unavailable:delivery-turn` falls below 5% (from 84.71%). If it stays above 20%, the population is not what the code
says it is: the rule is withdrawn and the unknowns are re-measured. Any busy or claim disagreement on a bootstrap-closed
agent is a classified difference in C8, never folded into an approved class.
