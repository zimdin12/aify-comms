# A bounded not-live fact for the delivery turn after a boot (2026-10-20, proposal rev 2)

A knowledge-rule change to the shadow fold (`service/api_core/delivery_turns.py`) and the delivery disjunct
(`comms_view._delivery_disjunct`). It needs the dev's review and then the operator's decision before any code
(D-UNKNOWN decision `bootstrap-knowledge-contract-question`). Rev 2 answers REVISE f42a601d: rev 1 minted a raw
`CLOSED`, which C6's bound does not prove and which mis-starts a turn still open across the boot.

## What the measurement found, and what it does not say

Cohort w20-w21 (comms 972cb300, env 9b68e0a1, 12.58 h, `complete`, read 2026-10-20T10:25:34Z, sha ec9372f9). These
are **claim evaluation counts** in each stratum, not agents, and the strata say nothing about an agent's whole history
(an unknown-to-unknown ambiguous close keeps origin never-known):

| field `totals.<variant>/claim` | P-1 | strict |
|---|---|---|
| `unavailable:delivery-turn` | 9,838 | 14,805 |
| never-known, lineage null or closed, no reporter since boot (`:null:no` + `:closed:no`) | 8,317 | 8,317 |
| never-known with a reporter since boot (incl. strict's `never-known:open:yes` 4,967) | 17 | 4,984 |
| ambiguous-closed | 1,504 | 1,504 |

## The fact C6's bound proves

C6 holds an open delivery turn only while `turn_is_still_live` (turn_liveness_policy.py:55-74, 1800 s,
liveness.py:171). `seen` is `startedAt`, or the later of start and touch when renewable, and renewal selects an anchor
but never moves one. So if every anchor of a turn is at most C, then at any `at > C + 1800 + NOW_RESOLUTION_SECONDS`
the turn is not live **in every completion**, open or closed, renewed or not. That proves the delivery disjunct
false. It proves nothing about raw `open`, `runId` or `startedAt`, which stay separate outputs (P0 C6:302-310,
s2c-owners:54-59) and stay `NOT_KNOWN`.

## The rule

1. **A semantic ceiling, not a diagnostic.** `DeliveryTurn` gains `ceiling`: an upper bound on every anchor the turn
   holds as `NOT_KNOWN`, or `NOT_KNOWN` itself. It lives in `_turns` and is computed in the same write as the turn.
   `_origin` and `_reported` stay diagnostic and decide nothing.
2. **The epoch.** The fold keeps `epoch`: the service clock at construction (B), and the time of each
   `lose_everything` (L). A key the fold has never written reads `NOTHING_KNOWN` with `ceiling = epoch`. A loss moves
   the epoch, and it applies to every key, since the publication it could not read may have touched any of them.
3. **Every write keeps the bound, or widens it to its own time.** The new ceiling is the maximum of the old
   ceiling, the anchors the turn knew before the write, and the write's `at` when the write could set an anchor:
   - reporter busy (touch now; start now or kept, as `turn_busy_signal.py:65-82` keeps it while raw busy is 1);
   - env `advanced` on a turn whose `open` is not known false;
   - a withheld record (a missed observation may have been an advance).

   `first`, `quiet`, `started`, `ended`, `ambiguous-closed` and a reporter clear set no anchor, so they keep the
   bound. A write that cannot compute its ceiling makes it `NOT_KNOWN`, which is today's unknown: the rule fails
   closed.
4. **The disjunct.** When `delivery_known(turn)` is false, `ceiling` is known, `frame.at` is known, and
   `frame.at > K`, where the cutoff `K = max(known anchors, ceiling) + 1800 + NOW_RESOLUTION_SECONDS`, the disjunct is
   **false** with provenance `delivery-bounded`. No renewal query is needed. Otherwise it is unknown, exactly as today.
   **Strict `>`, and why:** `turn_is_still_live` is live at an age of exactly 1800 (`0 <= age <= strict_seconds`,
   turn_liveness_policy.py:74), so at `at = C + 1800` a completion with an anchor at C is still live. The added
   resolution is margin for the epoch, which is read from a float clock while every other stamp is whole seconds. At
   `at = K` the disjunct is unknown; the first whole-second `at` after K is false.

The dev's countercase: raw busy since B-100 survives the boot, and a reporter busy comes at B+1802. The turn becomes
open, with run R, touch B+1802, start `NOT_KNOWN`, and ceiling B+1802. At B+1803 the disjunct is unknown, not held
and not released. Today's writer would give strict age 1902 and not held, and a fresh start would give held. Both
completions stay possible, so unknown is the only truthful answer.

## Stage assumed

The S2c candidate before S4. The fold hears C6's writers: heartbeat reporters and applied env records. Today's hook
turn-start, turn-end and ready writers write `agent_turn_state` and never the fold. C7 deletes them. The bound is a
fact about the fold's turn, which is the C6 candidate, and C8 measures where it differs from today's row. The end
state persists the delivery turn, so this gap belongs to the shadow. Wall clock, as `turn_is_still_live` uses: a
backward step can under-state a ceiling, and the clock-safe-age slice (C1, gated on N2) must cover the ceiling before
any qualifying day.

## Counting it

`delivery-bounded` is its own provenance count beside, not inside, `unavailable:delivery-turn`. A bounded evaluation
is a known false disjunct, so putting it in the unknown-origin partition would break that partition's sum contract.
The D-UNKNOWN reader reports it per site and variant.

A bounded false is one component, not an output. A true fresh record ORed with a bounded false disjunct still holds,
and today's busy agrees. Claim also reads occupancy, steer and the other gate facts. So C8 counts a **final** busy or
claim output that differs from today's **and** carries `delivery-bounded` provenance. Every such unapproved difference
is disqualifying.

## Witnesses, one per obligation

1. A key never written: unknown at K = epoch+1800+resolution, `delivery-bounded` false at K+1. RED with the rule
   removed, the resolution dropped, or `>` relaxed to `>=`.
2. Raw open across the boot, then a reporter busy after the bound: raw stays open with start `NOT_KNOWN`, and the
   disjunct is unknown at at+1. RED if the fold mints `CLOSED` or a start (rev 1's behaviour).
3. Verified renewal: a bounded turn whose owner heartbeats still reads false after the bound. A renewed turn whose
   touch the fold saw reads by its anchor. RED if renewal is taken to move an anchor.
4. An env `advanced` or a withhold at t moves the ceiling to t. A `quiet` republication does not. RED if either is
   swapped.
5. `lose_everything` at L > B: unknown at B+1800+resolution+1 and at L+1800+resolution, false at L+1800+resolution+1.
   RED if a loss does not move the epoch.
6. Counting, two separate checks. The existing unknown-origin partition still sums (the reader's `sumBroken` stays
   empty) while bounded evaluations occur. `delivery-bounded` equals the number of disjunct calls that returned it.
   RED if a bounded evaluation lands in the unknown partition.
7. Masking control: a fresh record with busy true plus a bounded false disjunct gives busy true, and C8 records no
   difference. A fresh record with busy false plus a bounded false disjunct gives busy false. If today's answer is busy,
   that is one counted difference carrying `delivery-bounded`. RED if differences are counted per component, or if the
   masked case is counted.

## What would prove this wrong (pre-registered)

The window: the first one opened after the effective cutoff K (the later of boot and the last loss, + 1800 +
resolution), by the same strict `>`. A window that contains an epoch move does not count.

- **Coverage, not correctness:** removing the 8,317 mechanically leaves 17 of 1,521 (1.12%). Never-known's share of
  P-1/claim `unavailable:delivery-turn` therefore shows only that the bound reaches the idle population. Below 5%
  supports that. 5-20% is inconclusive and gets exactly one more window. If that window is also inconclusive, the
  rule is withdrawn and the result goes to the operator; there is no third window. Above 20% withdraws the rule. A
  zero denominator counts as inconclusive under the same one-more-window limit.
- **Correctness:** final busy and claim output differences carrying `delivery-bounded` provenance (witness 7). One
  unapproved difference is a failed rule, not a class.
