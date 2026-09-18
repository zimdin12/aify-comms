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

## Left, in order of value

1. **Share launcher renders.** About 44 `install.sh --emit-wrappers` renders run across the Python
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
3. **Python duplicates, about 300 tests.** The named areas were worked on 2026-09-18: 77 tests
   removed (5,425 -> 5,348 passing, 6 skipped both times), each after reading it and the test that
   covers it. Done:
   - `_is_operator_closed_contract` deleted with its 10 tests: it fired only when `require_reply` was
     already false, so `_contract_reply_expected` returned the same value without it.
   - removed as duplicates: status engine (1), `test_a_dead_agent_is_not_promoted_to_working.py`
     (deleted; its two derived tests moved into `test_status_with_dispatch.py`), terminal vocabulary
     (4), browser origin (11), dispatch control claim (11; the IO test gained `failed`/`cancelled`),
     usage cache (5), channel offline replay (3), hermes channel routing (3), contract receipt
     repair (5), outbound activity (4), session mode switch (5), tombstone timestamp (4, they tested a
     copy of the predicate), heartbeat arbitration (4), console lease pin (1).
   - fixed so they can fail, each watched red under a product mutation: the dead-terminal no-logs
     test (wrong logger), the fleet pulse board (agent was never online), the most-recent-send test
     (asserted only truthiness).
   The rest of the ~300 was not enumerated in this file and is still open.
4. **JS duplicates, about 60 tests**: five regex tests in `claude-wrapper-determinism` that
   `claude-wrapper-behaviour` covers by running the wrapper; the codex bypass asserted three times;
   `destructive-tool-descriptions.test.js`; about 16 "server.js kept none / registered once" import
   `deepEqual`s; four hand-typed "injected, not imported" lists in dashboard tests that one
   "no module imports app.js" gate would replace.
5. **Two more dead modules**, entangled with other tests: `hermes-channel.js` (no importer; its tests
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
