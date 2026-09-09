# v0.6.3 acceptance ledger

**What this is.** One row per OBLIGATION, in the shape review asked for: required behaviour, the
changed producer/consumer paths, the exact test and assertion, a candidate-bound result, and a
disposition. Written by hand rather than scanned, because the thing being recorded is what each
change must be true for — which no scanner knows.

**What it is not.** `scripts/acceptance-ledger.py` is a discovery inventory: it reports that 95
surviving product files changed in `aed8b590..HEAD` are each NAMED by at least one test, with 0
bare and 35 deleted. Review reproduced those counts and accepted them as an inventory while
correctly declining them as this ledger — a name match is not an obligation. The inventory finds
candidates; this says what they owe.

**Two scope corrections review made, both verified here before being adopted:**

- **Deletions are not evidence-free.** Removing ownership, teardown, control-loop or
  terminal-manager responsibilities needs an explicit retirement disposition. They stay in the
  population; rows R-1..R-3 below are theirs.
- **The lockfile is a shipping input.** The Dockerfile's `COPY mcp/stdio/package.json mcp/stdio/package-lock.json` is followed by `RUN cd mcp/stdio && npm ci`, so excluding it as "not behaviour" was wrong. Rows P-1a and P-1b are its
  obligation. (Named by content rather than by line, because a line number in a doc rots -- the
  rule `test_docs_name_symbols_not_line_numbers.py` enforces on the files it watches, and this
  one is not among them.)

**Status.** PRODUCT NOT YET CERTIFIED — review's original-range coverage is open and this ledger
does not close it. No product-code defect is established. The deploy and the tag are the operator's.

---

## What every PASSES IN TESTS row below is bound to

A result with no candidate attached is not a receipt. Every such row was measured on:

- **Candidate:** `0bdb788c` plus the working tree committed as this ledger's own change. A row
  measured on anything else says so in its own cell.
- **The five suites, ONE run each, all exit status 0**, quoted here so every figure in this
  document reconciles against the same run:

| suite | command | result | exit |
|---|---|---|---|
| python | `python -m pytest service/tests -q -n 8 --dist loadfile` | 5,618 passed, 10,923 subtests | 0 |
| bridge | `cd mcp/stdio && node tests/run-all.mjs` | all 363 suites passed; 1 test skipped in `runtime-launch-helpers.test.js` | 0 |
| dashboard | `cd service/new_dashboard && node --test *.test.mjs` | 1,709 passed, 0 skipped | 0 |
| aify-wrapper | `cd ~/projects/aify-wrapper && node --test tests/*.test.js` | 219 passed, 0 skipped | 0 |
| aify-env | `cd ~/projects/aify-env && npm test` | 1,683 tests, 1,682 passed, 1 skipped | 0 |

`TIME_WAIT` was **295** before the python run and **12,183** after it, against this host's 16,384-port
ephemeral range — the socket pressure CLAUDE.md documents, sampled DURING the run rather than
between runs. Nothing failed, so it attributes nothing here; it is recorded because a later red must
be read against a measured before-and-after rather than a remembered one.

**PASSES IN TESTS is not PROVEN, and neither is a deployed-object failure a receipt for a candidate
run.** B-1's row carries both and says which is which.

## Behaviour — the console transport, which is this version's product change

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| B-1 | The broadcast sequence counts FRAMES, so a flush coalescing N posts advances it by ONE | `service/terminal_write_queue.py` | `test_the_broadcast_seq_counts_frames_not_posts.py` — `test_a_coalesced_flush_emits_one_frame_and_advances_by_more_than_one` asserts `broadcasts[0]["seq"] == 1` for three posts | PASSES IN TESTS on the candidate, in the python run quoted above. SEPARATELY: the CONTAINER's own copy of this module FAILS the same test (`3 != 1`, then `4 != 3`) — that is adverse evidence about the DEPLOYED object and is not a second receipt for the candidate | Met by the candidate. Unshipped — the running build predates it |
| B-2 | A gap recovery HOLDS frames arriving during its fetch and does not start a second one | `service/new_dashboard/xterm-mount.mjs`, `console-cursor.mjs`, `realtime-socket.mjs` | `the-console-does-not-stall-while-it-resyncs.test.mjs`; `a-mount-keeps-the-frames-that-arrive-while-it-fetches.test.mjs` (11 cases incl. a recovery double taking `resyncing`) | PASSES IN TESTS on the candidate, in the dashboard run quoted above | Met by the candidate |
| B-3 | `view=console` answers the rendered snapshot, and drops the stored tail ONLY when a snapshot exists to replace it | `service/api_core/terminal_snapshot_view.py`, `service/routers/terminals.py` | `test_the_console_projection_carries_what_it_paints.py`; the mutant that drops the tail unconditionally is in its battery and is caught | PASSES IN TESTS on the candidate, in the python run quoted above | Met by the candidate |
| B-4 | An UNNUMBERED chunk clears the live screen's sequence, so the GET answers `null` and the browser replays rather than trusting a stale number | `service/terminal_snapshot.py`, `service/api_core/terminal_output.py` | `test_live_terminal_screen.py`; `test_status_reads_the_live_screen_not_the_stored_tail.py` | PASSES IN TESTS on the candidate, in the python run quoted above | Met by the candidate |

**Not claimed by any row above:** that these repair the operator's reported lag. The lag is
UNATTRIBUTED — 106 verified continuous minutes of live observation produced zero wire gaps, so the
mechanism is present in the deployed build and was not caught firing.

## Retirement — the environment-bridge tier, 35 deleted modules

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| R-1 | Managed worker OWNERSHIP, teardown and survivor reaping are gone from the bridge, and aify-env owns them | deleted: `managed-ownership.mjs`, `managed-teardown-ownership.js`, `managed-teardown-sweeps.mjs`, `single-agent-teardown.mjs`, `reap-managed-survivors.js` | `scripts/deleted-import-census.py` — searches every surviving `.js/.mjs/.cjs/.py` for an import/require/from specifier naming any deleted file | PROVEN for imports: 0 of 107 deleted files (35 product, 72 tests) is imported anywhere, with both controls in the same run. **BEHAVIOUR: UNREVIEWED IN THIS RANGE** | Retired to aify-env. Its behavioural half is open — see the note below and the open list |
| R-2 | Terminal MANAGEMENT — the manager, control loop, runtime and capability probes — is gone from the bridge | deleted: `terminal-manager.mjs`, `terminal-control-loop.mjs`, `terminal-control.js`, `terminal-runtime.js`, `terminal-capability.mjs`, `terminals-are-possible.mjs`, `terminal-attach-notice.js`, `terminal-exit-report.js`, `terminal-text.js` | same census; `aify-comms doctor`'s `bridge-terminal` row moved to `aify-env doctor` (`docs/AIFY_ENV_BOUNDARY.md`) | PROVEN for imports, same run. **BEHAVIOUR: UNREVIEWED IN THIS RANGE** | Retired to aify-env. Same open behavioural half |
| R-3 | ENVIRONMENT advertisement, identity and the control loop are gone from the bridge | deleted: `environment-advertisement.mjs`, `environment-identity.mjs`, `environment-control-loop.mjs`, `environment-cwd-roots.mjs`, `environment-runtimes.js`, `env-client.mjs`, `env-term-shim.mjs`, `delegated-stream.mjs`, `delegated-exit.mjs` | same census; `env-bridge` and `tier-version` doctor rows | PROVEN for imports, same run. **BEHAVIOUR: UNREVIEWED IN THIS RANGE** | Retired to aify-env. Same open behavioural half |

**The import half is proved by a census that answers the question the rows actually ask.** These
rows cited `no-missing-sibling-imports.test.js` and `moved-names-resolve.test.js` until review
pointed out that neither proves "zero remaining importers of any deleted module": the first asks
whether a resolved sibling exists, the second whether a moved NAME resolves, and both are green in a
tree that still imports a deleted module from somewhere they do not walk.
`scripts/deleted-import-census.py` asks it directly over the range's own deletion list, with a
POSITIVE control (`doctor-predicates.js`, 15 importers found by the same search) and a NEGATIVE
control (a name that was never a file, 0) in the same invocation. Driven by ADDING what it watches
for: a `require('./terminal-manager.mjs')` planted in a surviving module took it to
`NOT CLEAN: 1`, exit 1, naming the file and line; removing it returned exit 0.

**The behavioural half is UNREVIEWED IN THIS RANGE, and that is a narrower statement than "no
evidence exists".** aify-env's suite runs on every commit in this session and is green (1,683 above),
and specific tests there are pointed at these responsibilities — `owned-processes.test.js` ("the
writing instance stamps itself as the OWNER", "stopping removes only its own") and
`orphans-die-with-the-environment.test.js` ("a process outlives a KILLED environment, and the next
one reaps it") were READ rather than trusted to their titles. What does not exist is a MAPPING from
each retired obligation to an assertion that carries it: a passing external suite is a suite, not an
obligation-to-assertion mapping, and citing one as though it were is the shape review rejected in
the discovery inventory. R-1 belongs with R-2 and R-3 here and in the open list.

**A count of files mentioning a word is NOT evidence**, and a first pass at these rows did exactly
that — 112 test files matching "teardown", 51 matching "ownership". The search that produced those
counts was itself broken: `git grep -E "a\|b"` reads the escaped pipe as a LITERAL, so three topic
searches returned 0 while a fourth WITHOUT alternation returned 104 and looked like a working
control. A control has to exercise the same FORM as the thing it controls.

**WHAT IS ALSO THIN, separately: the cross-repo seam.** Five tests that drove a REAL aify-env through
the deleted modules were deleted with them, leaving one
(`the-credential-ref-we-write-is-one-aify-env-resolves.test.js`). So each side is exercised in its
own suite and the JOIN between them is proved once. That is a real limit and it belongs to the next
version.

## Packaging

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| P-1a | The MANIFEST, the LOCKFILE and npm's record of the install name the same wrapper commit | `mcp/stdio/package.json`, `mcp/stdio/package-lock.json` (the Dockerfile copies both, then runs `npm ci`) | `the-wrapper-pin-is-not-behind-a-template-change.test.js` — "THE GATE: this repo consumes exactly the pin it declares", via `consumedPinVerdict({packagePin, lockPin, installedPin})` | PROVEN on this host: all three read `5c62d91`, the third from `node_modules/.package-lock.json`. **The branch matters** — `consumedPinVerdict` returns `ok:true` with `installedPin` empty, so where npm's install record cannot be read the gate compares TWO values and not three, and the row is then only manifest-to-lock | Met, with the two-value branch named |
| P-1b | The wrapper BYTES in `node_modules` are the bytes of the commit the manifest pins | `mcp/stdio/node_modules/aify-wrapper` (what `install.sh` renders from) | `the-installed-wrapper-is-the-pinned-commit.test.js` — every file of the pinned tree that npm publishes, compared byte for byte, with the four `wrappers/*.sh.in` templates asserted to be among those compared | PROVEN where an upstream checkout exists: 19 files compared, 0 differing, all four templates present. **SKIPS BY NAME otherwise** — with no checkout, or a checkout not holding the pin, it reports what went unasked instead of passing | Met on this host. A clean clone gets a named skip, never a green |
| P-2 | The three repos declare ONE version and the release recipe touches every declaring file | `VERSION`, `mcp/stdio/version.js`, `package.json`, `package-lock.json`, `.claude-plugin/plugin.json` | `test_version_single_source.py`; `version-consistency.test.js` | PASSES IN TESTS at the CURRENT declared version, in the runs quoted above | **NOT MET FOR v0.6.3.** All three repos still declare `0.6.2`, and v0.6.2 was never tagged. The bump, stamp and rebuild are unperformed; the tag is the operator's |

**Why P-1 became two rows.** Review's finding was that one row was carrying two different claims with
one verdict, and the weaker one was doing the work: agreement between RECORDS is not provenance of an
OBJECT. CLAUDE.md records the failure that separates them as MEASURED — the sha was raised in both
package files, `npm install` reported success, and `node_modules/aify-wrapper` still held the previous
code, because npm trusts a tree matching the lock it was just handed. Every record agreed throughout.
The remedy written there is a hand grep of the installed file; P-1b is that grep, run by the suite.
It was driven three ways: one changed byte in an installed template turns it red naming the file; a
template npm did not ship turns it red naming the unchecked artifact rather than passing on a vacuous
comparison; and a real checkout that does not hold the pin makes it SKIP with the reason, never pass.

## Settings — B7's audit

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| S-1 | Every declared setting is READ by something outside its declaration | `service/api_core/settings.py` | `test_every_setting_has_a_reader.py` | PASSES IN TESTS on the candidate, in the python run quoted above | Met. Two deliberately inert keys carry their reason at the declaration |
| S-2 | The drawn surface and the declaration agree BY NAME, in both directions | `service/new_dashboard/settings-panel.mjs` | `test_the_settings_surface_agrees_with_the_declaration.py` — five states, five kills, including an unreadable schema | PASSES IN TESTS on the candidate, in the python run quoted above | Met |
| S-3 | A setting whose LABEL promises a unit is converted in that unit, at EVERY site that converts it | readers in `service/routers/shared.py`, `maintenance.py`, `contracts.py`, `api_core/reply_contract.py` | `test_a_setting_labelled_with_a_unit_is_read_in_that_unit.py` — AST-based, one judgement per conversion SITE; nine mutants killed | PASSES IN TESTS on the candidate. Chain identity REPAIRED — see below | Met for Python readers, with the scope and the hand-audit stated as separate claims below |
| S-4 | `manual_session_mode` does what its label promises | `service/new_dashboard/session-rail.mjs`, `settings-panel.mjs` | `test_every_dashboard_setting_has_a_reader.py` `KNOWN_INERT` | **ADVERSE — it is READ, SHOWN, and does NOTHING.** The panel calls it the operator's control over the session-mode chips; the rail renders them regardless | **OPEN OPERATOR DECISION** since 2026-09-08: restore the gating or delete the field. Both are visible to the operator |

**S-3 carries three claims and they must not be collapsed into one verdict.**

1. **What the GATE proves.** Nine mutants, each constructing a specific wrong-unit reading, each
   killed, each judged by the one obligation it violates rather than by the whole file: days read as
   minutes, days divided instead of multiplied, megabytes read as kilobytes, megabytes divided, a
   millisecond chain granted to bytes, a comment that cannot restore a removed factor, an hours
   factor applied to bytes, hours handed to SQLite as minutes, and two mutually exclusive branches
   each converting megabytes to kilobytes.
2. **What the HAND AUDIT claims, which the gate does not.** All fourteen unit- and sentinel-bearing
   settings were traced by hand on 2026-09-09 and every one was correct. That is a person's reading,
   PROVEN by inspection and unrepeatable by the suite; the gate exists so it does not have to be
   repeated, not because it proves it.
3. **The SCOPE, which is Python readers.** A dashboard module rendering "over the 500 MB limit" is
   display, converts a different value, and is not judged. An unscoped gate that silently skipped
   those files would look identical to this one.

**Chain identity was the ninth mutant, and it is repaired.** Review drove two mutually exclusive
branches, each doing `m * 1024` — kilobytes on either path, so neither is ever right. The gate
collected `[1024, 1024]` across the FILE, multiplied them to 1,048,576, matched what MB promises, and
passed: its own arithmetic manufactured a correct answer out of two wrong ones. A conversion is an
EXPRESSION and not a file, so each outermost multiplicative expression is now one SITE and each site
is judged alone. Driven both ways — the per-site gate kills that mutant, and the same mutant against
a gate with the split REMOVED goes green, reproducing review's false green. That is what establishes
the split as the thing doing the work. Fixing it does not widen the Python-only scope, which stays
as stated.

---

## What is open, stated as such

1. **Review's original-range product coverage.** Theirs, explicitly incomplete, and this ledger does
   not substitute for it.
2. **R-1, R-2 and R-3 behavioural evidence** — retired to aify-env, whose suite is green and whose
   relevant tests are named above, but with no obligation-to-assertion mapping established in this
   range. Imports are PROVEN; behaviour is UNREVIEWED.
3. **The cross-repo seam**, proved once, where it used to be proved six times.
4. **P-2** — v0.6.3 is not declared anywhere.
5. **S-4** — an operator decision.
6. **The deploy.** 149 commits behind; the running build carries the B-1 defect, demonstrated
   against its own module.
