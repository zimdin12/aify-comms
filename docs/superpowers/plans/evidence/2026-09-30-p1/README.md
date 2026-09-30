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
