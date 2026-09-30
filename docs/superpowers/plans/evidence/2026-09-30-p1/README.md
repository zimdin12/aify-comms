# P1 evidence: the agent-definition store

What was run against aify-env `next/env-owned-agents` at **fd2f8bd** (tree `354000e0`), which implements
P0 C1-C3 as amended through aify-comms `269b7ab1`. The tree tested is the tree committed: nothing changed
between the runs below and the commit.

| file | what it is |
|---|---|
| `mutations.json` | 54 mutations, each one rule of C1-C3 or the store broken on purpose, with the tests that must catch it |
| `mutate.py` | applies one mutation, runs its tests, restores the file's bytes from a copy (never `git checkout`) |
| `mutations-result.txt` | the run: 54/54 killed |
| `aify-env-full-suite-verdicts.txt` | `npm test` in aify-env: every test's verdict line and the totals (2248 tests, 2244 pass, 4 skipped, 0 fail) |
| `other-suites.txt` | the aify-comms and aify-wrapper suites with `AIFY_ENV_REPO` pointed at the branch |

Three survivors along the way, each resolved rather than listed:

- "store-owned fields required": the mutation added a name to a set that only drives the unknown-field
  check, so it changed nothing. It was replaced by one that does require the field, which is killed.
- "store writes candidate-valid only": `set` builds its identity itself, so no input reaches that guard.
  The property is now tested directly: every file the store writes, adoption included, reads back as a
  normalized definition carrying the ledger's identity.
- "not-committed gives back the incarnation": no test settled a NEW id as not-committed. One does now.

Two defects the tests found in the work in progress, both fixed before the commit: an id such as
`constructor` or `toString` read as defined through `ledger.ids[id]` (Object.prototype's members), and
`set` reported a missing harness as "the undefined launcher is not installed".

To rerun: `python mutate.py <aify-env checkout> mutations.json`.

## After the review of 3aa5df7 (R1-R8 and the canonical-order gap)

The repair is aify-env **26878fb** (tree `647be4a8`); the contract change it carries is C3's ordering
rule in this commit. The files above were rerun on that tree; the first round's results are in git
history.

| file | what it is |
|---|---|
| `review-regressions-RED-at-3aa5df7.txt` | `tests/agent-definition-review-regressions.test.js` run against 3aa5df7, before the repair: 7/7 fail. R2-R5 fail on their findings' own assertions. R1, R6 and R7 fail because their injection seam or boundary does not exist at 3aa5df7, so their reason-bound REDs are the mutants below |
| `mutations-result.txt` | 65 mutations, 65 killed, now with the tests that killed each one. Every R1-R8 mutant dies on its own regression; the ordering mutant on the golden vector |
| `aify-env-full-suite-verdicts-after-review.txt` | npm test at 26878fb: 2255 tests, 2251 pass, 4 skipped, 0 fail |

## After the review of 26878fb (the R7 and R8 residuals)

The repair is aify-env **70f9ace** (tree `9b982590`). `mutations-result.txt` and the suite file were
rerun on it: 67/67 killed, and `aify-env-full-suite-verdicts-after-review2.txt` holds 2258 tests,
2254 pass, 4 skipped, 0 fail. `review2-RED-at-26878fb.txt` runs the new regressions against 26878fb:
the adoption-listing case reports complete, the gate finds `definitionsDir` exported, and the wildcard
relay relays it. A second R7 guard (no snapshot complete while an entry reads `not-adopted`) was
written and then removed: nothing but a conflict produces that reading, and a conflict is already
incomplete, so no test could fail on it.

The driver now decodes test output as UTF-8 and records the failing test names; the first run of
this round crashed decoding it as code page 1257, and its `finally` restored every file (checked).
