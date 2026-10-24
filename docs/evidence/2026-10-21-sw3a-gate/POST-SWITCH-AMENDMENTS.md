# Post-switch amendment A10: stale publisher yielding

This amendment records task #5's implementation of the operator's 2026-10-22 dead-key ruling, recorded in
`DECISIONS.md` at `985ec638`. It applies to future qualification of the sandbox gate maintained on `098/sw3d`.
It does not change frozen SW6 records, results, source manifests or the prior qualification verdict.

## Superseded case

`CASES` in `scripts/sandbox/gate/cases.mjs` on `098/sw3d` declares `stale-second-holder`, amendment A4:

- Expected: "eligibility unknown: withheld, steer included".
- RED: "stale holders are ignored (known false: delivered)".

Task #5 deliberately supersedes that expectation. A publisher yields only after three of its own announced
`reportEveryMs` intervals of receipt silence, and only to a fresh, readable peer on the same machine that lists
the same agent. No key is deleted or retired. A renewed report can participate again. No announced cadence means
no inferred expiry, including a legacy saved key with no historical contact pair. A restored pair proves only
contact age, never fresh data.

## Required before any future qualification

1. Amend the `stale-second-holder` case and its judge to require the fresh idle peer's state and known-idle
   eligibility after the secondary key's derived silence limit, and delivery on the next claim pass. Withholding
   solely because that provably stale secondary key remains is then the failure. Preserve controls for no fresh
   same-agent coverage, missing or invalid cadence, the pre-expiry boundary and genuinely live competing holders.
2. Amend `fresh-busy-beside-unknown`, amendment A5, without changing its unknown-before-steer refusal obligation.
   Its current `busy-beside-unknown` scenario uses the same `staleSecondHolder` helper as A4, so that peer now yields
   instead of proving unknown eligibility. Use a genuinely unknown non-yielding peer, such as a report that announces
   no interval, and assert that uncertainty before judging the steer refusal. The expected refusal and its RED stay.
3. Update the affected fixture, judge, self-check and mutation controls together, then qualify that new gate against
   the new candidate. The current gate cannot qualify task #5 unchanged. Keep old source, raw runs and judgments as
   historical evidence rather than rewriting their outcomes.

The service regressions for the implemented rule are in
`service/tests/test_a_silent_publisher_yields_to_a_live_one.py`. The original unknown-refusal assertions remain in
`service/tests/test_the_operational_view_reads_env_busy_without_timing_turns.py`, using a not-reporting peer.
No sandbox gate code or frozen evidence is changed by this amendment file.
