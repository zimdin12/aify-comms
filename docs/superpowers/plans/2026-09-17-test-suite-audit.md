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
   The rest of the ~300 was not enumerated in this file and is still open.
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
