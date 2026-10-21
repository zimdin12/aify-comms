# SW3b results: RED (2026-10-21)

The pair comms `78270d30` + aify-env `f704bcdf` fails the gate under PREREGISTRATION.md. All three attempts exited 1.
None was rerun or replaced.

| attempt | exit, UTC | primary | cleanup |
|---|---|---|---|
| q1 | `q1 exit=1 at 13:29:33Z` | 12/14 | complete, every owned resource read back absent |
| q2 | `q2 exit=1 at 14:03:25Z` | 12/14 | complete |
| q3 | `q3 exit=1 at 14:37:13Z` | 11/14 | complete |

## Failures

| case | q1 | q2 | q3 | cause | at fault |
|---|---|---|---|---|---|
| default-send-unknown | FAIL | FAIL | FAIL | C7 and C5 came from one sender, and comms folds a send into its sender's queued run (`_find_mergeable_queued_run`), so `defaultRunId == queueRunId` | the case; fixed in `7f81e1d1` (098/sw3c) |
| stop-restart | FAIL | FAIL | FAIL | a defined agent's stop is delegated to its host (`control_agent` → `delegate`, D9c), so `_apply_agent_stop_or_resume` never cancels; the terminal-close sweep in `terminal_runs.py` fails the queued seed. Same source on main | the case's expectation, which nobody decided; held for the operator (dashboard 01M5FDG04RQP1Z7M6CQQ5X7JTT) |
| legacy-busy-env-idle | pass | pass | FAIL | the stand-in logged `delivered` 10 ms after the run existed; `claimed()` saw the claim through a direct snapshot, and the evidence was read before the watcher's 0.5 s poll wrote it | the apparatus; fixed in `7f81e1d1` |

## Validity

- Source roster: each attempt logs 14 sha256 lines. 14 of 14 match `e7750b9f`'s blobs in all three (`git show e7750b9f:<path>`).
  Negative control: the same lines against `11d8f3ae` match 11 of 14. Not logged: `campaign.py`, the mutation controller,
  which no attempt runs; and `scripts/sandbox/env.Dockerfile`, the runner's helper. The gate builds from `gate/env.Dockerfile`
  (`gate.mjs`, the env build context). That an attempt never reads the runner's helper is ASSUMED.
- No `REUSING images` line in any log (`grep -c` gives 0, 0, 0).

## Chronology, from receipts

- Preregistration commit `bac42188`: 12:55:02Z (committer date).
- Push: 12:55:05Z, from the local reflog.
- q1 log file created: 12:55:11Z. This is the log's birth, not an independently observed process start.

## Where things are

- The logs are `logs/q1.gate.txt`, `logs/q2.gate.txt`, `logs/q3.gate.txt` and `logs/exits.txt`. They are not the
  preregistered `<q>/gate.txt`, because the gate refuses an out dir that already exists.
- `q1/`, `q2/` and `q3/` are each attempt's own out dir: `attempt.json`, plus one `evidence.json` per scenario.
