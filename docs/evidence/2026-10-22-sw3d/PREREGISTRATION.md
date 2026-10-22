# SW3d preregistration: three qualification attempts on a new pair (2026-10-22)

Written and committed before the first attempt starts. Plan `docs/superpowers/plans/s4-hard-switch/2-sandbox-gate.md`,
"Pass rule", with the gate owner's amendments A1-A9 (`docs/evidence/2026-10-21-sw3a-gate/AMENDMENTS.md`). SW3b
(`docs/evidence/2026-10-21-sw3b`, RED) stands. This does not replace it: it is a new set, on a new pair and a new
apparatus.

## The pinned pair

| side | commit | standing |
|---|---|---|
| comms | `78270d30e2f9a7b92dd93d1d3fcb6bf5d42ac4d0` (branch 098/sw5-candidate) | SW5 candidate, APPROVE_SOURCE, not landed; the same commit SW3b ran |
| aify-env | `ed9ebe5acd5597c1f45474f492c089e1c6109728` (aify-env main) | APPROVE_SOURCE_ENV_ED9EBE5; what the operator's switch-time restart onto main would run |

## The apparatus

- The gate at `b70699c437105788302652bc34d2482b1f10693f`: `7f81e1d1` (approved), `87f83a97` and `16f57682` (probe
  captures, approved), and A9 (`APPROVE_SOURCE_A9_B70699C4`). This branch adds only this folder.
- Each attempt logs the sha256 of every gate source as its first lines. Those must match `b70699c4`'s blobs.
- aify-env is read through `git archive` from `~/projects/aify-env` (`AIFY_ENV_REPO`); its checkout is not touched.

## The attempts

Three, `q1`, `q2`, `q3`, run one after another, never concurrently, with no other Docker or suite run beside them.
Each:

    node scripts/sandbox/gate/gate.mjs --comms=78270d30e2f9a7b92dd93d1d3fcb6bf5d42ac4d0 \
      --env=ed9ebe5acd5597c1f45474f492c089e1c6109728 --attempt=<q> --out=docs/evidence/2026-10-22-sw3d/<q>

- No `--scenarios` filter: every frozen case runs. The on-demand observations stay out (they are not on this branch).
- No `SANDBOX_GATE_IMAGES`: both images are built from the two commits in every attempt.
- stdout and stderr of each attempt go to `docs/evidence/2026-10-22-sw3d/runs/<q>.gate.txt`. Each exit status and its
  UTC time are appended to `runs/exits.txt`. The folder is named `runs`, not `logs`, because the repo ignores `logs/`.

## The rule, fixed now

- **Green** only if all three attempts exit 0, which needs every case judged PASS and the cleanup readback complete.
- **Red** if any attempt exits nonzero, whatever the cause. One red attempt fails the set for this pair.
- Every attempt is kept, failures included. A failed attempt is not rerun or replaced.
- The `daemon-lost` observation (A3) is recorded and not judged.

## What would prove the switch wrong

Any of these, in any attempt:

- a case FAIL or an `ERROR` verdict;
- a cleanup line other than `cleanup: complete`;
- a `REUSING images` line;
- a source sha256 line that differs from `b70699c4`.

The first means the candidate or the pair does not behave as the frozen cases require. The last three void the attempt
as qualification evidence.
