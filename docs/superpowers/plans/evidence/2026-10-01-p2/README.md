# P2 evidence: plugins follow the registry (P0 C8)

What was run against aify-env `next/env-owned-agents` at **c1a4596**, which implements P0 C8 with the
clarifications under "Settled while building P2" in the P0 document. The tree tested is the tree
committed: nothing changed between the runs below and the commit.

| file | what it is |
|---|---|
| `mutations.json` | 37 mutations, each one rule of C8 broken on purpose |
| `mutate.py` | P1's driver plus the P2 test set, and a hang reported as `HUNG`, never counted as killed |
| `mutations-result.txt` | the run: 36/37 killed, each with the tests that killed it |
| `aify-env-full-suite-verdicts.txt` | `npm test` in aify-env: every verdict line and the totals (2262 tests, 2258 pass, 4 skipped, 0 fail) |
| `other-suites.txt` | the aify-comms and aify-wrapper suites with `AIFY_ENV_REPO` pointed at the branch |

## The survivor

"The claim loop keeps claiming while held" survives, and it is equivalent in behaviour: every claim pass
reads the phase again after its setup and returns before claiming, so a loop that keeps going while
held claims nothing. The only difference is wasted setup calls, which the control loop also makes while
held, so no test can tell them apart. The loop condition is kept because it stops those calls.

## What the tests found

- **A pass could start after the detach had decided.** A pass's setup (`advertisement()`,
  `cwdRoots()`) ran before `controlPass` was recorded, so a detach landing in it waited for nothing.
  The pass is now tracked whole and re-reads the phase. Witness: "A PASS WHOSE SETUP OUTLASTS THE
  DETACH".
- **A reverted registry change left the plugin held for ever.** Nothing asked it to detach again, and
  nothing un-held it, so it refused starts until a daemon restart. `resume()` was added.
- **The doctor read a held plugin as "claiming work".** Its claimer is still accepted, and it starts
  nothing. The `claiming` row now fails with "registry change pending".
- **The 0.8 branch still carried the withdrawn line-feed translation.** It forked after those commits.
  Main's revert is merged at 998b2e2.
- **P1's evidence failed this repo's whitespace gate**: its `mutations.json` lacked a final newline,
  and the RED transcript had 4 bytes of trailing spaces. Both are fixed, and `git diff -w` on the
  transcript is empty.

A mutant can kill by hanging. The first run had no hang budget: "detach sends the offline beat" waited
for ever on a held heartbeat, and the 30-minute background limit stopped the run with that file still
mutated. It was restored from the driver's own backup and checked byte for byte. Every witness now has
a 10 s timeout, and the driver reports a subprocess timeout as `HUNG`.

## What this does not check

- **The daemon's follow wiring** (`followServices` with its `following ??=` single flight and its
  `logLine` reporting) is not executed by any test. Only its guard statement is, taken out of
  `bin/aify-env.mjs` and run. That this statement runs on every advertiser beat is read from the
  source, not tested.
- **Nothing here ran against a real service or a real registry edit.** Every result above is PASSES
  IN TESTS: the real aify-comms plugin with a fake transport and fake processes.
- **`aify-comms`'s Python suite** has two failures:
  - the version gate, red until HEAD is tagged (VERSION is 0.7.7 on a tree past `v0.7.7`);
  - the whitespace gate, which caught the P1 files above. It was rerun on the fixed tree, and the
    result is appended to `other-suites.txt`.

To rerun: `python mutate.py <aify-env checkout> mutations.json`.
