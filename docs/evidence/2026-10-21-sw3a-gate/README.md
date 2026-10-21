# SW3a: the sandbox gate's harness (2026-10-21, successor after review sw3a-source-1792542290852)

Plan `docs/superpowers/plans/2026-10-20-s4-hard-switch.md`, page 2 (approved at 31a790ac). SW3a builds the harness:
the stand-in, the frozen cases and their REDs. Qualifying a candidate (three attempts against one pinned pair) is
SW3b, and the gate qualifies nothing until the runner (`scripts/sandbox/env-against-comms.mjs`, a5c658ff) is accepted.

The first pass was 88efbf12. Its review (R1-R8) is closed here; every change to page 2's fixtures, checkpoints or
interfaces is an entry in [AMENDMENTS.md](AMENDMENTS.md), each standing only once the reviewer accepts it.

## What is here

| file | what it is |
|---|---|
| `scripts/sandbox/gate/gate.mjs` | the driver: one attempt against `--comms=<ref> --env=<ref>`; `--self-check` judges the judges |
| `scripts/sandbox/gate/cases.mjs` | the thirteen page 2 cases and one amendment case, as twelve scenarios, plus one unjudged observation |
| `scripts/sandbox/gate/judges.mjs` | pure judges over what a scenario gathered; a failure names every condition that did not hold |
| `scripts/sandbox/gate/pair.mjs` | one scenario's comms, relay and aify-env containers, and everything a case may do to them or read |
| `scripts/sandbox/gate/relay.mjs` | the relay aify-env reaches comms through: it can refuse aify-env's publications alone, and logs every report |
| `scripts/sandbox/gate/stand-in.mjs`, `claude-aify.sh` | the stand-in agent and the launcher aify-env starts it by |
| `scripts/sandbox/gate/probe.py` | runs inside comms: API calls with the container's own key, and a change-only timeline on comms' clock |
| `scripts/sandbox/gate/env.Dockerfile` | the gate's aify-env image: the runner's, plus build tools for node-pty, the stand-in and the relay |
| `scripts/sandbox/gate/self-check.mjs` | every judge against its healthy models and one countermodel per condition, the review's histories among them |
| [review-replay/](review-replay/) | the review's own synthetic histories, replayed through 88efbf12's judges and this commit's |
| [arms/](arms/) | the mutation arms over the judges |

The runner is reused, not copied: `env-against-comms.mjs` exports its machinery and runs its CLI only as the entry point.

## How a case runs

Each scenario gets a fresh pair on an internal network, built from archives of the two named commits, no port
published:
- comms, answering as `comms-real`;
- the relay, answering as `comms`, the only name aify-env and the stand-in know;
- aify-env, with the stand-in defined as its managed agent.

A send cold-starts the stand-in: aify-env starts it as `claude-aify` in a terminal, with its own lifetime. It then does
what a Claude worker's bridge does: registers, beats a channel-sidecar heartbeat, claims runs and controls, and reports
`turn-start` and `turn-end` to aify-env's real `POST /agents/:id/turn-event`. The harness drives it through one
control file and reads it through one log, which records each delivery, control, turn event and empty claim poll.

**Three things are captured apart (review R5).**
- *Contact* is the relay's log: every report aify-env sent, the interval it announced (`reportEveryMs`), and whether
  comms applied it (204).
- *DATA* is comms' rows, from `probe.py watch`: a change-only timeline of what comms holds, every 0.5 s, on comms'
  clock.
- *Eligibility* is read from neither. It is what the claim gate did (claimed or withheld, against a claimer seen
  polling) and the word comms showed.

**Clocks.** comms' timestamps are whole seconds. An ordering is judged on comms' seconds and on the order the watcher
first saw the two events, so a tie inside one second cannot pass a claim the watcher saw first.

**A hold is bounded.** A withheld run is judged over its window only. A claim or receipt after the window closes is
the lawful release, judged by the case's own release rule, never as a breach of the hold (R1).

## Review R1-R8, and where each is closed

| item | closed by |
|---|---|
| R1 bounded holds, lawful release | `withheld` judges claims, receipts and steers inside the window only; the healthy models carry the release after it, and the review's lawful-release history passes |
| R2 Q waits for idle, D does not | env-resumed: the queueIfBusy send on the first pass after the idle acceptance, in seconds and watcher order; the steerable send after the fresh acceptance and by the first pass after idle, never held to idle |
| R3 cold-start population | A8: the relay refuses aify-env's publications from before it starts; the records are read back empty before the send, and the judge fails any other population |
| R4 publication-only silence | A2: the relay refuses `POST /agent-state` alone, so the claimer stays in contact and sends create runs; A3: the whole-daemon pause is a separate, unjudged observation. No timer was changed |
| R5 announced cadence | the silence is three of the intervals aify-env announced, from its last applied report; the second holder announces 1000 ms; contact, DATA and eligibility are captured apart. An absent announcement fails the fixture by name |
| R6 eligibility before steer | A5: the new case `fresh-busy-beside-unknown` |
| R7 named queued work | A6: a seeded queueIfBusy run, queued at the stop, must be claimed on the first pass after the new lifetime's record; the send made while stopped need not create a run |
| R8 the last-known value | A7: `agent.lastKnown = {status, appliedAt}`, compared with the prior record's word and application time |

## Against the decision-2 pair (comms 36028554, aify-env 7a855db0): the REDs

From the receipt attempt `decision2-red` ([attempt.json](attempt.json), [decision2-red.txt](decision2-red.txt)): both
images built from those commits (`imagesReused: false`), 34 minutes, cleanup read every owned resource back absent.
Every source file hashed at the attempt's start hashes the same in this commit. Both readers are unwired there, so
every case whose RED is the reader still taking the legacy path must fail.

No case failed a fixture: every input page 2 and the amendments freeze was constructed and read back. Each RED below
is the case's own.

| case | result | what failed |
|---|---|---|
| legacy busy, env idle | **RED** | held: never claimed |
| legacy idle, env busy | **RED** | claimed and received inside the hold, before the idle publication |
| busy, steerable run | pass | (none) |
| stale second holder | **RED** | claimed and received while a holder was stale |
| aify-env silenced | **RED** | word `online`, no lastKnown; both sends claimed and received inside the window |
| aify-env resumed | **RED** | both sends claimed before the fresh publication was accepted |
| default send while unknown | **RED** | claimed and received inside the window |
| restart while idle | **RED** | word `online` before the snapshot; claimed before it |
| restart mid-turn | **RED** | word `online`; no lastKnown |
| restored, then changes | **RED** | word `online` after the changes |
| cold start | **RED** | claimed inside the hold, before the new lifetime's record |
| a run running on an idle record | pass | (none) |
| stop, then restart | **RED** | after the stop `available`; after the first turn `working`; the seeded run never claimed |
| fresh busy beside unknown (A5) | **RED** | the steerable run claimed and received inside the window |

The observation `daemon-lost` (A3): after three announced intervals of the whole daemon stopped, comms reads the
environment `offline` (`spawnClaim.canClaim: false`), and both sends are stored with no run.

## The judging, proven

- **Self-check** ([self-check.txt](self-check.txt)): 185/185 witnesses pass. Every judge passes its healthy models and
  fails each countermodel. Every page 2 case's words are found in page 2, and every amended case's words in its own
  amendment entry.
- **The review's histories** ([review-replay/replay.txt](review-replay/replay.txt)): the review's ten synthetic rows,
  pinned by sha256, replayed through 88efbf12's judges and this commit's. 88efbf12 gives the review's recorded verdict
  on all ten. The four defects (lawful release, Q before idle, empty drain, negative prose) are RED on 88efbf12 and
  GREEN here; the six controls keep their verdicts, and a matched last-known control passes. Rows are adapted only by
  added marks, printed with their reasons, and 88efbf12 gives the same verdict on the adapted rows. Run with the old
  judges in place of the new, the replay exits 1 on exactly the four defects.
- **Mutation arms** ([arms/run_arms.py](arms/run_arms.py), [arms/summary.json](arms/summary.json)): 27/27 killed, all
  restored by byte copy and checked by sha256, baseline and after both exit 0. They cover each review item's guard.
  An arm counts as killed only when the self-check exits nonzero AND a FAIL line names that arm's witness. The replay
  exits 1 under the six arms that revert a reviewed defect. Per-arm captures are kept beside the driver's run, not
  committed.

## What remains not buildable as written

1. **Two cases cannot be RED on unwired readers:** *busy, steerable run* (both readers steer a busy agent) and *a run
   running on an idle record* (comms promotes from the active run). Their page 2 REDs are mutations of the candidate.
2. **"online" right after a restart** is unreachable: aify-env publishes a fresh lifetime as `turn-unknown`. A6 judges
   page 2's `online` after the first turn.
3. **The last-known note needs SW5** to expose `agent.lastKnown` (A7).
4. **Whole-daemon loss** stores sends with no run (A3); whether a wired candidate must do otherwise is a send-path
   question outside these cases.
5. **A pair without decision 2** fails the announcement fixtures by name rather than running the cases.
6. **The runner** (a5c658ff) is still unaccepted, so nothing here qualifies a candidate.

## What this does not show

- No candidate exists yet (SW5). Every RED page 2 names is a mutation of the candidate, exercised only once SW5 exists.
- Real Claude and Hermes queueing is not exercised. The stand-in is bounded protocol evidence.
- The builds may fetch dependencies; the internal network isolates the runs only.
