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
2. **Real sleeps.** DONE 2026-09-18 except `pi-runtime`: the cache test mocks `Date`, the codex
   cancel grace mocks `setTimeout`, `doctor-actually-runs` shares one report, and the dashboard
   resize debounce is mocked. Each was mutation-checked red. `pi-runtime` is left: its 21 `taskkill`
   calls cost 12 s of a 15.6 s run (measured), but the sessions are built inside `launchRuntimeRun`
   and `pi-session.js` is 993 lines, so there is no clean place to inject a killer yet.
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
   The rest was worked through file by file later the same day: see the two "Python duplicates"
   sections at the end, one per half of `service/tests`.
4. **JS duplicates.** PARTLY DONE 2026-09-18: the claude, codex, hermes-resume, destructive-tool,
   retry-nonce, strict-mcp, refresh-chip, status-chip and prose-pin duplicates. Judged NOT duplicates
   after mutation runs: the xterm source checks in `app.test.mjs` other than safeFit/rAF (no other
   dashboard test goes red when the font-await supersession guard, `ownsPty`, the remount identity
   check or the xterm options are broken), the third `hermes-daemon-default-killtree` test, and
   `install-node-pty-recovery`. The two remaining jobs are DONE 2026-09-18; see "Item 4, finished" below.
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

**Prose pins, DELETED 2026-09-18 (v0.6.15)**: `test_the_oversized_table_names_the_real_two.py`
(19 tests over a CLAUDE.md table, 8 s), `test_the_ranked_decision_list_counts_itself.py` (5),
`test_the_ledgers_receipts_name_trees_that_contain_them.py` (5) and
`test_comments_name_the_constant_the_code_uses.py` (4 tests, 22 s, never fired since written). Each
guarded the wording of a document, not behaviour. The CLAUDE.md table the first one guarded was a
hand-copied cache of a lookup, so the table went too: CLAUDE.md now gives the one-line command that
ranks files by size, and its 190-line section is 38. Link, removed-command and skill-name gates are
kept; they have caught real defects.

## Item 4, finished (2026-09-18)

**Bridge: one derived gate replaces the per-slice ownership census.**
`mcp/stdio/tests/each-name-has-one-owner.test.js` asks two questions over every bridge module
(`bridgeSources()`), not over a typed list of names:

- every `server.tool("<name>"` is registered by exactly one module, every call names its tool, and the
  set found in source equals the set `registerAllTools` registers at runtime (so a registrar that stops
  calling a group's wrapper goes red);
- no exported name is declared at top level by a second module. 27 such forks already exist (mostly
  `claude-channel.js`, a standalone process, and `MACHINE_ID` in nine places); they are recorded exactly
  and may only shrink.

`declaredNames(source)` was added to `tests/bridge-sources.mjs`, sharing its patterns with
`declaringModules`; the export parser is `exportedNames` from `tests/missing-imports.mjs`. Negative and
positive controls are in the file.

Removed or trimmed, all now covered by that gate: the "server.js kept none / registered exactly once"
checks in `agent-reporting-tools`, `artifact-tools`, `channel-tools`, `dashboard-tool`,
`dispatch-tools`, `environment-tools`, `inbox-tools`, `lifecycle-tools`, `search-tool`,
`self-record-tools`, `usage-tool` and `registration-tool`; the `declaringModules` deepEquals and
server.js redeclaration checks in `aify-service-endpoint`, `bridge-agent-state`, `bridge-build`,
`bridge-instance`, `dedupe`, `hermes-gateway-config`, `launch-identity`, `local-active-run`,
`local-store`, `agent-heartbeat`, `agent-summary`, `resident-gateway-status`, `runtime-adapter`,
`registration-inputs`, `run-controls`, `safe-name` and `session-mode`. Kept: every `isUsedInBridge`
assertion, and the checks for names the gate cannot see because they are not exported
(`ACTIVE_SERVER_URL`, `summarizeContract`, `summarizeEnvironment`, the three
`claude-turn-detector-state` bindings) or because they name a retired implementation (`readBuildTag`).
`pi-terminal-frame.test.js` is deprecated and untouched.

Removing the `local-active-run` census left `clearLocalActiveRun` named by no test, which
`every-export-is-named-by-a-test` caught; it now has a direct behaviour test.

Mutation evidence: a `function clearLocalActiveRun() {}` plus a leftover
`server.tool("comms_dashboard", …)` appended to `server.js` turned the fork and duplicate-tool tests red;
deleting `registerSelfRecordTools(server, z);` from `register-tools.mjs` turned the source-equals-runtime
test red (naming `comms_status` and `comms_describe`); deleting `ACTIVE_RUNS.delete` from
`local-active-run.mjs` turned the new direct test red. Each file was restored with `cp` from a backup.

**Dashboard: `service/new_dashboard/no-module-imports-app.test.mjs`** derives the module population
(every non-test `.js`/`.mjs` beside `app.js`) and fails if any imports `app.js` by static, bare,
dynamic or re-export form. It replaced the four hand-typed "injected, not imported" lists in
`refresh-cycle`, `run-inspector`, `session-console` and `xterm-mount` (the last keeps its
"arrives as a parameter" check). Coverage change, stated: importing one of those names from a SIBLING
that does not import `app.js` was forbidden by the lists and is allowed now, since it drags no `app.js`
code in. Mutation: `import { renderAll } from "./app.js";` at the top of `refresh-cycle.mjs` turned the
gate red naming that file; restored with `cp`.

## Python duplicates, files `test_m*` to `test_z*`, 2026-09-18

This half went from 2,808 to 2,605 collected tests, a drop of 203. The before figure is from a
collect-only run at `d42431a0` and the after figure from the same run at the end of this work. The
bar for each removal:

- the removed test's property is named;
- a named surviving test proves the same property;
- a product mutation turned both the survivor and the removed test red;
- the product file was restored with `cp` from a backup.

Two files were deleted: `test_managed_via_wrapper_adapter.py` and `test_settings_manual_mode.py`.
Only dated plan docs still name them. The four prose-pin files and the `deprecated-runtime: pi` files
were not touched. Survivors below are written as `file::test`, with the file's `test_` prefix
dropped.

**Installer and service config (9).**

| Removed | Survivor |
|---|---|
| `service_example_config::the_owned_set_is_not_empty`; `service_json::every_refused_key_is_a_real_config_attribute` | `service_json::the_refused_set_matches_what_the_stamp_actually_writes`, plus the hostile-load test |
| `redeploy_keeps_delegation::a_delegated_launcher_reports_its_endpoint` | `::a_value_that_means_on_is_on[1]` |
| `comms_command::THE_INSTALL_RECORD_IS_STILL_READABLE` | `::the_reader_actually_recovers_the_endpoint`, which is strictly stronger: blanking the endpoint reddens only the survivor |
| `comms_command::no_api_key_is_baked...` | `no_launcher_carries_the_service_key::A_CONFIGURED_KEY_REACHES_NO_LAUNCHER` and `::no_launcher_declares_a_key_variable_at_all` |
| `installer_asks::it_matches_the_read_only_path...` | `::with_no_key_and_no_terminal...` and `installer_finds_the_key::no_key_anywhere_is_an_ANSWER...` |
| `components::the_probe_can_say_both_yes_and_no` | `::missing_only_lists_nothing_when_everything_is_present` |
| `wrapper_marker::the_marker_is_present_and_is_not_still_a_placeholder` | `::the_marker_carries_the_wrapper_package_version` |

Also merged: bare-run and unknown-option refusal became one test with two argvs.

**API, SSE, websocket, usage and roster (32).**

| Removed | Survivor |
|---|---|
| 9 `websocket_is_not_a_way_around_the_front_door` origin cases | the matching `one_policy_decides_whether_a_browser...` tests; the case-insensitive, unparseable, IPv6 and no-Origin inputs were merged into them |
| `ws_connection_manager` exact duplicate no-op broadcast | `::test_broadcasting_with_no_connections_is_a_no_op` |
| `ws_connection_manager` two dead-client tests | `::a_client_that_dies_mid_fanout_is_still_the_one_disconnected` |
| 3 `sse_api_client_fails_closed` pass-through tests | their `sse_api_client` twins |
| `route_inventory` snapshot and not-empty tests | `route_metadata_inventory::route_metadata_matches_the_snapshot` |
| `managed_via_wrapper_adapter.py` (6 tests) | `api_v2_regressions::managed_via_wrapper_for_runtime_handles_bool_list_none` |
| `runtime_adapter_contract::EVERY_adapter_carries_the_agent_id...` | `resume_command_carries_agent_id::every_wrapper_runtime_emits_aify_agent` |
| `usage_cache::fresh_pool_keeps_numbers` | `usage_cache_no_mutation::a_pool_that_becomes_fresh_again...` |
| `settings_manual_mode.py` (3 tests) | `api_v2_regressions::settings_include_dashboard_appearance_defaults` |
| `unmeasured::the_two_states_are_DISTINGUISHABLE` | `::NEVER_MEASURED_says_so` and `::MEASURED_AND_EMPTY...` |
| `usage_quota_classification::the_whole_payload_is_flagged...` | `usage_cache::elapsed_reset_window_blanked_even_with_fresh_post` |

**Terminal and console (39).**

| Removed or merged | Survivor |
|---|---|
| `terminal_status_transition` empty-string refusal | `::a_finished_terminal_is_never_resurrected` |
| `terminal_control_status` failed-resize duplicate | `::a_FAILED_control_implies_failed` |
| the stop-control tests shared by `stop_control_survives_reconcile` and `stop_control_degraded_environment` | merged into online and degraded subTests; `survives::the_OTHER_sweep_also_spares_a_stop` is kept because product comments cite it |
| `stop_worker::real_terminal_gets_a_bridge_side_stop_control` | `::every_live_terminal_for_the_agent_is_stopped` |
| `stuck_stopping` pairs and `two_bridges` loser test | merged |
| two `virtual_terminal_id` mint tests | `::every_terminal_id_mint_uses_a_known_prefix` |
| 3 `terminal_diagnostics` first-fatal tests | `::yields_the_root_cause_line_from_the_real_incident` |
| `terminal_snapshot` reset-and-clear test | `terminal_ansi::the_snapshot_opens_by_resetting_and_clearing` |

Also removed, each with a named survivor in the slice's commit message:

- the ANSI wide-char, awaiting-input, idle-hint and resize pairs;
- the size-poll flush test;
- the live-screen unnumbered and no-ESC tests;
- the served-seq CONTIGUOUS test and the one-generation control;
- the write-queue order and retry tests, now covered by the `ONE_write` and requeue tests;
- the write-path trim tests, now covered by `trim_terminal_output`;
- the inserts-agree REASON test, the listing route-registered test and the discriminator controls;
- the fallback NULL-pointer test;
- the rename tests, merged three into one.

**Session, spawn and reconcile (45).**

| Removed | Survivor |
|---|---|
| `session_identity_sticky` 409 tests | `session_resolve_pair::both_routes_409_when_there_is_nothing_pending` |
| `resolve_pair` adopt, retain, 404 and 410 tests | `sticky::confirm_repins_to_pending`, `sticky::keep_clears_pending...`, `removed_agent_is_refused_everywhere` |
| `session_control_refusals` cold-start reason | `session_restart_refusal::a_non_coldstartable_runtime...`, which is the stronger test |
| `session_mode_fsm` active-run block | `session_mode_switch_refusals::an_active_dispatch_run_blocks_the_switch...` and the force test |
| `session_mode_audit` event tests | `switch_refusals::the_switch_is_recorded_as_an_audit_event` |
| `sessions_list` duplicated filter and env-degraded tests | `::ACTIONABLE_terminal_sessions_stay_visible`, `::cleanly_finished...`, and the `environment_status_vocabulary` and `env_status_fails_open_by_decision` tests |
| `spawn_requests_io` NO_websocket and NO_CURRENT_BRIDGE | `::a_QUEUED_request_is_claimed_by_the_polling_bridge` |
| `reap_stale_orphan_bridges::fresh_bridge` | `::just_past_the_floor_is_safe`, which is the stronger test |
| `start_agent_live_session_gate` constant and lost tests | the fixed `::every_terminal_status_leaves_the_agent_startable` |

Also removed:

- `spawn_request_list_pairs_each_spec::many_rows`;
- the prompts default, slim and saving tests;
- the bounded-rescue duplicate;
- the orphaned and reconcilable same-input tests;
- the supersession-guard scanner test;
- the two-tiers list-replace test and the row-says DISAGREE test;
- the model repair test and the upsert just-written test;
- the two dashboard pointer tests, which only checked that a JS file exists.

Merged: the restart, start-refusal, undelivered, dead-terminal historical-console, spawn-brief,
model-shape and two-tiers groups.

**Status and turn (44).**

| Removed | Survivor |
|---|---|
| `status_engine::hermes_working_while_delivering` | `::working_when_in_turn` |
| mocked `status_taxonomy` available test | the unmocked twin |
| the `status_decision_branches` sidecar-died, booting, blocked-outranks and counter tests | the booting-query-once and blocked-branch tests |
| three `status_starting` tests | `::every_derivable_status_is_declared`, `::just_inside_the_window` and the flag tests |
| the `status_engine_integration` turn tests | the `turn_start_attribution` superseded-guard, REAL-clear and LIVE-clear tests |
| the `turn_end_noop_fastpath` pair | `turn_start_attribution::the_NO_OP_path_broadcasts_NOTHING` |
| the `status_in_turn_ceiling_is_anchored` latch and anchor tests | `::the_boundary_is_the_backstop...`, `::NO_event_kind_moves_the_anchor...`, `TheTwoTABLES::the_discriminators_are_not_vacuous` |
| the `turn_busy_delivery_ceiling` duplicates | the `turn_liveness_policy` anchor, future-timestamp and absolute-bound tests |
| four `status_deliverability` hermes and env-bridge cases | the claude twins, which take the same `_worker_liveness_for` path |
| two `status_broadcast` manual and derived tests | `::a_manual_status_is_recognised_CASE_INSENSITIVELY` and `::an_ORDINARY_status_is_not_treated_as_manual` |
| two `status_refresh_is_not_n_plus_one` cost tests | the read-ONCE tests |

Also merged: the three `resident_hermes_missing_handle` tests into one.

**Messaging, reply contract, ntfy and unread (34).**

| Removed | Survivor |
|---|---|
| the `tombstone_resurrection_needs_a_real_timestamp` copy-of-predicate test | `tombstone_resurrection_gate::a_bridge_with_no_real_start_time_cannot_restore`, which now runs the six words through the real gate |
| `untrusted_subject` quoting and empty-subject pairs | cross-merged into the surviving file's exact-output tests |
| the `reply_reminders` cadence tests | `reply_contract_reminder_cadence` |
| the `ntfy_send_path_wiring` health source pins | `ntfy_relay::HealthEndpointBlastRadiusTests` |
| four `ntfy_relay` secret tests | `ntfy_url_containment` |
| four `orphan_unread_cleanup` tests | `::a_mixed_table_loses_ONLY_the_orphans` |
| three `three_unread_counts` tests | one exact 1/2/5 test |
| `recent_messages_pages_backwards` plan test | `the_recent_messages_poll::it_walks_the_timestamp_index` |
| `message_idempotency` truncated-body test | `messaging_refusals::every_send_path_refuses`, which gained a no-row check |
| two `unsend` tests | the `operator_privilege_must_be_proven` tests |
| the `search_scope` direction tests | merged |
| two `message_timestamps` source pins and one retention test | `retention::a_message_past_the_window_is_expired` |

**Tests that could not fail, now fixed and each watched red under a mutation:**

- `runtime_adapter_contract::every_shipped_adapter_declares_a_HUMAN_display_name` (it asserted truthiness only);
- `row_capabilities_table::a_null_runtime_config...` (it never stored a null);
- `stop_control_degraded` non-stop mismatch (its terminal was already stopping);
- `terminal_awaiting_input_hint` spinner-verb footer (another alternative matched first);
- `trim_terminal_output` (it asserted truthiness only);
- `start_agent_live_session_gate` (the `ended_at` seed masked the gate);
- `ready_status_endpoint::patch_ready_false...` (it read the echoed request, not the stored bit).

**Deleted because they could not fail and a survivor covers them:**

- `unmeasured::rows_still_reach_the_summary`;
- `runtime_adapter_contract::the_seal_leaves_the_environment...` (it tested `mock.patch.dict` only);
- `status_broadcast` polled manual END_TO_END;
- `turn_busy_delivery_ceiling::zero_age_still_holds`;
- the `status_deliverability` hermes stale-sidecar test (it had no console);
- `message_timestamps::a_string_cutoff_would_delete_every_message` (it tested SQLite only).

**Judged NOT duplicates after mutation, and kept:**

- `machine_id_casing` and `machine_id_normalisation` test different functions.
- The SSE channel and agent send tools are separate copies of the logic.
- `sse_container_tools` is the only test covering non-`comms_*` tools.
- `the_env_plugin_*` were kept.
- `installer_asks` weak-key-on-`--ask` exercises the `--ask` branch.
- `test_no_orphaned_imports_in_control_plane` versus `test_no_dead_imports`: both go red on an unused
  import, but their reach rules differ and the tree-wide gate's docstring records "both gates stay".
- `sessions_list::history_cannot_evict_a_live_session...` is weak: it goes red only when two things
  break at once.
- `reply_reminders::no_further_reminder_once_answered` and the `read_receipt` genuine-merged test
  each pin a branch nothing else pins.
- Each slice's commit message lists the rest.

**Findings outside test code, for a decision:**

- `service/tests/data/route_inventory.txt` is no longer read by any test. It is still listed in
  `test_fixtures_are_tracked.py`, which is in the other half, so delete the two together.
- `_row_capabilities` raises `AttributeError` for a hermes row whose `runtime_config` is JSON `null`.
- A comment in `status_inputs.py` says `test_resident_hermes_missing_handle_status.py` pins
  `resident_bridge_stale = True`. It does not: removing that assignment stays green.
- The prefetch at `routers/analytics.py:182` is not reached by any test.
- The `orphan_messages.py` docstring calls `m.to_agent IS NOT NULL` load-bearing. Dropping it alone
  changes no result.
- In `_managed_via_wrapper_for_runtime`, the pi branch is redundant with the delivery-mode gate.

## Python duplicates, files `test_a*` to `test_l*` and `scripts/tests`, 2026-09-18

This half went from 2,419 test functions (2,452 collected) to 2,035 (2,070 collected): 384 functions
fewer, measured by counting `def test_` and by a collect-only run at `d42431a0` and at the end of
this work. Those figures exclude `test_comments_name_the_constant_the_code_uses.py`, which main
deleted as a prose pin. One test file was deleted, `test_default_settings_plan4.py`, along with one
data file, `service/tests/data/route_inventory.txt`, whose only reader went with the m-z
duplicates. `test_api_v2_regressions.py` went from 390 tests to 314.

By manifest: 251 tests deleted, 179 merged into 74 tests (as `subTest` or `parametrize`, every input
case kept), 10 fixed in place, and 5 rewritten because they could not fail.

**How each entry was proved.** Eight agents each worked one slice in an isolated worktree. For every
entry an agent recorded the candidate, the survivor and one product mutation. A separate replay
harness then applied every mutation twice:

- on the unchanged base tree, where the candidate had to FAIL;
- on the merged tree, where the survivor had to FAIL.

In both passes the same nodes had to PASS unmutated first. Every product file was restored by
copying back its bytes. The final results were 409 of 409 survivor groups red and 390 of 397
candidate groups red. The 7 candidates that stayed green are exactly the deleted or merged entries
recorded as unable to fail.

**Three defects in the instrument, found before its results were used:**

- Subtest failures print as `SUBFAILED(...)` or `SUBFAILED[...]`, not `FAILED`, and were at first
  read as green.
- Python's `.pyc` check compares source mtime in whole seconds plus size. Two mutations of equal
  length applied within one second therefore reused the first mutation's bytecode. That turned a
  real red green, and a stale mutated `.pyc` could have outlived its restore. The harness now runs
  with `PYTHONDONTWRITEBYTECODE=1` and purges the mutated module's cache around each run.
- A control run whose node id no longer exists exits 4 and runs nothing. Two of those turned out
  to be real mutual deletions.

**Mutual deletions, found by the control and re-pointed, each replayed red:**

- slice 1 relied on `a_refused_heartbeat...::the_row_still_reflects_the_ACCEPTED_bridge_after_a_refusal`,
  which slice 4 merged into `::a_REFUSED_claim_says_refused_and_names_who_holds_it`;
- slice 8 relied on `both_endpoint_readers_agree::both_readers_find_the_endpoint_in_a_launcher...`,
  which slice 5 folded into `::both_readers_find_the_endpoint_for_every_client_the_installer_renders`;
- slice 1 relied on `turn_busy_delivery_ceiling::abandoned_turn_busy_past_ceiling_releases_delivery`,
  which the m-z work deleted. It now points at `::the_ceiling_is_the_one_constant_not_a_second_number`,
  which the m-z work kept.

In the other direction, none of the tests removed here is named anywhere in the m-z record or its
commit messages. The check searched both full names and abbreviated prefixes, with controls in each
direction. The four `sessions_list` environment-ageing tests the m-z work removed named this half's
env-status files as survivors, and all four properties still go red after both halves: degraded
ageing and decision states in `env_status_fails_open_by_decision` and
`environment_status_vocabulary`, and fresh-online in `status_deliverability`.
`environment_status_vocabulary::only_the_heartbeat_states_age_out` reads the same
`_ENVIRONMENT_HEARTBEAT_STATUSES` it checks, so it cannot catch `degraded` being dropped from that
set; `env_status_fails_open_by_decision::a_DEGRADED_environment_still_ages_out` is what does.

**Left weak, not fixed:**

- `api_v2_regressions::status_is_pure_event_long_ceiling_not_short_window`: no product mutation was
  found that turns its behaviour check red.
- The orphan-reaper assertion of `api_v2_regressions::managed_hygiene_keeps_live_console`: two
  guards each spare the row.
- `environment_actionable_sql::it_binds_exactly_one_parameter` errors at setup rather than failing,
  so it cannot be proved red-red.
- In `orphan_messages.py`, `m.to_agent IS NOT NULL` changes no result. The m-z record also notes
  this.

Survivors are written as `file::test`, with the file's `test_` prefix and the test's `test_` prefix
dropped.

| candidate file | removed or merged | survivor |
|---|---|---|
| `a_capability_sequence_does_not_become_screen_content` | `a_chunk_ending_in_a_partial_private_csi_does_not_swallow_later_text` | covered by `a_capability_sequence_does_not_become_screen_content::feeding_the_sequence_one_byte_at_a_time_still_removes_it` |
| `a_channel_message_is_never_invisible_to_both_readers` | `a_null_recipient_row_is_in_the_transcript` | covered by `a_channel_message_is_never_invisible_to_both_readers::the_transcript_count_agrees_with_the_rows_it_returns` |
| `a_channel_message_is_never_invisible_to_both_readers` | `an_empty_string_recipient_row_is_ALSO_in_the_transcript` | covered by `a_channel_message_is_never_invisible_to_both_readers::the_transcript_count_agrees_with_the_rows_it_returns` |
| `a_channel_message_is_never_invisible_to_both_readers` | `a_fan_out_copy_is_NOT_in_the_transcript` | covered by `a_channel_message_is_never_invisible_to_both_readers::the_transcript_count_agrees_with_the_rows_it_returns` |
| `a_claimed_stop_survives_the_removal_that_races_it` | `the_claim_reports_only_the_rows_it_actually_won` | covered by `a_claimed_stop_survives_the_removal_that_races_it::A_ROW_ANOTHER_CLAIMER_TAKES_MID_FLIGHT_IS_NOT_REPORTED_AS_OURS` |
| `a_claimed_stop_survives_the_removal_that_races_it` | `POSITIVE_CONTROL_the_claim_returns_the_stop_when_nothing_races_it` | covered by `a_claimed_stop_survives_the_removal_that_races_it::A_SUCCESSFUL_CLAIM_STILL_CARRIES_ITS_PAYLOAD_WHEN_THE_REMOVAL_WINS` |
| `a_contract_filter_does_not_lose_rows_to_the_limit` | `an_unfiltered_query_still_honours_its_limit` | covered by `a_capped_list_says_it_is_capped::EVERY_ROUTE_REPORTS_TRUNCATION_WHEN_IT_IS_TRUNCATED` |
| `a_contract_filter_does_not_lose_rows_to_the_limit` | `the_fixture_produces_both_states` | covered by `a_contract_filter_does_not_lose_rows_to_the_limit::the_row_count_does_not_depend_on_the_page_size` |
| `a_control_writes_the_terminal_status_its_own_sql_compares` | `a_mixed_case_stop_is_stored_as_the_readers_expect` | merged into `a_control_writes_the_terminal_status_its_own_sql_compares::a_mixed_case_stop_is_stored_as_the_readers_expect` |
| `a_control_writes_the_terminal_status_its_own_sql_compares` | `a_mixed_case_stop_still_stamps_stopped_at` | merged into `a_control_writes_the_terminal_status_its_own_sql_compares::a_mixed_case_stop_is_stored_as_the_readers_expect` |
| `a_control_writes_the_terminal_status_its_own_sql_compares` | `a_mixed_case_stop_still_returns_the_session_to_managed` | merged into `a_control_writes_the_terminal_status_its_own_sql_compares::a_mixed_case_stop_is_stored_as_the_readers_expect` |
| `a_dead_bridge_cannot_hold_a_row_hostage` | `A_LIVE_INCUMBENT_STILL_WINS_ON_START_TIME` | covered by `a_refused_heartbeat_says_it_was_refused::a_REFUSED_claim_says_refused_and_names_who_holds_it` |
| `a_dead_bridge_cannot_hold_a_row_hostage` | `a_beat_with_no_start_time_still_cannot_take_a_LIVE_row` | covered by `a_refused_heartbeat_says_it_was_refused::THE_2026_09_02_BEAT_verbatim_is_refused_and_says_why` |
| `a_dead_terminal_does_not_hold_its_tail_forever` | `POSITIVE_CONTROL_the_sweep_can_see_a_held_tail` | covered by `a_dead_terminal_does_not_hold_its_tail_forever::A_LIVE_TERMINAL_KEEPS_ITS_BUFFER` |
| `a_dead_terminal_does_not_hold_its_tail_forever` | `A_STOPPED_TERMINAL_RELEASES_ITS_BUFFER` | covered by `a_dead_terminal_does_not_hold_its_tail_forever::EVERY_ENDED_STATUS_RELEASES_not_just_stopped` |
| `a_dead_tui_is_not_diagnosed_from_its_screen` | `the_captured_line_is_recognised_as_decoration` | covered by `a_dead_tui_is_not_diagnosed_from_its_screen::a_tui_screen_yields_no_diagnosis_rather_than_a_confident_one` |
| `a_declared_cascade_actually_fires` | `FOREIGN_KEYS_ARE_ON_for_a_service_connection` | covered by `a_declared_cascade_actually_fires::THE_EFFECT_deleting_a_run_removes_its_controls` |
| `a_dispatch_status_is_normalised_before_it_is_stored` | `a_mixed_case_status_is_stored_as_the_readers_expect_it` | covered by `a_dispatch_status_is_normalised_before_it_is_stored::a_mixed_case_terminal_status_still_settles_the_run` |
| `a_dispatch_status_is_normalised_before_it_is_stored` | `a_lowercase_status_is_stored_unchanged` | covered by `a_dispatch_status_is_normalised_before_it_is_stored::a_mixed_case_running_still_stamps_started_at` |
| `a_dispatch_status_is_normalised_before_it_is_stored` | `the_monotonic_guard_still_refuses_to_reopen_a_finished_run` | merged into `a_dispatch_status_is_normalised_before_it_is_stored::the_monotonic_guard_still_refuses_to_reopen_a_finished_run` |
| `a_dispatch_status_is_normalised_before_it_is_stored` | `a_mixed_case_reopen_attempt_is_refused_too` | merged into `a_dispatch_status_is_normalised_before_it_is_stored::the_monotonic_guard_still_refuses_to_reopen_a_finished_run` |
| `a_failed_tail_write_does_not_duplicate_the_chunk` | `POSITIVE_CONTROL_a_working_write_accumulates_once` | covered by `a_failed_tail_write_does_not_duplicate_the_chunk::THE_CHUNK_IS_NOT_APPENDED_TWICE_WHEN_THE_WRITE_FAILS` |
| `a_failed_tail_write_does_not_duplicate_the_chunk` | `POSITIVE_CONTROL_the_refusing_db_really_refuses` | covered by `a_failed_tail_write_does_not_duplicate_the_chunk::THE_CHUNK_IS_NOT_APPENDED_TWICE_WHEN_THE_WRITE_FAILS` |
| `a_failed_tail_write_does_not_duplicate_the_chunk` | `POSITIVE_CONTROL_the_screen_tracks_the_tail_when_nothing_fails` | covered by `a_failed_tail_write_does_not_duplicate_the_chunk::AND_NOT_WHEN_THE_RETRY_COALESCES_WITH_NEWER_OUTPUT` |
| `a_failed_tail_write_does_not_duplicate_the_chunk` | `THE_SCREEN_DOES_NOT_KEEP_A_CHUNK_THE_TAIL_ROLLED_BACK` | covered by `a_failed_tail_write_does_not_duplicate_the_chunk::AND_NOT_WHEN_THE_RETRY_COALESCES_WITH_NEWER_OUTPUT` |
| `a_failed_tail_write_does_not_duplicate_the_chunk` | `A_REFUSED_EVENT_INSERT_ROLLS_THE_HELD_TAIL_BACK_TOO` | merged into `a_failed_tail_write_does_not_duplicate_the_chunk::A_REFUSED_EVENT_INSERT_OR_COMMIT_ROLLS_THE_HELD_TAIL_BACK_TOO` |
| `a_failed_tail_write_does_not_duplicate_the_chunk` | `A_REFUSED_COMMIT_ROLLS_THE_HELD_TAIL_BACK_TOO` | merged into `a_failed_tail_write_does_not_duplicate_the_chunk::A_REFUSED_EVENT_INSERT_OR_COMMIT_ROLLS_THE_HELD_TAIL_BACK_TOO` |
| `a_heartbeat_does_not_blank_what_it_did_not_mention` | `an_advertisement_shaped_beat_keeps_the_launcher_facts` | covered by `a_heartbeat_does_not_blank_what_it_did_not_mention::a_field_the_caller_omitted_keeps_its_stored_value` |
| `a_heartbeat_does_not_blank_what_it_did_not_mention` | `lists_the_caller_omitted_keep_their_stored_value` | merged into `a_heartbeat_does_not_blank_what_it_did_not_mention::a_field_the_caller_omitted_keeps_its_stored_value` |
| `a_heartbeat_does_not_blank_what_it_did_not_mention` | `the_advertised_roots_note_is_not_overwritten_by_silence` | merged into `a_heartbeat_does_not_blank_what_it_did_not_mention::a_field_the_caller_omitted_keeps_its_stored_value` |
| `a_heartbeat_does_not_blank_what_it_did_not_mention` | `a_label_the_caller_omitted_is_not_replaced_by_the_id` | merged into `a_heartbeat_does_not_blank_what_it_did_not_mention::a_field_the_caller_omitted_keeps_its_stored_value` |
| `a_heartbeat_does_not_blank_what_it_did_not_mention` | `a_caller_that_names_the_environment_still_renames_it` | merged into `a_heartbeat_does_not_blank_what_it_did_not_mention::a_field_the_caller_DOES_send_still_overwrites` |
| `a_heartbeat_does_not_blank_what_it_did_not_mention` | `a_caller_that_speaks_still_replaces_the_lists` | merged into `a_heartbeat_does_not_blank_what_it_did_not_mention::a_field_the_caller_DOES_send_still_overwrites` |
| `a_heartbeat_does_not_blank_what_it_did_not_mention` | `a_new_row_is_still_named_after_its_id` | merged into `a_heartbeat_does_not_blank_what_it_did_not_mention::a_brand_new_row_gets_empty_rather_than_inherited` |
| `a_host_ends_the_terminals_it_no_longer_holds` | `a_held_terminal_is_not` | covered by `a_host_ends_the_terminals_it_no_longer_holds::a_confirmed_terminal_the_host_does_not_hold_ENDS` |
| `a_host_ends_the_terminals_it_no_longer_holds` | `a_STARTING_terminal_is_not_the_hosts_to_have_confirmed` | covered by `a_host_ends_the_terminals_it_no_longer_holds::a_starting_terminal_and_another_environments_terminal_are_left_alone` |
| `a_host_ends_the_terminals_it_no_longer_holds` | `no_grace_selects_a_fresh_row_too` | covered by `a_host_ends_the_terminals_it_no_longer_holds::an_online_beat_waits_out_the_grace_and_an_offline_beat_does_not` |
| `a_host_ends_the_terminals_it_no_longer_holds` | `an_offline_beat_from_a_bridge_that_does_not_own_the_row_ends_nothing` | covered by `a_host_ends_the_terminals_it_no_longer_holds::a_REFUSED_beat_from_an_older_bridge_ends_nothing` |
| `a_live_bridge_can_re_adopt_a_managed_agent` | `an_OFFLINE_environments_bridge_is_refused` | covered by `a_live_bridge_can_re_adopt_a_managed_agent::liveness_is_read_at_PATCH_TIME_not_at_registration` |
| `a_live_bridge_can_re_adopt_a_managed_agent` | `THE_DEFECT_a_live_environment_bridge_takes_its_agent_back` | covered by `a_live_bridge_can_re_adopt_a_managed_agent::an_accepted_claim_brings_its_own_environment` |
| `a_live_bridge_does_not_shelter_an_abandoned_spawn` | `A_LIVE_BRIDGE_DOES_NOT_SHELTER_A_STUCK_CLAIM` | merged into `a_live_bridge_does_not_shelter_an_abandoned_spawn::the_error_names_the_rule_that_fired` |
| `a_live_bridge_does_not_shelter_an_abandoned_spawn` | `a_recent_spawn_on_a_live_bridge_is_left_alone` | covered by `a_live_bridge_does_not_shelter_an_abandoned_spawn::a_running_row_still_gets_its_own_rules` |
| `a_live_session_is_never_pushed_off_the_page` | `the_live_session_survives_a_page_full_of_newer_dead_ones` | covered by `a_live_session_is_never_pushed_off_the_page::history_is_what_a_bounded_page_loses` |
| `a_liveness_frame_is_actually_recorded` | `THE_FRAME_IS_RECORDED` | covered by `a_liveness_frame_is_actually_recorded::THE_GUARD_THAT_READS_IT_ACTUALLY_SEES_IT` |
| `a_long_poll_is_not_a_slow_request` | `a_slow_claim_still_reads_slow_UNDER_its_own_wait` | covered by `a_long_poll_is_not_a_slow_request::the_wait_is_subtracted` |
| `a_long_poll_is_not_a_slow_request` | `slow_WORK_under_a_poll_is_still_reported` | covered by `a_long_poll_is_not_a_slow_request::the_line_reports_WORK_and_names_the_wait` |
| `a_misconfigured_agent_is_not_live` | `MISCONFIGURED_is_the_one_that_moved` | covered by `a_misconfigured_agent_is_not_live::the_live_side_is_not_empty` |
| `a_process_host_can_ask_what_to_run` | `THE_SPAWNS_ROLE_REACHES_THE_WORKER` | merged into `a_process_host_can_ask_what_to_run::the_launch_carries_the_aify_variables_the_worker_needs` |
| `a_process_host_can_ask_what_to_run` | `the_session_handle_reaches_the_runtimes_OWN_variable` | merged into `a_process_host_can_ask_what_to_run::the_launch_carries_the_aify_variables_the_worker_needs` |
| `a_process_host_can_ask_what_to_run` | `A_CLAUDE_WORKER_IS_TOLD_IT_IS_WRAPPER_BACKED` | merged into `a_process_host_can_ask_what_to_run::the_launch_carries_the_aify_variables_the_worker_needs` |
| `a_process_host_can_ask_what_to_run` | `the_response_is_JSON_SERIALISABLE_end_to_end` | covered by `a_process_host_can_ask_what_to_run::the_launch_carries_the_program_AND_its_argv` |
| `a_rebuilt_console_screen_says_so` | `a_terminal_reset_counts_as_a_full_clear` | merged into `a_rebuilt_console_screen_says_so::a_full_clear_on_the_main_screen_makes_it_whole_again` |
| `a_refused_heartbeat_says_it_was_refused` | `the_row_still_reflects_the_ACCEPTED_bridge_after_a_refusal` | merged into `a_refused_heartbeat_says_it_was_refused::a_REFUSED_claim_says_refused_and_names_who_holds_it` |
| `a_removal_does_not_delete_the_stop_it_just_wrote` | `POSITIVE_CONTROL_removing_an_agent_still_removes_it` | covered by `removed_agent_is_refused_everywhere::reading_a_removed_agent_also_answers_410` |
| `a_reported_count_counts_the_rows_it_names` | `member_count_is_the_length_of_the_member_list` | covered by `a_reported_count_counts_the_rows_it_names::the_member_count_moves_when_somebody_joins` |
| `a_reported_count_counts_the_rows_it_names` | `the_smallest_channel_the_api_can_make_still_agrees` | covered by `a_reported_count_counts_the_rows_it_names::the_member_count_moves_when_somebody_joins` |
| `a_reporting_host_owns_its_terminal` | `A_MISMATCHED_ID_KEEPS_THE_TERMINAL_WHEN_THE_HOST_IS_REPORTING_IT` | covered by `a_reporting_host_owns_its_terminal::THE_TWO_CASES_ARE_DISTINGUISHABLE` |
| `a_reporting_host_owns_its_terminal` | `a_mismatched_id_DOES_release_when_nothing_has_reported_it` | covered by `a_reporting_host_owns_its_terminal::THE_TWO_CASES_ARE_DISTINGUISHABLE` |
| `a_reporting_host_owns_its_terminal` | `a_matching_bridge_id_keeps_the_terminal` | covered by `a_reporting_host_owns_its_terminal::THE_TWO_CASES_ARE_DISTINGUISHABLE` |
| `a_run_that_owed_no_reply_is_not_told_one_is_missing` | `a_run_that_OWED_a_reply_is_told_the_reply_is_missing` | merged into `a_run_that_owed_no_reply_is_not_told_one_is_missing::BOTH_runs_are_closed_when_the_terminal_ends` |
| `a_run_that_owed_no_reply_is_not_told_one_is_missing` | `a_run_that_owed_NO_reply_is_not` | merged into `a_run_that_owed_no_reply_is_not_told_one_is_missing::BOTH_runs_are_closed_when_the_terminal_ends` |
| `a_run_that_owed_no_reply_is_not_told_one_is_missing` | `the_two_runs_do_not_get_the_SAME_sentence` | merged into `a_run_that_owed_no_reply_is_not_told_one_is_missing::BOTH_runs_are_closed_when_the_terminal_ends` |
| `a_send_to_an_unstartable_agent_is_refused` | `the_FIXTURE_really_produces_a_misconfigured_agent` | covered by `a_send_to_an_unstartable_agent_is_refused::a_MISCONFIGURED_recipient_is_not_launchable` |
| `a_send_to_an_unstartable_agent_is_refused` | `the_REASON_names_the_status_so_the_sender_can_act` | covered by `a_send_to_an_unstartable_agent_is_refused::a_MISCONFIGURED_recipient_is_not_launchable` |
| `a_send_to_an_unstartable_agent_is_refused` | `every_non_live_status_would_be_refused` | covered by `a_misconfigured_agent_is_not_live::the_live_side_is_not_empty` |
| `a_sidecar_does_not_take_ownership_on_register` | `THE_DEFECT_a_managed_wrapper_child_does_not_take_ownership` | covered by `a_sidecar_does_not_take_ownership_on_register::the_rest_of_runtime_state_is_untouched_by_the_guard` |
| `a_sidecar_does_not_take_ownership_on_register` | `a_brand_new_managed_agent_records_no_owner_rather_than_a_wrong_one` | covered by `a_sidecar_does_not_take_ownership_on_register::the_environment_bridge_can_still_claim_it_afterwards` |
| `a_spawn_is_refused_for_a_runtime_the_host_cannot_start` | `a_row_with_no_bridgeLastSeen_at_all_still_spawns` | covered by `a_spawn_is_refused_for_a_runtime_the_host_cannot_start::the_same_spawn_is_ACCEPTED_when_the_host_says_the_runtime_is_there` |
| `a_spawn_is_refused_for_a_runtime_the_host_cannot_start` | `a_row_with_no_available_KEY_still_spawns` | covered by `a_spawn_is_refused_for_a_runtime_the_host_cannot_start::an_ABSENT_available_is_not_a_refusal` |
| `a_spawn_is_refused_for_a_runtime_the_host_cannot_start` | `a_FRESH_stamp_is_live` | covered by `a_spawn_is_refused_for_a_runtime_the_host_cannot_start::the_boundary_is_the_named_window_and_it_is_ONE_number` |
| `a_stale_build_can_say_it_is_stale` | `a_stale_build_reports_a_NON_ZERO_behind_count` | merged into `a_stale_build_can_say_it_is_stale::behind_count_TRACKS_staleness` |
| `a_stale_spawn_request_is_not_a_promise` | `a_fresh_request_still_backs_a_dispatch` | covered by `a_stale_spawn_request_is_not_a_promise::the_boundary_is_where_it_says_it_is` |
| `a_stale_spawn_request_is_not_a_promise` | `a_stale_request_is_not_evidence_that_anybody_is_coming` | covered by `a_stale_spawn_request_is_not_a_promise::the_boundary_is_where_it_says_it_is` |
| `a_stuck_spawn_cannot_block_its_own_remedy` | `a_spawn_that_updated_RECENTLY_is_not` | covered by `a_stuck_spawn_cannot_block_its_own_remedy::PROGRESS_is_the_latest_of_the_three_stamps_not_creation` |
| `a_system_notice_does_not_close_a_reply_contract` | `a_CANCELLED_run_behaves_the_same` | merged into `a_system_notice_does_not_close_a_reply_contract::a_FAILED_run_is_mirrored_but_its_contract_stays_OPEN` |
| `a_terminal_records_how_it_ended` | `a_clean_exit_records_zero` | covered by `a_terminal_records_how_it_ended::a_clean_exit_reaches_that_record_as_zero_rather_than_as_silence` |
| `a_terminal_records_how_it_ended` | `a_failing_exit_records_its_code` | covered by `a_terminal_records_how_it_ended::the_terminal_record_every_other_consumer_gets_carries_the_exit` |
| `a_terminal_records_how_it_ended` | `the_columns_start_as_NULL_rather_than_zero` | covered by `a_terminal_records_how_it_ended::the_terminal_record_every_other_consumer_gets_carries_the_exit` |
| `a_terminal_records_how_it_ended` | `an_older_bridge_that_sends_neither_still_works` | covered by `a_terminal_records_how_it_ended::the_console_tail_says_so_when_nothing_was_reported` |
| `a_terminals_event_page_says_it_is_a_page` | `A_SHORT_HISTORY_IS_NOT_A_PAGE` | covered by `a_terminals_event_page_says_it_is_a_page::EXACTLY_the_cap_is_not_truncated` |
| `accessor_rewrite` | `a_full_line_comment_is_untouched` | covered by `accessor_rewrite::a_mixed_file_is_reproduced_exactly_except_the_code_reference` |
| `accessor_rewrite` | `a_trailing_comment_is_untouched` | covered by `accessor_rewrite::a_mixed_file_is_reproduced_exactly_except_the_code_reference` |
| `accessor_rewrite` | `string_literals_of_every_quoting_style_are_untouched` | covered by `accessor_rewrite::a_mixed_file_is_reproduced_exactly_except_the_code_reference` |
| `accessor_rewrite` | `a_sql_string_containing_the_name_is_untouched` | covered by `accessor_rewrite::a_mixed_file_is_reproduced_exactly_except_the_code_reference` |
| `accessor_rewrite` | `a_module_level_string_assignment_is_untouched` | covered by `accessor_rewrite::a_mixed_file_is_reproduced_exactly_except_the_code_reference` |
| `accessor_rewrite` | `an_fstring_literal_part_is_untouched` | covered by `accessor_rewrite::a_mixed_file_is_reproduced_exactly_except_the_code_reference` |
| `accessor_rewrite` | `the_code_reference_is_still_rewritten` | covered by `accessor_rewrite::a_mixed_file_is_reproduced_exactly_except_the_code_reference` |
| `accessor_rewrite` | `it_rewrites_uses_outside_the_accessor` | covered by `accessor_rewrite::attribute_access_on_the_constant_is_rewritten` |
| `accessor_rewrite` | `it_leaves_everything_else_byte_identical` | covered by `accessor_rewrite::a_mixed_file_is_reproduced_exactly_except_the_code_reference` |
| `accessor_rewrite` | `blank_line_runs_are_preserved_exactly` | covered by `accessor_rewrite::a_mixed_file_is_reproduced_exactly_except_the_code_reference` |
| `accessor_rewrite` | `the_result_always_parses` | covered by `accessor_rewrite::it_does_NOT_rewrite_the_accessor_import` |
| `accessor_rewrite` | `the_substring_heuristic_would_have_been_wrong` | covered by `accessor_rewrite::a_REAL_implementation_that_imports_is_NOT_a_shim` |
| `active_run_lookup` | `a_CLAIMED_run_is_active` | merged into `active_run_lookup::a_CLAIMED_or_RUNNING_run_is_active` |
| `active_run_lookup` | `a_RUNNING_run_is_active` | merged into `active_run_lookup::a_CLAIMED_or_RUNNING_run_is_active` |
| `active_run_lookup` | `a_DELIVERED_channel_run_awaiting_a_reply_is_found` | merged into `active_run_lookup::a_DELIVERED_channel_or_resident_run_awaiting_a_reply_is_found` |
| `active_run_lookup` | `a_RESIDENT_delivery_counts_too` | merged into `active_run_lookup::a_DELIVERED_channel_or_resident_run_awaiting_a_reply_is_found` |
| `active_run_lookup` | `a_BLANK_exclusion_excludes_nothing` | covered by `active_run_lookup::an_active_run_blocks_a_new_dispatch` |
| `agent_control_refusals` | `a_start_with_a_claimable_environment_creates_a_spawn_request` | covered by `a_start_says_whether_it_replaces_a_live_instance::the_START_BUTTON_replaces_and_an_agent_starting_one_does_not` |
| `agent_id_reuse_refusals` | `the_two_messages_are_genuinely_different` | covered by `agent_id_reuse_refusals::a_MANUAL_register_is_told_which_flag_to_pass` |
| `agent_id_reuse_refusals` | `the_two_rename_refusals_are_distinguishable` | covered by `agent_id_reuse_refusals::renaming_onto_a_TOMBSTONED_id_is_refused_and_says_to_clear_it` |
| `agent_rename_covers_every_agent_reference` | `the_UNRESOLVED_bucket_is_empty_and_that_is_the_end_state` | covered by `agent_rename_covers_every_agent_reference::REPOINTED_matches_what_the_rewrite_actually_does` |
| `agent_session_mode_switch` | `switch_hermes_managed_to_resident_with_force_succeeds` | merged into `agent_session_mode_switch::switch_hermes_managed_to_resident_without_gateway_succeeds` |
| `agent_session_mode_switch` | `switch_resident_to_managed_force_reports_missing_backing` | merged into `agent_session_mode_switch::switch_resident_to_managed_without_backing_reports_missing_backing` |
| `agent_status_inputs` | `a_claude_terminal_SHOWING_A_PROMPT_is_awaiting` | covered by `agent_status_inputs::the_same_prompt_under_a_DIFFERENT_agent_id_is_not_served_from_cache` |
| `agent_status_inputs` | `the_globals_guard_in_agent_config_defect_is_satisfied_in_its_new_module` | covered by `agent_status_inputs::an_alias_runtime_still_normalises` |
| `agent_status_read_gate` | `ready_cache_without_live_worker_is_downgraded_in_response` | merged into `agent_status_read_gate::get_agent_downgrades_stale_online_with_no_live_worker` |
| `agent_status_read_gate` | `downgrade_corrects_response_without_persisting` | covered by `agent_status_read_gate::get_agent_downgrades_stale_online_with_no_live_worker` |
| `an_advertisement_does_not_disarm_supersession` | `a_bridge_that_names_itself_still_replaces_the_stored_id` | covered by `an_advertisement_does_not_disarm_supersession::THE_DEFECT_an_advertisement_leaves_supersession_armed` |
| `an_env_supplied_build_identity_is_disclosed` | `an_env_supplied_SHA_is_recorded_by_name` | covered by `an_env_supplied_build_identity_is_disclosed::the_version_ENDPOINT_emits_it_only_when_it_happened` |
| `an_interrupt_is_named_not_guessed` | `a_provider_error_is_still_not_ours` | covered by `authored_failure_text_is_not_provider_evidence::a_real_provider_throttle_is_not_mistaken_for_service_text` |
| `an_interrupt_is_named_not_guessed` | `it_is_still_service_authored_and_still_not_provider_evidence` | covered by `authored_failure_text_is_not_provider_evidence::the_classifier_STILL_matches_the_token_so_the_guard_is_what_saves_it` |
| `an_orphan_is_a_removed_agent_not_a_missing_row` | `THE_DEFECT_the_dashboard_inbox_survives` | covered by `an_orphan_is_a_removed_agent_not_a_missing_row::THE_MIXED_CASE_deletes_only_the_removed_one` |
| `an_orphan_is_a_removed_agent_not_a_missing_row` | `a_message_to_a_REMOVED_agent_is_still_deleted` | covered by `an_orphan_is_a_removed_agent_not_a_missing_row::THE_MIXED_CASE_deletes_only_the_removed_one` |
| `an_orphan_is_a_removed_agent_not_a_missing_row` | `a_READ_message_to_a_removed_agent_is_history` | covered by `an_orphan_is_a_removed_agent_not_a_missing_row::THE_MIXED_CASE_deletes_only_the_removed_one` |
| `an_orphan_is_a_removed_agent_not_a_missing_row` | `a_CHANNEL_BROADCAST_row_is_not_an_orphan` | covered by `an_orphan_is_a_removed_agent_not_a_missing_row::THE_MIXED_CASE_deletes_only_the_removed_one` |
| `an_unknown_status_event_is_not_silently_dropped` | `a_near_miss_is_NOT_known` | covered by `an_unknown_status_event_is_not_silently_dropped::is_known_agrees_with_the_table` |
| `an_update_says_what_it_changed` | `the_comparison_can_say_both_yes_and_no` | covered by `an_update_says_what_it_changed::A_REGRESSION_IS_NAMED_AND_FAILS` |
| `ansi_strippers_agree` | `both_patterns_strip_every_case_identically` | covered by `ansi_strippers_agree::the_two_patterns_are_literally_the_same` |
| `ansi_strippers_agree` | `both_patterns_strip_every_case_identically` | covered by `ansi_strippers_agree::the_two_patterns_are_literally_the_same` |
| `api_key_middleware` | `the_right_key_in_the_HEADER_is_admitted` | covered by `api_key_middleware::the_header_name_is_matched_case_insensitively` |
| `api_key_middleware` | `health_is_reachable_without_a_key` | covered by `api_key_middleware::every_documented_skip_prefix_is_actually_skipped` |
| `api_key_middleware` | `a_browser_with_no_key_is_still_refused` | covered by `api_key_middleware::NO_key_is_refused_with_an_actionable_message` |
| `api_key_middleware` | `the_cookie_name_is_read_and_written_from_ONE_constant` | covered by `api_key_middleware::a_valid_key_in_the_URL_is_exchanged_for_a_cookie` |
| `api_v2_is_composition_only` | `it_exports_no_helper_under_any_name` | covered by `api_v2_is_composition_only::every_top_level_statement_is_composition` |
| `api_v2_is_composition_only` | `it_declares_no_functions_or_classes` | covered by `api_v2_is_composition_only::every_top_level_statement_is_composition` |
| `api_v2_regressions` | `reconcile_does_not_close_live_channel_turn_at_thirty_minutes` | covered by `reconcilable_runs_query::class_3_spares_a_run_whose_agent_still_has_a_LIVE_session` |
| `api_v2_regressions` | `dispatch_run_events_are_bounded_and_cursor_paginated` | covered by `dispatch_run_queries::the_limit_is_CAPPED_AT_FIFTY_without_an_error`; `dispatch_run_queries::NEXT_BEFORE_pages_through_the_log_without_repeating_or_skipping` |
| `api_v2_regressions` | `environment_list_api_and_dashboard_render_surface` | covered by `a_refused_heartbeat_says_it_was_refused::a_REFUSED_claim_says_refused_and_names_who_holds_it`; `dashboard_redirect::redirect_defaults_to_request_host_on_new_dashboard_port` |
| `api_v2_regressions` | `managed_worker_gateway_lost_rests_available_not_stopped` | covered by `resident_loss_settlement::a_managed_worker_rests_COLD_STARTABLE_not_stopped` |
| `api_v2_regressions` | `terminal_output_responses_expose_monotonic_output_sequence` | covered by `terminal_write_queue_batching::every_post_in_one_batch_shares_the_frame_it_will_become`; `the_broadcast_seq_counts_frames_not_posts::consecutive_flushes_are_consecutive_numbers` |
| `api_v2_regressions` | `environment_heartbeat_persists_terminal_capabilities` | covered by `two_tiers_describe_one_environment_without_erasing_each_other::the_bridge_beating_after_aify_env_keeps_the_runtimes`; `a_start_says_whether_it_replaces_a_live_instance::ANY_OTHER_terminal_launches_as_a_start`; `two_tiers_describe_one_environment_without_erasing_each_other::a_bridge_that_did_NOT_stand_down_still_works` |
| `api_v2_regressions` | `orphan_spawn_reconcile_fails_only_dead_bridge_old_spawns` | covered by `a_live_bridge_does_not_shelter_an_abandoned_spawn::a_FRESH_claim_is_left_alone_on_either_bridge`; `a_live_bridge_does_not_shelter_an_abandoned_spawn::an_ancient_spawn_on_a_live_bridge_is_failed` |
| `api_v2_regressions` | `prune_orphaned_dispatch_runs_respects_ttl_window` | covered by `api_v2_regressions::prune_orphaned_dispatch_runs_deletes_terminal_runs_for_tombstoned_agent` |
| `api_v2_regressions` | `host_reported_dead_pty_is_idempotent_and_pid_guarded` | covered by `bugd_coldstart_selfheal::report_dead_pid_mismatch_leaves_wrapper_child_live` |
| `api_v2_regressions` | `stale_resident_bridge_with_turn_busy_is_not_working` | covered by `status_decision_branches::a_stale_resident_bridge_beats_turn_busy` |
| `api_v2_regressions` | `explicit_queue_releases_once_turn_busy_passes_the_antistrand_ceiling` | covered by `turn_busy_delivery_ceiling::the_ceiling_is_the_one_constant_not_a_second_number` |
| `api_v2_regressions` | `explicit_queue_stays_held_while_raw_turn_busy_is_aged_but_set` | covered by `turn_busy_delivery_ceiling::turn_busy_just_inside_ceiling_still_holds` |
| `api_v2_regressions` | `has_live_terminal_session_counts_recovering` | covered by `the_live_terminal_filter_has_one_owner::THE_LIVE_FILTER_KEEPS_THE_RULED_MEMBER`; `the_live_terminal_filter_has_one_owner::NO_QUERY_SPELLS_THE_LIVE_TERMINAL_SET_BY_HAND` |
| `api_v2_regressions` | `pi_console_requires_handle_unless_fresh_context_requested` | covered by `terminal_and_console_refusals::a_pi_console_without_a_session_handle_is_refused`; `terminal_and_console_refusals::freshContext_is_the_way_past_it` |
| `api_v2_regressions` | `managed_via_wrapper_setting_defaults_to_off` | covered by `default_settings_plan4::managed_via_wrapper_defaults_to_codex_hermes_only` |
| `api_v2_regressions` | `managed_claude_spawn_uses_settings_default_model` | merged into `api_v2_regressions::managed_spawn_uses_settings_defaults_and_persists_runtime_config` |
| `api_v2_regressions` | `managed_codex_spawn_uses_settings_defaults_and_persists_runtime_config` | merged into `api_v2_regressions::managed_spawn_uses_settings_defaults_and_persists_runtime_config` |
| `api_v2_regressions` | `managed_pi_spawn_uses_settings_defaults_and_persists_runtime_config` | merged into `api_v2_regressions::managed_spawn_uses_settings_defaults_and_persists_runtime_config` |
| `api_v2_regressions` | `runtime_settings_update_existing_managed_agents_globally` | merged into `api_v2_regressions::runtime_settings_update_existing_managed_agents_globally` |
| `api_v2_regressions` | `runtime_settings_update_existing_managed_pi_agents_globally` | merged into `api_v2_regressions::runtime_settings_update_existing_managed_agents_globally` |
| `api_v2_regressions` | `idle_attached_console_reports_active_not_working` | merged into `api_v2_regressions::attached_console_without_active_run_reports_active_not_working` |
| `api_v2_regressions` | `managed_hygiene_reaps_ghost_console_row` | merged into `api_v2_regressions::managed_hygiene_reaps_ghost_console_row` |
| `api_v2_regressions` | `managed_hygiene_reaps_hermes_ghost_console_row` | merged into `api_v2_regressions::managed_hygiene_reaps_ghost_console_row` |
| `api_v2_regressions` | `managed_hygiene_reaps_orphan_worker` | merged into `api_v2_regressions::managed_hygiene_reaps_each_orphan_ONCE` |
| `api_v2_regressions` | `managed_hygiene_reaps_each_orphan_ONCE` | merged into `api_v2_regressions::managed_hygiene_reaps_each_orphan_ONCE` |
| `api_v2_regressions` | `a_repeat_sweep_appends_no_SECOND_event` | merged into `api_v2_regressions::managed_hygiene_reaps_each_orphan_ONCE` |
| `api_v2_regressions` | `a_repeat_sweep_does_not_reinvalidate_the_live_state` | merged into `api_v2_regressions::managed_hygiene_reaps_each_orphan_ONCE` |
| `api_v2_regressions` | `service_does_not_blindly_inject_claude_startup_enter` | covered by `api_v2_regressions::a_closed_console_is_cold_started_and_the_message_typed_into_it` |
| `api_v2_regressions` | `spawn_request_rejects_non_live_modes` | covered by `spawn_request_refusals::the_spawn_mode_allowlist_is_exactly_managed_warm` |
| `api_v2_regressions` | `session_recover_rejects_duplicate_pending_spawn` | covered by `session_control_refusals::a_restart_is_refused_while_a_spawn_is_already_in_flight` |
| `api_v2_regressions` | `resident_reregister_with_new_handle_upserts_one_session_row` | covered by `resident_session_upsert::a_RELAUNCH_updates_the_same_row` |
| `api_v2_regressions` | `claim_ignores_missing_message_ids_in_buffered_body` | covered by `api_v2_regressions::triggered_send_merges_existing_future_queue` |
| `api_v2_regressions` | `agent_stop_marks_resident_owner_for_bridge_termination` | covered by `agent_stop_resume::the_stop_note_differs_by_session_mode` |
| `api_v2_regressions` | `dispatch_rejects_message_only_mode` | covered by `messaging_refusals::dispatch_no_longer_takes_message_only_and_says_what_to_use` |
| `api_v2_regressions` | `managed_claude_dispatch_uses_claude_aify_terminal_turn` | merged into `api_v2_regressions::a_closed_console_is_cold_started_and_the_message_typed_into_it` |
| `api_v2_regressions` | `managed_dispatch_starts_headless_pty_for_terminal_runtimes` | merged into `api_v2_regressions::a_closed_console_is_cold_started_and_the_message_typed_into_it` |
| `api_v2_regressions` | `message_send_starts_managed_pty_for_hermes_when_console_is_closed` | merged into `api_v2_regressions::a_closed_console_is_cold_started_and_the_message_typed_into_it` |
| `api_v2_regressions` | `message_send_to_managed_claude_starts_claude_aify_and_inputs_dashboard_message` | merged into `api_v2_regressions::a_closed_console_is_cold_started_and_the_message_typed_into_it` |
| `api_v2_regressions` | `managed_dispatch_native_runtime_uses_terminal_backing_by_default` | merged into `api_v2_regressions::a_closed_console_is_cold_started_and_the_message_typed_into_it` |
| `api_v2_regressions` | `managed_dispatch_to_active_console_terminal_forwards_to_pty` | merged into `api_v2_regressions::an_open_console_receives_the_message_as_typed_input` |
| `api_v2_regressions` | `message_send_delivers_to_active_console_pty_without_queuing_run` | merged into `api_v2_regressions::an_open_console_receives_the_message_as_typed_input` |
| `api_v2_regressions` | `message_send_to_managed_claude_uses_console_turn_when_console_open` | merged into `api_v2_regressions::an_open_console_receives_the_message_as_typed_input` |
| `api_v2_regressions` | `recent_claude_idle_prompt_does_not_close_before_settling_window` | merged into `api_v2_regressions::a_claude_terminal_turn_stays_open_until_its_screen_is_idle_and_quiet` |
| `api_v2_regressions` | `triggered_response_send_does_not_require_another_reply` | merged into `api_v2_regressions::a_triggered_send_owes_a_reply_by_its_type_unless_told_otherwise` |
| `api_v2_regressions` | `triggered_info_send_does_not_require_reply_by_default` | merged into `api_v2_regressions::a_triggered_send_owes_a_reply_by_its_type_unless_told_otherwise` |
| `api_v2_regressions` | `triggered_info_send_can_explicitly_require_reply` | merged into `api_v2_regressions::a_triggered_send_owes_a_reply_by_its_type_unless_told_otherwise` |
| `api_v2_regressions` | `triggered_review_and_error_sends_expect_reply_by_default` | merged into `api_v2_regressions::a_triggered_send_owes_a_reply_by_its_type_unless_told_otherwise` |
| `api_v2_regressions` | `dispatch_claim_signals_stopped_for_disabled_agent` | merged into `api_v2_regressions::dispatch_claim_signals_stopped_only_for_a_disabled_agent` |
| `api_v2_regressions` | `dispatch_claim_does_not_signal_stopped_for_active_agent` | merged into `api_v2_regressions::dispatch_claim_signals_stopped_only_for_a_disabled_agent` |
| `api_v2_regressions` | `multi_recipient_send_tracks_per_recipient_message_ids` | merged into `api_v2_regressions::a_multi_recipient_send_or_dispatch_tracks_per_recipient_message_ids` |
| `api_v2_regressions` | `multi_recipient_dispatch_tracks_per_recipient_message_ids` | merged into `api_v2_regressions::a_multi_recipient_send_or_dispatch_tracks_per_recipient_message_ids` |
| `api_v2_regressions` | `terminal_controls_get_the_SAME_distinction` | merged into `api_v2_regressions::init_db_fails_terminal_controls_for_superseded_environment_bridge` |
| `api_v2_regressions` | `reminder_not_skipped_when_turn_busy_is_same_runs_own_repulse` | merged into `api_v2_regressions::reminder_busy_gate_separates_own_repulse_other_work_and_active_runs` |
| `api_v2_regressions` | `reminder_skipped_when_turn_busy_is_a_different_run` | merged into `api_v2_regressions::reminder_busy_gate_separates_own_repulse_other_work_and_active_runs` |
| `api_v2_regressions` | `reminder_skipped_when_agent_has_active_dispatch_run` | merged into `api_v2_regressions::reminder_busy_gate_separates_own_repulse_other_work_and_active_runs` |
| `api_v2_regressions` | `reminder_fires_when_agent_not_busy_at_all` | merged into `api_v2_regressions::reminder_busy_gate_separates_own_repulse_other_work_and_active_runs` |
| `api_v2_regressions` | `dispatch_claim_ignores_stale_embedded_message_ids_when_marking_read` | covered by `dispatch_source_message_ids_are_structural::a_body_cannot_mint_a_read_receipt_for_an_unrelated_message` |
| `api_v2_regressions` | `periodic_dispatch_reconcile_sends_contract_reminders` | covered by `api_v2_regressions::terminal_contract_with_lost_backing_still_gets_reminder` |
| `api_v2_regressions` | `wake_on_message_send_to_available_agent_queues_dispatch` | covered by `dispatch_channel_claim::wrapper_backed_send_without_managed_session_coldstarts_backing` |
| `api_v2_regressions` | `send_to_available_managed_codex_no_session_coldstarts_with_autobind` | covered by `lifecycle_phase7::coldstart_autobinds_first_online_env_when_no_prior_session` |
| `api_v2_regressions` | `turn_start_endpoint_sets_turn_busy_idempotent` | covered by `turn_start_attribution::the_INSERT_path_attributes_the_turn_to_the_prompt_hook` |
| `api_v2_regressions` | `turn_start_does_not_clobber_in_flight_managed_dispatch` | covered by `turn_start_attribution::an_IN_FLIGHT_DISPATCH_keeps_its_bridge` |
| `api_v2_regressions` | `channel_route_delivered_awaiting_reply_shows_online_not_working` | merged into `api_v2_regressions::channel_and_resident_route_delivered_awaiting_reply_shows_online_not_working` |
| `api_v2_regressions` | `resident_route_delivered_awaiting_reply_shows_online_not_working` | merged into `api_v2_regressions::channel_and_resident_route_delivered_awaiting_reply_shows_online_not_working` |
| `api_v2_regressions` | `idle_virtual_rpc_workers_not_closed_when_in_flight_run` | covered by `idle_worker_query::a_worker_with_work_IN_FLIGHT_is_spared` |
| `api_v2_regressions` | `session_handle_patch_drops_unexpanded_placeholder` | covered by `input_limit_refusals::the_handle_is_SANITISED_before_it_is_measured` |
| `api_v2_regressions` | `agent_console_tail_control_a_screen_from_the_first_byte_is_not_rebuilt` | covered by `a_rebuilt_console_screen_says_so::control_a_screen_fed_from_its_first_byte_is_not_reconstructed` |
| `api_v2_regressions` | `agent_console_input_unknown_caller_rejected` | covered by `console_input_caller_gate::an_UNREGISTERED_caller_is_a_403_not_a_400` |
| `api_v2_regressions` | `agent_console_input_no_live_console_returns_clear_message` | covered by `console_input_caller_gate::a_registered_caller_gets_past_the_gate` |
| `api_v2_regressions` | `stop_worker_broadcasts_agent_status` | merged into `api_v2_regressions::stop_worker_and_control_stop_broadcast_agent_status` |
| `api_v2_regressions` | `control_stop_broadcasts_agent_status` | merged into `api_v2_regressions::stop_worker_and_control_stop_broadcast_agent_status` |
| `api_v2_regressions` | `has_live_managed_wrapper_child_true_for_fresh_bridge` | covered by `bugd_coldstart_selfheal::report_dead_pid_mismatch_leaves_wrapper_child_live` |
| `api_v2_regressions` | `switch_pi_managed_to_resident_is_rejected_without_force` | covered by `session_mode_switch_refusals::a_managed_only_runtime_cannot_be_switched_to_resident` |
| `api_v2_regressions` | `switch_pi_managed_to_resident_with_force_warns_but_proceeds` | covered by `session_mode_switch_refusals::forcing_a_managed_only_runtime_resident_works_and_says_what_it_costs` |
| `api_v2_regressions` | `derived_session_status_dead_managed_terminal_failed_is_stopped` | covered by `api_v2_regressions::get_sessions_serves_derived_status_for_dead_managed_terminal` |
| `api_v2_regressions` | `dead_session_reconcile_catches_stale_denorm_with_failed_terminal` | covered by `api_v2_regressions::reconcile_dead_session_status_marks_dead_backings_stopped` |
| `api_v2_regressions` | `coldstart_spawn_request_carries_stored_session_handle` | merged into `api_v2_regressions::coldstart_spawn_request_carries_the_stored_session_handle_or_none` |
| `api_v2_regressions` | `coldstart_spawn_request_empty_handle_when_agent_has_none` | merged into `api_v2_regressions::coldstart_spawn_request_carries_the_stored_session_handle_or_none` |
| `api_v2_regressions` | `derived_session_status_live_managed_terminal_attached_is_running` | merged into `api_v2_regressions::derived_session_status_follows_live_truth` |
| `api_v2_regressions` | `derived_session_status_live_resident_fresh_bridge_is_running` | merged into `api_v2_regressions::derived_session_status_follows_live_truth` |
| `api_v2_regressions` | `derived_session_status_dead_resident_no_fresh_bridge_is_stopped` | merged into `api_v2_regressions::derived_session_status_follows_live_truth` |
| `api_v2_regressions` | `derived_session_status_stopped_agent_is_stopped` | merged into `api_v2_regressions::derived_session_status_follows_live_truth` |
| `api_v2_regressions` | `derived_session_status_terminal_stored_status_passes_through` | merged into `api_v2_regressions::derived_session_status_follows_live_truth` |
| `api_v2_regressions` | `channel_delivery_receipt_is_not_persisted_as_chat_reply` | covered by `dashboard_run_report::a_DELIVERY_RECEIPT_is_not_persisted_as_a_reply`; `dashboard_run_report::a_REAL_claude_reply_is_still_mirrored` |
| `borrowed_accessors_return_the_owner` | `no_accessor_calls_itself` | covered by `borrowed_accessors_return_the_owner::every_accessor_returns_the_routers_own_object` |
| `both_endpoint_readers_agree` | `both_readers_find_the_endpoint_in_a_launcher_install_sh_writes_today` | covered by `both_endpoint_readers_agree::both_readers_find_the_endpoint_for_every_client_the_installer_renders[claude]` |
| `both_transports_declare_the_same_tool_surface` | `an_sse_dispatch_can_set_a_priority` | covered by `both_transports_declare_the_same_tool_surface::every_parameter_difference_is_declared_with_a_reason` |
| `bridge_liveness_beat` | `an_EMPTY_kind_cannot_demote_a_managed_wrapper_child` | covered by `bridge_liveness_beat::an_empty_kind_never_blanks_an_ordinary_stored_kind` |
| `change_feed` | `a_commit_reports_its_writes` | covered by `change_feed::a_commit_whose_writes_matched_no_rows_reports_nothing` |
| `claim_emptiness` | `a_run_present_beats_every_absent_directive` | covered by `claim_emptiness::a_CLAIMED_RUN_is_not_empty` |
| `claim_emptiness` | `the_two_control_predicates_still_AGREE` | covered by `claim_emptiness::a_NULL_controls_value_is_also_actionable` |
| `claim_emptiness` | `dispatch_claim` | merged into `claim_emptiness::every_route_passes_its_own_predicate_and_scope_and_its_lock_result_is_EMPTY` |
| `claim_emptiness` | `dispatch_controls_claim` | merged into `claim_emptiness::every_route_passes_its_own_predicate_and_scope_and_its_lock_result_is_EMPTY` |
| `claim_emptiness` | `terminal_controls_claim` | merged into `claim_emptiness::every_route_passes_its_own_predicate_and_scope_and_its_lock_result_is_EMPTY` |
| `claim_emptiness` | `environment_control_claim` | merged into `claim_emptiness::every_route_passes_its_own_predicate_and_scope_and_its_lock_result_is_EMPTY` |
| `claim_emptiness` | `spawn_request_claim` | merged into `claim_emptiness::every_route_passes_its_own_predicate_and_scope_and_its_lock_result_is_EMPTY` |
| `claim_emptiness` | `every_route_calls_its_predicate_EMPTY_on_its_own_lock_result` | merged into `claim_emptiness::every_route_passes_its_own_predicate_and_scope_and_its_lock_result_is_EMPTY` |
| `claimer_lease` | `acquire_makes_lease_live` | covered by `claimer_lease::release_clears_lease_immediately` |
| `claimer_lease` | `no_lease_ever_falls_back_to_sidecar_check` | covered by `claimer_lease::agent_has_live_claimer_released_lease_is_not_deliverable` |
| `code_currency` | `a_missing_half_is_NOT_stale_either` | covered by `code_currency::a_missing_half_is_UNKNOWN_and_never_current` |
| `console_capability_gate` | `an_advertised_list_that_is_present_but_unusable_still_names_it` | covered by `console_capability_gate::an_unadvertised_runtime_is_reported_as_a_selection_problem` |
| `console_capability_gate` | `the_host_and_selection_refusals_are_different_messages` | covered by `console_capability_gate::an_unadvertised_runtime_is_reported_as_a_selection_problem` |
| `console_command_resume` | `claude_managed_includes_resume` | merged into `console_command_resume::the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it` |
| `console_command_resume` | `claude_interactive_includes_resume_when_handle_known` | merged into `console_command_resume::the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it` |
| `console_command_resume` | `codex_managed_includes_resume` | merged into `console_command_resume::the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it` |
| `console_command_resume` | `codex_interactive_includes_resume_when_handle_known` | merged into `console_command_resume::the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it` |
| `console_command_resume` | `codex_no_handle_no_resume` | merged into `console_command_resume::the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it` |
| `console_command_resume` | `hermes_managed_includes_resume` | merged into `console_command_resume::the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it` |
| `console_command_resume` | `pi_managed_includes_resume` | merged into `console_command_resume::the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it` |
| `console_command_resume` | `pi_interactive_no_resume` | merged into `console_command_resume::the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it` |
| `console_input_caller_gate` | `the_refusals_carry_different_status_codes` | covered by `console_input_caller_gate::an_UNREGISTERED_caller_is_a_403_not_a_400` |
| `console_reads_the_store_that_recorded_something` | `the_events_answer_when_the_column_does_not` | covered by `console_reads_the_store_that_recorded_something::it_serves_what_the_events_recorded` |
| `console_reads_the_store_that_recorded_something` | `neither_store_saying_anything_is_its_own_answer` | covered by `console_reads_the_store_that_recorded_something::a_terminal_that_recorded_nothing_says_it_DIED_rather_than_that_it_is_idle` |
| `console_reads_the_store_that_recorded_something` | `the_fixture_reproduces_the_shape_that_failed` | merged into `console_reads_the_store_that_recorded_something::it_serves_what_the_events_recorded` |
| `container_definition_merge` | `the_merge_is_only_one_level_deep` | covered by `container_definition_merge::a_nested_dict_is_MERGED_key_by_key_not_replaced` |
| `container_definition_merge` | `the_returned_defaults_are_not_polluted_by_the_merge` | covered by `container_definition_merge::definitions_do_not_leak_into_each_other` |
| `container_definition_merge` | `a_resolvable_shared_with_is_accepted` | covered by `container_definition_merge::a_forward_reference_is_fine_because_validation_is_a_second_pass` |
| `container_manager_lifecycle` | `resolve_url_is_none_until_the_container_has_a_hostname` | covered by `container_manager_loops_and_config::internal_url_needs_both_a_hostname_and_a_port` |
| `container_manager_loops_and_config` | `a_nested_block_is_MERGED_not_replaced` | covered by `container_definition_merge::a_nested_dict_is_MERGED_key_by_key_not_replaced` |
| `container_manager_loops_and_config` | `defaults_merge_into_every_definition` | covered by `container_definition_merge::defaults_fill_in_what_a_definition_omits` |
| `container_manager_loops_and_config` | `a_shared_with_pointing_at_nothing_is_refused_AT_LOAD` | covered by `container_definition_merge::an_unresolvable_shared_with_raises_at_load` |
| `container_manager_loops_and_config` | `no_containers_configured_is_not_an_error` | covered by `container_definition_merge::a_config_with_no_containers_block_is_empty_not_an_error` |
| `container_manager_loops_and_config` | `idle_seconds_is_zero_when_nothing_has_been_requested` | covered by `container_manager_loops_and_config::a_container_that_has_never_been_REQUESTED_is_not_idle` |
| `container_manager_operations` | `starting_background_tasks_starts_both_loops` | covered by `container_manager_operations::stopping_background_tasks_CANCELS_them` |
| `container_manager_operations` | `a_container_that_answers_200_is_healthy` | covered by `container_manager_operations::it_KEEPS_POLLING_past_a_refusal` |
| `container_proxy_forwarding` | `hop_by_hop_headers_are_not_relayed_across_the_proxy` | covered by `container_proxy_header_filter::every_hop_by_hop_header_is_stripped` |
| `container_proxy_header_filter` | `the_stripped_set_is_the_contract` | covered by `container_proxy_header_filter::every_credential_header_is_stripped` |
| `container_route_proxy` | `the_body_and_query_reach_the_container` | covered by `container_proxy_forwarding::the_method_url_and_body_are_forwarded_unchanged` |
| `container_route_proxy` | `a_container_that_is_DOWN_is_started_by_the_first_request` | covered by `container_route_proxy::every_down_state_triggers_a_start` |
| `container_route_proxy` | `a_proxied_request_RESETS_the_idle_clock` | covered by `container_route_proxy::the_TARGETS_clock_is_the_one_reset_for_a_shared_name` |
| `contract_row_serialisation` | `a_shorter_body_is_not_padded` | covered by `contract_row_serialisation::the_message_body_is_preferred_and_the_run_body_is_the_fallback` |
| `controls_for_ended_runs_are_closed` | `a_second_pass_closes_nothing_new` | covered by `controls_for_ended_runs_are_closed::an_ALREADY_SETTLED_control_is_not_touched` |
| `current_session_picker_prefers_live` | `fresher_dead_row_does_not_shadow_a_live_attached_session` | covered by `current_session_picker_prefers_live::every_live_status_outranks_a_fresher_dead_row` |
| `dashboard_assets_are_compressed` | `a_module_comes_back_gzipped` | merged into `dashboard_assets_are_compressed::a_module_comes_back_gzipped_intact_and_still_revalidated` |
| `dashboard_assets_are_compressed` | `the_module_still_parses_after_the_round_trip` | merged into `dashboard_assets_are_compressed::a_module_comes_back_gzipped_intact_and_still_revalidated` |
| `dashboard_assets_are_compressed` | `the_revalidation_headers_still_ride_along` | merged into `dashboard_assets_are_compressed::a_module_comes_back_gzipped_intact_and_still_revalidated` |
| `dashboard_redirect` | `the_configured_url_wins_over_the_request` | covered by `dashboard_redirect::legacy_dashboard_entry_points_redirect_to_new_dashboard` |
| `dashboard_redirect` | `a_configured_url_without_a_trailing_slash_gets_exactly_one` | merged into `dashboard_redirect::a_configured_url_ends_in_exactly_ONE_trailing_slash` |
| `dashboard_redirect` | `a_configured_url_with_SEVERAL_trailing_slashes_gets_exactly_one` | merged into `dashboard_redirect::a_configured_url_ends_in_exactly_ONE_trailing_slash` |
| `dashboard_redirect` | `surrounding_whitespace_does_not_make_it_configured` | merged into `dashboard_redirect::an_EMPTY_or_WHITESPACE_value_falls_back_to_deriving` |
| `dashboard_redirect` | `an_EMPTY_value_falls_back_to_deriving` | merged into `dashboard_redirect::an_EMPTY_or_WHITESPACE_value_falls_back_to_deriving` |
| `dashboard_redirect` | `the_hostname_comes_from_the_REQUEST_not_from_localhost` | covered by `dashboard_redirect::redirect_defaults_to_request_host_on_new_dashboard_port` |
| `dashboard_redirect` | `the_path_is_always_the_ROOT_of_the_dashboard` | covered by `dashboard_redirect::redirect_defaults_to_request_host_on_new_dashboard_port` |
| `dashboard_redirect` | `an_ordinary_hostname_is_NOT_bracketed` | covered by `dashboard_redirect::redirect_defaults_to_request_host_on_new_dashboard_port` |
| `dashboard_redirect` | `a_full_IPv6_address_is_bracketed` | merged into `dashboard_redirect::an_IPv6_host_is_RE_BRACKETED` |
| `dashboard_redirect` | `the_output_never_carries_DOUBLE_brackets` | covered by `dashboard_redirect::an_IPv6_host_is_RE_BRACKETED` |
| `dashboard_run_report` | `a_completed_managers_summary_becomes_a_dashboard_message` | merged into `dashboard_run_report::a_completed_managers_summary_becomes_an_INFO_dashboard_message` |
| `dashboard_run_report` | `the_report_is_an_INFO_message_not_a_response` | merged into `dashboard_run_report::a_completed_managers_summary_becomes_an_INFO_dashboard_message` |
| `dashboard_run_report` | `the_subject_is_PREFIXED_so_it_reads_as_an_update` | merged into `dashboard_run_report::the_subject_is_PREFIXED_once_so_it_reads_as_an_update` |
| `dashboard_run_report` | `an_already_prefixed_subject_is_NOT_prefixed_twice` | merged into `dashboard_run_report::the_subject_is_PREFIXED_once_so_it_reads_as_an_update` |
| `dashboard_run_report` | `a_run_with_NO_SUBJECT_gets_a_usable_one` | merged into `dashboard_run_report::the_subject_is_PREFIXED_once_so_it_reads_as_an_update` |
| `dashboard_run_report` | `every_coordinator_role_is_accepted` | merged into `dashboard_run_report::every_coordinator_role_is_accepted_in_any_case` |
| `dashboard_run_report` | `the_role_is_matched_case_insensitively` | merged into `dashboard_run_report::every_coordinator_role_is_accepted_in_any_case` |
| `dashboard_run_report` | `an_EXPLICIT_report_from_the_agent_suppresses_the_machine_one` | merged into `dashboard_run_report::an_EXPLICIT_report_from_the_agent_suppresses_the_machine_one_and_is_RECORDED` |
| `dashboard_run_report` | `the_suppression_is_RECORDED_rather_than_silent` | merged into `dashboard_run_report::an_EXPLICIT_report_from_the_agent_suppresses_the_machine_one_and_is_RECORDED` |
| `dashboard_run_report` | `a_dashboard_started_runs_summary_becomes_a_RESPONSE` | merged into `dashboard_run_report::a_dashboard_started_runs_summary_becomes_a_LINKED_RESPONSE` |
| `dashboard_run_report` | `the_mirrored_reply_is_LINKED_to_the_run` | merged into `dashboard_run_report::a_dashboard_started_runs_summary_becomes_a_LINKED_RESPONSE` |
| `dashboard_run_report` | `the_subject_is_the_shared_handoff_subject` | merged into `dashboard_run_report::a_dashboard_started_runs_summary_becomes_a_LINKED_RESPONSE` |
| `dashboard_run_report` | `an_EXISTING_dashboard_reply_is_LINKED_rather_than_duplicated` | merged into `dashboard_run_report::an_EXISTING_dashboard_reply_is_LINKED_rather_than_duplicated_as_a_HANDOFF` |
| `dashboard_run_report` | `linking_is_recorded_as_a_HANDOFF_event` | merged into `dashboard_run_report::an_EXISTING_dashboard_reply_is_LINKED_rather_than_duplicated_as_a_HANDOFF` |
| `dashboard_run_report` | `the_two_functions_never_both_fire_for_one_run` | covered by `dashboard_run_report::a_run_NOT_started_by_the_dashboard_is_not_mirrored` |
| `dashboard_run_report` | `the_report_is_RECORDED_on_the_run` | merged into `dashboard_run_report::the_report_is_written_ONCE_and_RECORDED_on_the_run` |
| `dashboard_run_report` | `the_report_is_written_ONCE` | merged into `dashboard_run_report::the_report_is_written_ONCE_and_RECORDED_on_the_run` |
| `dashboard_serves_assets_not_tests` | `the_mount_still_serves_the_dashboard` | covered by `dashboard_assets_are_compressed::a_module_comes_back_gzipped_intact_and_still_revalidated` |
| `db_lock_error_predicate` | `the_message_python_sqlite3_actually_raises` | merged into `db_lock_error_predicate::every_real_contention_form_is_recognised` |
| `db_lock_error_predicate` | `the_table_level_variant` | merged into `db_lock_error_predicate::every_real_contention_form_is_recognised` |
| `db_lock_error_predicate` | `a_wrapped_message_still_matches` | merged into `db_lock_error_predicate::every_real_contention_form_is_recognised` |
| `db_lock_error_predicate` | `a_busy_variant` | merged into `db_lock_error_predicate::every_real_contention_form_is_recognised` |
| `db_lock_error_predicate` | `a_word_that_merely_STARTS_with_busy_still_matches` | merged into `db_lock_error_predicate::every_real_contention_form_is_recognised` |
| `db_lock_error_predicate` | `the_SQLITE_BUSY_result_code_spelling` | merged into `db_lock_error_predicate::every_real_contention_form_is_recognised` |
| `db_lock_error_predicate` | `the_check_is_CASE_INSENSITIVE` | merged into `db_lock_error_predicate::every_real_contention_form_is_recognised` |
| `db_lock_error_predicate` | `the_exception_TYPE_is_not_consulted` | merged into `db_lock_error_predicate::every_real_contention_form_is_recognised` |
| `db_lock_error_predicate` | `an_ordinary_error_is_not_contention` | merged into `db_lock_error_predicate::nothing_else_is_contention` |
| `db_lock_error_predicate` | `a_readonly_database_is_NOT_contention` | merged into `db_lock_error_predicate::nothing_else_is_contention` |
| `db_lock_error_predicate` | `a_missing_table_is_NOT_contention` | merged into `db_lock_error_predicate::nothing_else_is_contention` |
| `db_lock_error_predicate` | `an_INTEGRITY_error_is_not_contention` | merged into `db_lock_error_predicate::nothing_else_is_contention` |
| `db_lock_error_predicate` | `an_exception_with_no_message_is_not_contention` | merged into `db_lock_error_predicate::nothing_else_is_contention` |
| `db_lock_error_predicate` | `None_is_not_contention` | merged into `db_lock_error_predicate::nothing_else_is_contention` |
| `db_lock_error_predicate` | `BLOCKED_is_not_LOCKED` | merged into `db_lock_error_predicate::BLOCKED_and_UNLOCKED_are_not_LOCKED` |
| `db_lock_error_predicate` | `UNLOCKED_is_not_LOCKED` | merged into `db_lock_error_predicate::BLOCKED_and_UNLOCKED_are_not_LOCKED` |
| `db_pool` | `CONTROL_a_returned_connection_is_reused` | covered by `db_pool::rows_come_back_as_Row_even_after_a_caller_changed_the_factory` |
| `db_pool` | `a_PRAGMA_makes_the_connection_unpoolable` | merged into `db_pool::a_PRAGMA_through_any_execute_helper_makes_the_connection_unpoolable` |
| `dead_terminal_spawn_query` | `a_spawn_whose_only_terminal_is_dead_is_finalizable` | covered by `dead_terminal_spawn_query::the_two_queries_PARTITION_the_candidates` |
| `dead_terminal_spawn_query` | `a_LIVE_SIBLING_spares_the_spawn` | covered by `dead_terminal_spawn_query::the_two_queries_PARTITION_the_candidates` |
| `dead_terminal_spawn_query` | `a_spawn_with_NO_dead_terminal_is_not_masked` | covered by `dead_terminal_spawn_query::the_two_queries_PARTITION_the_candidates` |
| `dead_terminal_spawn_query` | `a_finalizable_spawn_is_not_ALSO_counted_as_masked` | covered by `dead_terminal_spawn_query::the_two_queries_PARTITION_the_candidates` |
| `default_capabilities_adapter` | `codex_managed_has_full_set` | merged into `default_capabilities_adapter::managed_runtimes_advertise_managed_run_interrupt_and_steer` |
| `default_capabilities_adapter` | `opencode_managed_has_prompt_async_steer` | merged into `default_capabilities_adapter::managed_runtimes_advertise_managed_run_interrupt_and_steer` |
| `default_capabilities_adapter` | `pi_managed_still_advertises_managed_run_and_steer` | merged into `default_capabilities_adapter::managed_runtimes_advertise_managed_run_interrupt_and_steer` |
| `default_settings_plan4` | `managed_via_wrapper_defaults_to_codex_hermes_only` | covered by `api_v2_regressions::settings_include_dashboard_appearance_defaults` |
| `default_settings_plan4` | `managed_pty_eager_spawn_defaults_to_true` | covered by `api_v2_regressions::settings_include_dashboard_appearance_defaults` |
| `deleting_shared_things_requires_an_owner` | `a_stranger_cannot_delete_someone_elses_artifact` | merged into `deleting_shared_things_requires_an_owner::a_stranger_cannot_delete_someone_elses_artifact_and_it_stays` |
| `deleting_shared_things_requires_an_owner` | `a_refused_artifact_delete_leaves_it_in_place` | merged into `deleting_shared_things_requires_an_owner::a_stranger_cannot_delete_someone_elses_artifact_and_it_stays` |
| `deleting_shared_things_requires_an_owner` | `a_MEMBER_cannot_delete_a_channel_they_did_not_create` | merged into `deleting_shared_things_requires_an_owner::a_MEMBER_cannot_delete_a_channel_they_did_not_create_and_its_history_stays` |
| `deleting_shared_things_requires_an_owner` | `a_refused_channel_delete_keeps_the_channel_and_its_messages` | merged into `deleting_shared_things_requires_an_owner::a_MEMBER_cannot_delete_a_channel_they_did_not_create_and_its_history_stays` |
| `derive_is_exhaustively_covered` | `derive_is_total` | covered by `derive_is_exhaustively_covered::no_input_produces_a_status_outside_the_vocabulary` |
| `dispatch_buffer_marker_forgery` | `a_forged_marker_in_a_body_does_not_reach_the_rendered_item` | covered by `dispatch_buffer_marker_forgery::a_body_full_of_forged_markers_does_not_inflate_the_count` |
| `dispatch_channel_claim` | `codex_managed_wrapper_backed_claims_channel` | merged into `dispatch_channel_claim::codex_and_hermes_managed_wrapper_backed_claims_channel` |
| `dispatch_channel_claim` | `hermes_managed_wrapper_backed_claims_channel` | merged into `dispatch_channel_claim::codex_and_hermes_managed_wrapper_backed_claims_channel` |
| `dispatch_claude_coldstart` | `send_to_dead_managed_claude_coldstarts_spawn_request` | merged into `dispatch_claude_coldstart::send_to_dead_managed_claude_coldstarts_ONE_spawn_request` |
| `dispatch_claude_coldstart` | `send_is_idempotent_on_existing_claimable_spawn` | merged into `dispatch_claude_coldstart::send_to_dead_managed_claude_coldstarts_ONE_spawn_request` |
| `dispatch_control_claim` | `a_pending_control_is_handed_to_the_runs_TARGET` | covered by `dispatch_control_claim::a_waiting_poll_returns_as_soon_as_there_IS_a_control` |
| `dispatch_control_refusals` | `a_recognised_status_gets_past_the_allowlist_in_ANY_casing` | merged into `dispatch_control_refusals::a_recognised_status_is_accepted_and_STORED_normalised_in_ANY_casing` |
| `dispatch_control_refusals` | `a_completed_control_reports_and_STORES_the_normalised_status` | merged into `dispatch_control_refusals::a_recognised_status_is_accepted_and_STORED_normalised_in_ANY_casing` |
| `dispatch_control_refusals` | `a_failed_control_stores_the_normalised_status_too` | merged into `dispatch_control_refusals::a_recognised_status_is_accepted_and_STORED_normalised_in_ANY_casing` |
| `dispatch_control_settlement_names_its_actor` | `a_settlement_with_NO_actor_is_refused` | merged into `dispatch_control_settlement_names_its_actor::a_settlement_with_NO_actor_is_refused_and_NAMES_the_cause_and_the_fix` |
| `dispatch_control_settlement_names_its_actor` | `an_EMPTY_actor_is_refused_like_a_missing_one` | merged into `dispatch_control_settlement_names_its_actor::a_settlement_with_NO_actor_is_refused_and_NAMES_the_cause_and_the_fix` |
| `dispatch_control_settlement_names_its_actor` | `the_refusal_NAMES_the_cause_and_the_fix` | merged into `dispatch_control_settlement_names_its_actor::a_settlement_with_NO_actor_is_refused_and_NAMES_the_cause_and_the_fix` |
| `dispatch_control_settlement_names_its_actor` | `the_claiming_machine_CAN_settle_it` | merged into `dispatch_control_settlement_names_its_actor::the_claiming_machine_CAN_settle_it_and_its_actor_is_STORED_and_AUDITED` |
| `dispatch_control_settlement_names_its_actor` | `the_actor_is_STORED_on_the_control` | merged into `dispatch_control_settlement_names_its_actor::the_claiming_machine_CAN_settle_it_and_its_actor_is_STORED_and_AUDITED` |
| `dispatch_control_settlement_names_its_actor` | `the_actor_appears_in_the_run_audit_trail` | merged into `dispatch_control_settlement_names_its_actor::the_claiming_machine_CAN_settle_it_and_its_actor_is_STORED_and_AUDITED` |
| `dispatch_controls_claim_io` | `the_leftovers_stay_PENDING` | covered by `dispatch_controls_claim_io::at_most_TWENTY_controls_are_claimed_at_once` |
| `dispatch_controls_claim_io` | `the_requester_key_is_FROM_not_from_agent` | covered by `dispatch_controls_claim_io::the_returned_control_carries_what_a_bridge_needs_to_act` |
| `dispatch_controls_claim_io` | `the_wrong_host_is_NOT_an_error` | merged into `dispatch_controls_claim_io::a_bridge_on_ANOTHER_HOST_claims_nothing` |
| `dispatch_controls_claim_io` | `the_SAME_HOST_claims_normally` | covered by `dispatch_control_claim::the_linux_and_WSL_spellings_of_ONE_machine_match` |
| `dispatch_controls_claim_io` | `the_REQUEST_object_is_never_read` | covered by `dispatch_controls_claim_io::a_PENDING_control_is_returned_and_marked_claimed` |
| `dispatch_controls_claim_io` | `NO_run_id_claims_across_all_of_the_agents_runs` | covered by `dispatch_controls_claim_io::a_PENDING_control_is_returned_and_marked_claimed` |
| `dispatch_endpoint_wrapper_backed` | `dispatch_endpoint_routes_codex_wrapper_backed_to_channel` | merged into `dispatch_endpoint_wrapper_backed::dispatch_endpoint_routes_codex_and_hermes_wrapper_backed_to_channel` |
| `dispatch_endpoint_wrapper_backed` | `dispatch_endpoint_routes_hermes_wrapper_backed_to_channel` | merged into `dispatch_endpoint_wrapper_backed::dispatch_endpoint_routes_codex_and_hermes_wrapper_backed_to_channel` |
| `dispatch_fix_hint` | `a_resident_agent_whose_bridge_is_gone_is_told_to_restart_the_wrapper` | covered by `dispatch_fix_hint::the_branch_keys_on_a_SUBSTRING_of_prose_written_elsewhere` |
| `dispatch_fix_hint` | `resident_PI_is_presence_only_and_must_be_SPAWNED` | covered by `dispatch_fix_hint::presence_only_outranks_the_unlaunchable_branch_for_pi` |
| `dispatch_run_queries` | `the_list_filters_by_TARGET_agent` | merged into `dispatch_run_queries::the_filters_narrow_by_TARGET_SENDER_and_STATUS_and_COMBINE` |
| `dispatch_run_queries` | `the_list_filters_by_SENDER` | merged into `dispatch_run_queries::the_filters_narrow_by_TARGET_SENDER_and_STATUS_and_COMBINE` |
| `dispatch_run_queries` | `the_list_filters_by_STATUS` | merged into `dispatch_run_queries::the_filters_narrow_by_TARGET_SENDER_and_STATUS_and_COMBINE` |
| `dispatch_run_queries` | `a_limit_of_ZERO_is_refused` | merged into `dispatch_run_queries::a_limit_ABOVE_THE_CEILING_or_ZERO_is_refused_rather_than_silently_capped` |
| `dispatch_run_queries` | `a_limit_ABOVE_THE_CEILING_is_refused_rather_than_silently_capped` | merged into `dispatch_run_queries::a_limit_ABOVE_THE_CEILING_or_ZERO_is_refused_rather_than_silently_capped` |
| `dispatch_run_queries` | `a_runs_SOURCE_CONTROLS_are_attached` | merged into `dispatch_run_queries::controls_are_attached_to_the_RIGHT_run` |
| `dispatch_run_queries` | `a_run_is_never_reported_as_blocking_ITSELF` | covered by `dispatch_run_queries::blockedBy_is_only_computed_for_QUEUED_runs` |
| `dispatch_run_queries` | `an_UNKNOWN_order_is_refused` | merged into `dispatch_run_queries::an_UNKNOWN_order_or_a_ZERO_limit_or_cursor_is_refused` |
| `dispatch_run_queries` | `a_limit_of_ZERO_is_refused` | merged into `dispatch_run_queries::an_UNKNOWN_order_or_a_ZERO_limit_or_cursor_is_refused` |
| `dispatch_run_queries` | `a_before_of_ZERO_is_refused` | merged into `dispatch_run_queries::an_UNKNOWN_order_or_a_ZERO_limit_or_cursor_is_refused` |
| `dispatch_run_queries` | `HAS_MORE_is_false_on_the_last_page` | covered by `dispatch_run_queries::NEXT_BEFORE_pages_through_the_log_without_repeating_or_skipping` |
| `dispatch_run_queries` | `HAS_MORE_is_reported_when_the_page_is_not_the_whole_log` | covered by `dispatch_run_queries::NEXT_BEFORE_pages_through_the_log_without_repeating_or_skipping` |
| `dispatch_run_state` | `the_returned_id_is_the_one_that_was_written` | merged into `dispatch_run_state::a_control_is_recorded_as_PENDING_for_its_run_under_the_RETURNED_id` |
| `dispatch_run_state` | `a_control_with_NO_REQUESTER_still_records_who_is_unknown` | merged into `dispatch_run_state::the_control_is_ALSO_recorded_as_a_run_EVENT_naming_the_requester` |
| `dispatch_run_state` | `a_QUEUED_run_is_completed_by_a_reply` | merged into `dispatch_run_state::a_QUEUED_or_DELIVERED_run_is_completed_by_a_reply_and_stamped` |
| `dispatch_run_state` | `a_DELIVERED_run_is_completed_by_a_reply` | merged into `dispatch_run_state::a_QUEUED_or_DELIVERED_run_is_completed_by_a_reply_and_stamped` |
| `dispatch_run_state` | `completion_stamps_FINISHED_AT` | merged into `dispatch_run_state::a_QUEUED_or_DELIVERED_run_is_completed_by_a_reply_and_stamped` |
| `dispatch_run_state` | `the_STATUS_is_matched_case_insensitively` | merged into `dispatch_run_state::a_QUEUED_or_DELIVERED_run_is_completed_by_a_reply_and_stamped` |
| `dispatch_run_state` | `a_CHANNEL_delivery_completes_even_while_CLAIMED` | merged into `dispatch_run_state::a_CHANNEL_RESIDENT_or_TERMINAL_run_completes_even_while_CLAIMED_or_RUNNING` |
| `dispatch_run_state` | `a_RESIDENT_delivery_completes_even_while_claimed` | merged into `dispatch_run_state::a_CHANNEL_RESIDENT_or_TERMINAL_run_completes_even_while_CLAIMED_or_RUNNING` |
| `dispatch_run_state` | `a_TERMINAL_dispatch_completes_even_while_claimed` | merged into `dispatch_run_state::a_CHANNEL_RESIDENT_or_TERMINAL_run_completes_even_while_CLAIMED_or_RUNNING` |
| `dispatch_run_state` | `the_MODE_is_matched_case_insensitively` | merged into `dispatch_run_state::a_CHANNEL_RESIDENT_or_TERMINAL_run_completes_even_while_CLAIMED_or_RUNNING` |
| `dispatch_run_state` | `a_CLAIMED_managed_run_is_NOT_completed` | merged into `dispatch_run_state::a_CLAIMED_or_RUNNING_managed_run_is_NOT_completed` |
| `dispatch_run_state` | `a_RUNNING_managed_run_is_NOT_completed` | merged into `dispatch_run_state::a_CLAIMED_or_RUNNING_managed_run_is_NOT_completed` |
| `dispatch_run_state` | `an_ALREADY_COMPLETED_run_is_not_re_finished` | covered by `dispatch_run_state::an_EXISTING_finished_at_is_not_overwritten` |
| `dispatch_run_state` | `only_the_named_messages_runs_are_touched` | covered by `dispatch_run_state::blank_and_duplicate_message_ids_are_dropped_before_the_query` |
| `dispatch_run_state` | `nothing_to_cancel_returns_an_empty_list` | covered by `dispatch_run_state::a_run_that_has_ALREADY_STARTED_is_left_alone` |
| `dispatch_run_state` | `the_returned_ids_are_the_ones_actually_cancelled` | covered by `dispatch_run_state::a_run_that_has_ALREADY_STARTED_is_left_alone` |
| `dispatch_source_message_ids_are_structural` | `a_real_buffer_item_still_yields_its_source_id` | covered by `dispatch_source_message_ids_are_structural::several_buffered_items_all_yield_their_ids` |
| `dispatch_source_message_ids_are_structural` | `several_buffered_items_all_yield_their_ids` | covered by `read_receipt_injection_via_dispatch_body::a_GENUINE_merged_buffer_still_recovers_every_source_id` |
| `dispatch_source_message_ids_are_structural` | `the_primary_id_is_returned_even_with_no_body` | covered by `read_receipt_injection_via_dispatch_body::the_primary_id_is_always_kept` |
| `docs_do_not_send_readers_to_a_retired_check` | `every_check_the_doctor_runs_is_in_the_table` | covered by `the_doctor_table_lists_the_real_checks::no_running_check_is_undocumented` |
| `each_declared_host_field_survives_omission` | `every_declared_field_has_a_sentinel` | covered by `each_declared_host_field_survives_omission::every_declared_field_survives_omission_and_yields_to_contradiction` |
| `ended_status_sets_agree` | `the_group_is_pairwise_equal_not_merely_each_equal_to_a_constant` | covered by `ended_status_sets_agree::all_four_hold_the_same_statuses` |
| `ended_status_sets_agree` | `the_status_vocabulary_is_the_ended_half_not_everything` | covered by `ended_status_sets_agree::all_four_hold_the_same_statuses` |
| `env_status_fails_open_by_decision` | `a_SILENT_bridge_with_a_GOOD_timestamp_still_ages_to_offline` | covered by `environment_status_vocabulary::only_the_heartbeat_states_age_out` |
| `env_status_fails_open_by_decision` | `terminal_DECISIONS_are_never_aged_by_a_timestamp` | covered by `environment_status_vocabulary::only_the_heartbeat_states_age_out` |
| `environment_actionable_sql` | `a_fresh_online_environment_is_actionable` | covered by `environment_actionable_sql::a_stamp_with_no_zone_suffix_is_still_datable` |
| `every_host_owned_field_is_declared` | `both_carriers_are_represented` | covered by `each_declared_host_field_survives_omission::both_carriers_are_actually_exercised` |
| `every_host_owned_field_is_declared` | `every_declared_field_exists_on_the_model` | covered by `each_declared_host_field_survives_omission::every_declared_field_survives_omission_and_yields_to_contradiction` |
| `github_compare_update_check` | `NOTHING_ELSE_from_the_payload_crosses_the_boundary` | covered by `github_compare_update_check::the_three_counts_are_lifted_from_the_payload` |
| `gpu_allocator` | `the_device_total_is_the_sum_of_its_tenants` | covered by `gpu_allocator::status_reports_what_an_operator_needs_to_place_work` |
| `health_reports_how_many_dashboards_are_connected` | `it_reports_a_number` | covered by `health_reports_how_many_dashboards_are_connected::the_number_follows_the_manager` |
| `health_reports_which_build_answered` | `health_still_reports_status_first` | covered by `health_reports_how_many_dashboards_are_connected::a_broken_manager_cannot_take_the_container_down` |
| `hermes_channel_routing` | `managed_claude_in_channel_managed_runtimes` | covered by `hermes_channel_routing::managed_claude_channel_behavior_unchanged` |
| `hermes_resume_readiness` | `the_last_token_helper_is_rfind_without_the_substring_bug` | covered by `hermes_resume_readiness::the_last_occurrence_of_each_word_decides` |
| `hook_refresh_branch_runs` | `somebody_elses_hook_does_not_count_as_ours` | covered by `reinstall_keeps_the_hook::somebody_elses_hook_is_not_ours[claude]` |
| `idle_worker_query` | `an_idle_managed_worker_is_selected` | covered by `idle_worker_query::a_DELIVERED_run_owing_NO_reply_does_not_spare_it` |
| `inbox_message_view` | `FULL_mode_includes_the_body` | covered by `inbox_message_view::a_long_body_is_CLIPPED_in_the_preview_but_not_in_the_body` |
| `inbox_message_view` | `a_message_that_is_NOT_a_reply_has_NO_parentContext_key` | covered by `inbox_message_view::an_EMPTY_in_reply_to_is_not_a_reply` |
| `info_completion_signal` | `the_signal_is_not_simply_always_false` | covered by `info_completion_signal::a_completion_claim_still_closes_the_contract` |
| `input_limit_refusals` | `a_description_of_exactly_2000_characters_is_accepted` | covered by `input_limit_refusals::the_description_cap_counts_CHARACTERS_not_bytes` |
| `install_claude_session_validate` | `rendered_wrapper_has_no_unsubstituted_placeholders` | covered by `no_launcher_ships_an_unsubstituted_placeholder::no_client_renders_a_literal_placeholder` |
| `install_claude_session_validate` | `claude_wrapper_defines_validate_helper` | covered by `install_claude_session_validate::claude_wrapper_validate_is_non_fatal` |
| `install_claude_session_validate` | `rendered_wrapper_is_not_empty` | covered by `install_claude_session_validate::claude_wrapper_validate_is_non_fatal` |
| `install_claude_turn_hooks` | `turn_start_hook_wires_userpromptsubmit_and_posttooluse` | covered by `resident_hooks_reach_the_service_authenticated::claude_hooks_replace_the_curl_entries_once_and_keep_the_users` |
| `install_claude_turn_hooks` | `turn_end_hook_wires_stop_and_post_compaction_session_start` | covered by `resident_hooks_reach_the_service_authenticated::claude_hooks_replace_the_curl_entries_once_and_keep_the_users` |
| `install_hermes_plugin_enable_is_bounded` | `a_hermes_that_hangs_is_abandoned_rather_than_waited_on` | covered by `install_hermes_plugin_enable_is_bounded::a_hermes_that_IGNORES_TERM_is_still_abandoned` |
| `install_hermes_session_rediscover` | `hermes_installer_patches_visible_session_bind` | covered by `hermes_aify_plugin::gateway_patch_registers_visible_bind_method` |
| `install_hermes_session_rediscover` | `hermes_installer_preserves_wrapper_active_session_file` | covered by `hermes_aify_plugin::active_session_file_patch_preserves_wrapper_file` |
| `installed_endpoint_recovery` | `reads_the_endpoint_out_of_a_launcher_install_sh_writes_today` | covered by `both_endpoint_readers_agree::both_readers_find_the_endpoint_for_every_client_the_installer_renders` |
| `installed_endpoint_recovery` | `an_empty_directory_answers_nothing_rather_than_a_guess` | covered by `both_endpoint_readers_agree::both_readers_say_nothing_for_an_empty_directory` |
| `installed_endpoint_recovery` | `an_unrendered_template_is_not_an_endpoint` | covered by `both_endpoint_readers_agree::both_readers_refuse_an_unrendered_template` |
| `launch_mode_is_normalised` | `it_is_idempotent` | covered by `launch_mode_is_normalised::the_other_known_modes_survive` |
| `launcher_state_reaches_the_environment_row` | `the_model_keeps_the_two_launcher_fields` | covered by `launcher_state_reaches_the_environment_row::a_heartbeat_carrying_launcher_state_exposes_it_on_the_row` |
| `listen_long_poll` | `a_message_THIS_agent_has_read_is_not_redelivered` | covered by `listen_long_poll::returning_a_message_MARKS_it_read_so_it_is_not_redelivered` |
| `live_screen_resize` | `zero_and_none_mean_default_not_minimum` | covered by `live_screen_resize::the_requested_grid_is_clamped_before_it_is_applied[0-0-expected3]` |
| `live_screen_resize` | `a_failed_resize_is_visible_in_the_count` | covered by `live_screen_resize::an_unusable_dimension_drops_the_screen_rather_than_keeping_a_bad_one` |
| `live_state_cache_expire_vs_drop` | `expire_and_drop_are_not_interchangeable` | covered by `live_state_cache_expire_vs_drop::expire_keeps_the_derived_value_readable` |
| `live_state_cache_expire_vs_drop` | `set_then_get_round_trips` | covered by `live_state_cache_expire_vs_drop::expire_keeps_the_derived_value_readable` |
| `longpoll` | `returns_immediately_when_first_attempt_is_non_empty` | covered by `claim_emptiness::an_actionable_result_ends_the_wait_on_the_FIRST_attempt` |
| `longpoll` | `wait_ms_zero_is_legacy_single_attempt` | covered by `claim_emptiness::waitMs_zero_never_calls_the_predicate_at_all` |
| `longpoll` | `lock_error_becomes_empty_result_not_a_raise` | covered by `db_lock_error_predicate::a_lock_error_becomes_the_long_polls_EMPTY_result` |
| `longpoll` | `non_lock_error_still_raises` | covered by `db_lock_error_predicate::a_NON_lock_error_still_propagates_through_the_long_poll` |
| `longpoll` | `lock_without_lock_result_propagates` | covered by `db_lock_error_predicate::without_a_lock_result_even_contention_propagates` |
| `onboarding_guides` | `skill_mirrors_match_and_stay_under_existing_budget` | covered by `skill_mirror_parity::claude_and_agents_skill_mirrors_are_identical` |

**Could not fail, found while doing this** (each stayed green under the mutation of the
property it is named for):

- `api_v2_regressions::managed_hygiene_keeps_online_console`: rewritten as `api_v2_regressions::managed_hygiene_keeps_live_console`, which goes red.
- `api_v2_regressions::busy_claude_terminal_output_does_not_close_running_turn`: rewritten as `api_v2_regressions::a_claude_terminal_turn_stays_open_until_its_screen_is_idle_and_quiet`, which goes red.
- `api_v2_regressions::claude_spinner_after_prompt_does_not_close_running_turn`: rewritten as `api_v2_regressions::a_claude_terminal_turn_stays_open_until_its_screen_is_idle_and_quiet`, which goes red.
- `api_v2_regressions::old_idle_claude_prompt_does_not_close_new_terminal_turn`: rewritten as `api_v2_regressions::a_claude_terminal_turn_stays_open_until_its_screen_is_idle_and_quiet`, which goes red.
- `api_v2_regressions::claude_prompt_footer_alone_does_not_report_blocked`: rewritten as `api_v2_regressions::a_finished_claude_screen_that_asks_nothing_is_not_awaiting_input`, which goes red.
- `api_v2_regressions::claude_done_narration_with_your_call_does_not_report_blocked`: rewritten as `api_v2_regressions::a_finished_claude_screen_that_asks_nothing_is_not_awaiting_input`, which goes red.
- `api_v2_regressions::the_claimed_STATUS_counts_even_with_no_claimed_at`: rewritten as `api_v2_regressions::init_db_fails_controls_for_superseded_environment_bridge`, which goes red.
- `api_v2_regressions::terminal_route_delivered_does_not_pin_working_status`: deleted; `active_run_lookup::a_MANAGED_delivery_is_excluded` goes red under the same mutation.
- `a_declared_cascade_actually_fires::THE_PRAGMA_SURVIVES_THE_EXECUTESCRIPT_IT_IS_SET_IN`: deleted; `a_declared_cascade_actually_fires::THE_EFFECT_deleting_a_run_removes_its_controls` goes red under the same mutation.
- `a_long_poll_is_not_a_slow_request::a_child_task_REBINDING_the_var_does_not_reach_the_parent`: deleted; `a_long_poll_is_not_a_slow_request::the_holder_survives_the_middleware_TASK_BOUNDARY` goes red under the same mutation.
- `a_run_that_owed_no_reply_is_not_told_one_is_missing::the_fixture_starts_with_both_runs_OPEN`: deleted; `a_run_that_owed_no_reply_is_not_told_one_is_missing::BOTH_runs_are_closed_when_the_terminal_ends` goes red under the same mutation.
- `console_reads_the_store_that_recorded_something::a_strip_gate_would_have_accepted_it`: deleted; `console_reads_the_store_that_recorded_something::the_exit_marker_alone_says_nothing` goes red under the same mutation.
- `db_pool::a_PRAGMA_through_the_other_execute_helpers_makes_the_connection_unpoolable`: rewritten as `db_pool::a_PRAGMA_through_any_execute_helper_makes_the_connection_unpoolable`, which goes red.
- `api_v2_regressions::agents_list_refreshes_expired_cached_status_from_environment`: fixed in place, now red under that mutation.
- `api_v2_regressions::working_via_turnbusy_has_short_refresh_after`: fixed in place, now red under that mutation.
- `api_v2_regressions::environment_list_marks_missing_heartbeat_offline_and_orders_stably`: fixed in place, now red under that mutation.
- `api_v2_regressions::managed_hygiene_keeps_live_console`: fixed in place, now red under that mutation.
- `a_capability_sequence_does_not_become_screen_content::feeding_the_sequence_one_byte_at_a_time_still_removes_it`: fixed in place, now red under that mutation.
- `a_dead_terminal_does_not_hold_its_tail_forever::it_costs_nothing_when_nothing_is_held`: fixed in place, now red under that mutation.
- `agent_status_read_gate::get_agent_downgrades_stale_online_with_no_live_worker`: fixed in place, now red under that mutation.
- `agent_status_read_gate::list_agents_downgrades_stale_online_with_no_live_worker`: fixed in place, now red under that mutation.
- `agent_status_read_gate::stale_synth_vterm_row_does_not_keep_agent_online`: fixed in place, now red under that mutation.
- `hermes_resume_readiness::the_last_occurrence_of_each_word_decides`: fixed in place, now red under that mutation.

**Judged NOT duplicates after mutation, and kept** (the candidate went red under a mutation
its look-alike survived, or the pair crosses a slice boundary and was left alone):

- `api_v2_regressions::channel_history_excludes_inbox_fanout_rows` vs `a_channel_message_is_never_invisible_to_both_readers::AChannelMessageIsNeverInvisibleToBothReadersTests::test_a_fan_out_copy_is_NOT_in_the_transcript`: channel_send.py canonical INSERT drops req.priority (always 'normal'): candidate RED, the whole transcript file, test_message_idempotency.py and test_a_capped_list_says_it_is_ca...
- `api_v2_regressions::buffered_active_status_does_not_overwrite_stopped_terminal` vs `terminal_status_transition::test_a_finished_terminal_is_never_resurrected`: terminal_output.py `next_status = _terminal_status_transition(stored_status, status)` -> `next_status = status` (call site bypasses the transition gate): candidate RED; all 71 t...
- `api_v2_regressions::managed_hermes_online_requires_live_channel_sidecar` vs `status_deliverability (test_managed_hermes_channel_enabled_with_stale_sidecar_is_available / _with_live_sidecar_is_online)`: channel_delivery.py drops 'hermes' from _CHANNEL_SIDECAR_DELIVERY_RUNTIMES: candidate RED; every hermes test in test_status_deliverability.py, test_resurrect_managed_console.py,...
- `api_v2_regressions::managed_claude_online_requires_live_console` vs `status_deliverability::ClaudeDeliverabilityTests (test_managed_claude_with_live_pty_and_live_sidecar_is_online)`: channel_delivery.py `if sidecar_live and console_live:` -> `if sidecar_live:` (a headless live sidecar counts as a worker): candidate RED; test_status_deliverability.py, test_re...
- `api_v2_regressions::stale_claimed_run_aged_out_despite_live_bridge` vs `orphaned_runs_query::OrphanedRunsQueryTests::test_the_CEILING_ages_out_a_run_whose_bridge_is_still_alive`: orphaned_managed_runs.py drops `await _invalidate_agent_live_state(db, target_agent)` after aging out a run: candidate RED; test_orphaned_runs_query.py and test_reconcile_sweep_...
- `api_v2_regressions::managed_via_wrapper_routes_dispatch_as_channel` vs `dispatch_endpoint_wrapper_backed::DispatchEndpointWrapperBackedTests::test_dispatch_endpoint_routes_hermes_wrapper_backed_to_channel`: execution_mode.py `if settings is not None and _managed_via_wrapper_for_runtime(settings, runtime):` -> `if _managed_via_wrapper_for_runtime(settings or {}, runtime):` (a call w...
- `api_v2_regressions::rejects_cross_os_codex_live_cwd_registration` vs `registration_cwd_gate::RegistrationCwdGateTests::test_a_windows_drive_cwd_is_refused_on_posix_hosts_either_way`: registration.py stops calling _validate_registration_cwd: candidate red, unit survivor green (it calls the validator directly). Only the candidate pins the route wiring.
- `api_v2_regressions::terminal_end_closes_active_claude_terminal_run` vs `a_run_that_owed_no_reply_is_not_told_one_is_missing::ARunThatOwedNoReplyIsNotToldOneIsMissing::test_BOTH_runs_are_closed_when_the_terminal_ends`: terminal_runs.py run_status forced to 'failed' for a stopped terminal: candidate red (asserts cancelled), survivor green (asserts only not running).
- `api_v2_regressions::A_LIVENESS_FRAME_DOES_NOT_RESET_THE_IDLE_CLOSE_CLOCK` vs `idle_terminal_run_closers_agree::IdleTerminalRunClosersAgreeTests::test_both_close_an_idle_run_and_leave_the_terminal_alone`: claude idle closer reads updated_at instead of output_at: candidate red, survivor green (it passes quiet_seconds=0).
- `api_v2_regressions::session_stop_marks_resident_owner_for_bridge_termination` vs `agent_stop_resume::AgentStopResumeTests::test_the_stop_note_differs_by_session_mode`: the SESSION-control resident stop note in agent_sessions.py changed: candidate red, survivor green. It is a second implementation of the note, not the agent-control one.
- `api_v2_regressions::session_restart_queues_spawn_request_from_stored_spec` vs `session_control_refusals::SessionControlRefusalTests::test_a_restart_creates_a_spawn_request_that_reuses_the_saved_backing`: session_restart.py drops req.body from initial_message: candidate red, survivor green.
- `api_v2_regressions::deleted_agent_tombstone_blocks_auto_reregister_until_explicit_restore` vs `lifecycle_phase7::AgentDeleteTombstoneTests (all 5) and agent_id_reuse_refusals::TombstoneRegistrationGateTests`: routers/agents/registration.py: delete the '_enforce_tombstone_registration_gate(req, tombstone)' call. The candidate goes red (auto re-register answers 200); test_agent_id_reus...
- `a_dead_tui_is_not_diagnosed_from_its_screen::prose_on_a_drawing_screen_is_still_not_a_cause` vs `terminal_diagnostics::DecoratedMarkerLineIsNotAnEpitaphTests::test_decoration_anywhere_still_blocks_the_FALLBACK`: fallback guard checks only lines[0] instead of any line: candidate (decoration last) red, look-alike (decoration first) green; the reverse holds for lines[-1]. Together they pin...
- `agent_control_refusals::a_start_with_a_claimable_environment_creates_a_spawn_request` vs `agent_control_refusals::AgentControlRefusalTests::test_clicking_start_twice_during_a_slow_boot_is_not_an_error`: session_ops.py start returns spawnRequested False: candidate RED, looked_like GREEN (the second click answers spawnPending).
- `accessor_rewrite::no_constants_is_a_no_op` vs `accessor_rewrite::AccessorRewriteExactOutputTests::test_a_constant_that_does_not_appear_is_a_byte_for_byte_no_op`: accessor_rewrite.py early return for an empty constants list returns "" instead of source: candidate RED, looked_like GREEN.
- `active_run_lookup::the_exclusion_only_matches_THAT_run` vs `active_run_lookup::BlockingActiveRunTests::test_a_run_EXCLUDES_ITSELF`: active_run_lookup.py exclusion drops the runId comparison (any non-empty exclude_run_id unblocks): candidate RED, looked_like GREEN.
- `bridge_liveness_beat::a_RESIDENT_beat_cannot_demote_a_managed_wrapper_child` vs `bridge_liveness_beat::BridgeLivenessBeatTests::test_an_EMPTY_kind_cannot_demote_a_managed_wrapper_child (deleted)`: bridge_liveness_beat.py guard IN list without 'managed-wrapper-child': candidate RED, looked_like GREEN. The EMPTY test was deleted as a duplicate of test_an_empty_kind_never_bl...
- `an_orphan_is_a_removed_agent_not_a_missing_row::THE_MIXED_CASE_deletes_only_the_removed_one` vs `an_orphan_is_a_removed_agent_not_a_missing_row::AnOrphanIsARemovedAgentNotAMissingRow::test_a_message_to_a_LIVE_agent_is_untouched`: orphan_messages.py tombstone EXISTS replaced by the old `NOT EXISTS agents` absence test: candidate RED, looked_like GREEN (a registered agent is not an orphan under either pred...
- `console_reads_the_store_that_recorded_something::the_column_still_wins_when_it_says_something` vs `console_reads_the_store_that_recorded_something::ConsoleTailServesTheEventsTests::test_it_still_serves_the_column_when_the_column_has_the_output`: terminal_diagnostics.richest_recording: prefer the column only when the events say nothing -> pure test red, endpoint test green (the endpoint calls richest_recording(streamed,...
- `code_currency::the_verdict_is_never_a_boolean` vs `code_currency::CodeCurrencyTests::test_differing_identities_are_stale_and_BOTH_travel`: code_currency.CURRENT = 'ok' -> candidate red (it pins the wire literals), test_matching_identities_are_current green (compares against the constant).
- `channel_offline_replay::message_beyond_horizon_not_replayed` vs `channel_replay_query::ChannelReplayQueryTests::test_a_message_OUTSIDE_the_window_is_not`: dispatch_queue: pass '-100 years' instead of the horizon cutoff -> candidate red, query test green (it passes its own cutoff).
- `console_command_resume::the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it` vs `service/tests/runtimes/test_console_command (per-adapter command strings)`: capabilities._default_console_argv: handle = '' -> resume test red, every runtimes/test_console_command.py and test_terminal_argv_accompanies_command.py test green; it is the on...
- `dispatch_control_settlement_names_its_actor::an_UNCLAIMED_control_may_be_settled_by_a_named_actor` vs `dispatch_control_refusals::DispatchControlRefusalTests::test_a_recognised_status_is_accepted_and_STORED_normalised_in_ANY_casing`: controls.py owner check without the claimed_machine guard (`if claimed_machine != actor_machine`): unclaimed test (sends a machineId) red, refusals test (no machineId) green
- `dead_terminal_spawn_query::the_masked_count_sees_exactly_what_the_guard_spared` vs `dead_terminal_spawn_query::DeadTerminalSpawnQueryTests::test_the_two_queries_PARTITION_the_candidates`: dead_terminal_spawn_query.py masked count with `AND NOT EXISTS live`: masked_count test red, PARTITION green (it counts the purely dead spawn as 1)
- `longpoll::notify_wakes_waiter_and_re_attempts` vs `claim_emptiness::InsideTheRealLongPollLoopTests::test_an_empty_result_waits_and_a_notify_returns_the_work`: notify() made a no-op: candidate red (10s fallback), survivor green (woke on its 3s fallback)
- `install_claude_turn_hooks::turn_hooks_are_installed_for_claude` vs `resident_hooks_reach_the_service_authenticated::test_claude_hooks_replace_the_curl_entries_once_and_keep_the_users`: removed the install_claude_turn_start_hook call line: candidate red, survivor green (it calls the hook functions itself)
