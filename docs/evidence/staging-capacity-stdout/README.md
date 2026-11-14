# Capacity stdout evidence

2026-11-14. Base0151beea6cece4cb952cd52f743890fd2a97751d. The staging capacity probe now reads `df`'s value from stdout only. The Docker result helper exposes stdout while keeping its existing combined diagnostic `out`. The capacity caller preserves the old command-completion refusal before crediting capacity. Product delta +5/-2 is seven changed lines across docker.mjs and stack.mjs, under ten.

## Observed input and scope

The E5 run4 receipt contains a successful `docker run --pull missing ... df -B1 --output=avail /` result. Its combined output starts with `Avail` and60691738624bytes, then ends with Docker pull diagnostics and `Status: Downloaded newer image ...`. Taking the last combined line therefore produces NaN.

The tracked fixture retains only this command's public output, not host paths or credentials. Its supplied stdout/stderr reconstruct the known df header/value and following pull diagnostics; the old receipt did not capture separate streams. Their concatenation was compared byte-for-byte with the original run4 `out`: SHA256f41518873b17a1ace20fd08745d95490d29885d35c00cc8b9318f0b55f1db670,24stdoutbytes and1231diagnosticbytes. This is a composed fixture test, not a newly executed native pull.

The ordering was amended to allow this repair before E5 lands because the files are disjoint. No E5 manifest, Dockerfile, layout, session, scenario or tests were changed. No Docker command, build, installer, provider call, live restart or deployment was executed.

## PASSES IN TESTS

- Old source: exactly the first-pull assertion failed with `the Docker disk has NaN GB free; a run needs 40 GB`. Seven controls passed, including the cached image and incomplete-command refusals. Actual exit1.
- The new tests compose the real Docker result mapper and real capacity guard. Only process execution is injected. First-pull and cached cases require the actual60691738624byte value, a credited capacity check and all four staging-resource queries.
- Restoring combined-output parsing produces two assertion failures: first-pull diagnostics corrupt the value, and numeric stderr falsely rescues malformed stdout. Seven controls stay healthy. Actual exit1.
- Deleting the new caller completion guard produces three named combined refusal/no-capacity-credit assertions red. Nonzero, timed-out and signalled results credit nothing and perform no resource listings in the correct implementation. Six controls stay healthy. Actual mutant exit1; guard restored.
- Final sequential 13-file staging selection:206passed, zero failed/skipped,34.11seconds,actual exit0. Includes existing image-build, capacity, CLI, consent, generated/observed isolation, manifest, cleanup, process-fixture, session, shell syntax and shallow-source controls. The CLI assertion also confirms stderr stays in `out` while stdout excludes it.

The40GBfloor, exclusive-run check, diagnostics and existing fail-closed behavior are unchanged. No numeric-line hunting was introduced. This result is limited to the composed process fixture and targeted controls; it is not a live daemon or native first-pull rerun.

## Reproduce

With a private HOME/config/data/temp and no inherited agent, Herdr or credential environment:

```sh
node --test mcp/stdio/tests/staging-capacity.test.js mcp/stdio/tests/staging-cli.test.js
node --test --test-concurrency=1 mcp/stdio/tests/staging-*.test.js
```

For the channel mutant, replace `disk.stdout` with `disk.out` in the capacity calculation. For the completion mutant, delete only the new status/signal/errorCode refusal. Run the capacity test file and restore source after each. The original old-source RED uses the first-pull and cached fixture tests over the base product.

Raw unmodified `capacity-stdout-*` receipts and the sealed `run-targeted.py` are retained under `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`. Each receipt records its exact argv/environment metadata and actual child exit. `original-red.txt`, `merged-mutant.txt`, `guard-mutant.txt` and `qualified.txt` are the load-bearing runs. Automatic CHECK certification is separate and was not granted.
