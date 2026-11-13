# #49 shared wrapper pin

2026-11-13. Comms selects wrapper `c09fd03cb17d076acd8cd9c20ad5517efbea09d0`, matching env
`345fd0c3cab1b035bde1b5dd3ceb82359be595a5`. Later wrapper commits are deliberately not selected for this E1 tuple.

The old derived package was removed before npm installed the exact Git SHA in the isolated development checkout.
Lifecycle scripts were disabled; this is dependency materialization, not native PTY or installed-launcher qualification.
Package intent, lock resolution, local installed-lock identity and actual template contents agree. The installed
Claude template contains the explicit root guard. A separate comparison derived 140 published paths from the pinned
package manifest, including its docs glob, and found zero missing or differing files against Git. Template/package
positive controls and an absent-path control ran with that comparison. npm's Git-dependency integrity warning is
retained; this does not independently verify an archive digest.

PASSES IN TESTS, using frozen wrapper/env source worktrees and an isolated HOME:

```text
node --test <seven selected pin/registry/spec files>: 44 passed, 0 failed, skipped or cancelled; exit 0
python -m pytest <seven selected spec/layer/fixture files> -q: 55 passed, 38 subtests passed; exit 0
```

The Node selection is `the-installed-wrapper-is-the-pinned-commit`,
`the-wrapper-pin-is-not-behind-a-template-change`, `service-registry`, `install-bakes-the-registry-fragments`,
`registry-output-matches-what-consumers-recorded`, `wrapper-exports-are-read-by-the-bridge` and `service-spec`, all
under `mcp/stdio/tests/` with `.test.js` suffixes. The Python selection covers the real served spec, service runtime
boundary, import cycles/dead imports, size, layer direction and tracked fixtures.

Raw commands and results are retained as `issue49-pin-c09-npm.txt`, `issue49-pin-c09-node-uri.txt` and
`issue49-pin-c09-python.txt`; package path identity is in `PIN-C09-PACKAGE.json`. An initial Node invocation used a
native Windows path for `--import` and failed before loading the tests. Its record is retained as
`issue49-pin-c09-node.txt`; the corrected invocation uses a file URL. No product fix or test weakening caused that green.

Env source and author receipts were checked separately. Its final fixture runs do not prove the real entry's connect
CLI argument composition, sudo, policy elevation or E1 success. Those remain the single composed staging run's job.
No service rebuild, host install, relaunch, policy edit or staging run was performed for this pin.
