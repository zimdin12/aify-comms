# Pinned session helper

Base: comms `a6805a44193ccc995a5bb48543a43710c7d43281`. Wrapper source: `8c7d8f43c92d21440e582368240e4bd9bb79dfcd`. The wrapper's `ClaudePinLookup` already calls `<bridge>/session-for-agent.mjs`; this change supplies that missing comms-owned helper. No wrapper code changes.

The 46-line helper mirrors `agent-for-handle.mjs` ownership: load settings, resolve the destination's key with the existing resolver, query the named agent, and print only the controlled reply. Its pure `pinReply` validates runtime and UUID. HTTP failures and invalid responses never become a pin. The keyed fetch uses manual redirects and a 2000 ms abort signal, inside the wrapper's existing 3000 ms process timeout. Nothing writes a registry, credential, session or transcript.

## Qualification

PASSES IN TESTS, not a live installation or runtime relaunch. The test copies actual bridge runtime files into a private home at `.aify-comms/mcp/stdio`, preserving their bytes. It executes that helper as a real Node child. It also drives the existing wrapper `ClaudePinLookup` with its default synchronous spawn, empty input, no shell, 3000 ms timeout and 4096-byte buffer. A loopback fixture requires a synthetic key. No helper stub supplies the result.

The named agent and its neighbor return distinct UUIDs. The real wrapper selects the correct pin, then refuses the neighbor's pin when its selected-profile transcript is removed. A missing handle and 404 return `none`; another runtime returns `wrong-runtime`; malformed UUIDs, control characters, wrong types, unreadable JSON, service failure, missing authentication and unreachable service return `unavailable`. A settings-only key succeeds. A stalled HTTP response finishes through the helper's own bound, not the test process kill. A redirect receiver sees no request, while its direct positive control is reachable.

The missing-helper baseline fails the named installed-path assertion, after the known existing bridge helper was copied successfully. Removing UUID validation fails four named corpus cases while eleven cases pass; the parent subtest summary also fails. The guard was restored before final qualification.

Final focused Node selection: **48 passed, zero failed or skipped**, six files, 4.47 seconds. Governing Python selection: **31 passed plus 2 subtests**, exit zero, 13.56 seconds. No full-suite or provider qualification is claimed.

## Reproduce

Use isolated home/config/data/temp paths and drop agent, lease, Herdr and credential environment variables before the commands. Supply source-only peer checkouts through the maintained sibling resolver. Run Node from `mcp/stdio` and Python from the repository root.

```text
node --test tests/session-for-agent.test.js tests/agent-for-handle.test.js tests/a-fetch-with-headers-never-follows-a-redirect.test.js tests/aify-service-endpoint.test.js tests/registry-credential.test.js tests/the-credential-read-agrees-with-aify-env.test.js
python -m pytest service/tests/test_no_new_oversized_source_file.py service/tests/test_service_runtime_boundary.py service/tests/test_fixtures_are_tracked.py service/tests/test_no_import_cycles.py service/tests/test_leaves_do_not_import_the_carrier.py -q
```

Raw commands, sanitized environment and actual exit status are retained in `C:/Users/Administrator/projects/comms-evidence-fixes-20261106/`:

- `session-helper-missing-red.txt`, missing helper, expected assertion failure.
- `session-helper-green.txt`, initial helper and shared controls, exit zero.
- `session-helper-line-boundary-red.txt`, despite the exploratory filename, this run is GREEN. The newline-bearing UUID is already refused; no product correction was needed.
- `session-helper-uuid-mutant-red.txt`, four corpus cases fail plus their parent summary, eleven controls pass.
- `session-helper-node-final.txt` and `session-helper-governing-final.txt`, final exit-zero selections above.

Tested file SHA256s:

- `mcp/stdio/session-for-agent.mjs`: `66d7edcaa6c3562db7f189b4173fed313a9f9bf2e9ba302e05aa0d8e52af2059`.
- `mcp/stdio/tests/session-for-agent.test.js`: `19e0b4d0cb71e6332c37af1d8abd9091a23a03dde1ff7f80b5f5f78a03a9ec58`.

The local ledger's CHECK commands have no automatic approval or certificate. Direct isolated receipts are separate evidence. Candidate review is required before push. Adoption on a real host requires its normal coordinated bridge install and relaunch, neither performed here.
