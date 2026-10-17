# aify-env c8ff21c: one cancelled doctor-live test, UNATTRIBUTED

Open item. The cause is not known. The later green runs do not replace this one.

## The run

- Object: aify-env `c8ff21ce6854e6c8381ca5011fa4c48b1e6df0b0` (accepted, later fast-forwarded to aify-env main).
- Setup: a fresh detached worktree `~/projects/aify-env-merge-c8ff21c`. Its `node_modules` was a junction to
  `~/projects/aify-env/node_modules` (checkout at 8d2e54f; `package.json` and `package-lock.json` are unchanged
  between 8d2e54f and c8ff21c). Windows 11, node v22.20.0, run from comms-tech-lead's agent shell.
- Invocation: `npm test > npm-test-c8ff21c-run1-exit1.txt 2>&1; echo $?`, which printed `npm test exit=1`.
- Result: `# tests 3129, # pass 3124, # fail 0, # cancelled 1, # skipped 4`.
- The cancelled test: `tests/doctor-live.test.js:184`, "NEGATIVE CONTROL, ON THE SAME REAL DAEMON: change the disk
  and the row goes FAILED". It reported `failureType: 'cancelledByParent'` with the error "Promise resolution is still
  pending but the event loop has already resolved", after 1334.9 ms. Its positive sibling (`ok 1149`) passed just
  before it.
- Its `finally` ran: afterwards `git status --short` was empty, and there was no `__disk-build-negative-control-*`
  file in `lib/`.
- The transcript is `npm-test-c8ff21c-run1-exit1.txt` (sha256
  `f1e11db93f2ce26deedeadf45a7874f7fae771df966821a194f9966cfce7d584`). The cancellation is at line 7098.

## The runs after it

The same host, the same setup (the e15792e worktree had the same junction), run one after another:

| run | head | exit | tests | pass | fail | cancelled | skipped |
|---|---|---|---|---|---|---|---|
| c8ff21c-2 | c8ff21c | 0 | 3129 | 3125 | 0 | 0 | 4 |
| e15792e-1 | e15792e | 0 | 3050 | 3046 | 0 | 0 | 4 |
| c8ff21c-3 | c8ff21c | 0 | 3129 | 3125 | 0 | 0 | 4 |
| e15792e-2 | e15792e | 0 | 3050 | 3046 | 0 | 0 | 4 |

It occurred once in 3 runs of the candidate and 0 times in 2 runs of the base. That does not separate the change from
the environment. The stack does not touch `doctor-live.test.js` or any doctor or code-current path
(`git diff --name-only e15792e c8ff21c`), but that does not rule out an indirect effect.

## What would attribute it

The next occurrence, with its full report kept. The error says the test's awaited promise outlived every ref'd handle,
so the place to look is which await in the test (startDaemon, a `/health` fetch, the 1 s poll sleep) had nothing
holding the loop open.

This is not the same as the 3555 ms silent file death in the cc3999c run, or the f79dbc8 R2 baseline failure
(explained since: the witness waited too early).
