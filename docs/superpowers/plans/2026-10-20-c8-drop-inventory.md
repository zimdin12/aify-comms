# If C8 (the one-day live comparison) is dropped: what is switch-needed and what is comparison-only

**SUPERSEDED 2026-10-20** by the reclassified table in `s4-hard-switch/4-inventory-reclassified.md` (branch 098/s4-plan, 7e481315), written after the operator ruled that comms never times turns. Kept as the first pass.

Read-only inventory, 2026-10-20, comms main 9d49da71. A = switch-needed, B = comparison-only, C = mixed.
Evidence labels: PROVEN (read in code or plan), ASSUMED, UNCLEAR.

## First, a correction to what was told to Steven

The chat answer and dashboard question 01M5D4TG say "the real switched system keeps its turn record in the database,
so it has no such gap". **That is not built, and no plan specifies it.**
- PROVEN: the delivery turn exists only as the in-memory fold.
  - `DeliveryTurns` (delivery_turns.py:160-170) and the module global `FOLD` (:300).
  - Its docstring says "Diagnostic only" and "never reads or writes `agent_turn_state`" (:1-6).
  - s2c-owners.md:17 calls it "one in-memory object per agent, shadow-owned".
- PROVEN: the env-record mirror is in memory too.
  - `mirror = Mirror()` (partial_status_shadow.py:392).
  - agent_state_shadow.py only INSERTs and DELETEs `agent_state_shadow_rows` (:280-297), and nothing reads them back
    at boot. B5's "mirror reloaded after a service restart" is unbuilt.
- PROVEN: P0 C6 defines the delivery turn's fields (P0:302) and C7 deletes `agent_turn_state` (P0:360). No plan
  names a table that persists the delivery turn after S4.
- Consequence: if S4 promotes the fold and mirror as they are, every comms restart blanks delivery knowledge in
  PRODUCTION, not just in the comparison. Busy and claim would answer unknown until each agent's next turn end (fold)
  or next publication (mirror). The bootstrap gap is then a product problem.
- Fix options, all A:
  - persist the delivery turn (a table replacing `agent_turn_state`'s run half);
  - or seed the fold from `agent_turn_state` while the legacy routes keep applying (status-switch.md:63-65 keeps
    them through 0.9.8);
  - plus reload the mirror from `agent_state_shadow_rows` at boot.

## Table

| item | class | evidence |
|---|---|---|
| Bootstrap 30-min rule (098/bootstrap-proposal) | B as written / C in effect | It is a rule on the in-memory fold. With a persisted or seeded delivery turn (above) it is unnecessary. Without one, the same gap hits the switched product, and some boot answer is needed (A). PROVEN (delivery_turns.py:160-170) |
| Fold provenance (a): build sets per fold entry, window lifting | B | Feeds `fold_unverified` and window qualification only (fold-provenance plan "(a)", "The window rule"; shadow_provenance.py:1). PROVEN |
| Fold provenance (b): clock-safe `turn.appliedAgeMs` in aify-env | A if strictExpiry ships | `strictExpiry` = earliest `appliedAt - ageMs` + 1800 (P0:341-353). A forward clock step inflates the age (s2c-owners.md, B6 "The age assumes a continuous producer clock"). PROVEN |
| N2, the stale-anchor / strictExpiry contract | A | It is C6's product rule for how long a stale record's open turn holds delivery (P0:312-317, :341-353). The dev routed it as a product-contract change (finish-0-9 memory, 10-20). PROVEN |
| B6c stale-turn ceiling module | B | "Diagnostic only... this slice measures and does not qualify" (stale_turn_ceiling.py:1-25). The product law it measures is N2/strictExpiry (A). PROVEN |
| B6a/B6b `build` on records | B | Used for window provenance (s2c-owners.md, B6 "Windows know their env builds"). PROVEN |
| B6 `turn.ageMs` | A | Input to strictExpiry (P0:341). PROVEN |
| S2c slice 1, recorder per owner | B | shadow_owners.py:1, shadow_windows.py:1. PROVEN |
| S2c slice 2, DeliveryTurns fold | C | The merge rules (delivery_turns.py:107-157) are C6's product delivery turn (A). Its in-memory, shadow-owned home, the knowledge-unknown seeding and the diagnostics (`_origin`, `_reported`, :168-207) are B. PROVEN |
| S2c slice 3, commsView busy | C | `holds_delivery` and the disjuncts (comms_view.py:104-147) are A. Recording, evidence and the `Basis` are B (comms_view.py:1-19). PROVEN |
| S2c slice 4, busy consumers (queue, claim, reminder, stranded) | C | Candidate expressions A; recording B (busy_consumers.py:1). PROVEN |
| S2c slice 5, readiness, live counts, promotion | C | Same split (view_owners.py:1). Rows 2/2b (D8) are "not implemented" (s2c-owners.md slice 5), so they are a product gap (A). PROVEN |
| S2c-E evidence (E1/E2) | B | shadow_evidence.py:1, shadow_evidence_store.py:1. S5 deletes them (status-switch.md:80-83). PROVEN |
| S2c 6b D-UNKNOWN reader and origins | B | delivery_unknown_report.py:1; "a diagnostic deleted with the shadow" (delivery_turns.py:25-27). PROVEN |
| S2c 6c / B5 publisher retirement | A | A superseded key left in the mirror makes agents ambiguous and unknown in the product view (s2c-owners.md 6c; partial_status_shadow.py:207-212). Landed comms 7c1f76da plus env 2ae603de. PROVEN |
| B5b mirror reload at boot | A | Unbuilt (see the correction). PROVEN absent |
| S2c slice 7, C8 unseen-class controls | C | The C8 list (P0:403-406: reordered/tied events, a delayed prior-lifetime hook, publication loss, stale/duplicate/conflicting publications, a reused pid, an unknown probe, a renewed long turn, an expired turn, an invalid timestamp, stopped, undefined) are deterministic tests. Keep them as switch witnesses (A); drop their recording into windows (B). PROVEN (list) |
| "Promotion on every path" slice | C | The slice instruments promotion at the word paths for C8 (s2c-owners.md slice 5, first bullet): B. The product need is that the pure view applies row 5 at every reader with `dispatch_state` available: A, part of S4. PROVEN |
| B4 (screen, background-shell, starting inputs reach aify-env) | A | Needed for aify-env's word to be right (finish-0.9 plan line 65). PROVEN (plan); its implementation status UNCLEAR |
| B2 (system stops) | A, done | Landed and deployed 0cc4e58c. PROVEN |
| B3 (mode follows definition) | A, done | Deployed. PROVEN |
| S4 witnesses (P7) | A | test_p7_witness_delivery_hold.py (6), run_correlated (3), steer_bypass (8). "Green on today's code first" (status-switch.md:59-60). PROVEN |
| S4 pure commsView serving every reader | A | status-switch.md:61-62. PROVEN |
| S4 delivery hold rebased on `holdsDelivery` | A | status-switch.md:62. PROVEN |
| S4 `unknown` word (Q1) | A | status-switch.md:12-13. PROVEN |
| S4 reverse shadow during the legacy interval | B | "The old engine keeps computing in a reverse shadow: the S2 recorder compares old against new" (status-switch.md:64-65). PROVEN |
| Legacy interval with rollback | A | The rollback revert plus the rollback test (status-switch.md:67-72). The safety net when C8 is dropped. PROVEN |
| Launchers report only to aify-env | A, S5 step 1 (0.9.9) | status-switch.md:77. PROVEN |
| S5 deletions | A (cleanup) | Engine, detectors, tables, shadow recorder, S2c-E (status-switch.md:74-84). Dropping C8 makes all the B rows deletable in S5 too. PROVEN |
| agent-state-shadow doctor row | B | It reports partial comparisons only. PROVEN (doctor output 10-20) |
| C1 parent (fold provenance including the B6c ceiling) | B | Qualification only. PROVEN (fold-provenance plan title) |

## Minimum sandbox qualification if C8 is dropped

The obligations C8 protected (P0:376-387), and what can witness each.

| obligation | deterministic test today | sandbox (env-against-comms) |
|---|---|---|
| status word (rows 1-6, Q1 unknown, Q3 offline) | partial | YES with a scripted agent: define, start, idle, busy, stop, and an undefined agent |
| busy / holdsDelivery (strict, P-1) | P7 delivery_hold | YES: hold while busy, release at turn end |
| send-time queue | P7 delivery_hold | YES: a send while busy queues, then delivers after |
| claim (steer, non-steer) | P7 steer_bypass | YES |
| worker readiness | partial | YES for a spawned managed worker |
| live counts | none dedicated | YES (a read of the count) |
| reminder skip | P7 run_correlated | PARTIAL: needs a reply-owed run and the sweep clock |
| stranded-reply guard | P7 run_correlated | PARTIAL, same |
| running-run promotion | none at the word paths | YES: a run running on an idle record shows `working` |
| stale-turn ceiling / strictExpiry | unit only | NO in real time (1800 s). Unit or clock-injected only |
| restart survival (the persistence gap above) | none | YES: restart comms mid-turn, then read busy and claim. This is the test that would catch the correction above |

The sandbox's limits, today:
- PROVEN: its agent is defined but never started; the stub `claude-aify` exits 1 (sandbox README, d99421e8 report).
- A spawned agent needs either a scripted stub runtime that drives turn-start and turn-end through aify-env, or real
  runtime credentials in the container. ASSUMED: the stub is feasible. Real claude, codex and hermes need logins or
  API keys, and none of them is in the sandbox.
- Not reproducible in a sandbox: Hermes gateways, resident claude panes, the dev-channels prompt, real provider
  stalls. Those surface after the switch; the rollback is the net.
