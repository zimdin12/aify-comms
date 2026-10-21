# aify-env SIGTERM with a live managed worker does not retire its key (2026-10-21)

Asked by aify-dashboard task #39. The pair is comms `78270d30` and aify-env `f704bcdf`. The gate is at `aec786ed`, and
`75c7a518` for the control.

| run | worker | aify-env exit | publisher after | rows after |
|---|---|---|---|---|
| `shutdown-with-worker` | live stand-in, one delivery | 0 | `changes`, publication 3, unchanged | 1 |
| `shutdown-no-worker` (control) | defined, never started | 0 | `retired`, reason `shutdown`, 1 s after the stop | 0 |

With a worker, the env log reads `SIGTERM: stopping 1 managed process(es)` … `tree-kill`, then
`agent state retirement: not-proven (1 lifetimes)` (`with-worker.evidence.json`, `envLog`).

The cause, read from source and not yet proven by a fix: `Runner.stop` returns after `killTree` without waiting for the
child's observed exit. Only that exit (`finish` → `ManagedLifetimes.end` → `endManaged`) records the lifetime as ended.
So when the shutdown asks for `retirementProof`, the lifetime is still unproven.

Reproduce:

    node scripts/sandbox/gate/gate.mjs --comms=78270d30 --env=f704bcdf --attempt=x \
      --scenarios=shutdown-with-worker,shutdown-no-worker --out=<new dir>

An observation run exits 1 by construction, because it judges no case (`0/0 cases passed`). Read `attempt.json` →
`observations`.
