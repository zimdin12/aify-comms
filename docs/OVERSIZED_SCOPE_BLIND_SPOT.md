# The 1000-line goal has never covered two of its largest files

**Status:** finding, needs one ruling. Measured 2026-08-14. **No change made** — widening the gates means
either failing the suite or adding allowlist entries, and adding an entry is a reviewer decision.

## What was missed

The goal is "every non-test source file over 1000 lines". The twelve-file list it came with, and every
sweep I have run since, covered `.py`, `.js` and `.mjs`. A sweep across **all** tracked file types:

| file | lines | in the 12-file goal? | visible to either gate? |
|---|---|---|---|
| `install.sh` | **4,371** | no | **no** |
| `service/new_dashboard/app.js` | 3,612 | yes | yes |
| `mcp/stdio/server.js` | 2,040 | yes | yes |
| `service/new_dashboard/styles.css` | 1,844 | no | **no** |

`install.sh` is the largest source file in the repo — larger than `app.js` — and it is a first-class
product artifact: CLAUDE.md's repo-layout table lists it as "Client installer", and re-running it is a
required step in the release recipe.

## Why neither gate can see them

Not an oversight in the allowlist; a scope limit in the gates themselves.

* `service/tests/test_no_new_oversized_source_file.py` — `SERVICE.rglob("*.py")`
* `mcp/stdio/tests/no-new-oversized-source-file.test.js` — `/\.m?js$/`

This is the same silent-shrink shape found twice already in this series (`moved-names-resolve` naming two
carriers, `test_leaves_do_not_import_the_carrier` naming one directory): a gate whose stated purpose is
general while its code is specific. **A file a gate cannot see cannot be enforced against, so `install.sh`
could double in size without any test noticing.**

## `install.sh` is not shaped like the other two, and that is the crux

| | lines | share |
|---|---|---|
| heredoc payloads — the wrappers and MCP configs it EMITS | 2,184 | 50% |
| comments | 1,182 | 27% |
| blank | 184 | 4% |
| shell logic (57 functions and their bodies) | 821 | 19% |

**Half the file is content it writes to disk, not code it executes.** A 4,371-line installer that emits
2,184 lines of wrapper scripts is not the defect that a 4,371-line module of logic is — the thing the
1000-line rule exists to prevent is a unit nobody can hold in their head, and the emitted payloads are
data with no control flow. The actual logic is 821 lines across 57 functions, which would pass the rule
comfortably.

`styles.css` is the same argument in stronger form: 1,844 lines of declarations, no logic at all.

## The ruling

**Is `install.sh` in scope for the 1000-line goal?**

- **If NO** — the gates should still be WIDENED to see `.sh` and `.css`, with both files added to
  `oversized-allowlist.json` carrying this measurement. An explicit exemption is checkable and can rot
  honestly; invisibility cannot. This is the cheaper answer and I would recommend it, on the grounds that
  half of `install.sh` is emitted data.
- **If YES** — the work is real but different in kind from the eleven files already done: extracting the
  heredoc payloads into template files under `config/` or `templates/` would take it to roughly 2,200,
  and the 57 functions would then need grouping. It is not a relocation series like the others, because
  shell has no import mechanism — sourcing a second file changes the installer's single-file property,
  which is itself deliberate (it is fetched and run standalone).

Either way the gates should stop being blind. What I have not done is pick, because adding an allowlist
entry is exactly the decision the gate exists to force upward.
