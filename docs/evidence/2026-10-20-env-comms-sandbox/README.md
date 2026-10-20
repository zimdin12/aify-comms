# aify-env's retirement against comms' receiver, end to end in a throwaway sandbox (2026-10-20)

`scripts/sandbox/env-against-comms.mjs` builds aify-comms from one commit and aify-env from another (`git archive`, so
no checkout is touched). It runs both containers on a Docker network created with `--internal`, publishes no port,
and puts comms' `/data` on a tmpfs, so no volume is made. It defines one managed agent, lets the env publish, stops
the env with SIGTERM, and reads comms' own database read-only before and after: each publisher's cursor and its row
count.

**The internal network is runtime isolation only.** It is created after the two builds, and `docker build` has no
network-off flag, so a build may fetch dependencies. This is not an offline or hermetic build proof. Build only refs
you trust. No host path is mounted into either runtime container.

**Ownership and cleanup.**
- The run id is a random UUID, and the work dir comes from `mkdtemp`.
- A resource is owned only if this run created it under a name it first read back as absent.
- Cleanup removes exactly those ids, then reads each one back. The run exits 0 only when the primary check holds and
  every owned resource reads back absent.
- A failed readback is reported on its own `cleanup:` line, naming the ids still present or unreadable. Nothing beyond
  the owned ids is ever deleted.
- Every child command has a timeout, and the primary steps share a 30-minute run deadline.
- A SIGKILL of the runner cannot be handled. Each owned id is printed as it is created (`owned ...` lines), for manual
  recovery.

**The key.** It is generated per run and redacted from every printed line, error paths included.

## Receipts (fresh, 2026-10-20, replacing the first runs' logs)

Each log starts with the sha256 of the driver and of both helpers, and ends with `terminal: exit N` and the shell's
own `exit status`. The files in this commit hash to: driver
`4d9e27a606559c12b9220b7bbc83a7649431cd1111a70d6f66295d63941c9fc7`, `env.Dockerfile`
`190a4941d207e774c73ae3fcda2882ebb4bed1fe162836c3a254fb7f0a572bd4`, and `sandbox-entry.sh`
`fe475b05d083356cbabe99406bf2fbb4f5dd88075ce0ca2446229d522ec55abc`.

| Run | comms | aify-env | Expected | Result |
|---|---|---|---|---|
| [run-b5.txt](run-b5.txt) | 7c1f76da (B5 receiver) | 2ae603de (B5 sender) | the managed key retires | AS EXPECTED, cleanup complete, exit 0. `default`: snapshot, publication 1, 1 row → retired, reason `shutdown`, publication 2, 0 rows, same generation. Residents unchanged. The env logged `retirement: confirmed` |
| [run-before-b5-control.txt](run-before-b5-control.txt) | 7c1f76da | 3f87aad (the parent of the B5 sender a1d8c74) | the key stays | AS EXPECTED, cleanup complete, exit 0. Every cursor is unchanged, and no retirement was sent |
| [run-comms-before-receiver-control.txt](run-comms-before-receiver-control.txt) | 12491a42 (before the B5 receiver) | 2ae603de | the key stays | AS EXPECTED, cleanup complete, exit 0. Every cursor is unchanged. The env logged `unconfirmed, not-applied` |

**What `retired` asserts.** For each managed key: kind `retired`, reason `shutdown`, the same generation, a
publication above the snapshot's, and 0 rows. Every residents cursor is byte-equal, and no publisher appears or
disappears. **What `kept` asserts:** every cursor is byte-equal before and after.

[self-check.txt](self-check.txt) is `--self-check`, 15 witnesses with their controls:
- **The key:** a failing child prints a synthetic key through the runner's own error and log paths, and the key never
  appears in the output.
- **Cleanup:** an owned resource still present fails the run, and a primary pass with failed cleanup exits 1. A
  listing that times out is "unknown", not empty.
- **Ownership:** an existing name is refused and never owned.
- **The predicate:** each single-field countermodel of `retired` fails.

[self-check-arms.txt](self-check-arms.txt) breaks each guard in turn, and every arm goes red. Each file is restored by
byte copy and checked. The driver is [self-check-arms.py](self-check-arms.py).

Reproduce from this repo, with aify-env's checkout at `~/projects/aify-env` (or `AIFY_ENV_REPO`):

```
node scripts/sandbox/env-against-comms.mjs --self-check
node scripts/sandbox/env-against-comms.mjs --comms=7c1f76da --env=2ae603de --expect=retired
node scripts/sandbox/env-against-comms.mjs --comms=7c1f76da --env=3f87aad --expect=kept
node scripts/sandbox/env-against-comms.mjs --comms=12491a42 --env=2ae603de --expect=kept
```

## What this does not show

- **comms' in-memory mirror.** The run reads the cursor and rows from the database. No HTTP route exposes the mirror,
  and a probe process cannot read the service's memory. Removal from the mirror rests on
  `service/tests/test_publisher_retirement.py`.
- **A live fleet.** The agent is defined but never started, and the env runs with a stub `claude-aify` that exits 1.
  A retirement after real managed stops is not exercised.
- **The live daemon's first stop.** The running aify-env predates B5. Its first stop sends no retirement, so the paired
  comms restart is still the interim. Restarting it is the operator's action.
- **The dashboard as a receiver.** That is aify-dashboard's own sandbox.
- **Other runtime details:**
  - The comms image is unstamped (`0.0.0-dev`).
  - node-pty does not load in `node:24-slim`, so the env ran with piped stdio. Agent state uses no terminal.
  - comms trusts the host name `comms` (`TRUSTED_HOSTS=comms,comms:8800`), because the env calls it by its network
    alias.
