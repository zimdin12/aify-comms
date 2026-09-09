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

**Even the strong tier is not an obligation.** It says a selected test DECLARES a static import of
the exact file -- not that the test ran, and not that it exercises the change or any branch of it.
The inventory finds candidates; this ledger says what they owe.

**Two scope corrections review made, both verified here before being adopted:**

- **Deletions are not evidence-free.** Removing ownership, teardown, control-loop or
  terminal-manager responsibilities needs an explicit retirement disposition. They stay in the
  population; rows R-1..R-3 below are theirs.
- **The lockfile is a shipping input.** The Dockerfile's `COPY mcp/stdio/package.json mcp/stdio/package-lock.json` is followed by `RUN cd mcp/stdio && npm ci`, so excluding it as "not behaviour" was wrong. Rows P-1a and P-1b are its
  obligation. (Named by content rather than by line, because a line number in a doc rots -- the
  rule `test_docs_name_symbols_not_line_numbers.py` enforces on the files it watches, and this
  one is not among them.)

**Status.** PRODUCT NOT YET CERTIFIED — review's original-range coverage is open and this ledger
does not close it.

**FOUR PRODUCT-CODE DEFECTS ARE ESTABLISHED AND FIXED**, and this line said none was until the
review was pointed at the product rather than at the instruments. They are in the section below,
each with the commit that closed it. A fifth -- the conditional-start race -- was traced in
source, left open at the tag, and closed after it.

The tag `v0.6.3` is cut. **The DEPLOY is the operator's**, and `main` is ahead of the tag by the
conditional-start work.

---

## What every PASSES IN TESTS row below is bound to

A result with no candidate attached is not a receipt. Every such row was measured on:

- **Candidate: `09e8df6a`** -- the commit the tested tree BECAME, written after that commit
  exists rather than predicted before it. A candidate has to be a thing another person can check
  out: an earlier version of this line named "the tree at `05ad53df` carrying round sixteen's
  repairs", and `05ad53df` is immutable and holds neither X-1 nor the repaired census.
- **The five suites, ONE run each, all exit status 0**, taken immediately before that commit so
  every figure in this document reconciles against the same tree:

| suite | command | result | exit |
|---|---|---|---|
| python | `python -m pytest service/tests -q -n 8 --dist loadfile` | 5,638 passed, 10,956 subtests | 0 |
| bridge | `cd mcp/stdio && node tests/run-all.mjs` | all 364 suites passed; 1 test skipped in `runtime-launch-helpers.test.js` | 0 |
| dashboard | `cd service/new_dashboard && node --test *.test.mjs` | 1,712 passed, 0 skipped | 0 |
| aify-wrapper | `cd ~/projects/aify-wrapper && node --test tests/*.test.js` | 223 tests, 222 passed | 0 |
| aify-env | `cd ~/projects/aify-env && npm test` | 1,699 tests, 1,698 passed, 1 skipped | 0 |

**AND THIS TABLE HAD GONE STALE UNDER ITS OWN CANDIDATE LINE.** It quoted 5,623 / 363 / 1,709 /
219 / 1,683 while naming a commit several steps past them -- so the "one run" it promises was
neither one run nor of that tree. Two copies of one fact, and the copy nobody scrolls back to is
the one that rots: the failure this repo documents at length, inside the section that exists to
prevent it.

`TIME_WAIT` was **294** before the python run and roughly **12,200** after it, against this
host's 16,384-port ephemeral range -- the socket pressure CLAUDE.md documents, sampled DURING the
run rather than between runs. Nothing failed, so it attributes nothing here; it is recorded so a
later red is read against a measured before-and-after rather than a remembered one. A reading of
**1,888** taken before an earlier run today was residue from the run before it and NOT ambient
load, which is worth naming because that number looks exactly like a host condition and this
repo has a record of somebody reporting one.
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
UNATTRIBUTED. Observation totals **286 minutes across SIX guarded windows** -- 60, 60, 60, 55, 45 and 6
-- producing **zero** wire gaps in every one. **That is cumulative, not one uninterrupted run**, and
an earlier version of this line said "226 verified continuous minutes", which claims a continuity
property no window has: the longest single uninterrupted window is 60 minutes. The three 60-minute
windows were taken 2026-09-09 and made **22,521**, **24,160** and **23,579** comparisons across 5
terminals each, with 0 gaps, 0 drops and 0 unnumbered frames in every one. Arrival intervals were p50 382.9ms / p05
12.4ms / min 7.9ms and p50 237.4ms / p05 12.7ms / min 8.1ms, and **no margin is derived from them**:
they are receiver-side spacing, and the queue, its flush timer, the event loop and the socket all sit
between a POST and an arrival.

**What 286 cumulative minutes of zero supports, and what it does not.** It supports "no wire gap
was seen in any of the six windows watched", each bounded by its own start and end.

**AND IT MEASURES THE LIVE COALESCING RATE, which this ledger previously called unmeasurable from
the wire.** The hedge was "equally consistent with a queue numbering correctly" -- true before
anyone had established which queue was serving, and not true now.
`check-deployed-console-transport.py` runs the CONTAINER's own `terminal_write_queue.py` against
this version's frame-sequence test and it FAILS, and that test pins the shape exactly: one
broadcast per flush, numbered with the cumulative POST count (three posts, `len(broadcasts) == 1`,
seq 3 where 1 is required). So on THIS build an observed step of N **is** the number of posts that
flush carried. Zero steps over one across **106,676 recorded comparisons** in the five windows whose
counts are recorded means **no flush coalesced in any of them**.

**That is still bounded windows on one fleet, and it attributes nothing.** Nothing here observes a
console, a fetch, a reset or a person waiting; the trigger condition simply did not occur while it
was watched. The mechanism is present in the deployed build and has not been caught firing.

## Retirement — the environment-bridge tier, 35 deleted modules

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| R-1 | Managed worker OWNERSHIP, teardown and survivor reaping are gone from the bridge, and aify-env owns them | deleted: `managed-ownership.mjs`, `managed-teardown-ownership.js`, `managed-teardown-sweeps.mjs`, `single-agent-teardown.mjs`, `reap-managed-survivors.js` | `scripts/deleted-import-census.py` — searches every surviving `.js/.mjs/.cjs/.py` for the deleted file's NAME as a fixed string, then places each mention in a comment or in code | PROVEN that no LITERAL spelling of these names appears outside a comment: of 107 deleted files (35 product, 72 tests), 84 are not spelled anywhere in the searched population, 22 appear only inside comments or docstrings, and 1 in a test FIXTURE's string literal. Two controls, fourteen carriers and a V8 parse-preservation differential in the same run; classification by occurrence SPAN, with AST byte columns converted before they meet character offsets and all four ECMAScript line terminators ending a `//` comment. **NOT unreachability** — an escaped, concatenated or computed specifier evaluates to the same path with no literal hit, and nothing here resolves a specifier. **BEHAVIOUR: MAPPED obligation by obligation to READ assertions in aify-env — see the table below. PASSES IN TESTS there, not PROVEN on the operator's fleet** | Retired to aify-env, and its behavioural half is now mapped rather than asserted |
| R-2 | Terminal MANAGEMENT — the manager, control loop, runtime and capability probes — is gone from the bridge | deleted: `terminal-manager.mjs`, `terminal-control-loop.mjs`, `terminal-control.js`, `terminal-runtime.js`, `terminal-capability.mjs`, `terminals-are-possible.mjs`, `terminal-attach-notice.js`, `terminal-exit-report.js`, `terminal-text.js` | same census; `aify-comms doctor`'s `bridge-terminal` row moved to `aify-env doctor` (`docs/AIFY_ENV_BOUNDARY.md`) | PROVEN not spelled outside a comment, same run and same scope limit. **BEHAVIOUR: MAPPED to read assertions in aify-env — see the table below. PASSES IN TESTS there, not PROVEN on the operator's fleet** | Retired to aify-env, and its behavioural half is now mapped |
| R-3 | ENVIRONMENT advertisement, identity and the control loop are gone from the bridge | deleted: `environment-advertisement.mjs`, `environment-identity.mjs`, `environment-control-loop.mjs`, `environment-cwd-roots.mjs`, `environment-runtimes.js`, `env-client.mjs`, `env-term-shim.mjs`, `delegated-stream.mjs`, `delegated-exit.mjs` | same census; `env-bridge` and `tier-version` doctor rows | PROVEN not spelled outside a comment, same run and same scope limit. **BEHAVIOUR: MAPPED to read assertions in aify-env — see the table below. PASSES IN TESTS there, not PROVEN on the operator's fleet** | Retired to aify-env, and its behavioural half is now mapped |

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

**R-1'S BEHAVIOURAL HALF IS NOW MAPPED, obligation by obligation.** Review's objection to the
earlier version was that a passing external suite is a suite and not a mapping. This is the
mapping: each retired responsibility, the aify-env test that carries it, and the assertion that
does the carrying. Every row below was read; none is cited on the strength of its title.

| retired obligation | the assertion that carries it now |
|---|---|
| a process is attributed to the instance that started it | `owned-processes.test.js` — "the writing instance stamps itself as the OWNER, without being asked", and "an explicit owner is honoured over the default" |
| one instance's stop does not touch another's processes | same file — "several processes accumulate, and stopping removes only its own", asserting the record still holds exactly `["p2"]` afterwards |
| every managed process is stopped when the environment goes down | `shutdown.test.js` — "every managed process is stopped BEFORE the process exits" and "the record is cleared only after the stops were attempted" |
| a stop that THROWS does not strand the rest | same file — "one process that refuses to die does not strand the others", which asserts `stop-failed:a`, `stopped:b` AND `exit:0`: the failure is recorded, the sibling still stops, and the exit still happens |
| the whole process TREE dies, not just the launcher | `orphans-die-with-the-environment.test.js` — "a GRANDCHILD dies too — killing the launcher is not enough", which starts a REAL daemon and a launcher whose agent is a CHILD of the script, the shape that leaked two `sleep` processes; and `kill-tree.test.js` — "windows kills the whole tree, forcibly, in one call", "posix targets the process GROUP first, then the process" |
| a process that outlived its environment is collected by the next one | `orphans-die-with-the-environment.test.js` — "a process outlives a KILLED environment, and the next one reaps it", asserting the pid started, was recorded, and was reaped |
| and reaping does not kill something that merely REUSED the pid | `orphan-reap.test.js` — "a pid that VERIFY rejects is skipped", asserting `plan.reap` is empty and the skip reason is "not ours any more"; and "a verify that THROWS is treated as a rejection, not as permission". `reaper.test.js` — "a probe that THROWS makes the entry UNKNOWN, never dead" |

**WHAT THIS IS STILL NOT.** These run in aify-env's suite, on this machine, in the same session —
they are PASSES IN TESTS there, not PROVEN on the operator's fleet, and this ledger cannot make
them the latter. What has changed is that the claim is now checkable: a reader can open each named
test and see whether the assertion says what this table says it says.

**R-2 AND R-3 ARE NOT MAPPED THIS WAY YET.** Their tests are still cited by title, which is the
weaker claim review rejected, and they stay in the open list until somebody reads them the way
these were read. "Partially mapped" is a different statement from "mapped", and collapsing the
two here would repeat the mistake this section exists to correct.
**R-2 AND R-3 ARE NOW MAPPED THE SAME WAY**, and the four marked ✓ were opened and read rather
than cited on the strength of a title -- which is how the R-1 table found two that carried more
than they promised.

| retired obligation | the assertion that carries it now |
|---|---|
| **R-2** a process really runs on a terminal, streams, and is released | `pty-real.test.js` — "a process started on a REAL terminal runs, streams, and is released", with "the smoke script exits by itself, which is what makes this test possible" as its own precondition |
| **R-2** output reaches the service IN ORDER, and one slow POST does not reorder it | ✓ `terminal-output-arrives-in-order.test.js` — asserts the first chunk goes immediately, that a second is HELD while the first is in flight (`calls.length` stays 1), and that it goes once the first settles. Also "a failed POST does not stop the next chunk", "TWO TERMINALS DO NOT BLOCK EACH OTHER", and a bounded backlog that drops the OLDEST |
| **R-2** input reaches the process, and a write that cannot land is REFUSED | `input-resize.test.js` — "input written to a process ARRIVES, observed by what it echoes back"; "writing to an unknown id is refused, not silently dropped"; "writing to a process that has EXITED is refused" |
| **R-2** a resize applies with the numbers given, and a PIPED process says it did not | ✓ same file — the terminal case asserts `ok` true and the pty received exactly `{cols: 120, rows: 40}`; the piped case reports that it did not apply, which is the refusal aify-comms' own plugin was ignoring until `09b07ee` |
| **R-2** the terminal is given back on Ctrl+C, before the process exits | `ctrl-c-gives-the-terminal-back.test.js` — "THE TERMINAL IS OUT OF RAW MODE BEFORE THE PROCESS EXITS", "...even when stopping the plugins takes real time", "THE SECOND Ctrl+C ALSO LEAVES A USABLE TERMINAL", each with a positive control that the shutdown ran at all |
| **R-2** an exit says HOW it ended, and a kill is not recorded as a clean one | `an-exit-says-how-it-ended.test.js` — clean exits keep code 0 with the owning agent, non-zero keeps its number, "a process killed from outside is not recorded as a clean exit", and "a STOP records no code, because nobody watched it end" |
| **R-3** the host is described from what it IS, failing closed | `advertise.test.js` — "kind is read from the host, in the order the bridge reads it", "kind and os are DIFFERENT questions, and wsl is where that shows", "availability carries the reason, and an unfound runtime is not dropped", "it FAILS CLOSED, inheriting the detector's own rule" |
| **R-3** a booted daemon really advertises, and only a 2xx counts as accepted | ✓ `the-daemon-really-advertises.test.js` — a booted daemon posts to every registered service; it sends NO id and NO cwdRoots, the two the service owns; and a 401 leaves `health.advertising` FALSE so the bridge keeps the job, with a positive control asserting the daemon actually attempted a beat first. "a 500 is treated the same as a 401 — any non-2xx is not an acceptance" |
| **R-3** the view does not invent terminal support it was not told about | ✓ `the-view-does-not-invent-terminal-support.test.js` — available and unavailable both travel, the conpty backend flag survives, and "a missing or malformed block is UNAVAILABLE with a reason, not available" asserts both the false and the presence of a reason. "an environment that did not answer at all says so" |

**THE SAME LIMIT AS R-1.** These run in aify-env's suite on this machine — PASSES IN TESTS there,
not PROVEN on the operator's fleet — and this ledger cannot make them the latter. What has changed
is that a reader can open each named test and check whether the assertion says what this table
says. **What is still THIN is the cross-repo seam**, which row X-1 covers for addresses only.
**WHAT THIS SECTION USED TO SAY, and why review was right to refuse it.** It cited aify-env's suite
being green (1,683 tests at the time; 1,699 now) and named a handful of its files, two of which had been read. Review's
answer was exact and is the reason the tables above exist: **a passing external suite is a suite,
not an obligation-to-assertion mapping**, and citing one as though it were is the same shape they
had already rejected in the discovery inventory. A green suite says some tests pass. It does not say
which retired responsibility any of them carries, and nobody could check it without doing the work
that had not been done.

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
| X-2 | Every FIELD aify-env's plugin sends is one the receiving route's model declares | same plugin against this app's request models | same test — the harness records each emitted BODY, and every top-level key is checked against `route.body_field.field_info.annotation`'s declared names and aliases | PASSES IN TESTS on the candidate. Driven both ways: renaming `only_if_no_live_session` to camelCase on the wire, and adding a field no model declares, each turn it red and name the field | Met for TOP-LEVEL fields. **Pydantic IGNORES an undeclared field**, so a renamed key is not an error anywhere — the request succeeds, the value never arrives, and the symptom is behaviour that quietly stops happening. Nested objects are NOT judged here |
| X-3 | aify-env's own READERS work on what this service actually answers | `lib/startable-agents.mjs` (aify-env) against real `/agents` and `/sessions` responses | `test_the_env_plugin_reads_what_this_service_answers.py` — seeds a managed agent and two sessions, fetches through the real routes, and runs the plugin's OWN `startabilityOf` and `restartTargetFor` on the payload | PASSES IN TESTS on the candidate. Four reader mutants killed: a field we do not send (`last_seen`), a live-vocabulary drift, a different session-mode spelling, and a reader that stops refusing a live session | Met for the two readings that gate "start available agent". **The vocabulary check is the part only this side can answer** — if the reader's live statuses were ones this service never emits, the check would never fire and every session would read restartable. Other responses and any live round trip are NOT covered |

## Packaging

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| P-1a | The MANIFEST, the LOCKFILE and npm's record of the install name the same wrapper commit | `mcp/stdio/package.json`, `mcp/stdio/package-lock.json` (the Dockerfile copies both, then runs `npm ci`) | `the-wrapper-pin-is-not-behind-a-template-change.test.js` — "THE GATE: this repo consumes exactly the pin it declares", via `consumedPinVerdict({packagePin, lockPin, installedPin})` | PROVEN on this host: all three read `5c62d91`, the third from `node_modules/.package-lock.json`. **The branch matters** — `consumedPinVerdict` returns `ok:true` with `installedPin` empty, so where npm's install record cannot be read the gate compares TWO values and not three, and the row is then only manifest-to-lock | Met, with the two-value branch named |
| P-1b | Every file the pinned commit PUBLISHES is present in `node_modules` and is that commit's bytes | `mcp/stdio/node_modules/aify-wrapper` (what `install.sh` renders from) | `the-installed-wrapper-is-the-pinned-commit.test.js` — the required population is DERIVED from the pinned commit's own `package.json` (`files[]`, `bin` targets, the manifest itself); every path in it must be PRESENT and byte-identical | PROVEN where an upstream checkout exists: 19 required paths derived from 48 tracked files, 0 missing, 0 differing, all four templates in the derived set. **SKIPS BY NAME otherwise** | Met on this host. **The first version INTERSECTED instead of deriving** — it compared only files that happened to be present, so review passed it with `install.sh` deleted and again with `render.sh` deleted, both published by the pinned manifest and one a `bin` target that invokes the other. Both carriers now fail it |
| P-2 | The three repos declare ONE version and the release recipe touches every declaring file | `VERSION`, `mcp/stdio/version.js`, `package.json`, `package-lock.json`, `.claude-plugin/plugin.json` | `test_version_single_source.py`; `version-consistency.test.js` | PASSES IN TESTS at the CURRENT declared version, in the runs quoted above | **MET FOR aify-comms, and that is narrower than this row's title.** `VERSION`, `version.js`, both manifests and `plugin.json` declare `0.6.3` and `v0.6.3` is tagged. **aify-env and aify-wrapper still declare `0.6.2` ON PURPOSE** -- they are separate products on separate cadences, which is what `tier-version` checks as a MINIMUM rather than as equality, so "the three repos declare ONE version" was never the real obligation and this row overstated it. v0.6.2 was never tagged in any of them. The stamp is a gitignored build artifact and the rebuild is the operator's |

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

## The PRODUCT verdict, and the four defects it established

**REVISE / WOULD NOT SHIP AS-IS**, 2026-09-09, after the operator redirected review off the
acceptance instruments and onto the shipping diff. Every earlier round had been scoped to
instruments; this one was not, and it found four real defects in one pass. All four are fixed and
mutation-proven below. **That is the argument for the redirection, not against the earlier
rounds** -- and it is also the plainest evidence that instrument quality is not product quality.

| # | where | what it did | fixed by |
|---|---|---|---|
| P1 | aify-env `console-session.mjs` | a menu **Attach** landed the keyboard on the WRONG AGENT: the menu resolved bravo by identity, then `syncProcesses` reconciled the selection back onto the row the pane was already bound to. Reproduced with the pane revealed; the hidden-pane case was always correct | `ceb863d` |
| P1 | aify-env `pane-buffer.mjs`, `output-follower.mjs` | a pane showing **"live TUI -- this pane cannot draw it"** still forwarded keystrokes. `view()` has three refusal branches and the input gate re-derived only one | `2b47416` |
| P2 | aify-comms `console-actions.mjs` | a **failed resync stranded every frame it held** and marked the recovery finished, so a final prompt arriving during the fetch was never painted. A REGRESSION in this range: the same execution at `aed8b590` paints it | `93d9054b` |
| P2 | aify-env plugin `terminal-controls.mjs` | a **refused write or resize was reported as completed**, so the service stored a geometry the pty had rejected. NOT a regression -- it reproduces at the env base | `09b07ee` |

**EACH FIX IS DRIVEN BY ITS PRE-FIX SHAPE.** Restoring the old code kills the new tests and
nothing else, except in the blind-input case where it also kills five existing assertions -- which
is the single decision working, since the notice and the gate now come from one place.

**THE REVIEW'S SCOPE IS NOT FULL-RANGE CLEARANCE, and it says so itself.** Covered: the console
queue/tail/snapshot -> cursor/socket/mount/resync chain, env input/follower/pane/menu, the plugin's
Runner/control/output/start contracts, and selected service ownership/reconciliation/restart
slices. NOT covered: wrapper/server/runtime/doctor/install/distribution, deletion and lifecycle
obligations, daemon/bootstrap/credential/launcher paths, native process-tree safety, full dashboard
behaviour, and cross-repo integration. Path touches were 32 of 426 in comms and 26 of 105 in env,
**and those are file counts, not behavioural coverage**.

**ONE FINDING IS NOT FIXED AND IS AN OPEN DESIGN ITEM.** Start checks a sessions list and then
sends an unconditional Restart (`agent-starter.mjs` -> `api.mjs` -> `service/routers/
session_control.py`), so the service can select and stop a terminal that became live between the
two. The reviewer traced it in source and did not execute a race, and their own reading is that it
needs **conditional start semantics at the authority** rather than another client-side freshness
check. That is a service-side contract change, it is not a regression in this range, and it is
recorded here rather than attempted in a release cut.

---
## What is open, stated as such

1. **Review's original-range product coverage.** Delivered as a bounded verdict on 2026-09-09 --
   four defects, all fixed -- with an explicit list of what it did NOT read. See the section above.
   The unread complement is still unread, and a finding's absence where nobody looked is not a
   finding of absence.
2. **Conditional start semantics at the authority — CLOSED after the tag, and it took TWO tries.**
   The verdict's design
   finding: aify-env reads which agents have no live session, then restarts the one it chose, and a
   worker starting between those two round trips turned a start into a STOP of a live terminal.
   `POST /sessions/{id}/control` now accepts `only_if_no_live_session`, re-evaluates it against the
   rows it is about to act on, and refuses with 409 naming the live session. The caller states the
   belief it acted on; the dashboard's own Restart button sends no flag and stays unconditional.
   Four service-side mutants killed and three client-side, both ends pinned — a field nothing sets
   changes nothing. **It is NOT in the `v0.6.3` tag**, which was cut before it.

   **THE FIRST VERSION DID NOT CLOSE IT, AND I PUBLISHED THAT IT HAD.** `get_db` returns a plain
   connection with `isolation_level=''`, so a SELECT starts no transaction: the guard read with
   `in_transaction=False`, and review reproduced the original race surviving it — another
   connection committing `session=running` after the read and before the route's first write, with
   the restart then queuing a stop for the terminal that had just come live. A check that is not
   atomic with the act it authorises is a check with a window in it. `BEGIN IMMEDIATE` now reserves
   the writer BEFORE the governing read; the interleaving is driven in the suite at exactly the
   point review inserted it, and the competing commit is refused rather than landing. Removing the
   reservation makes it land again.

   Two corrections to my own reasoning came out of this, and both were mine to make:
   the ordering claim — I asserted the guard must precede the dispatch interrupt or a refusal would
   already have interrupted the agent, and mutation showed that test green either way, because one
   commit at the end means a refusal discards every write whatever the order. That is true and it is
   NOT sufficient: it says nothing about whether the deciding read is atomic with the writes. I had
   treated the second as following from the first, and published CLOSED on the strength of it.
3. **The cross-repo seam is the remaining retirement gap** — R-1, R-2 and R-3 are now mapped
   obligation by obligation to read assertions in aify-env, whose suite is green and whose
   relevant tests are named above, but with no obligation-to-assertion mapping established in this
   range. What is PROVEN is that no literal spelling of a deleted module's name appears outside
   a comment — not unreachability, since nothing resolves a specifier. Behaviour is UNREVIEWED.
4. **The cross-repo seam — THREE LAYERS COVERED, still UNVERIFIED FOR THIS RELEASE.** X-1 proves
   the plugin's ADDRESSES, X-2 the top-level FIELDS it sends, and X-3 that its own READERS work on
   what this service actually answers — all three driven from both sides — and
   `the-credential-ref-we-write-is-one-aify-env-resolves.test.js` proves the credential
   reference's grammar and directory agreement, not lifecycle integration. What remains unproved:
   nested body shapes, the responses X-3 does not read, auth, and any LIVE round trip, where six
   tests used to drive a real aify-env. Moving them out of this release is an owner's decision and
   has not been made.
5. **P-2** — CLOSED: `VERSION`, `version.js`, both manifests and `plugin.json` declare `0.6.3`,
   and the tag is cut. `service/_build_stamp.json` is GITIGNORED — a build artifact regenerated
   by `scripts/stamp.sh` immediately before the container build, so it is not part of the tag
   and stamping it here proves only that the recipe runs. The DEPLOY is still the operator's.
6. **S-4** — an operator decision.
7. **The deploy, and it is THREE stale artifacts rather than one.** Read from
   `aify-comms doctor --json` for this line: `service` FAIL (the container), `bridge-installed`
   FAIL (`~/.aify-comms` holds `3e7387a`, and commits since then changed real bridge source, not
   only tests), and `skills-installed` FAIL (the trees in `~/.claude/skills`, the codex mirror and
   the hermes copy do not match the checkout, so B7's skill edits have reached no agent). The
   container rebuild and `install.sh` are separate actions and neither implies the other.
8. **What the running build is.** `3e7387a6`, version `0.6.2`, with HEAD **161 commits ahead** --
   read from `GET /health` and `git rev-list --count`, with the served build confirmed an ancestor
   of HEAD. It carries the B-1 defect, demonstrated against its own module. **That figure moves
   with every push**: it read 149 while this ledger's own four commits were being made, which is
   why it is stated with the two commands that answer it rather than as a number to quote.
9. **Reported by the same doctor run and NOT this version's:** `session-handles` FAIL -- three
   conversations claimed by more than one agent, eight agents involved, which is the standing issue
   that reports rather than refuses. Recorded here so a reader of this ledger's doctor output is
   not left to wonder whether v0.6.3 caused it.
