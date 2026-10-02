# P6r3: the repair of comms-senior-dev's REVISE of P6r2 (2026-10-02)

The review bound comms 6047aa1e and aify-wrapper ab316a4
(`C:/Users/Administrator/AppData/Local/hermes/cache/scratch/ab316a4-6047aa1e-P6r2-review/PARENT-REVIEW.md`).
It approved the service half (C1-C4, G1, G2), the install-all budget, the PowerShell retirement within its
render and dispatch scope, H2 and the helper's own stop; it blocked on five. Each is repaired here, with a test
watched red on the reviewed code and mutants that re-open it.

| finding | repair | test | mutants |
|---|---|---|---|
| L1 only a first-argument `chat` got the defaults | the subcommand is found by hermes' own rule (`command_argv`, with the value flags its parser snapshots), and the defaults go right after it; an attached `-mVALUE` counts as the operator's model | aify-wrapper `tests/hermes-takes-model-and-effort.test.js`, "THE CHAT IS FOUND PAST HERMES' OWN TOP-LEVEL FLAGS" (`-m own/model chat`, `--provider p chat`, `-mown/model chat`, `--provider=p chat -q hi`, and `-m chat` as the control) | 7, `mutations-wrapper` |
| L2 a definition's NUL or CR never reached the guard | the reader names a control-character field (`AIFY_DEF_CONTROL`) beside the value; codex refuses one it selects, so `-m` or a managed value still wins over it | `tests/codex-takes-model-and-effort-on-its-app-server.test.js` (four NUL/CR cases refused, two overrides launched), `tests/agent-definition-defaults.test.js` | 5 |
| H1 the `.cmd` ended with 0 whatever the launcher returned | every `.cmd` shim ends `endlocal & exit /b %AIFY_EXIT%` | `service/tests/test_install_hermes_session_rediscover.py::test_the_cmd_returns_the_launchers_exit_status`: the rendered shim run by cmd.exe in front of a stand-in launcher exiting 78, 0 and 3 | 2, `mutations-install` |
| H3 the effort's own rule set nothing when the marker held a durable key | the effort takes delivery's rule: the marked session by its live id, else the newest; C9 amended to say so | `mcp/stdio/tests/hermes-session-effort.test.js` (6): five markers, each compared with the real `waitForActiveSession` on the same list | 2, `mutations-bridge` |
| H4 the stop was outside the acquired lifetime and teardown entry | the effort starts inside the loop's `try`, and teardown and giving up stop it before they await | `mcp/stdio/tests/hermes-managed-host.test.js`: teardown entry, give-up entry, exhausted bring-up | 4 |

## The choice in H3

The bridge lane's report (`bridge-install-review/REPORT.md` in the P6r2 review) named two ways: make every producer
and consumer obey "the marked session, else the only one, else none", or obtain a narrower authority amendment.
The parent verdict asked for the first, so this is a newly submitted amendment, not an option the verdict gave
(corrected after the review of P6r3). Production's law is recency. Delivery takes the marked session by its live id,
else the newest. The resume sync rewrites the marker to the newest session's durable key every pass. So a marker
is usually a durable key that names no live id, and delivery goes to the newest. Making delivery refuse that would
change live delivery on evidence of no harm. The effort now follows delivery instead, and the C9 text is amended.
One difference stays disclosed: delivery waits out a short relaunch grace before taking a newest session that
predates the delivery, and the effort does not, so a session that is about to go may get the effort once.

## Files

- `make-batteries.py`, `mutations-*.json` and `-result.txt`: run through `../2026-10-01-p2/mutate.py` with a
  600 s limit.
- No full suite run was asked for, and none is quoted.
