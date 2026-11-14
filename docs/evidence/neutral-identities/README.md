# Neutral identities

This is the names-only slice of roadmap step 7 and comms issue 40, based on `8507947ec6cba858ca670e0c67060c2db6263bf4`. It replaces operational identities in comments, maintained fixtures and teaching material. It does not implement the broader comment-history cleanup, change runtime behavior, move a dependency pin, or replace the frozen rollout source. The change belongs to the next build.

## Scope and preservation

`service/tests/test_no_agent_names.py` derives recognition from role suffixes rather than a roster. Bare roles and neutral fixtures are accepted. Code-file citations ending in `.js`, `.mjs` or `.py` name modules, not agents. The scan reads Git-tracked maintained source, tests and both teaching mirrors. Captured text output and documentation JSON receipts retain their original bytes; maintained JSON contract fixtures and source drivers are checked. Unreadable maintained text fails rather than disappearing from the scan. Embedded NUL in valid UTF-8 test input remains inspectable.

The initial inventory matched 1,294 lines across 382 files, including technical citations subsequently classified separately. Fixture substitutions keep prefix, suffix and case relationships. Paired sanitizer inputs and results, hook identities and record filenames, and search needles and their fixtures were reviewed together. The search fixture now distinguishes an ID match from a role-only match. Historical quotations whose identities changed are labelled generalized. A historical review report has a neutral dated filename and its observed pointer resolves to it.

Independent comparison with the base finds 61 changed Python implementation bodies AST-identical after excluding docstrings and SQL comments, and 33 JavaScript/shell bodies identical outside comments. All 413 captured files checked, including gzip and extensionless captures, remain byte-identical. Prior captures still describe their original source and execution, not a rerun of the edited drivers. The main skill mirrors are byte-identical at 10,632 characters; its existing ceiling falls from 10,640 to that measured value.

## Qualification

PASSES IN TESTS on isolated fixture homes and databases:

- Python changed/governing selection: **1,341 passed plus 457 subtests**, 108 files, 47.25 seconds. Eight copies of the existing Starlette TestClient deprecation warning are reported by the workers.
- Bridge changed/governing selection: **460 passed, zero failed or skipped**, 51 files.
- Dashboard changed selection: **388 passed, zero failed or skipped**, 20 files.

These are targeted populations, not a full-suite claim. No live installation, deployment, restart, provider turn, release or public issue closure was performed.

The original tree reached the named identity assertion with healthy recognizer and population controls. After cleanup, a planted synthetic prefixed identity in a production docstring produced exactly one source violation, with both control tests passing. The original bytes were restored before final qualification.

## Retained evidence and reproduction

The persistent record is `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`:

- `neutral-names-before.json`: all 2,713 initial tracked file hashes and the original matched-line population.
- `neutral-names-original-red.txt`: the first instrument wrongly rejected a valid embedded-NUL parser fixture. This is not a credited identity regression result.
- `neutral-names-original-assertion-red.txt`, `neutral-names-first-green.txt`, `neutral-names-planted-red.txt`: original assertion red, clean green and planted-name control.
- `neutral-names-python.txt`: initial 1,327 passes and 14 E2E setup errors. The child reset `PYTHONPATH` and could not import `uvicorn`. A private fixture user-site `.pth` points at the same existing dependency tree used by the parent; no host or global package was installed. The reset-path present/absent control and the separate 14-case E2E pass are in `neutral-names-e2e-deps-control.txt` and `neutral-names-e2e-corrected.txt`.
- `neutral-names-bridge.txt` and `neutral-names-dashboard-final.txt`: initial fixture-pair and search-data failures, retained rather than relabelled green.
- `neutral-names-python-final.txt`, `neutral-names-bridge-final.txt`, `neutral-names-dashboard-corrected.txt`: final exit-zero runs. Their first JSON lines contain the exact commands and sanitized environment. Matching `*-selection.json` files list every selected test file.
- `neutral-names-semantics.json` and `neutral-names-verify.py`: independent body comparisons and the complete byte-preserved capture population.

Reproduce from a fresh isolated checkout using those saved selections: Python uses `python -m pytest -p xdist.plugin -n 8 --dist loadfile -q`; Node uses `node --test --test-concurrency=4`, with the recorded source-only peer preload for bridge tests. `neutral-names-tests.py` supplies the explicit populations through the existing sealed runner and refuses to overwrite old receipts.

The local unlazy ledger has no automatic CHECK approval or certificate. Direct isolated execution receipts remain separate; no `ALL MET` result is claimed.
