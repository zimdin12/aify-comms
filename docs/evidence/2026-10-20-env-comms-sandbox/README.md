# aify-env's retirement against comms' receiver, end to end in a throwaway sandbox (2026-10-20)

`scripts/sandbox/env-against-comms.mjs` builds aify-comms from one commit and aify-env from another (`git archive`, so
no checkout is touched). It runs both on a Docker network created with `--internal`, with no published ports, and
puts comms' `/data` on a tmpfs, so no volume is made. It defines one managed agent, lets the env publish, stops the
env with SIGTERM, and reads comms' own database read-only before and after: each publisher's cursor and its row count.
The key is generated per run and never printed. Everything carrying the run's label is removed at the end, pass or
fail, and the last log line before the verdict says what was left.

Adapted from aify-dashboard's `docs/evidence/sandbox-2026-10-20/run.mjs`.

| Run | comms | aify-env | Expected | Result |
|---|---|---|---|---|
| [run-b5.txt](run-b5.txt) | 7c1f76da (main, B5 receiver) | 2ae603de (B5 sender) | the managed key retires | AS EXPECTED: `default` went from `snapshot`, publication 1, 1 row to `retired`, reason `shutdown`, publication 2, 0 rows. The residents key is unchanged. The env logged `retirement: confirmed ["aify-comms"=confirmed]` |
| [run-before-b5-control.txt](run-before-b5-control.txt) | 7c1f76da | 3f87aad (the parent of the B5 sender a1d8c74) | the key stays | AS EXPECTED: both cursors unchanged, the env sent no retirement |
| [run-comms-before-receiver-control.txt](run-comms-before-receiver-control.txt) | 12491a42 (before the B5 receiver) | 2ae603de | the key stays | AS EXPECTED: both cursors unchanged, the env logged `unconfirmed, not-applied` |

Reproduce from this repo, with aify-env's checkout at `~/projects/aify-env` (or `AIFY_ENV_REPO`):

```
node scripts/sandbox/env-against-comms.mjs --comms=7c1f76da --env=2ae603de --expect=retired
node scripts/sandbox/env-against-comms.mjs --comms=7c1f76da --env=3f87aad --expect=kept
node scripts/sandbox/env-against-comms.mjs --comms=12491a42 --env=2ae603de --expect=kept
```

`--no-agent` skips the definition, so the managed key holds no row.

What this does not show:

- **comms' in-memory mirror.** The run reads the cursor and the rows from the database. It does not read the mirror.
  No HTTP route exposes the mirror's view, and a probe process cannot read the service's memory. "K is absent from
  the mirror" is covered by `service/tests/test_publisher_retirement.py`, not by this run.
- **A live fleet.** The agent is defined but never started, and the env runs with a stub `claude-aify` that exits 1.
  A retirement after real managed stops was not exercised.
- **The live daemon's first stop.** The running aify-env predates B5. Its first stop sends no retirement, so the
  paired comms restart is still the interim for that stop.
- The comms image is unstamped (`0.0.0-dev`). node-pty does not load in `node:24-slim`, so the env ran with piped
  stdio. Agent state uses no terminal.
- comms trusts the host name `comms` here (`TRUSTED_HOSTS=comms,comms:8800`), because the env calls it by its network
  alias.
