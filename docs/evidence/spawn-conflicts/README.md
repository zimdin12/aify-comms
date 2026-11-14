# Typed spawn conflicts

This is the spawn-conflict remainder of comms issue 26, based on `24af9f9df69011275cc5ad30d63fe91d9ccf286b`. Only two existing HTTP 409 refusals change shape:

```json
{"detail":{"code":"DEFINITION_WAITING","openRequestId":"defreq_example","message":"\"newbie\" already has a request waiting for its host (defreq_example)"}}
{"detail":{"code":"ALREADY_DEFINED","message":"Agent \"newbie\" is defined in aify-env on win32:host-a; start it, and its spawn is built from that definition"}}
```

Human messages remain unchanged for valid receipts. `DEFINITION_WAITING` reuses the lifecycle owner's request-id grammar, 1 to 128 ASCII letters, digits, dot, underscore or hyphen, starting alphanumeric. An invalid stored definition id gives a plain-string 409 without publishing an unusable id or changing the reservation. `ALREADY_DEFINED` has no `openRequestId`. Other conflicts keep their prior response shapes. No endpoint, UI, action, admission order or queue effect is added.

The existing race test still waits for a held write transaction and proves that a definition committed before admission prevents a spawn and its spec. Its opposite-order control remains. Waiting-definition tests cover pending and claimed receipts, valid id boundaries, four invalid ids and unchanged stored rows. Existing undefined-agent, missing-store and invalid-creation controls remain.

## Evidence and reproduction

Persistent receipts are in `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`, never a temporary directory. Their first JSON line records the exact invocation, explicit selected files and sealed fixture environment; `ACTUAL_EXIT` records the command under test.

- `spawn-conflicts-original-red.txt`: both named codes fail against the old sentence forms, three tests and eight subtests failing with 18 tests passing as controls.
- `spawn-conflicts-first-green.txt`: 21 tests plus eight subtests pass after the product change.
- `spawn-conflicts-id-mutant.txt`: deleting only the stored-id guard fails four combined response/nonmutation assertions, while 21 tests and four subtests pass. The guard is restored before qualification.

Python qualification uses `python -m pytest -p xdist.plugin -n 8 --dist loadfile -q` with the exact population in the final receipt. It includes owning spawn tests, lifecycle replay/fences, refusal coverage, the API boundary, documentation, identity and source-size checks. Private development dependencies are copied only after package and lock bytes match; no host install or install scripts run.

PASSES IN TESTS: `spawn-conflicts-final-python.txt` records 494 tests plus 255 subtests passed across 21 files, eight existing Starlette warnings, 31.30 seconds and exit zero. Runtime changes are eight added and three removed lines across two existing owners, under the twenty-line cap. The definition-creation module has 97 lines; the spawn router has 508, already beyond the 400-line review signal. This change adds no new responsibility to that router and does not refactor unrelated admission rules.

The dashboard owner supplied live both-forms reader `ced40ce623178c76c04066fe86299117fa03f757`; the lead separately confirmed that identity before lifting the push hold. Those live observations remain attributed to their owners. Source publication is not a comms deployment, and removal of the dashboard's sentence fallbacks waits for the lead's deployed identity. No real-agent action, rebuild or environment restart is performed here.

The automatic ledger CHECK has no explicit user approval or certificate. Direct successful receipts do not claim ALL MET.
