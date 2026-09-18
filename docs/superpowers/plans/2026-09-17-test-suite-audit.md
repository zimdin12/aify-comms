# Test suite audit, 2026-09-17

The operator asked for fewer duplicate tests, less slop and a faster suite. Three read-only audits
covered the Python service tests, the install and doc-gate tests, and the bridge and dashboard JS
tests. This file records what was done and what is left, so the rest can be worked in batches.

## Done in v0.6.12

| change | effect |
|---|---|
| test-process socketpairs close abortively (`service/tests/conftest.py`) | peak TIME_WAIT during a full run 12,803 -> 374; ends the 56-minute hang |
| `dead_imports.py` scans the tree only for modules with an unused import | 54 s -> 9 s |
| `components.sh` finds its directory without `dirname` | test file 33 s -> 6.5 s; the empty-PATH run stopped reading a wrong `missing` |
| install.sh skips the shim PATH step on a render, and the step now works | one powershell start-up less per render; the step had never added anything |
| 8 Python test files deleted (duplicates, SQL typed into the test, a mode removed long ago) | 39 tests |
| 5 dead bridge modules deleted with their tests | 5 suites |
| a dashboard gate that could never fail (`\b` in a template string) | fixed, mutation-checked |
| an uncleared 8 s timer in `bridge-exits-when-its-terminal-goes` | 8.7 s -> 0.8 s |

Suites after: Python 5,833 in 89 s (was 5,872 in 125 s), bridge 371 files in 65 s, dashboard 1,784.

## Done 2026-09-18 (items 1 and 5 below)

| change | effect |
|---|---|
| seven Python files read `_launchers.render()` instead of rendering privately; the service-key file renders once, not three times | Python `--emit-wrappers` runs 45 -> 41 in a full run, 12 -> 8 in those seven files (counted with a subprocess hook; the per-process cache means the exact figure depends on which worker a file lands on) |
| the four `*-wrapper-determinism` files render once each | bridge renders in those files 10 -> 4 |
| `hermes-channel.js`, `terminal-env.js`, `child-env-hygiene.mjs` deleted with their tests | no importer in `mcp/stdio`, `install.sh`, the launcher templates or aify-env; the derived "every identity name is written or stripped" check moved onto `launch_env.py` |

Not done: rendering once per session on the xdist controller. A cross-worker cache was built and
measured: it took `_launchers` renders from 10 to 6, but under `--dist loadfile` the other workers
WAIT for the render instead of doing it, so wall time did not move (subset 53 s vs 52 s; full-suite
wall on this host swung 138-232 s for the same code). Most remaining renders (29 of 41) are in files
that need a real directory: `a_reinstall_leaves...` (8), `install_keeps_the_delegation...` (7),
`both_endpoint_readers_agree` (5), `installed_launchers_carry_a_real_fingerprint` (4).

## Left, in order of value

1. **DONE 2026-09-18 (see above).** **Share launcher renders.** About 44 `install.sh --emit-wrappers` renders run across the Python
   suite and at least 13 more across the bridge suite; only 5 are distinct (claude, codex, hermes, pi,
   claude with `--mcp-transport sse`). Render once per session (`pytest_sessionstart` on the
   controller; once in `run-all.mjs`) and hand the text or directory to the tests. Python files to
   move onto `_launchers.render()`: `test_install_claude_session_validate`,
   `test_install_codex_session_rediscover`, `test_install_pi_session_rediscover`,
   `test_install_hermes_leak`, `test_install_hermes_session_rediscover` (renders twice),
   `test_wrapper_marker_names_the_package_that_stamped_it`, `test_no_launcher_carries_the_service_key`
   (three identical renders). Bridge: `claude-`, `hermes-`, `pi-`, `codex-wrapper-determinism`.
2. **Real sleeps.** `managed-wrapper-cache.test.js` waits five real 5.1 s timers (mock `Date`);
   `session-fixes` waits a 5 s grace; `doctor-actually-runs` runs `doctor.js` four times (share one
   report); `pi-runtime` makes 21 real `taskkill` calls (inject a killer).
3. **Python duplicates, about 300 tests**, listed per area by the audit. Delete only after reading both
   sides, as the eight files above were:
   - status engine: `test_status_engine::test_managed_alive_is_online_never_idle` duplicates
     `::test_managed_online_when_alive_worker_present`; most of
     `test_a_dead_agent_is_not_promoted_to_working.py` repeats `test_status_with_dispatch.py`.
   - terminals: 4 tests in `test_terminal_status_vocabulary.py` repeat `test_terminal_status_transition.py`.
   - browser origin: 8 tests in `test_a_page_on_another_site_cannot_drive_this_service.py` repeat
     `test_one_policy_decides_whether_a_browser_may_drive_this_service.py`.
   - dispatch: 10 tests in `test_dispatch_control_claim.py` repeat `test_dispatch_controls_claim_io.py`.
   - tests that cannot fail: `test_spawn_dead_terminal_finalize::test_nothing_is_logged_when_there_is_nothing_to_report`
     (listens on the wrong logger), `test_console_working_lease::test_lease_ttl_spans_the_keepalive_cadence`
     (pins a file deleted in v0.6.2), `test_chat_analytics::test_fleet_pulse_window_and_board`
     (asserts only inside an `if` that is never true).
   - dead product branch: `_is_operator_closed_contract` can never change a result; about 11 tests cover it.
4. **JS duplicates, about 60 tests**: five regex tests in `claude-wrapper-determinism` that
   `claude-wrapper-behaviour` covers by running the wrapper; the codex bypass asserted three times;
   `destructive-tool-descriptions.test.js`; about 16 "server.js kept none / registered once" import
   `deepEqual`s; four hand-typed "injected, not imported" lists in dashboard tests that one
   "no module imports app.js" gate would replace.
5. **DONE 2026-09-18 (see above).** **Two more dead modules**, entangled with other tests: `hermes-channel.js` (no importer; its tests
   plus part of `hermes-gateway-liveness.test.js`) and `terminal-env.js` + `child-env-hygiene.mjs`
   (held equal to `service/api_core/launch_env.py` by `test_the_launch_environment_has_one_owner.py`).

## Decided

**RETIRED 2026-09-18 on the operator's word (v0.6.13).** The family below was deleted with its
fixtures, `extract_method.py` and its gates; product comments naming a proof now say it was retired.

**The `*_split_is_inert` family**: 34 files, 288 tests, about 6,600 lines, 41 frozen function copies
under `service/tests/data`, `extract_method.py` (1,611 lines) and `test_every_extraction_claim_has_a_proof.py`.
Each proves an extraction was byte-inert when it happened; by its own docstring it says nothing about
whether the code is correct, and every later behaviour change to those functions must be declared in
it (this release paid that twice). The v0.5.x refactor series it guarded closed on 2026-08-17.
It was the largest single reduction available.

**Prose pins** that guard wording rather than behaviour: `test_the_oversized_table_names_the_real_two.py`
(18 tests over a CLAUDE.md table), `test_the_ranked_decision_list_counts_itself.py`,
`test_the_ledgers_receipts_name_trees_that_contain_them.py`, `test_comments_name_the_constant_the_code_uses.py`.
Link, removed-command and skill-name gates are worth keeping; they have caught real defects.
