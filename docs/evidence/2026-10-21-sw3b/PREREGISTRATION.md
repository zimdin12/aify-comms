# SW3b preregistration: three qualification attempts (2026-10-21)

Written and committed before the first attempt starts. Plan `docs/superpowers/plans/s4-hard-switch/2-sandbox-gate.md`,
"Pass rule".

## The pinned pair

| side | commit | standing |
|---|---|---|
| comms | `78270d30e2f9a7b92dd93d1d3fcb6bf5d42ac4d0` (branch 098/sw5-candidate) | SW5 candidate, APPROVE_SOURCE, not landed |
| aify-env | `f704bcdf5526d3fc4e325296b3cc49ffb5231d5c` (aify-env main) | merged; carries reportEveryMs and outside-roots → misconfigured |

## The apparatus

- The gate at `e7750b9f` (APPROVE_SOURCE for SW3a), unchanged; this branch adds only this folder.
- Each attempt logs the sha256 of every gate source as its first lines; those must match `e7750b9f`'s blobs.
- aify-env is read through `git archive` from `~/projects/aify-env` (`AIFY_ENV_REPO`); its checkout is not touched.

## The attempts

Three, `q1`, `q2`, `q3`, run one after another, never concurrently. Each:

    node scripts/sandbox/gate/gate.mjs --comms=78270d30e2f9a7b92dd93d1d3fcb6bf5d42ac4d0 \
      --env=f704bcdf5526d3fc4e325296b3cc49ffb5231d5c --attempt=<q1|q2|q3> --out=docs/evidence/2026-10-21-sw3b/<q1|q2|q3>

- No `--scenarios` filter: every frozen case runs.
- No `SANDBOX_GATE_IMAGES`: both images are built from the two commits in every attempt.
- stdout and stderr of each attempt are kept as `<attempt>/gate.txt`, with its exit status.

## The rule, fixed now

- **Green** only if all three attempts exit 0, which needs every case judged PASS and the cleanup readback complete.
- **Red** if any attempt exits nonzero, whatever the cause: a case FAIL, an ERROR verdict, an incomplete cleanup, or
  an apparatus failure. One red attempt fails the gate for this pair.
- Every attempt is kept, failures included. A failed attempt is not rerun or replaced. Another set of three is a new
  preregistration, on a new pair if either commit changes.
- The `daemon-lost` observation (amendment A3) is recorded and not judged.

## What would prove the switch wrong

Any of these, in any attempt:

- a case FAIL;
- an `ERROR` verdict;
- a cleanup line other than `cleanup: complete`;
- a `REUSING images` line;
- a source sha256 line that differs from `e7750b9f`.

The first two would mean the candidate or the pair does not behave as the frozen cases require. The last three would
void the attempt as qualification evidence.
