# Optional definition fact

The env owner confirmed the wire spelling before implementation: a per-record optional `definition` string, exactly `valid`, `invalid`, `unavailable` or `none`. Present null, empty string and `unknown` are invalid. Absence means unsupplied or an older sender, not implicit `none`. This is independent of `stateCause` and of the existing nested `launch.definition` reference object.

The producer's existing `FACT_WORDS.definition` agrees, but its current host output omits this fact. These new receiver tests are prospective wire-contract fixtures, not a claim that env already emits the field. The sender owner holds emission until the lead confirms receiver support is live.

The field is optional in the strict record validator and checked with the existing `_word` predicate. Values are declared once in the existing JSON vocabulary contract and derived as an immutable set by its existing Python reader. The stored-row projection already serializes the whole admitted record, so no storage code changes are needed. The test reads the complete JSON back for every value, then verifies that omission removes the optional fact rather than retaining or inventing `none`.

## Tests and retained evidence

Persistent record: `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`. Each receipt starts with the exact command and isolated environment and ends with `ACTUAL_EXIT`.

- `record-definition-original-red.txt`: all four confirmed values fail at the intended unknown-record-field assertion on the old receiver. Thirteen tests and 64 subtests pass as controls.
- `record-definition-first-green.txt`: 13 tests and 68 subtests pass.
- `record-definition-guard-mutant.txt`: deleting the enum guard produces nine malformed-value failures and two downstream namespace checks that see the corrupted prior state. The latter are not two additional guard defects.
- `record-definition-guard-isolated-mutant.txt`: after restoring a valid baseline before the namespace checks, the same deletion produces exactly nine combined refusal/nonmutation failures, with 13 tests and 59 subtests passing. The guard is restored before final qualification.

Malformed values include null, empty/unknown/case-changed strings, booleans, numbers, arrays and objects. The envelope still refuses a top-level `definition`; `launch.definition` still refuses a fact string and requires its original reference shape. Existing sender examples remain unchanged and exercise absent-field compatibility.

Qualification uses `python -m pytest -p xdist.plugin -n 8 --dist loadfile -q` with the saved explicit population and exact env sibling selected through `AIFY_ENV_REPO`. No new endpoint, UI, inferred classification, sender output, install, rebuild, environment restart or public closure is included. Source publication is not deployed receiver support or permission to emit the field.

PASSES IN TESTS: `record-definition-final-python.txt` records 148 tests plus 221 subtests passed across 19 files, eight existing Starlette warnings, 20.64 seconds and exit zero. `record-definition-final-node.txt` records the existing cross-language vocabulary agreement test passing, zero failed or skipped. The product delta is seven added and one removed line across three existing files, eight changed lines under the ten-line cap. This is targeted qualification, not a full-suite or live-sender claim.

The local automatic CHECK has no explicit user approval or certificate. Direct successful receipts do not claim ALL MET.
