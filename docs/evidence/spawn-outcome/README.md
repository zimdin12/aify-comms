# Structured spawn outcome evidence

2026-11-14. Base cef5fb0debf37d8c0c57e3b89f96e41ef41b3e5d. Scope is two additive definition-receipt fields, `spawnState` and `spawnReason`. Product delta is +21/-10 across five existing files, 31 changed lines within the revised 32-line cap.

## Contract and implementation

`vocabulary.json` declares `pending`, `queued` and `none` once. Its existing Python reader derives `SPAWN_STATES`. The existing additive migration adds `spawn_state` and `spawn_reason` with empty defaults, so historical rows remain unsupplied.

The applied-creation report stores `pending` beside its existing consequence. The existing spawn settlement returns state, reason and the unchanged human consequence together. Its caller stores all three in one update inside the existing write transaction. A queued spawn has `queued` and an empty stored reason. A lifetime mismatch or queue refusal has `none` and the same reason used in its existing human sentence.

The pure serializer returns only declared stored states. It exposes a stored string reason only for `none`. Invalid states and non-string reasons are omitted. It never examines `consequence` to obtain either field. Existing scheduling still uses its original owed-consequence marker; this change does not replace that mechanism. No route or existing response field changed, including the previous `spawnRequestId` addition.

## PASSES IN TESTS

- Original base product: a real claimed creation was reported applied before its definition publication. The named assertion failed because its `spawnState` was absent rather than `pending`. One failure, one healthy creation control, 16 deselected, exit 1.
- Owning tests after implementation and controls: 20 tests and 29 subtests passed, exit 0.
- Final serializer mutation: accept any nonempty stored state and allow reasons for all declared states. Seven intended assertions failed, with 18 tests and 24 subtests healthy, exit 1. Two route assertions catch reason leakage on actual pending/queued outcomes. Five combined response/nonmutation assertions catch hidden reasons and unknown, wrong-case and BLOB states. Both predicates were restored before final qualification.
- Final 26-file Python selection: 252 tests and 235 subtests passed, eight existing Starlette deprecation warnings, 16.19 seconds, exit 0.
- Final eight-file Node selection: 131 passed, zero failed or skipped, exit 0.

Tests cover both report/push orderings, both `none` paths, durable state and reason values, list/report replay, no duplicate spawn, and consequence rewording independent of structured fields. Valid states, unknown/wrong-case/empty/null/BLOB states, hidden pending/queued reasons and null/BLOB reasons are checked against unchanged database rows. The legacy-table fixture removes both new columns, reapplies the real migration twice, and checks all three old consequence forms remain unclassified. A queued receipt also survives two migration reapplications.

## Reproduce and retained receipts

Use a private HOME/config/data/temp and no inherited agent, Herdr or credential environment. The sealed runner disables pytest plugin autoload, so explicitly load xdist.

For the full final selections, use the exact 25-file Python and seven-file Node commands in [the prior receipt evidence](../spawn-request-id/README.md), adding `service/tests/test_vocabulary_contract.py` to Python and `mcp/stdio/tests/vocabulary-agreement.test.js` to Node. Python flags remain `-p xdist.plugin -q -n 8 --dist loadfile`.

A small direct reproduction is:

```sh
python -m pytest service/tests/test_a_spawn_defines_its_agent_first.py service/tests/test_definition_change_requests.py service/tests/test_vocabulary_contract.py -q
```

For the original RED, retain the new applied-creation state test over the base product and select `-k 'applied_creation_spawn_state or a_new_id'`. For the final mutation, replace `state in SPAWN_STATES` in the serializer's state-map construction with `bool(state)`, and replace `spawn.get('spawnState') == 'none'` with `spawn.get('spawnState') in SPAWN_STATES`. Run the full owning file, then restore both expressions.

Raw unmodified `spawn-outcome-*` receipts and `run-targeted.py` are retained under `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`. Each receipt contains the exact command and environment metadata, complete output and actual child exit status. Relevant suffixes are `original-red.txt`, `controls-green.txt`, `final-serialization-mutant.txt`, `final-qualified.txt` and `final-node.txt`. Earlier `qualified.txt`, `node.txt` and `serialization-mutant.txt` precede the final serializer readability pass and are not relabelled as final runs.

Matching manifests and lockfiles were checked before copying retained dependencies into this isolated checkout. No installer or install script ran. No full suite, live agent, deployment, host restart, provider call or public closure is claimed. Direct test receipts do not supply an automatic CHECK certificate.
