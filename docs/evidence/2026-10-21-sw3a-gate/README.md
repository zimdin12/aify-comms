# SW3a: the sandbox gate's harness (2026-10-21)

Plan `docs/superpowers/plans/2026-10-20-s4-hard-switch.md`, page 2 (approved at 31a790ac). SW3a builds the harness:
the stand-in, the frozen cases and their REDs. Qualifying a candidate (three attempts against one pinned pair) is
SW3b, and the gate does not qualify anything until the runner (`scripts/sandbox/env-against-comms.mjs`, a5c658ff) is
accepted.

## What is here

| file | what it is |
|---|---|
| `scripts/sandbox/gate/gate.mjs` | the driver: one attempt against `--comms=<ref> --env=<ref>`; `--self-check` judges the judges |
| `scripts/sandbox/gate/cases.mjs` | page 2's thirteen cases as eleven scenarios; `expected` and `red` are page 2's own words |
| `scripts/sandbox/gate/judges.mjs` | pure judges over what a scenario gathered; a failure names every condition that did not hold |
| `scripts/sandbox/gate/pair.mjs` | one scenario's comms and aify-env containers, and everything a case may do to them or read |
| `scripts/sandbox/gate/stand-in.mjs`, `claude-aify.sh` | the stand-in agent and the launcher aify-env starts it by |
| `scripts/sandbox/gate/probe.py` | runs inside comms: API calls with the container's own key, and a change-only timeline on comms' clock |
| `scripts/sandbox/gate/env.Dockerfile` | the gate's aify-env image: the runner's, plus build tools for node-pty and the stand-in |
| `scripts/sandbox/gate/self-check.mjs` | every judge against a passing model and one countermodel per condition |

**The runner is reused, not copied.** `env-against-comms.mjs` now exports its machinery and runs its CLI only as the
entry point. Its cleanup also removes and reads back a `volume`, because the gate keeps comms' data across a
`docker restart` in the image's anonymous volumes (each one owned). Its own `--self-check` still passes 15/15.

## How a case runs

Each scenario gets a fresh pair on an internal network: comms built from one commit, aify-env from another, both
archives of the named commits, no port published. The stand-in is defined in aify-env, and a send cold-starts it.
aify-env starts it as `claude-aify` in a terminal, with its own lifetime. That is why the gate's aify-env image needs
node-pty: without a terminal, comms never asks the host to start a worker.

The stand-in then does what a Claude worker's bridge does:
- registers and beats a channel-sidecar heartbeat;
- claims runs through `/dispatch/claim`, and steer or interrupt controls through `/dispatch/controls/claim`;
- reports `turn-start` and `turn-end` to aify-env's real `POST /agents/:id/turn-event` for its lifetime. A refused or
  unanswered event is retried, because a paused aify-env answers nothing.

The harness drives it through one control file (its turn, and the status a claimed run is written with). It answers
through one log, which records each delivery, control, turn event and empty claim poll. A withheld run is judged only
against a claimer seen polling.

**Observations are on one clock.** `probe.py watch` appends what comms holds whenever it changes, every 0.5 s,
stamped by the comms container. The stand-in's log is stamped by the same Docker VM clock. comms' own timestamps are
whole seconds, so an ordering is judged on comms' seconds (a tie within the second is admitted) and on the order the
watcher first saw the two events.

Fixtures, where page 2 leaves the means open:
- **The legacy answer** is set through comms' own legacy route (`POST /agents/:id/turn-start`), as a hook does.
- **The stale second holder** is a second publisher key on the same machine, posted to `/agent-state`, made stale by
  real receipt silence (185 s). A second aify-env holding the same agent was not built.
- **"aify-env paused"** is `SIGSTOP` of the daemon process only. Pausing the container would stop the stand-in too,
  and a withheld run could no longer be told from a claimer that was not asking.
- **A comms restart** is `docker restart` of the same container, with its data kept.

## Against today's main (comms bbb34c13, aify-env ccf9a7e): the REDs

From the receipt attempt `main-red` ([attempt.json](attempt.json), [main-red.log](main-red.log)). Main's readers are
unwired, so every case whose page 2 RED is "the reader still takes the legacy path" must fail on main.

Comms and aify-env images were built from those two commits, with no reused image (`imagesReused: false`). The attempt
took 31 minutes, and cleanup read every owned resource back absent. Every source file hashed at the attempt's start
hashes the same in this commit.

| case | on main | what failed | page 2's RED? |
|---|---|---|---|
| legacy busy, env idle | **RED** | held: never claimed | yes: the legacy reader holds |
| legacy idle, env busy | **RED** | delivered inside the hold, before the idle publication | yes: it delivers mid-turn |
| busy, steerable run | pass | (none) | no; see the first point below |
| stale second holder | **RED** | delivered while a holder was stale | yes: stale holders ignored |
| aify-env paused | **RED** | word `offline`; no last-known note; **the sends created no run** | no; see the second point below |
| aify-env resumed | **RED** | no queued work to drain: a send created no run | no; same cause |
| default send while unknown | **RED** | the send created no run | no; same cause |
| restart while idle | **RED** | word `online` before the snapshot; delivered before it | yes: restored rows promoted |
| restart mid-turn | **RED** | word `online`; no last-known note | yes |
| restored, then changes | **RED** | word `online` after the changes | yes: changes treated as complete |
| cold start | **RED** | claimed before comms accepted the new lifetime's record | no; see the third point below |
| a run running on an idle record | pass | (none) | no; see the first point below |
| stop, then restart | **RED** | after the stop the word read `available` | yes: the stop row skipped |

## The judging, proven

- `node scripts/sandbox/gate/gate.mjs --self-check` ([self-check.txt](self-check.txt)): 126/126 witnesses pass. Every
  judge passes its model and fails each countermodel, and every case's `expected` and `red` are found verbatim in
  page 2.
- Mutation arms ([arms/run_arms.py](arms/run_arms.py), [arms/summary.json](arms/summary.json)): 15/15 killed on the
  judges this commit carries, all restored (baseline and after both exit 0). Each arm breaks one guard in a judge or a
  case, runs the self-check, keeps its full output, and is restored by byte copy and checked by sha256. An arm counts
  as killed only when the self-check exits nonzero AND a FAIL line names that guard's witness. The per-arm stdout and
  stderr are kept beside the driver's run, not committed: their FAIL lines are in `summary.json`.

## What page 2 asks that is not buildable as written

1. **Two cases cannot be RED on main.**
   - *Busy, steerable run:* both readers deliver a steerable send to a busy agent.
   - *A run running on an idle record:* main promotes from the active run anyway.

   Their page 2 REDs (the carve-out removed; promotion skipped at the view) are mutations of the candidate.
2. **"aify-env paused" cannot produce queued sends** (the paused, resumed and default-send cases).
   - comms ages the environment's claimer heartbeat against 90 s, so the paused aify-env reads offline at 90 s.
   - That is half the 180 s receipt silence the case waits for.
   - Sends made at 185 s are stored with no run: "Owning environment ... is offline; only its bridge can host this
     managed worker".
   - So these three cases fail on any candidate that keeps today's send-time path, which page 1 keeps. One of the
     following must change before SW3b:
     - the cases' expected answers, to "stored, not run";
     - the silence, made to stop only aify-env's publications while its claimer keeps beating;
     - the 180 s or the 90 s.
   - The harness runs the case as page 2 states it, so the evidence shows the collision.
3. **The cold start fails on main through a different path.** Main's claim gate does not wait for the new lifetime's
   record, so the run is claimed early. The case's frozen RED (a hold that skips backing, so there is no spawn
   request) is a candidate mutation.
4. **Under a pause, "steer checked before eligibility" is unreachable.**
   - Every holder is then stale, and SW2's view returns no busy for them.
   - A check of busy placed first therefore never sees busy.
   - That mutant is reached only where a fresh busy holder sits beside unknown eligibility, which no frozen case
     sets up.
5. **The first case needs a non-steerable send.** With a default (steerable) send, main delivered it, so both readers
   gave the same answer. The case now sends with `queueIfBusy`.
6. **A restarted lifetime reads `unknown` until its first turn.** aify-env publishes a fresh lifetime as
   `turn-unknown`, and the candidate word for that is `unknown`. So the final restart case judges `online` after the
   new lifetime's first turn, which the harness drives. Right after the restart it reads `unknown`, and that is the
   case's own frozen RED.
7. **The last-known note has no field yet.** The judges accept `agent.lastKnown`, or a `statusNote` naming "last
   known". SW5 should name the field and the judge should pin it.

## What this does not show

- No candidate exists yet (SW5). Each frozen case was watched RED or not against main; every RED page 2 names is a
  mutation of the candidate, and is exercised only once SW5 is built.
- Real Claude and Hermes queueing (page 1's runtime table) is not exercised. The stand-in is bounded protocol evidence.
- The builds may fetch dependencies; the internal network isolates the runs only.
