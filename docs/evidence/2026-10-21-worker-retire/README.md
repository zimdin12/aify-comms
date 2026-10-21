# aify-env SIGTERM with a live managed worker does not retire its key (2026-10-21)

Asked by aify-dashboard task #39. The pair is comms `78270d30` and aify-env `f704bcdf`. The gate is at `aec786ed`, and
`75c7a518` for the control.

| run | worker | env container exit (`marks.exitCode`) | publisher after | rows after |
|---|---|---|---|---|
| `shutdown-with-worker` | live stand-in, one delivery | 0 | `changes`, publication 3, unchanged | 1 |
| `shutdown-no-worker` (control) | defined, never started | 0 | `retired`, reason `shutdown`, 1 s after the stop | 0; the record also carries a scenario error, the missing stand-in log, so it is a partial readback, not a clean run |
| `shutdown-with-worker` on aify-env `770c2c2` (the fix) | live stand-in | 0 | `retired`, reason `shutdown`, publication 4 | 0 |

With a worker, the env log reads `SIGTERM: stopping 1 managed process(es)` … `tree-kill`, then
`agent state retirement: not-proven (1 lifetimes)` (`with-worker.evidence.json`, `envLog`).

The cause, read from source and now proven by the fix (aify-env `770c2c2`, branch fix/stop-awaits-exit, whose log reads `retirement: confirmed`): `Runner.stop` returns after `killTree` without waiting for the
child's observed exit. Only that exit (`finish` → `ManagedLifetimes.end` → `endManaged`) records the lifetime as ended.
So when the shutdown asks for `retirementProof`, the lifetime is still unproven.

Reproduce:

    node scripts/sandbox/gate/gate.mjs --comms=78270d30 --env=f704bcdf --attempt=x \
      --scenarios=shutdown-with-worker,shutdown-no-worker --out=<new dir>

Every gate console here ends `terminal: exit 1` with `0/0 cases passed`, because an observation judges no case. The 0 in
the table is the stopped env container's exit code, not a gate result. Read `attempt.json` → `observations`.
