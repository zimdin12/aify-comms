# The status switch to aify-env: 0.9.8 (2026-10-18)

The 0.9 plan's P6-P8 (`2026-10-02-aify-env-owns-agent-state.md`, `2026-10-14-finish-0.9.md`). Steven ruled the
three policy questions on 2026-10-18 (DECISIONS.md c8756959). Those rulings are below. This page orders the work and
names the first slice.

## Where it stands, measured 2026-10-18 16:20Z

- B1 is live. aify-env a66d011 holds the residents role, and every relaunched resident reports to it.
- The live shadow comparison (`GET /agent-state/shadow-report`) has made 41,687 partial comparisons of the status word:
  - 30,129 agree and 11,558 disagree (27.7%);
  - another 35,618 are unavailable: 31,733 `unknown-knowledge`, 2,173 `unknown-state` and 1,712 `missing-row`.
- The recorder keeps only the LAST disagreement per source, so nobody can say what the 11,558 are. The two samples:
  - `stopped` vs `available` on a disabled managed agent. That is a stop that only comms knows (B2).
  - `online` vs `shell` on a resident running background work (B4's background-shell input).
- The qualification gaps that the report names itself: `turn.ageMs`, runtime provenance, roster gates, running-run
  promotion, and the other nine C8 owners (B6).

## Rulings that set the target

- **Q1:** an agent no aify-env reports reads grey `unknown`, not `offline`.
- **Q3:** a resident with no wake handle stays `offline` (DECISIONS 2026-06-18 kept). This is a comms fact layered
  over aify-env's word, so the view needs the wake-handle input.
- **Q4:** push and poll serve one view.
- **Q2 (10-14):** events from launchers older than 0.9 are refused as `unbound`.
- **P-1:** verified open turns have no wall-clock cap.

## Order

1. **S1, the instrument (comms, small).** The recorder counts disagreements by (source, mode, comms word, env word).
   It keeps one sample's inputs per key, and the shadow report returns the table. It is pure and bounded: one key
   per distinct pair, and the vocabularies are fixed. Witness: a fed sequence produces the exact table; removing
   the count makes the witness red.
2. **S2, classify (P6).** After S1 has run against the live fleet for a few hours, every key gets one of four
   labels, recorded in this file:
   - a B-gap (which of B2 to B6);
   - a bug in aify-env;
   - a ruled policy (Q1, Q3, Q2 or P-1);
   - unresolved.

   The table is ordered by volume.
3. **S3, B2 to B6 in aify-env, one reviewed slice each, largest volume first.**
   - B2: comms-only stops move into aify-env's journal (D9 `stoppedByOperator`).
   - B3: the two agents whose mode disagrees are corrected.
   - B4: the screen, background-shell and starting inputs.
   - B5: a publication on every turn event, and the mirror reloaded after a service restart.
   - B6: `turn.ageMs`, build versions, and the busy, queue and claim decisions instrumented.

   Each slice's success is its key's count falling to zero in the live table.
4. **Gate:** zero unresolved, and zero B-gap keys, over a full day of the live fleet.
5. **S4, the switch (P7).** It runs in this order:
   1. The witnesses (accepted 289373cc), green on today's code.
   2. One pure comms view, taking aify-env's word plus comms' own facts (run promotion, operator stop of an
      undefined agent, Q3's no-wake `offline`). It serves push and poll alike.
   3. Delivery hold reads aify-env's busy.
   4. `unknown` added.
   5. Launchers post only to aify-env.
6. **S5, the deletions (P8)**, after a legacy interval of one release, during which the old engine still computes
   and the shadow still compares. Rollback is reverting S4's view commit, which leaves the engine intact. Then the
   engine, detectors, turn tables, shadow recorder and latent spawners are deleted, each after proof that no live
   route reaches it (the C2 discriminator).

The 0.9.8 tag follows S4, with all five suites, both doctors green, and the operator's relaunch.

## What would prove this wrong

- A key S2 cannot label with the inputs aify-env and comms have between them: the switch waits, and the C2 ledger
  gains a row.
- A delivery decision that changes across S4 without a ruling: a witness goes red.
- A slice that does not move its key's count: the classification was wrong, so reclassify before building more.
