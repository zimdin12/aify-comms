# Sandbox runner: R1/R2 successor to a5c658ff

The fix for the review `REVISE_SOURCE_RUNNER_A5C658FF_R1_R2` (runner-acceptance-1792556712812). R3's redaction
and the DB predicates were closed there and are unchanged. Only `scripts/sandbox/env-against-comms.mjs` changes.
Its CLI is unchanged, so the reviewer's seam (the driver up to `const arg = (name) =>`) applies to both versions.

## What changed

- **R1, ownership.** `run` returns `stdout` (the command's answer, the only thing parsed as data) apart from `out`
  (stdout, stderr and the error, for diagnostics). `create` takes the id from stdout alone. It must be one Docker
  id (12 or 64 hex) that the created name resolves to, read back with `inspect`. Anything else throws "ownership is
  unknown", so the run fails, and the resource is owned by its name so cleanup still removes it. A failed create
  is now owned by name unless the name reads back absent; before, only a positive `present` owned it.
  `sha`, `observe` and the `docker wait` exit code also read stdout only.
- **R2, absence.** `exists` reads absent only on the daemon's not-found answer naming exactly this ref, per kind:
  status 1, empty stdout, and stderr equal to one of the forms below. Each form comes in two spellings,
  `Error response from daemon: …` and `Error: …`. Any other failure stays `unknown`.

  | kind | not-found forms |
  |---|---|
  | container | `No such container: <ref>` |
  | network | `network <ref> not found`, `No such network: <ref>` |
  | image | `No such image: <ref>`, `No such image: <ref>:latest` |
  | volume | `get <ref>: no such volume`, `No such volume: <ref>` |

  The Docker 29.8.2 wording was captured on this host with read-only inspects of a random absent name. The
  directive's assumed form "No such network/volume: <id>" is not what 29.8 says for networks and volumes.
- **R2, deadline.** While the primary steps run:
  - a command is not started after the run deadline;
  - its timeout is cut to what is left;
  - a result that comes back after the deadline reads as timed out, `[came back after the run deadline]`.

  The deadline is lifted before cleanup, so cleanup is never cut short, and each cleanup command stays bounded by
  its own timeout. `deadline()` is also checked before the primary predicate. The self-check's direct child now
  has a timeout, so every `spawnSync` call in the file names one (a witness checks this).

## Evidence

- **`histories.mjs`.** Adapted from the reviewer's `parent-probes-clock-coherent.mjs`: same seam, same fixture laws,
  same history names. It runs the driver's whole `sandbox()` body with a synthetic `spawnSync` and clock; no
  Docker, git or tar runs. One history is added, `deadline-late-return`: a result that returns after the deadline
  despite the cut timeout, the way a late kill does, so the acceptance check is what refuses it.
  - `histories-a5c658ff.jsonl`, mode `old`, exit 0. The reviewer's observations reproduce:
    - `stderr-id-loss`: exit 0 with 3 resources left;
    - `daemon-enoent`: exit 0 with 5 left;
    - `deadline-overrun` and `deadline-late-return`: exit 0 with 4 primary commands started after the deadline.

    His three private controls hold.
  - `histories-successor.jsonl`, mode `new`, exit 0. Every history ends SOUND, meaning exit 0 implies nothing
    left, no primary start after the deadline and no late result accepted:
    - `stderr-id-loss`: exit 0, 0 left;
    - `daemon-enoent`: exit 1, 5 left and named;
    - `deadline-overrun`: exit 1, refused by the cut timeout;
    - `deadline-late-return`: exit 1, refused by the late-result check;
    - healthy, in-budget and the generic daemon error: unchanged.
  - `histories-negative-control.jsonl`, the successor's expectations run against a5c658ff: exit 1, failing exactly
    stderr-id-loss, daemon-enoent, deadline-overrun and deadline-late-return.
- **`self-check.txt`.** `--self-check` reports 28/28. It adds witnesses for stdout-only ids, an id the name does
  not resolve to, a missing daemon socket, a not-found answer for another ref, each kind's 29.8 wording, start
  refusal, the cut at the deadline, a failed create left unreadable, and every spawnSync being bounded.
- **`arms.py` / `arms-summary.json`.** 11 mutation arms, 11 killed, each by every witness it names. The driver was
  restored by byte copy and checked by sha256, and the controls before and after are green. Full per-arm captures
  are in the scratchpad (`runner-r1r2/arms`).
  - **The first campaign is kept as `arms-run1`.** It had three non-kills:
    - a01 and a06 crashed the self-check before it printed the named FAIL. The witnesses now record a throw as a
      FAIL. a06 also showed that without the start refusal `run` passes a negative timeout and Node throws
      ERR_OUT_OF_RANGE, which would break `run`'s never-throws contract.
    - a09's expected witnesses wrongly included `deadline-overrun`. There the fixture refuses before the clock
      passes the deadline, so that history cannot reach the arm.
- **Native receipts.** The three committed Docker transcripts under `2026-10-20-env-comms-sandbox/` stay bound
  to a5c658ff's driver hash. They were not re-run, and they are not relabelled as this successor's.

## Carrying this onto the SW3a branch's runner variant

`098/sw3a-harness-2` (f5191387) carries a later variant of the same file: `export` prefixes, a CLI behind an
entry-point check, and volume cleanup. The bodies of `run`, `exists`, `create` and `sandbox` match a5c658ff once
the prefixes are removed. Carry the change as a patch of those bodies:

1. Apply the `run`, `exists`/`NOT_FOUND`/`DOCKER_ID`, `create`, `sha`/`observe`/`docker wait` and
   `primaryUntil` hunks unchanged, keeping each `export`. The `volume` row of `NOT_FOUND` is already present for the
   variant's volume cleanup.
2. Add the self-check witnesses unchanged.
3. In the variant, the gate's `scripts/sandbox/gate/pair.mjs` parses a command's `.out` as data at f5191387 lines
   66, 82, 92, 103, 108, 113, 170, 184 and 199. That is the same class as R1: a stderr line would corrupt the
   parsed value. Those reads move to `.stdout`. The two `docker logs` reads (57, 217) want both of the container's
   streams and stay on `.out`. Found by `git grep -n '\.out\b'` over `scripts/sandbox/gate/`; a control grep for
   `import` hits all three gate files.
4. `histories.mjs` cuts at `const arg = (name) =>`, which in the variant sits inside the entry-point `if`. Cut at
   `// The CLI, only when` there, or import the exported `sandbox` directly.
