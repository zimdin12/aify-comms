# 4. The inventory, reclassified (supersedes d227a123's table)

The classes are relative to the operator's two rulings of 2026-10-20 and comms-senior-dev's disposition
(reviewqueue-219/c8-n2-authority-1792516661010, INVENTORY-RECLASSIFICATION.json). The labels:
- **A:** switch-needed.
- **O:** the obligation is kept and the mechanism rewritten on the new authority.
- **W:** withdrawn comms turn timing.
- **B:** comparison-only. It leaves the plan, and its code goes in SW7.
- **env:** aify-env's own question.

None of these labels authorizes a product deletion outside SW7.

| # | item | class | disposition |
|---|---|---|---|
| 1 | Bootstrap 30-min rule | W | Withdrawn unbuilt (0a1ef84a) |
| 2 | Fold provenance (a), build sets and window lifting | B | Comparison-only |
| 3 | Fold provenance (b), clock-safe `appliedAgeMs` | env | Not a comms prerequisite; aify-env's fallback design |
| 4 | N2, strictExpiry | W | comms no longer times stale turns |
| 5 | B6c stale-turn ceiling module | B | Deleted in SW7 with the timed formulas |
| 6 | B6a/b `build` on records | B | Comparison-only; the optional wire field stays readable |
| 7 | B6 `turn.ageMs` | W | Not consumed by comms; optional wire compatibility only |
| 8 | S2c slice 1, recorder per owner | B | Comparison-only |
| 9 | S2c slice 2, the DeliveryTurns fold | W, obligation O | The fold is **dropped entirely**. Its obligation, no second delivery into the gap before the turn, is the persisted message handoff (page 1, SW-H) |
| 10 | S2c slice 3, commsView busy | W, obligation O | The timed disjuncts are **dropped entirely**. The obligation is the operational `holdsDelivery` from top-level `busy` with typed unknown (SW2) |
| 11 | S2c slice 4, busy consumers | W, obligation O | The old formulas, including `claim_candidate` and the 120-second `other_work`, are **dropped entirely**. The queue, claim (unknown before steer), reminder and stranded obligations are rebased (page 1) |
| 12 | S2c slice 5, readiness, live counts, promotion | O | Rebased on typed facts; unknown stays unknown; no fail-open default |
| 13 | S2c-E evidence | B | Comparison-only |
| 14 | S2c 6b D-UNKNOWN reader | B | Comparison-only |
| 15 | B5 publisher retirement | A, done | Landed 7c1f76da plus env 2ae603de |
| 16 | B5b mirror reload at boot | A | A restart barrier: restored is not fresh and not complete; unknown until the next publication (page 1) |
| 17 | S2c slice 7, unseen-class controls | O | Deterministic tests by obligation with new answers; the withdrawn-timer tests retire |
| 18 | Promotion on every path | O | Inside the one view |
| 19 | B4 inputs reach aify-env | A | Producers traced first (SW4) |
| 20 | B2 system stops | A, done | Deployed 0cc4e58c |
| 21 | B3 mode follows definition | A, done | Deployed |
| 22 | S4 witnesses (P7, 17 tests) | O | Obligations kept, expectations rebased |
| 23 | S4 pure commsView | A | With the new fact schema (top-level `busy`, availability, handoff) |
| 24 | S4 delivery hold | A | The new admission rule: unknown queues and withholds, steer included; no legacy fallback |
| 25 | S4 `unknown` word (Q1) | A | — |
| 26 | S4 reverse shadow | B | Not built |
| 27 | Legacy interval with rollback | A | Compatible rollback code and its live inputs (legacy routes, dual-posting launchers) until the gate |
| 28 | Launchers report only to aify-env | A | SW7, after the rollback gate |
| 29 | S5 deletions | A | SW7 order (page 3) |
| 30 | agent-state-shadow doctor row | B | Retires. The operational availability, handoff and reload check stays (A) |
| 31 | C1 parent | B | Comparison-only |

The sandbox obligations table in d227a123 is replaced by page 2's cases.
