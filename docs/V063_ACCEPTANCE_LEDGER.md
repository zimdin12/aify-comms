# v0.6.3 acceptance ledger

**What this is.** One row per OBLIGATION, in the shape review asked for: required behaviour, the
changed producer/consumer paths, the exact test and assertion, a candidate-bound result, and a
disposition. Written by hand rather than scanned, because the thing being recorded is what each
change must be true for — which no scanner knows.

**What it is not.** `scripts/acceptance-ledger.py` is a discovery inventory over the 130 product
files changed in `aed8b590..HEAD`: 35 deleted, 0 named by no test at all, and the 95 survivors split
**74 with a DECLARED STATIC IMPORT in a test, 15 code files only NAMED, and 6 outside the
tier's suffixes** (`VERSION`, `install.sh`, two manifests, `index.html`, `styles.css`).

**The IMPORT tier exists because review was right about the NAME tier.** A basename match is
satisfied by a mention in a comment, a fixture, or a stem collision. Python is answered by `ast`
over `service/tests/**/test_*.py`, JavaScript by V8 through
`vm.SourceTextModule().dependencySpecifiers`, which parses without executing -- 493 JS test files
read, 0 unparseable. Seven controls run in the same invocation, two on the NAME tier and five on
the IMPORT tier.

**WHAT THE TIER SAYS, at its real width.** A test DECLARES a static import of that exact file.
Not that anything ran: an `if False:` import is credited by the same helper, and so is one in a
test nothing executes. **STATIC imports only, with two verified instances of the limit rather than
a hypothesis**: `send-tools.mjs` is exercised by `send-tools.test.js` through `await import(...)`
and lands in the weaker tier, and `doctor.js` is deliberately never imported at all because
importing it RUNS the doctor. And the six outside the tier are a SUFFIX exclusion, not files
nothing can import -- Node imports a `package.json` with an import attribute.

**ITS FIRST VERSION GRANTED CREDIT ON A BASENAME.** Review redirected all twelve real specifiers
for `doctor-predicates.js` into a sibling directory, reparsed all 493 tests, and the row stayed
imported with zero canonical importers remaining. Paths are normalised and compared entire now, and
a control asserts that a colliding basename in the wrong directory is refused.
**Even the strong tier is not an obligation.** It says a test reached the file, not that it
exercises the change or any branch of it. The inventory finds candidates; this ledger says what they
owe.

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

- **Candidate: `0817a263`** -- the commit the tested tree BECAME, written after that commit exists
  rather than predicted before it. An earlier version of this line named "the tree at `05ad53df`
  carrying round sixteen's repairs"; `05ad53df` is immutable and holds neither X-1 nor the repaired
  census, so it named a tree nobody could check out. A candidate has to be a thing another person
  can obtain. A row measured on anything else says so in its own cell.
- **The five suites, ONE run each, all exit status 0**, quoted here so every figure in this
  document reconciles against the same run:

| suite | command | result | exit |
|---|---|---|---|
| python | `python -m pytest service/tests -q -n 8 --dist loadfile` | 5,623 passed, 10,934 subtests | 0 |
| bridge | `cd mcp/stdio && node tests/run-all.mjs` | all 363 suites passed; 1 test skipped in `runtime-launch-helpers.test.js` | 0 |
| dashboard | `cd service/new_dashboard && node --test *.test.mjs` | 1,709 passed, 0 skipped | 0 |
| aify-wrapper | `cd ~/projects/aify-wrapper && node --test tests/*.test.js` | 219 passed, 0 skipped | 0 |
| aify-env | `cd ~/projects/aify-env && npm test` | 1,683 tests, 1,682 passed, 1 skipped | 0 |

`TIME_WAIT` was **290** before the python run and **12,178** after it, against this host's 16,384-port
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
UNATTRIBUTED. Live observation is now **166 verified continuous minutes** producing **zero** wire
gaps: the earlier 106 (guarded windows of 55, 45 and 6 minutes) plus a 60-minute window taken
2026-09-09 that made **22,521 comparisons across 5 terminals** -- 0 gaps, 0 drops, 0 unnumbered
frames. Arrival intervals p50 382.9ms, p05 12.4ms, min 7.9ms, and **no margin is derived from
them**: they are receiver-side spacing, and the queue, its flush timer, the event loop and the
socket all sit between a POST and an arrival.

**What 166 minutes of zero supports, and what it does not.** It supports "no wire gap was seen in
the windows watched". It does NOT establish that the deployed defect is rare: zero steps over one is
equally consistent with every flush carrying exactly one post, which the wire alone cannot separate
from a queue numbering correctly. And nothing here observes a console, a fetch, a reset or a person
waiting. The mechanism is present in the deployed build and has not been caught firing.

## Retirement — the environment-bridge tier, 35 deleted modules

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| R-1 | Managed worker OWNERSHIP, teardown and survivor reaping are gone from the bridge, and aify-env owns them | deleted: `managed-ownership.mjs`, `managed-teardown-ownership.js`, `managed-teardown-sweeps.mjs`, `single-agent-teardown.mjs`, `reap-managed-survivors.js` | `scripts/deleted-import-census.py` — searches every surviving `.js/.mjs/.cjs/.py` for the deleted file's NAME as a fixed string, then places each mention in a comment or in code | PROVEN that no LITERAL spelling of these names appears outside a comment: of 107 deleted files (35 product, 72 tests), 84 are not spelled anywhere in the searched population, 22 appear only inside comments or docstrings, and 1 in a test FIXTURE's string literal. Two controls, fourteen carriers and a V8 parse-preservation differential in the same run; classification by occurrence SPAN, with AST byte columns converted before they meet character offsets and all four ECMAScript line terminators ending a `//` comment. **NOT unreachability** — an escaped, concatenated or computed specifier evaluates to the same path with no literal hit, and nothing here resolves a specifier. **BEHAVIOUR: UNREVIEWED IN THIS RANGE** | Retired to aify-env. Its behavioural half is open — see the note below and the open list |
| R-2 | Terminal MANAGEMENT — the manager, control loop, runtime and capability probes — is gone from the bridge | deleted: `terminal-manager.mjs`, `terminal-control-loop.mjs`, `terminal-control.js`, `terminal-runtime.js`, `terminal-capability.mjs`, `terminals-are-possible.mjs`, `terminal-attach-notice.js`, `terminal-exit-report.js`, `terminal-text.js` | same census; `aify-comms doctor`'s `bridge-terminal` row moved to `aify-env doctor` (`docs/AIFY_ENV_BOUNDARY.md`) | PROVEN not spelled outside a comment, same run and same scope limit. **BEHAVIOUR: UNREVIEWED IN THIS RANGE** | Retired to aify-env. Same open behavioural half |
| R-3 | ENVIRONMENT advertisement, identity and the control loop are gone from the bridge | deleted: `environment-advertisement.mjs`, `environment-identity.mjs`, `environment-control-loop.mjs`, `environment-cwd-roots.mjs`, `environment-runtimes.js`, `env-client.mjs`, `env-term-shim.mjs`, `delegated-stream.mjs`, `delegated-exit.mjs` | same census; `env-bridge` and `tier-version` doctor rows | PROVEN not spelled outside a comment, same run and same scope limit. **BEHAVIOUR: UNREVIEWED IN THIS RANGE** | Retired to aify-env. Same open behavioural half |

**The claim these rows make is what the census MEASURES, and the first version of both was wrong.**
They cited `no-missing-sibling-imports.test.js` and `moved-names-resolve.test.js` until review
pointed out that neither proves "zero remaining importers": the first asks whether a resolved
sibling exists, the second whether a moved NAME resolves, and both are green in a tree that still
imports a deleted module from somewhere they do not walk.

**Then the census replacing them was broken three ways in one review sitting**, and the third is the
one worth keeping. It matched `(import|require|from)` then a quoted name on ONE line, so a `require(`
or a dynamic `import(` with its specifier on the next line was missed. And its POSITIVE CONTROL
matched THE CENSUS ITSELF -- the word `import` inside the identifier `expect_importers`, on the line
naming the probe -- so with every real importer deleted the control still read "covered". The
instrument certified itself.

**And the repair was broken again a round later, at a different granularity.** It held a set of
comment LINES and asked whether a mention's line was in it, so `import("./x.mjs"); // note` read as
PROSE while the identical import without the note read as CODE. Review drove that through the whole
entrypoint. Classification is by occurrence SPAN now -- character offsets from `tokenize`, `ast` and
the JS scanner -- with FOURTEEN carriers, ten that must read as CODE and four as PROSE. Two of
them are an ASCII/non-ASCII pair that isolates a column UNIT rather than a shape: the AST reports
UTF-8 BYTE columns while `tokenize` reports CHARACTER columns, and joining them without conversion
let a docstring of accented characters swallow the code sharing its line. Three more are
ECMAScript's other line terminators -- U+2028, U+2029 and CR -- because the scanner ended a `//`
comment only at LF, so `// note<U+2028>import(...)` hid a live import inside a comment.

A V8 DIFFERENTIAL runs beside them over the repo's real files: everything the scanner calls a
comment is blanked and handed to `vm.SourceTextModule`, with a stretched-span arm that must fail
and files where even that parses dropped rather than counted. **What it establishes is PARSE
PRESERVATION over the files it judged, not comment correctness** -- deleting a whole statement
leaves valid JavaScript, which is exactly how the U+2028 carrier walked past it. It was published
as the stronger claim for one round.

**WHAT THE CENSUS ESTABLISHES, stated at its real width.** No LITERAL spelling of a deleted module's
name appears outside a comment in the searched population: 84 of 107 are not spelled anywhere, 22
appear only inside comments or docstrings, 1 inside a test fixture's string literal. It is NOT
unreachability. A dynamic import whose specifier spells one letter of the module name as a unicode
escape is valid, evaluates to the same path and
carries no literal hit; a concatenated or computed specifier does the same. Nothing here resolves a
specifier, so the claim stops at the spelling.

**AND IT FOUND TWO REAL DEFECTS OF THE SAME CLASS, in gates that were green.**
`every-module-is-imported-by-a-test.test.js` listed two DELETED modules among "the modules this
series created" and asserted of each that it is absent from the untested backlog and imported by a
test -- four assertions satisfied by absence, since a module that does not exist is in no list. And
`child-processes-cannot-inherit-live-carriers.test.js` exempted a deleted test file from its
carrier-sealing gate, which lets nothing through today and would silently exempt any future file of
that name. Both now assert that their own entries still exist, and both guards were driven by
putting a deleted name back.

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

**THE CROSS-REPO SEAM, and what each surviving test actually proves.** Five tests that drove a REAL
aify-env through the deleted modules went with them, leaving
`the-credential-ref-we-write-is-one-aify-env-resolves.test.js` -- which review has narrowed, and the
narrower reading is the correct one: it establishes that the credential REFERENCE this service
writes agrees in grammar and directory with what aify-env resolves. That is not lifecycle
integration, and this ledger described it as more than it is.

Row X-1 adds the ADDRESS half of the replacement join -- the half that fails silently, since a
renamed path answers 404 and surfaces as a failed claim rather than as a routing error. Request
bodies, response shapes, auth and any live round trip remain **UNVERIFIED FOR THIS RELEASE**. An
earlier version of this paragraph deferred them to the next version; that was a scope change this
ledger has no standing to make, so they sit in the open list below instead.

## The cross-repo seam that replaced the deleted one

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| X-1 | Every request aify-env's aify-comms plugin EMITS names a route this service serves, at that method | `lib/plugins/aify-comms/api.mjs` (aify-env) against this app's route table | `test_the_env_plugin_addresses_routes_this_service_serves.py` — every public method of the real `CommsApi` is CALLED through its own `fetchImpl` injection; each request is stamped with the method that was running, and EACH method must emit exactly one. 10 methods, 10 owned requests, 130 served routes, each matching exactly one | PASSES IN TESTS on the candidate. Driven EIGHT ways: a method that emits nothing, a silent method PAIRED with one emitting an extra valid request, a driver that exits non-zero while printing normal JSON, a wrong ACTUAL prefix, an unserved path, and the SERVICE dropping a path all turn it red; a whitespace refactor stays green; a named checkout with no plugin SKIPS | Met for the ADDRESS and METHOD. **Three earlier versions were each satisfied by less** — a regex over call sites that legal whitespace defeated, a `/api/v1` prefix the gate supplied itself, and a TOTAL where the claim needed a relation: `len(requests) == len(methods)` is satisfied by a silent method beside one sending twice. Bodies, responses, auth and a live round trip are NOT covered |

## Packaging

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| P-1a | The MANIFEST, the LOCKFILE and npm's record of the install name the same wrapper commit | `mcp/stdio/package.json`, `mcp/stdio/package-lock.json` (the Dockerfile copies both, then runs `npm ci`) | `the-wrapper-pin-is-not-behind-a-template-change.test.js` — "THE GATE: this repo consumes exactly the pin it declares", via `consumedPinVerdict({packagePin, lockPin, installedPin})` | PROVEN on this host: all three read `5c62d91`, the third from `node_modules/.package-lock.json`. **The branch matters** — `consumedPinVerdict` returns `ok:true` with `installedPin` empty, so where npm's install record cannot be read the gate compares TWO values and not three, and the row is then only manifest-to-lock | Met, with the two-value branch named |
| P-1b | Every file the pinned commit PUBLISHES is present in `node_modules` and is that commit's bytes | `mcp/stdio/node_modules/aify-wrapper` (what `install.sh` renders from) | `the-installed-wrapper-is-the-pinned-commit.test.js` — the required population is DERIVED from the pinned commit's own `package.json` (`files[]`, `bin` targets, the manifest itself); every path in it must be PRESENT and byte-identical | PROVEN where an upstream checkout exists: 19 required paths derived from 48 tracked files, 0 missing, 0 differing, all four templates in the derived set. **SKIPS BY NAME otherwise** | Met on this host. **The first version INTERSECTED instead of deriving** — it compared only files that happened to be present, so review passed it with `install.sh` deleted and again with `render.sh` deleted, both published by the pinned manifest and one a `bin` target that invokes the other. Both carriers now fail it |
| P-2 | The three repos declare ONE version and the release recipe touches every declaring file | `VERSION`, `mcp/stdio/version.js`, `package.json`, `package-lock.json`, `.claude-plugin/plugin.json` | `test_version_single_source.py`; `version-consistency.test.js` | PASSES IN TESTS at the CURRENT declared version, in the runs quoted above | **NOT MET FOR v0.6.3.** All three repos still declare `0.6.2`, and v0.6.2 was never tagged. The bump, stamp and rebuild are unperformed; the tag is the operator's |

**Why P-1 became two rows.** Review's finding was that one row was carrying two different claims with
one verdict, and the weaker one was doing the work: agreement between RECORDS is not provenance of an
OBJECT. CLAUDE.md records the failure that separates them as MEASURED — the sha was raised in both
package files, `npm install` reported success, and `node_modules/aify-wrapper` still held the previous
code, because npm trusts a tree matching the lock it was just handed. Every record agreed throughout.
The remedy written there is a hand grep of the installed file; P-1b is that grep, run by the suite over
a population the MANIFEST decides rather than the installation.
It is driven four ways: `install.sh` removed from the installation and `render.sh` removed from it --
review's own two carriers, both of which the intersecting version passed -- each turn it red naming
the MISSING file; one changed byte in a template turns it red naming that file; and a real checkout
that does not hold the pin makes it SKIP with the reason, never pass. The population control is
separate and asserts both size and subject: at least ten derived paths, and all four wrapper
templates among them, because byte-identity over an empty or shrunken set is satisfied by anything.

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
   range. What is PROVEN is that no literal spelling of a deleted module's name appears outside
   a comment — not unreachability, since nothing resolves a specifier. Behaviour is UNREVIEWED.
3. **The cross-repo seam — UNVERIFIED FOR THIS RELEASE, not deferred.** X-1 proves the plugin's
   ADDRESSES against this service's routes, driven from both sides, and
   `the-credential-ref-we-write-is-one-aify-env-resolves.test.js` proves the credential
   reference's grammar and directory agreement — not lifecycle integration. Request bodies,
   response shapes, auth and a live round trip are unproved, where six tests used to drive a real
   aify-env. Moving them out of this release is an owner's decision and has not been made.
4. **P-2** — v0.6.3 is not declared anywhere.
5. **S-4** — an operator decision.
6. **The deploy, and it is THREE stale artifacts rather than one.** Read from
   `aify-comms doctor --json` for this line: `service` FAIL (the container), `bridge-installed`
   FAIL (`~/.aify-comms` holds `3e7387a`, and commits since then changed real bridge source, not
   only tests), and `skills-installed` FAIL (the trees in `~/.claude/skills`, the codex mirror and
   the hermes copy do not match the checkout, so B7's skill edits have reached no agent). The
   container rebuild and `install.sh` are separate actions and neither implies the other.
7. **What the running build is.** `3e7387a6`, version `0.6.2`, with HEAD **161 commits ahead** --
   read from `GET /health` and `git rev-list --count`, with the served build confirmed an ancestor
   of HEAD. It carries the B-1 defect, demonstrated against its own module. **That figure moves
   with every push**: it read 149 while this ledger's own four commits were being made, which is
   why it is stated with the two commands that answer it rather than as a number to quote.
8. **Reported by the same doctor run and NOT this version's:** `session-handles` FAIL -- three
   conversations claimed by more than one agent, eight agents involved, which is the standing issue
   that reports rather than refuses. Recorded here so a reader of this ledger's doctor output is
   not left to wonder whether v0.6.3 caused it.
