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
  population; rows R1-R3 below are theirs.
- **The lockfile is a shipping input.** The Dockerfile's `COPY mcp/stdio/package.json mcp/stdio/package-lock.json` is followed by `RUN cd mcp/stdio && npm ci`, so excluding it as "not behaviour" was wrong. Row P-1 is its
  obligation. (Named by content rather than by line, because a line number in a doc rots -- the
  rule `test_docs_name_symbols_not_line_numbers.py` enforces on the files it watches, and this
  one is not among them.)

**Status.** PRODUCT NOT YET CERTIFIED — review's original-range coverage is open and this ledger
does not close it. No product-code defect is established. The deploy and the tag are the operator's.

---

## Behaviour — the console transport, which is this version's product change

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| B-1 | The broadcast sequence counts FRAMES, so a flush coalescing N posts advances it by ONE | `service/terminal_write_queue.py` | `test_the_broadcast_seq_counts_frames_not_posts.py` — `test_a_coalesced_flush_emits_one_frame_and_advances_by_more_than_one` asserts `broadcasts[0]["seq"] == 1` for three posts | PASSES IN TESTS. Candidate-bound: the container's own copy of this module FAILS the same test (`3 != 1`, then `4 != 3`), which is how the deployed defect was demonstrated | Met by the candidate. Unshipped — the running build predates it |
| B-2 | A gap recovery HOLDS frames arriving during its fetch and does not start a second one | `service/new_dashboard/xterm-mount.mjs`, `console-cursor.mjs`, `realtime-socket.mjs` | `the-console-does-not-stall-while-it-resyncs.test.mjs`; `a-mount-keeps-the-frames-that-arrive-while-it-fetches.test.mjs` (11 cases incl. a recovery double taking `resyncing`) | PASSES IN TESTS | Met by the candidate |
| B-3 | `view=console` answers the rendered snapshot, and drops the stored tail ONLY when a snapshot exists to replace it | `service/api_core/terminal_snapshot_view.py`, `service/routers/terminals.py` | `test_the_console_projection_carries_what_it_paints.py`; the mutant that drops the tail unconditionally is in its battery and is caught | PASSES IN TESTS | Met by the candidate |
| B-4 | An UNNUMBERED chunk clears the live screen's sequence, so the GET answers `null` and the browser replays rather than trusting a stale number | `service/terminal_snapshot.py`, `service/api_core/terminal_output.py` | `test_live_terminal_screen.py`; `test_status_reads_the_live_screen_not_the_stored_tail.py` | PASSES IN TESTS | Met by the candidate |

**Not claimed by any row above:** that these repair the operator's reported lag. The lag is
UNATTRIBUTED — 106 verified continuous minutes of live observation produced zero wire gaps, so the
mechanism is present in the deployed build and was not caught firing.

## Retirement — the environment-bridge tier, 35 deleted modules

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| R-1 | Managed worker OWNERSHIP, teardown and survivor reaping are gone from the bridge, and aify-env owns them | deleted: `managed-ownership.mjs`, `managed-teardown-ownership.js`, `managed-teardown-sweeps.mjs`, `single-agent-teardown.mjs`, `reap-managed-survivors.js` | `mcp/stdio/tests/no-missing-sibling-imports.test.js`, `moved-names-resolve.test.js` — a stale import fails loudly rather than resolving | PROVEN for imports: zero remaining importers of any deleted module, positive-controlled. BEHAVIOUR: PASSES IN TESTS in aify-env -- `owned-processes.test.js` ("the writing instance stamps itself as the OWNER", "stopping removes only its own"), `orphans-die-with-the-environment.test.js` ("a process outlives a KILLED environment, and the next one reaps it"), `orphan-reap.test.js`, `reaper.test.js`, `reaper-wiring.test.js`, `kill-tree.test.js`, `shutdown.test.js`, `a-dead-pid-is-not-killed-on-shutdown.test.js` | Retired to aify-env and EVIDENCED there. The two named first were read rather than trusted to their titles |
| R-2 | Terminal MANAGEMENT — the manager, control loop, runtime and capability probes — is gone from the bridge | deleted: `terminal-manager.mjs`, `terminal-control-loop.mjs`, `terminal-control.js`, `terminal-runtime.js`, `terminal-capability.mjs`, `terminals-are-possible.mjs`, `terminal-attach-notice.js`, `terminal-exit-report.js`, `terminal-text.js` | same import gates; `aify-comms doctor`'s `bridge-terminal` row moved to `aify-env doctor` (`docs/AIFY_ENV_BOUNDARY.md`) | PROVEN for imports. BEHAVIOUR: PASSES IN TESTS in aify-env — `pty-real.test.js`, `terminal-output-arrives-in-order.test.js`, `input-resize.test.js`, `the-resize-listener-is-registered-before-the-wait.test.js`, `ctrl-c-gives-the-terminal-back.test.js`, `an-exit-says-how-it-ended.test.js`, `an-exit-marker-lands-after-the-output-it-follows.test.js` | Retired to aify-env and EVIDENCED there. **The CROSS-REPO seam remains thin**: five tests that drove a real aify-env through the deleted modules went with them, and one survives (`the-credential-ref-we-write-is-one-aify-env-resolves.test.js`) |
| R-3 | ENVIRONMENT advertisement, identity and the control loop are gone from the bridge | deleted: `environment-advertisement.mjs`, `environment-identity.mjs`, `environment-control-loop.mjs`, `environment-cwd-roots.mjs`, `environment-runtimes.js`, `env-client.mjs`, `env-term-shim.mjs`, `delegated-stream.mjs`, `delegated-exit.mjs` | same import gates; `env-bridge` and `tier-version` doctor rows | PROVEN for imports. BEHAVIOUR: PASSES IN TESTS in aify-env — `advertise.test.js`, `the-daemon-really-advertises.test.js`, and `the-view-does-not-invent-terminal-support.test.js` for the capability half | Retired to aify-env and EVIDENCED there. Same cross-repo caveat as R-2 |

**How these rows moved from UNREVIEWED to evidenced.** A deletion's IMPORT obligation is provable
in this range and is proved. Its BEHAVIOURAL obligation — that the responsibility still happens,
in aify-env — is evidenced by aify-env's own suite, which runs on every commit in this session
(1,683 tests, 1 skipped). What changed is not the evidence but that it is NAMED: the tests above are
cited individually, and the two carrying R-1 were READ rather than trusted to their titles.

**A count of files mentioning a word is NOT what these rows say**, and a first pass did exactly that
-- 112 test files matching "teardown", 51 matching "ownership". Review had already rejected that
shape in the discovery inventory, and it would have been the same mistake twice. The search that
produced those counts was itself broken: `git grep -E "a\|b"` reads the escaped pipe as a LITERAL,
so three topic searches returned 0 while a fourth WITHOUT alternation returned 104 and looked like a
working control. A control has to exercise the same FORM as the thing it controls.

**WHAT IS STILL THIN, and it is the cross-repo seam rather than the responsibilities.** Five tests
that drove a REAL aify-env through the deleted modules were deleted with them, leaving one
(`the-credential-ref-we-write-is-one-aify-env-resolves.test.js`). So each side is evidenced in its
own suite and the JOIN between them is proved once. That is a real limit, and it belongs to the next
version rather than to these rows.

## Packaging

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| P-1 | The lockfile the container installs from matches the manifest, and the pinned wrapper sha is the one actually installed | `mcp/stdio/package.json`, `mcp/stdio/package-lock.json` (the Dockerfile copies both, then runs `npm ci`) | `mcp/stdio/tests/version-consistency.test.js`; `the-wrapper-pin-is-not-behind-a-template-change.test.js` | PASSES IN TESTS | Met. **Standing hazard recorded in CLAUDE.md: bumping the pin and running `npm install` has been MEASURED to change nothing** — npm trusts a tree matching the lock it was handed. Remove `node_modules/aify-wrapper` and reinstall, then grep the installed file |
| P-2 | The three repos declare ONE version and the release recipe touches every declaring file | `VERSION`, `mcp/stdio/version.js`, `package.json`, `package-lock.json`, `.claude-plugin/plugin.json` | `test_version_single_source.py`; `version-consistency.test.js` | PASSES IN TESTS at the CURRENT declared version | **NOT MET FOR v0.6.3.** All three repos still declare `0.6.2`, and v0.6.2 was never tagged. The bump, stamp and rebuild are unperformed; the tag is the operator's |

## Settings — B7's audit

| id | required behaviour | changed paths | exact test / assertion | result | disposition |
|---|---|---|---|---|---|
| S-1 | Every declared setting is READ by something outside its declaration | `service/api_core/settings.py` | `test_every_setting_has_a_reader.py` | PASSES IN TESTS | Met. Two deliberately inert keys carry their reason at the declaration |
| S-2 | The drawn surface and the declaration agree BY NAME, in both directions | `service/new_dashboard/settings-panel.mjs` | `test_the_settings_surface_agrees_with_the_declaration.py` — five states, five kills, including an unreadable schema | PASSES IN TESTS | Met |
| S-3 | A setting whose LABEL promises a unit is converted in that unit | readers in `service/routers/shared.py`, `maintenance.py`, `contracts.py`, `api_core/reply_contract.py` | `test_a_setting_labelled_with_a_unit_is_read_in_that_unit.py` — AST-based; eight mutants killed | PASSES IN TESTS. Hand-traced first: all 14 unit- and sentinel-bearing settings correct | Met for Python readers. **Scope stated: display code that converts a different value is not judged** |
| S-4 | `manual_session_mode` does what its label promises | `service/new_dashboard/session-rail.mjs`, `settings-panel.mjs` | `test_every_dashboard_setting_has_a_reader.py` `KNOWN_INERT` | **ADVERSE — it is READ, SHOWN, and does NOTHING.** The panel calls it the operator's control over the session-mode chips; the rail renders them regardless | **OPEN OPERATOR DECISION** since 2026-09-08: restore the gating or delete the field. Both are visible to the operator |

---

## What is open, stated as such

1. **Review's original-range product coverage.** Theirs, explicitly incomplete, and this ledger does
   not substitute for it.
2. **R-2 and R-3 behavioural evidence** — retired to aify-env, unreviewed in this range.
3. **P-2** — v0.6.3 is not declared anywhere.
4. **S-4** — an operator decision.
5. **The deploy.** 149 commits behind; the running build carries the B-1 defect, demonstrated
   against its own module.
