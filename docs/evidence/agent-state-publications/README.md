# Env publications at admission

This is comms issue 22's receiver-example slice, based on `107522a97f54a60d44ce3cec2567de0d6f79c074`, not the full migration from hand validators to runtime JSON Schema. The existing Python sibling resolver `env_repo` selects the producer checkout. The new test reads its `docs/agent-state/publications.json`, posts each entry's `body` unmodified to the real `/api/v1/agent-state` route, and compares the publisher cursor and complete stored-row projection after every accepted publication. It does not keep a second fixture list.

## Producer binding

Qualification selects env `b00741c467819f6305f841e4ee4bc98f3fd04186` through `AIFY_ENV_REPO`. The three inspected artifacts have SHA256:

- Schema, `docs/agent-state/schema.json`: `1d829d1daf8a4bb25859b67d6547ae61b1494a3b64a7a6d46665019d841c2a45`.
- Sender examples, `docs/agent-state/publications.json`: `8a2b4d795edbf364d4b2813dc0f210eba20ee6db77ccaa1a75a6dacb44913825`.
- Contract, `docs/AGENT-STATE-DASHBOARD-v1.md`: `30f49402a407d55752fd22535dad08e634d9c1e54be85a3ab3777f520d130291`.

The 26 bodies are captured through the real env host, reader, publisher and sender at an injected transport, with controlled inputs. They are not live fleet captures. They cover all four kinds and preserve their per-publisher order. The test follows the explicitly selected sibling; these hashes bind this qualification, not a new runtime dependency or an enforced permanent test pin. Missing producer artifacts fail rather than silently selecting another source or skipping the contract.

## Observed mismatch and repair

The unchanged receiver rejects `managed/screen-rule`, whose real `stateCause` is `folder-trust`, with HTTP 422 `unrecognised vocabulary`. The other 25 bodies and malformed/operator-attested controls pass. This was reported before the product edit. Env's schema and contract admit any nonempty cause string. The three-line product delta removes `_CAUSES` and replaces only that enum check with the existing `_text` predicate. Unknown state words, empty/non-string causes and the existing strict envelope, record, safe-integer, duplicate-key, size/depth, cross-row and ordering policies remain separate.

The retained malformed-cause test now rejects empty and non-string values rather than treating a new rule name as invalid. Wrong-kind and operator-attested bodies are derived by copying actual env examples for the negative controls only; their refusals must preserve both cursor and rows. An unmodified shutdown retirement remains the positive control. Existing receiver and mirror tests own the broader transport and ordering guards.

## Receipts and reproduction

Persistent record: `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`. Exact commands, selected files, isolated home/database environment and `AIFY_ENV_REPO` are in each receipt's first JSON line; `ACTUAL_EXIT` records the command's actual status.

- `agent-state-env-original-red.txt`: one named real-body subtest red, two tests and 27 subtests passing.
- `agent-state-env-first-green.txt`: 13 tests and 81 subtests passed.
- `agent-state-cause-guard-mutant.txt`: the first guard-deletion run reaches the three intended malformed-cause refusals. Its assertions were then combined with stored-state checks.
- `agent-state-cause-guard-combined-mutant.txt`: three combined refusal/nonmutation assertions red, with 13 tests and 78 subtests passing. Product guard restored before final qualification.

Use `python -m pytest -p xdist.plugin -n 8 --dist loadfile -q` with the final receipt's explicit population and the pinned env checkout. No producer code or fixtures are copied into comms, no sender test regeneration is run, and no runtime schema validator is introduced by this slice.

PASSES IN TESTS: `agent-state-env-final-python.txt` records 162 tests plus 164 subtests passed across 20 files, eight existing Starlette warnings, 16.63 seconds and exit zero. The new test's 26 producer bodies, both negative controls and the original shutdown positive control are included. All three producer artifacts remain byte-identical to the selected env commit after qualification. This is targeted receiver-example adoption, not a full-suite or installed-runtime claim.

## Live-impact limit

The requested retained-container log check cannot establish frequency or a latest cause-rejection time. Its positive incoming-request controls failed. The actual running container command has `--no-access-log`, and the receiver's generic vocabulary error does not identify which field failed. The local `agent-state-live-log-count.json` preserves the failed instrument result and bounded cutoff; its zero is not absence evidence. No logging configuration or live publication was changed. A dropped comms publication alone would not establish a wrong state in the separate dashboard's independent receiver.

No live installation, rebuild, environment restart, provider call or public issue closure is part of this change. The automatic ledger CHECK remains unapproved/uncertified; direct successful receipts do not claim ALL MET.
