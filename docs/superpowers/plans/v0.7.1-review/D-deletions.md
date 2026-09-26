# D — deletions in v0.7.0: what was removed that is still used, documented or needed

Reader D, 2026-09-26. Read-only over `v0.6.22..v0.7.0` (`dce0e771`, 181 commits, 28,127 deleted lines
outside tests and docs) plus the current checkouts of aify-env (`c4f4608`) and aify-wrapper
(`34b2a95`). All line numbers are at the `v0.7.0` tag.

**Result: 0 P1, 4 P2, 5 P3.** Nothing another repo calls or reads is gone. The P2s are docs that
send a reader to something deleted, plus helpers whose last product caller was deleted.

## Findings

### D1 (P2). KNOWN_ISSUES names a Python mirror and a test that 0.7 deleted
- **Removed:** `HANDLE_PLACEHOLDERS` in `service/runtimes/base.py`, and
  `service/tests/runtimes/test_hermes_session_discovery.py`. Both went in `b2451d86`.
- **Still referenced:** `KNOWN_ISSUES.md:218-221` says both mirrors hold the same set.
  `KNOWN_ISSUES.md:231` says that test "pins the four that ARE filtered".
- **Evidence:** `git grep -w HANDLE_PLACEHOLDERS v0.7.0` finds the name only in
  `mcp/stdio/adapters/base.js:8,74` and in docs. My check of every backticked file name in the live
  docs flagged this test path as the only one deleted in 0.7. Control: the same check reports
  `service/dashboard.html` as untracked, so it can say "missing".
- **Fix:** the issue is still open, but only on the JS side. Rewrite the entry to name `base.js` and
  `mcp/stdio/tests/adapters/contract.test.js:55`, which pins the placeholders.

### D2 (P2). Three docs point at the status taxonomy AGENTS.md no longer has
- **Removed:** `### Canonical status labels` from `AGENTS.md` (`729b648d`). `d0ab8d66` then moved the
  copy in KNOWN_ISSUES to `docs/history/KNOWN_ISSUES-archive.md:378`, marked superseded.
- **Still referenced:**
  - `docs/COMMUNICATION_GUIDE.md:5` links `../AGENTS.md#canonical-status-labels` and KNOWN_ISSUES.md.
  - `docs/DASHBOARD_SPEC.md:5` says "Use `AGENTS.md` for the canonical six-state status".
  - `docs/WEB_APP_DESIGN.md:4` says the same.
- **Evidence:** a link checker ran over all live markdown at both tags (171 links at v0.7.0). It found
  exactly one link that resolves at v0.6.22 and breaks at v0.7.0, which is this one. `v0.6.22:AGENTS.md:146`
  has the heading; v0.7.0 has none.
- **Fix:** point all three at "Status Meanings" in `.claude/skills/aify-comms/references/operations.md:136`,
  which is the table CLAUDE.md names.

### D3 (P2). A pending pilot's acceptance instrument was deleted
- **Removed:** `scripts/comms_baseline.py` (`6ad8ab13`).
- **Still referenced:** `docs/pilots/2026-08-09-thread-closure-label.md:86` says the label list is
  "exactly `TERMINAL_LABELS` in `scripts/comms_baseline.py`". Line 92 says to measure with
  `scripts/comms_baseline.py --days 7`. The pilot's status is "NOT RUNNING … needs operator
  adoption", so it is pending, not closed.
- **Evidence:** a stem search over v0.7.0 for every deleted file found this pilot doc as the only
  live instruction naming the script. Every other hit is a dated ledger or roadmap that records what
  a script found. The baseline it compares against, `docs/baselines/2026-08-09-comms-baseline-7d.json`,
  is still tracked.
- **Fix:** add one line saying the instrument lives at `v0.6.22:scripts/comms_baseline.py` (restore
  it with `git show` if the pilot is adopted), or mark the pilot retired.

### D4 (P2). Helpers whose last product caller the deletions took
Found by a token index of non-test code at both tags, with comment lines ignored and aify-env and
aify-wrapper counted as readers. It lists declarations kept at v0.7.0 whose product references fell
from more than zero to zero.
- **Controls.** Positive: it reports `writeDaemonPid`, which the source itself says is now test-only.
  Negative: `v0.7.0` against itself prints nothing.
- **Hits:** `declarationSpan` also appears, but it is intended test support (E1 kept it). The other
  hits:
- `mcp/stdio/runtimes.js:203` `resumeFlagsForRuntime`. Its only product caller,
  `extractRuntimeSessionHandleFromArgv`, was deleted in `d8eb3f77`. The docstring at `:196-201` still
  says it is exported "so the argv reader derives from the SAME table". Only
  `resume-flags-cover-every-runtime.test.js` reads it now. Fix: say it is the test's view of the
  table.
- `scripts/comment_spans.py:33` `JS_SUFFIXES` and `:187` `comment_spans()`. Their callers,
  `acceptance-ledger.py` and `deleted-import-census.py`, went in `6ad8ab13`. The one surviving
  importer, `test_no_source_bakes_in_a_host_path.py:31`, takes only `js_comment_spans` and
  `python_comment_spans`. Fix: delete the two.
- `mcp/stdio/hermes-daemon.js:94` `writeDaemonPid`. Its caller, `ensureDaemon`, went in `8e3d3e1e`.
  `:72-75` already says tests use it to seed a pid. Accept it as a test seam, or move it into the
  test.
- The loop-ready marker. `loopReadyFresh` was deleted in `d8eb3f77`. It was the only code that ever
  read `aify-hermes-loop-ready-<agent>`, and it was already test-only at v0.6.22.
  `hermes-delivery-loop.mjs:58-59,125-126` still writes and clears the marker, and no repo reads it
  (aify-env: 0 hits; control: 5 `lib` files mention hermes).
  - `install.sh:1043-1047` still defines `$AifyHermesLoopReadyJs`, with a comment claiming the
    wrapper "health-gates on" the marker. DECISIONS says it does not. aify-wrapper
    `hermes-aify.sh.in:580-583` has the same unused variable.
  - Fix: drop the write, the clear and both variables, or record why the marker stays.

### D5 (P3). Comments that still describe deleted tests, modules or doc entries
- `service/api_core/console_input_queue.py:12-17`: "THE TWO QUEUE FUNCTIONS ARE TWINS, deliberately
  not merged", pinned by `test_console_input_queueing_twins_agree.py`. `d95b9ce6` merged them, and
  `:196` now delegates to `:216`.
- `service/tests/test_terminal_session_inserts_agree.py:11` cites the same deleted test.
- `service/tests/test_recovering_is_live_but_not_active.py:34` cites
  `test_ended_status_sets_agree.py` (merged in `a944050c`).
- `mcp/stdio/tests/child-processes-cannot-inherit-live-carriers.test.js:12` names
  `hermes_carriers.py` as a live guard. It went with the Python discovery code it guarded, and no
  Python code reads a session carrier now.
- `mcp/stdio/adapters/hermes.js:118` "mirrors the service-side parser … `_read_active_session_file`".
  That parser was deleted in `b2451d86`.
- `mcp/stdio/server.js:584` and `service/new_dashboard/app.js:291` explain their wording by a
  `moved-names-resolve` gate that was deleted.
- `mcp/stdio/tests/no-unwatched-oversized-file.test.js:91,148` cite the deleted
  `test_install_keeps_the_delegation_the_host_chose.py` and `scripts/installed-delegation.sh`.
- `mcp/stdio/hermes-gateway.mjs:243` justifies a "DO NOT REMOVE" lever with "See KNOWN_ISSUES.md
  and DECISIONS.md". Both 4403 entries now exist only in `docs/history/` (`d0ab8d66`).
- `service/new_dashboard/index-controls-are-named.test.mjs:39` calls the codex console input "recorded in
  KNOWN_ISSUES.md". It was resolved (`5b9dafee`) and archived.
- `DECISIONS.md:175` counts `AIFY_TERMINAL_CONTROL_POLL_MS` 800 ms as current fleet load. The poll
  was deleted in `d2ab0894`.
- Fix: one sweep that points each comment at the owner that exists now.

### D6 (P3). A still-applicable watch item left KNOWN_ISSUES without a resolution
- **Moved:** "Watch: Managed-claude console churn / sidecar self-exit guard misfire", now at
  `docs/history/KNOWN_ISSUES-archive.md:417-419` (`d0ab8d66`). That commit's message says KNOWN_ISSUES
  "keeps only what is open".
- **Evidence:** a script compared the 44 v0.6.22 sections with the 16 live ones and the archive, and
  only this section and a category header came back with no resolved, fixed, retracted or
  superseded marker. The code the item describes is unchanged: `claude-channel.js:312,322` still
  gates on the immediate `process.ppid`.
- **Fix:** move the one bullet back under a "Watch" heading.

### D7 (P3). The installer's help text contradicts the install guides it points to
- `install.sh:72`: "--client opencode is intentionally disabled (managed OpenCode runs under
  aify-env too)". Rewritten in `696907f4`, the commit that deleted the opencode and pi config
  writers.
- `install.sh:177`: "Managed Pi remains supported through aify-env".
- The guides disagree. `install.opencode.md:6-9` says aify-env "has nothing to start for OpenCode and
  does not offer it". `install.pi.md` says "Treat it as unsupported".
- aify-env `lib/` and `bin/` contain 0 `opencode` hits (control: `hermes` hits in 5 files).
- **Fix:** make the help text match the guides.

### D8 (P3). DASHBOARD_SPEC still lists controls that were removed
- `docs/DASHBOARD_SPEC.md:213,215` list "stop bridge" and "reset to bridge-advertised roots".
  `949e25eb` removed the first and renamed the second.
- The file's header calls its screen inventory historical, hence P3.
- **Fix:** strike the two lines, or fold them into D2's edit to the same file.

### D9 (P3). A test sets an env var nothing reads
- `service/tests/test_the_console_branch_discriminator_separates_both_branches.py:55-59` sets
  `os.environ["AIFY_DB_PATH"]`. The only other reader, `scripts/measure-console-projection.py:166`,
  was deleted in `6ad8ab13`.
- The value stays in the worker's environment after the test.
- **Fix:** use a local variable.

## Deletion groups verified safe

Each of these was checked by a search whose control is stated.

| group | what was checked | control |
|---|---|---|
| HTTP routes | removed `@router.*` decorators in `service/` and `mcp/sse_server.py`: **none**; `route_metadata_inventory.txt`: one line added (`/bridges`), none removed. So no route aify-env or aify-wrapper calls is gone. | the same diff shows the added `/bridges` |
| Response and request JSON keys | quoted dict keys in `service/` (dashboard excluded): 565 at v0.6.22, 569 at v0.7.0, **0 removed** | the reverse diff finds the 4 added (`bridgeKind`, `bridges`, `build`, `liveWithinSeconds`) |
| MCP tool surface | stdio tool names 37 = 37; SSE tools 23 = 23; no `z.` parameter line removed; the `register-tools.mjs` deletion (-65) was comments and blank lines | 37 names read at each tag |
| Doctor check ids | the 12 ids are identical at both tags; the live docs' `spawn-delegation` references still resolve | 12 ids read at each tag |
| Installer flags | only `--delegate-spawns` changed, and it is kept as an alias of `--env-endpoint`; `--no-delegate-spawns` is a no-op. `install_opencode_config` and `install_pi_config` were unreachable at v0.6.22: `--client pi` and `--client opencode` `exit 1` at `v0.6.22:install.sh:206-216` before `register_mcp` | 9 case arms read at each tag |
| 44 deleted non-test files: container template (`setup.bat`, `add-service.sh`, `services/`, `integrations/openclaw`), v1 migration, `control_plane.py`, hermes api_server client, delegation switch, one-off scripts, frozen fixtures, dashboard helpers | no reference from `install.sh`, `redeploy.sh`, `Makefile`, `scripts/*.sh`, Docker files, skills, README, `install.*.md`, CLAUDE.md or ARCHITECTURE.md; `make setup` runs `setup.sh`, which exists | a stem search finds the D5 comment hits; the v1 search finds 6 files at v0.6.22 |
| Sibling repos | every aify-comms path aify-env and aify-wrapper name (24 in aify-env): **none** deleted in 0.7; no deleted export or env var appears in either repo; the wrapper calls `hermes-daemon-cli.js` only with `stop`, and `stop` is kept | feeding `service/control_plane.py` through the same check returns DELETED-IN-0.7 |
| Pre-0.7 bridges still running after an upgrade | the deleted modules had only static importers at v0.6.22 (`doctor-predicates.js:14`, `hermes-daemon.js:27`, `hermes-version.js:12`), so a process already running is unaffected; `install.sh:318-325` builds a fresh staging tree and swaps it in, so no stale module survives | a search of the same tree finds dynamic imports in 3 other files |
| Env vars no longer read (16) | no setter in `install.sh`, aify-env or aify-wrapper; the only mentions left are dated specs (the hermes api_server contract) and `DECISIONS.md:175` (D5) | `AIFY_ENV_REPO` found in the wrapper tests and the CLAUDE.md test notes |
| Recorded keeps (plan backlog E2, E6, E11, E12, E13, E14, E19; E1, E3, E5, E7 keep lists; usage collectors parked 2026-09-04) | all present at v0.7.0: `service/containers/`, `routers/containers.py`, `environment_claim.py`, `superseded_bridge_stops.py`, the `/environments/controls/claim`, `report-dead`, `console-working` and `usage-source` routes, `hermes-managed-gateway-session.js`, `hermes-session.js`, `codex-session.js`, `mcp/sse_server.py`, the opencode files, `pi-session.js`, `declarationSpan`/`functionSpan`/`BROWSER_GLOBALS`/`moduleScopeBrowserRefs`, `stopDaemon`/`defaultKillByPort`, `comment_spans.py`, `undefined_name_sweep.py`, `normalize_machine_id_casing.py`, the `~/.claude`/`~/.codex`/`~/.hermes` mounts, `collectOnce`/`collectConsumptionOnce`. No deletion contradicts a recorded keep | each is read by name at the tag |
| Borrow shims and `control_plane.py` | 0 `_borrowed_*` accessors and 0 product imports of `control_plane` left | at v0.6.22, 4 files define accessors and `api_v2.py` and `terminals.py` import `control_plane` |
| `agent_live_state` | `service/db.py:277` drops it at startup, and `test_the_retired_live_state_table_is_dropped.py` covers that; no writer is left | the table is seeded in the test's own control |
| Twins merged with their agreement tests | exactly one definition each now for `_ANSI_RE`, `createDeferred`, `parseProcLines`, `defaultKillTree` and `DelegatedManagedController`, so the deleted agreement tests have nothing left to compare | 4 `createDeferred` definitions at v0.6.22 |
| Dashboard | the deleted `inspect()` has 0 callers (4 files at v0.6.22); every name that lost `export` is still declared locally; nothing in the live UI names "Stop bridge" apart from the docs in D8 | the v0.6.22 count |
| DECISIONS and KNOWN_ISSUES moved to `docs/history` | the entries code comments cite (long-poll claims, read-path repairs, the snapshot replay, the 2026-09-22 routing ruling) are still live; the exceptions are in D5 and D6 | at least one live match for each entry checked |

## What I could not see

- HTTP calls made by hand or from other machines.
- Hand-set env vars that 0.7 stopped reading: `CLAUDE_COMMAND`, an alias of `AIFY_CLAUDE_COMMAND`;
  `AIFY_AGENT_NAME`; `AIFY_HERMES_APISERVER_KEY` and `_URL`; `AIFY_TERMINAL_CONTROL_POLL_MS`.
- Anyone running the deleted scripts, or the retired `node hermes-daemon-cli.js <agentId>` ensure
  form, by hand.
- Launchers rendered by an older installer on other hosts.
- **Instrument limits:**
  - The orphan index (D4) covers top-level Python and JS declarations only, not methods.
  - It drops only whole-line comments.
  - It treats any token match as a reader, so it can miss orphans but does not invent them.
