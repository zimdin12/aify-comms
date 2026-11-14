# Lifecycle conflict contract

Comms issue 26 changes only an existing admission refusal, not endpoints or UI. HTTP 409 for an open pending or claimed request has this body:

```json
{"detail":{"code":"OPEN_REQUEST","openRequestId":"life-1","message":"agent has an open lifecycle request life-1"}}
```

`openRequestId` comes from the reserved receipt and must match `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. Admission and conflict serialization use the same grammar. An invalid stored id refuses with a plain-string detail rather than publishing an unusable id. Other lifecycle conflicts keep their existing strings; other statuses keep their existing shapes, including validation-detail arrays. The definition-creation consumer extracts the human message instead of formatting the object as a dictionary.

Same-id replay returns the original intent and receipt before drift checks. The lost-response test raises a client read error after a real private submission commits, then recovers the same receipt with the same request id after definition drift, with no extra lifecycle row. It does not prove a dashboard outcome classifier or a real host effect. Existing revision, lifetime capture, host claim and launch fences remain tested in their owners. Stop, Hold, Release hold and delete rules are unchanged.

The dashboard owner supplied both-forms reader `b5e3b4b8dc0209bab8f2ed380c23f41e9c09b38a`; the lead separately confirmed its running identity before lifting the source-publication hold. Those live observations are attributed to their owners. No deployment or real-agent action was performed by this builder. Removal of the dashboard's string fallback waits for the actual comms rebuild.

## Retained tests and controls

Receipts live in `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`. Each starts with the exact command, checkout and sealed environment, and ends with `ACTUAL_EXIT`.

- `issue26-original-red.txt`: eight intended contract subtest failures on the old string response, 24 tests and 42 subtests passing.
- `issue26-first-green.txt`: 24 tests and 50 subtests passed after the queue change.
- `issue26-id-guard-mutant.txt`: removing the stored-id check fails the four invalid-id assertions, with 24 tests and 46 subtests passing. The guard was restored.
- `issue26-consequence-red.txt`: the new direct-consumer test fails on dictionary text while ten existing spawn tests pass, before the message extraction fix.
- `issue26-composed-python.txt`: initial expanded run fails on three restart test gaps already present on the unchanged base, plus a missing private dependency path. The separate restart-test follow-up preserves its baseline reproduction and repair. Private development dependencies were copied only after both package manifests matched; no host install or install scripts ran.

Final composed qualification follows integration of the separate test-only correction `94887c590012e203c87f6264324b02e332d133db`. `issue26-final-python.txt` records 599 passed plus 350 subtests across 34 files, eight existing Starlette warnings, 42.92 seconds and exit zero. `issue26-final-node.txt` records 37 passed across five files, zero failed or skipped and exit zero. The latter checks skill limits and existing transport/client behavior. The main skill mirrors are identical at 10,624 characters, eight fewer than before. The runtime delta is ten added and three removed lines across two existing owners, under the sixty-line cap.

This is not a full-suite, live deployment or host-runtime qualification claim. The local ledger has no automatic CHECK approval or certificate; direct test receipts do not claim ALL MET.
