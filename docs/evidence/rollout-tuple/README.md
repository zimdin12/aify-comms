# Rollout wrapper pin

2026-11-14. Comms selects wrapper `8c7d8f43c92d21440e582368240e4bd9bb79dfcd`, matching env `37c3d1f30389efdcaaaa8067b21138f97ae60d37`. This is the selected rollout pair, not an adoption of whatever the sibling main branches become later.

Only package intent and lock metadata change. The staged scripts and product modules remain those published in `3b6a3e44`. After inspecting the old derived package, it was removed from an isolated development checkout before npm installed the exact Git dependency with lifecycle scripts disabled. This is not a global/client install or native PTY build qualification. npm's Git-integrity warning remains in the receipt; no independent archive digest is claimed.

All 140 published package paths derived from the pinned manifest are present and byte-equal to exact Git blobs. Intent, lock resolution, the local installed lock and env's wrapper pin agree. Actual template and report reads confirm the retained root guard, deferred pane claim and pending-entry serialization. The first inventory matcher wrongly treated root `README.md` as a suffix pattern and expected five excluded historical evidence files. Exact root-file matching fixed the probe without changing the package. The corrected inventory and controls are in `package-identity.json`.

## Full bridge gate

PASSES IN TESTS: the maintained `node tests/run-all.mjs` command exits 0. It ran all 367 selected test files at concurrency 6 in 107.9 seconds. The observed selected set exactly matches the source inventory after its nine explicitly disabled deprecated Pi files are removed. Three passing files each contain one skipped case:

- `bridge-exits-when-its-terminal-goes.test.js`
- `a-plain-install-hands-off-to-the-chain-once.test.js`
- `runtime-launch-helpers.test.js`

Those three cases and all nine disabled files are not verified by this run. `bridge-result.json` retains the complete selected and disabled path populations. `bridge-excerpt.txt` contains the actual invocation header and the unmodified final summary, not the full log.

The outer runner passes Windows identity metadata and uses conventional `AppData/Local` and `AppData/Roaming` directories under its private HOME. It does not expose the real host's credential directories. Agent, lease, Herdr and key environment variables remain excluded. No test or product behavior was changed to make the suite pass. The prior sealed-path and Windows-identity failures are retained in the earlier pin record; this is a new full-suite green, not a relabelling of that red.

The command ran through the existing private `run-targeted.py` with `--windows-identity --windows-appdata`, then `node --import <exact peer preload file URL> tests/run-all.mjs` from `mcp/stdio`. The preload names only the two exact source worktrees above. Its full argv is retained in the excerpt. All 4,129 tracked source hashes across the three checkouts and both external runner/preload hashes remained unchanged after execution.

The separate path/pin/export controls passed 24 of 24 cases. Targeted served-spec and source-boundary checks passed 55 tests plus 38 subtests, with one existing warning. These are fixture checks, not live service or provider qualification.

Full raw receipts and the frozen source-hash population remain in the persistent project evidence directory as `rollout-pin-8c7-full-bridge.txt`, `rollout-pin-8c7-inputs.json`, `rollout-pin-8c7-path-pin-controls.txt`, `rollout-pin-8c7-python.txt` and `rollout-pin-8c7-npm.txt`.

## Next gate and limits

After this pin is published, one E1 run must use that exact comms commit with the exact env and wrapper SHAs above. The already published consent/refusal, privilege, independent-home, doctor and cleanup criteria stay unchanged. A red stops the rollout qualification with its evidence; a pin or test green is not an E1 result.

No live installation, stamp, rebuild, restart, relaunch, provider call, public issue comment, issue closure or release was performed by this pin change. The earlier passed E1 remains its own dated record and is not substituted for the rollout tuple's pending run.
