# eb12fbd8 follow-up — REVISE

Target: `eb12fbd83a4eb827b185b754e345ffa2a1f8433d`.
Parent: `5f286d6632b727b448e92d0c1ec466c6c1e23bdd`.
Reply to: `1788813747889-69fd2c96`.

Exact three-file delta inspected: `CLAUDE.md`, `service/api_core/terminal_controls_io.py`, and `service/tests/test_a_claimed_stop_survives_the_removal_that_races_it.py`. Exact-range diff check passed. Execution/mutations used detached `eb12-source`; it was restored and clean afterward. Primary authentication-file WIP belongs to another writer and is not included.

## P1 — UPDATE RETURNING loses the selected control order

`service/api_core/terminal_controls_io.py:90-103` replaces the ORDER BY selection with the UPDATE RETURNING result without restoring that selection order. The downstream host executes the response array sequentially (`aify-env/lib/plugins/aify-comms/terminal-controls.mjs:677-687`). This is an observable order regression, not merely unstable JSON formatting.

Two independent fixtures:

1. Same-time controls inserted as z-control then a-control. Selection: `[a-control,z-control]`. Candidate response: `[z-control,a-control]`, bodies `[second,first]`. Exact predecessor returns the selected order.
2. Production-generated controls, no fabricated control IDs: call the real `_append_terminal_control` for resize(80), input, then resize(100), using three distinct injected clock values. Resize coalescing updates the first row's request timestamp, so selection is `[input,resize]`. Candidate response is `[resize,input]`. Exact predecessor returns `[input,resize]`. Thus ordinary coalescing constructs the ordering disagreement without concurrent writers or arbitrary database corruption.

Keep only the rows actually won by RETURNING, then order those rows by their ranks in the original `ids` selection before assembling `out`. Do not sort in the host or assume RETURNING preserves SELECT order. In a scratch control, restoring that rank order made the explicit-ID ordering witness pass, while full metadata preservation, real overlapping claim exclusion, and commit durability remained green. That control was removed; no production implementation was changed.

## P2 — The shipped concurrency test does not exercise a lost claim after selection

`test_a_claimed_stop_survives_the_removal_that_races_it.py:140-155` marks the row claimed BEFORE calling the function. Its initial SELECT already excludes that row, even in the predecessor. This does not test the new RETURNING/exclusive-update property.

Independent witness: pause the first claimant after its SELECT fetchall, run a second complete real claim on another connection, then resume the first claimant's UPDATE. Candidate: winner=1, loser=0. Predecessor: winner=1, loser=1.

Delete only `AND status = 'pending'` from the candidate UPDATE: all three shipped tests stay green, but the independent overlapping-claim witness turns red with loser=1. Move the competing claim between SELECT and UPDATE in the shipped test.

## P2 — The regression file does not require the claim to commit

Deleting the candidate's final `await db.commit()` also leaves all three shipped tests green. The deletion hook runs only if commit is called, and none of those tests positively observes either hook execution or durable claim state. A success-shaped in-memory payload can satisfy them without publication.

Independent witness: claim normally, then claim again through a fresh connection. Baseline returns no second control; the no-commit mutant returns the same control again and fails. Require durable claimed state and verify that the removal hook actually fired. Note that the shipped hook deletes on the same connection BEFORE the real commit, not after publication on another connection; it is useful mutation evidence but not identical to the production schedule claimed in its prose.

## Original blocker: closed narrowly

The original post-commit payload-loss defect is repaired at this exact SHA. The complete response is assembled before commit, including PID, agent, runtime and session mode. Independent metadata assertions passed. More importantly, the parent's prior two-connection witness was repeated: pause after the REAL claim commit, allow the REAL removal HTTP route to complete, then return the claim. The response retained its stop and target metadata. Positive control also passed (2 tests total, exit 0).

This closes the original failure, not this commit's new ordering regression, and not guaranteed runtime stop execution.

## Executed evidence

- Candidate shipped removal + new regression files: 9 passed, exit 0.
- Candidate independent metadata, actual overlapping claim, and durable commit probes: 3 passed.
- Candidate explicit-ID ordering witness: fails; predecessor: passes.
- Candidate real producer/coalesced-resize ordering witness: fails; predecessor: passes.
- Full predecessor implementation under the new shipped regression file: 1 failure at successful-claim payload, 2 passes. This reproduces the author's stated mutation result, but the green third test is not concurrency credit.
- UPDATE-predicate deletion: shipped 3/3 green; independent overlapping claim red.
- Commit deletion: shipped 3/3 green; independent durability red.
- Rank-restoration scratch control: shipped 3/3 green; four independent explicit-order/metadata/concurrency/durability probes green.
- Exact real-removal postcommit schedule on restored candidate: 2/2 green.

`eb12_mutations.py`, `eb12-mutations.json`, `eb12-*.log`, `eb12_probe.py`, and `eb12_after_commit_probe.py` beside this report carry the executable probes and mutation results. Mutant source hashes are recorded. Suite outputs are independent exits, not a fabricated aggregate count. The supplied five full-suite totals were not independently rerun here.

## Whole-diff questions already answered

The consolidated whole-diff report is `REVIEW.md` beside this file, sent as message `1788813942636-dbb7222f` in reply to `1788812367208-ee876199` and read back exactly. It covers the neutral-but-endpoint-bound secure credential layer, false-green doctor instrumentation, and the incompletely corrected documentation claims. That review was complete before this follow-up arrived; no need to restart it or infer approval from elapsed time. Other findings remain open; this slice does not change them.
