# P6r2: the repair of comms-senior-dev's REVISE of P6r (2026-10-01)

The review bound comms 3ada3809 and aify-wrapper 9455583 and blocked on ten findings
(`C:/Users/Administrator/AppData/Local/hermes/cache/scratch/9455583-3ada3809-P6r-review/PARENT-REVIEW.md`).
Each is repaired, each repair has a test that was watched fail on the reviewed code, and each has a mutant
that re-opens it and is killed. Successor objects: aify-wrapper `next/env-owned-agents` (f60cfab repair,
ab316a4 a test budget) and the aify-comms commits on top of 159ace50, pinned to ab316a4.

| finding | repair | test (red on the reviewed code) | mutants |
|---|---|---|---|
| L1 a plain `hermes chat` had no effort consumer | the launcher adds `-m` / `--reasoning` after `chat`, only those the operator did not give | aify-wrapper `tests/hermes-takes-model-and-effort.test.js` (3 of 6 failed with the repair removed) | 4, `mutations-wrapper` |
| L2 codex wrote a newline into TOML | a control character in model or effort refuses with 78 before anything starts | aify-wrapper `tests/codex-takes-model-and-effort-on-its-app-server.test.js` | 2 |
| C1 the next start undid an effort change | the change is written to the agent's spawn specs too (`_respecify_effort`) | `service/tests/test_an_effort_change_reaches_the_start_it_is_for.py`, the review's own route chain: spawn, claim, running, change, restart, claim, running, real launch route | 2, `mutations-service` |
| C2 a concurrent usage-source edit was lost | `BEGIN IMMEDIATE` before the read, as `usage-source` takes it | same file, the review's interleaving: effort paused after its read, usage-source in another thread | 1 |
| C3 `runsWith` and the launch disagreed | one reader, `service/api_core/model_effort.py`, for the launch, `runsWith` and the defaults; one effort writer that drops `thinking` | same file, the review's three registration inputs compared against the real launch route; `test_harness_defaults.py` | 6 |
| C4 one damaged row took the list down | tolerant decoding in `definition_records.py` | same file | 1 |
| H1 Windows ran a PowerShell launcher with none of this | retired: `hermes-aify.cmd` runs the bash launcher, the installer removes an old `.ps1` | `service/tests/test_install_hermes_session_rediscover.py` (two tests, Windows) | 2, `mutations-install` |
| H2 overlapping passes set a session twice | one pass at a time | `mcp/stdio/tests/hermes-session-effort.test.js` (7) | 2, `mutations-bridge` |
| H3 effort chose the newest session, delivery the bound one | the bound session (the marker), else the only live one, else nothing | same file (6) | 2 |
| H4 the stop was discarded and did not fence a pending answer | the loop keeps and calls the stop; nothing is sent once stopped | same file (8); `hermes-managed-host.test.js` wiring test | 2 |

Two follow-ups the review disclosed are also repaired, each with a test and a mutant: a cleared effort can no
longer be supplied by the host's environment (G1: `AIFY_MANAGED_MODEL` / `AIFY_MANAGED_EFFORT` in
`unsetEnv`), and apply-defaults clears a stale `thinking` and a record's own `runtimeConfig.model` (G2).
The three left open are in KNOWN_ISSUES.md ("What the review of 0.8's model and effort left as follow-ups").

## Files

- `make-batteries.py` writes the four batteries; `mutations-*.json` and `-result.txt` are their runs through
  `../2026-10-01-p2/mutate.py`. Service 14/14, bridge 6/6, install 2/2 killed. Wrapper: 4/6 killed and 2
  HUNG at the 300 s limit in the first run, where the unmutated pair of files takes 94 s; the two rerun
  alone with a 900 s limit (`mutations-wrapper-hung-rerun*`) were both killed, in 94 s for the pair. Why the
  first run hung was not found.
- `cmd-to-bash-keeps-a-console.mjs` and its result: in a pseudo-console (node-pty), a native program started
  through a `.cmd` that runs Git Bash sees a TTY on stdin and stdout, as one started directly does; the piped
  control sees none on stdin. Not run: a classic console window, and hermes itself.
- `suites.txt`: all five suites on the successor.

## Not proven here

A resumed hermes session's model and effort, effort surviving a lazily built session, the PowerShell-typed
`hermes-aify` on a real console, and any installed relaunch. All need the operator's install and a relaunch.
