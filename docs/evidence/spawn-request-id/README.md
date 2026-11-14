# Structured spawn receipt evidence

2026-11-14. Base source e4a8bb4229c4078e8eb138448ffa6b1c773e38dd.

## Change and boundaries

The definition request now optionally returns `spawnRequestId`. The existing queue-success owner stores the actual lifecycle request id in a new `spawn_request_id` column, in the same transaction as its consequence. Both the writer and receipt serializer use the existing lifecycle `REQUEST_ID` grammar. Invalid or unavailable links are omitted. No route, scheduling rule or existing response field changed. The human `consequence` sentence is unchanged.

The existing additive migration supplies an empty default for historical rows. It does not parse or backfill old sentences. The serializer's local import avoids the existing reverse module dependency. Product delta is +7/-1 across three existing files, eight changed lines against a ten-line cap.

## Observed results

All results below are PASSES IN TESTS on private SQLite databases and a synthetic home. These are not live deployment or real-agent execution evidence.

- Old source: the real creation/report route queued a lifecycle request, then failed the assertion comparing its actual id with the missing structured field. One failed, one passed, 15 deselected, exit 1. This was an assertion failure, not a missing-column setup error.
- First implementation: 17 tests and 16 subtests passed, exit 0.
- Guard mutation: replaced only the two new full grammar checks with nonempty checks. Four named assertion failures appeared: an overlong generated id was stored/exposed, and stored leading-hyphen, overlong and newline ids were exposed. The assertions compare omission and stored state together. Sixteen tests and 13 subtests stayed healthy, including valid links and legacy omission. Exit 1. Both grammar checks were restored before qualification.
- Final source and tests: 239 Python tests and 180 subtests passed across the 25 files below, eight existing Starlette deprecation warnings, 16.97 seconds, exit 0.
- Existing receipt consumers: 130 tests passed across seven Node files below, zero failures or skips, exit 0.

The route tests compare the field with an independently read lifecycle row. They exercise report-after-push and push-after-report, report replay, list reads, two migration reapplications, and absence after a queue refusal. Rewording the saved consequence leaves the structured id unchanged. Replays leave exactly one queued spawn.

Serialization controls include valid one- and 128-character ids, null, empty, leading hyphen, 129 characters, newline and a SQLite BLOB. The deliberately modified stored-id fixtures prove serialization validation, not that an arbitrary grammar-valid id exists. A legacy-table fixture drops only the new column, reapplies the real migration twice, and confirms no link is inferred from old prose. An overlong generated-id fixture confirms existing queue/consequence behavior stays unchanged while the new link remains empty and omitted.

## Reproduce

Use the repository's installed Python and Node dependencies, a private HOME/config/data/temp, and no inherited agent, Herdr or credential environment. The captured runner sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, so xdist is loaded explicitly.

```sh
python -m pytest -p xdist.plugin \
  service/tests/test_a_spawn_defines_its_agent_first.py \
  service/tests/test_definition_change_requests.py \
  service/tests/test_agent_lifecycle_requests.py \
  service/tests/test_a_waiting_request_does_not_outlive_its_definition.py \
  service/tests/test_editing_a_defined_agent_becomes_a_request.py \
  service/tests/test_only_the_operator_changes_a_defined_agent.py \
  service/tests/test_legacy_routes_delegate_defined_agents.py \
  service/tests/test_agent_definition_push.py \
  service/tests/test_a_defined_agent_starts_from_its_definition.py \
  service/tests/test_definition_record_serves_the_edit_fields.py \
  service/tests/test_a_spawns_env_vars_reach_its_worker.py \
  service/tests/test_a_same_id_handoff_restarts_fresh.py \
  service/tests/test_an_old_database_still_boots.py \
  service/tests/test_a_migrated_database_matches_a_fresh_one.py \
  service/tests/test_a_p3a_database_gains_the_replay_outcome.py \
  service/tests/test_terminal_argv_migration_applies.py \
  service/tests/test_agent_rename_covers_every_agent_reference.py \
  service/tests/test_no_new_oversized_source_file.py \
  service/tests/test_leaves_do_not_import_the_carrier.py \
  service/tests/test_no_import_cycles.py \
  service/tests/test_no_dead_imports.py \
  service/tests/test_no_reconciler_is_dead_code.py \
  service/tests/test_service_runtime_boundary.py \
  service/tests/test_no_unruled_constant_coincidences.py \
  service/tests/test_status_set_literal_twins_are_frozen.py \
  -q -n 8 --dist loadfile

node --test \
  mcp/stdio/tests/environment-tools-remote.test.js \
  service/new_dashboard/mode-switch-auth.test.mjs \
  service/new_dashboard/inspector-forms.test.mjs \
  service/new_dashboard/definition-request-note.test.mjs \
  service/new_dashboard/agent-session-actions.test.mjs \
  service/new_dashboard/agent-requests.test.mjs \
  service/new_dashboard/agent-mode.test.mjs
```

For the original missing-field RED, put only the new durable-identity test onto the base product and run the owning file with `-k 'spawn_identity_is_durable or a_new_id'`. For the mutation, change the new writer predicate to `bool(request_id)` and the serializer's `REQUEST_ID.fullmatch(spawn_id)` to `bool(spawn_id)`, run the full owning file, then restore both predicates.

## Receipts and limits

Unmodified raw receipts and the sealed `run-targeted.py` are retained under `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`, using the `spawn-receipt-` prefix. Each receipt starts with the exact command/environment metadata and ends with the actual child exit code.

`original-red.txt`, `first-green.txt`, `nonempty-mutant.txt`, and `final-qualified.txt` identify the corresponding runs above. `qualified-xdist.txt` is an earlier 239-test pass before two final trigger/control assertions were added. `qualified.txt` retains the initial pre-collection exit 4 because disabled plugin autoload left `-n/--dist` unavailable. Explicit `-p xdist.plugin` corrected the invocation.

`node.txt` retains 102 passes and one import failure because the new worktree lacked `ws`. No product test in that file ran. Matching `package.json` and `package-lock.json` bytes were checked before copying the earlier isolated checkout's retained private dependencies, without an installer or install script. `node-materialized.txt` is the final 130-test pass.

This is a bounded source qualification, not the full suite or an exhaustive caller analysis. No live install, service rebuild, host restart, provider call or public closure was performed. Automatic gate certification remains unissued without explicit inspected CHECK approval; direct test receipts are not automatic certificates.
