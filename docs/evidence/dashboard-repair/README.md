# Dashboard paste repair evidence

## Scope and cause

Base commit: `5b55ee3bbca9f91048dcb1018fdbeb3540eaf016`.

`xterm-mount.mjs` manually read the clipboard and called `term.paste()` on Ctrl+V while Chromium also delivered its native paste event to the vendored xterm. One shortcut produced two HTTP input bodies containing `test`, then Enter submitted `testtest\r`.

The repair removes the manual clipboard read. Returning false from the custom Ctrl+V handler bypasses xterm key processing without cancelling the browser's native paste. This also prevents the old Ctrl+V control character when the async clipboard API is absent. Copy behavior stays unchanged. No content-based input or output suppression was added.

`extraction-proof.test.mjs` declares the exact before/after clipboard block in its existing `editedSince` mechanism. Without that declaration the byte-reconstruction gate correctly fails. The pristine fixture and gate logic are unchanged.

## Reproduction

Use an existing Chromium executable. No dependency installation is required by this browser fixture.

```bash
DASHBOARD_TEST_CHROME='C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1224/chrome-win64/chrome.exe' node service/new_dashboard/fixtures/dashboard-browser-check.mjs
```

Prefix the same command with `DASHBOARD_TEST_REF=5b55ee3b` to serve the unmodified base source, or `DASHBOARD_TEST_REF=3e7387a6` for the older deployed-source revision identified by the parent's HTTP hash preflight. Historical modules and vendored xterm come from `git show`; the browser driver and assertions remain the same.

The fixture starts only a temporary loopback HTTP/WebSocket server and a fresh headless Chromium profile, then closes both and removes the profile. It drives native keyboard events through the real vendored xterm, input handler, serialized HTTP poster, realtime socket, mount and recovery code. It does not start an agent or a PTY, contact the live service, deploy or install anything.

## Results

| Run | Passed | Failed | Log |
| --- | ---: | ---: | --- |
| Expanded browser fixture, base `5b55ee3b` | 5 | 5 | `browser-base-red.log` |
| Expanded browser fixture, older `3e7387a6` source | 2 | 8 | `browser-deployed-source-red.log` |
| Expanded browser fixture, repaired source | 10 | 0 | `browser-expanded-green.log` |
| Final browser rerun, repaired source | 10 | 0 | `browser-final-green.log` |
| Dashboard Node suite, unmodified base, dashboard cwd | 1712 | 0 | `suite-baseline-dashboard-cwd.log` |
| Dashboard Node suite, repaired source, dashboard cwd | 1712 | 0 | `suite-final-dashboard-cwd.log` |

The browser checks cover Ctrl+V, Ctrl+Shift+V, bracketed paste, absent async clipboard API, repeated deliberate pastes after reuse/remount, read-only input refusal, WebSocket retransmission versus identical text at a new sequence, mount/snapshot overlap, a quiet final frame during recovery, and gap recovery rejoining the live stream after one snapshot fetch.

Run the Node suite from its expected working directory:

```bash
cd service/new_dashboard
node --test *.test.mjs
```

The baseline suite run temporarily restored both edited tracked files from the base commit and restored the repair in a `finally` block. The browser fixture is not selected by the Node suite's `*.test.mjs` glob.

## Failures diagnosed, not waived

- The recovered browser fixture initially passed 9 checks and failed the WebSocket retransmission check. Its hand-built server encoded every payload with the 16-bit WebSocket length form, including short payloads. Chromium rejects that nonminimal encoding. The fixture now uses the shortest encoding required by RFC 6455. The same assertions then passed without changing realtime production code.
- Running the Node suite from the repository root causes seven producer-scan failures on the unmodified baseline. `realtime-dispositions.test.mjs` derives the service directory from `process.cwd()`. Running from `service/new_dashboard` fixes those failures without any test or production edit. Do not call the root-cwd run green.
- The intermediate repaired suite had two additional extraction-proof failures. These were caused by this repair, not Windows. The explicit clipboard `editedSince` declaration fixes them; both final and baseline dashboard-cwd suites pass all 1712 tests with no skips.
- The predecessor's first suite log also records a missing wrapper-template dependency. That failure was absent with the dependency link supplied to this recovery task. No dependencies were installed in this recovery.
- Historical browser logs include a harmless 404 lookup for an absent favicon. The asserted dashboard modules loaded and all checks ran.

## Attribution and limits

The new production fix is for paste handling only. Sequence retransmission already renders once on both historical sources tested. Mount overlap, quiet-frame recovery and gap recovery fail on `3e7387a6` but pass on the unmodified current base. Those recovery mechanisms were repaired before this change; this work adds browser evidence for them rather than claiming them as new fixes.

These checks do not establish that every source of live assistant-output duplication or rendering latency is solved. There is no live end-to-end latency benchmark, real-agent run, GPU-renderer validation, Firefox/Safari validation or installed-service verification here. The absent-clipboard-API case removes the API on an isolated secure loopback page; it is not a separate insecure-LAN-origin test.

Original `browser-red.log` and `browser-green.log` are preserved. Raw `.log` files remain in this local evidence directory but are ignored by Git and are not staged. `manifest.json` records their hashes and parsed result totals. The unused full baseline source copy was removed after comparing it with the base source. No commit or deployment was made; the parent must review the staged tree before committing.
