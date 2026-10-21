# SW3a: the sandbox gate's harness (2026-10-21, fourth successor, after review sw3a-acquisition-1792553532164)

Plan `docs/superpowers/plans/2026-10-20-s4-hard-switch.md`, page 2 (approved at 31a790ac). SW3a builds the harness:
the stand-in, the frozen cases and their REDs. Qualifying a candidate (three attempts against one pinned pair) is
SW3b, and the gate qualifies nothing until the runner (`scripts/sandbox/env-against-comms.mjs`, a5c658ff) is accepted.

The first pass was 88efbf12; its review raised R1-R8. The first successor, 5f4effad, closed R2-R8, and the reviewer
accepted amendments A1-A8 ([AMENDMENTS.md](AMENDMENTS.md)) on that carrier. The second successor, ef2264d9, repaired
F1 (R1's window precision) with the bucket model and the typed PASS/FAIL/UNKNOWN verdict, which its review kept. The
third, b7824b6a, closed F1.a and F1.b's prior-frame path: an absence witness has to be a successful read that began
after the end. This commit closes F1.b's last path: a failed final read no longer erases a control an earlier
successful frame showed. R2-R8, A1-A8, A6, F1.a and F1.b's prior-frame path are as they were.

## What is here

| file | what it is |
|---|---|
| `scripts/sandbox/gate/gate.mjs` | the driver: one attempt against `--comms=<ref> --env=<ref>`; `--self-check` judges the judges |
| `scripts/sandbox/gate/cases.mjs` | the thirteen page 2 cases and one amendment case, as twelve scenarios, plus one unjudged observation |
| `scripts/sandbox/gate/judges.mjs` | pure judges over what a scenario gathered; a failure names every condition that did not hold |
| `scripts/sandbox/gate/pair.mjs` | one scenario's comms, relay and aify-env containers, and everything a case may do to them or read |
| `scripts/sandbox/gate/relay.mjs` | the relay aify-env reaches comms through: it can refuse aify-env's publications alone, and logs every report |
| `scripts/sandbox/gate/stand-in.mjs`, `claude-aify.sh` | the stand-in agent and the launcher aify-env starts it by |
| `scripts/sandbox/gate/probe.py` | runs inside comms: API calls with the container's own key, and a change-only timeline on comms' clock, each frame stamped before and after its read |
| `scripts/tests/test_gate_probe_acquisition.py` | the collector's stamps, from its real `watch` loop on a scripted clock |
| `scripts/sandbox/gate/env.Dockerfile` | the gate's aify-env image: the runner's, plus build tools for node-pty, the stand-in and the relay |
| `scripts/sandbox/gate/self-check.mjs` | every judge against its healthy models and one countermodel per condition, the review's histories among them |
| [review-replay/](review-replay/) | the four reviews' synthetic histories, replayed through the judges each review read (88efbf12, 5f4effad, ef2264d9, b7824b6a) and this commit's |
| [arms/](arms/) | the mutation arms over the judges |
| [decision2-red/](decision2-red/), [rejudge.mjs](rejudge.mjs) | the receipt attempt's per-scenario evidence, and this commit's judges run over it again |

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

**A whole-second stamp is a one-second bucket (F1).** comms stamps a claim (`claimed_at`) or a control
(`requested_at`) with the second it happened in, while a window ends at a millisecond. `judges.mjs` `byEnd` places the
bucket [s, s + 1 s) against the end:
- wholly after the end: outside the window (a release);
- wholly before it: inside (a breach);
- straddling it: a millisecond observation decides.
  - *Inside:* the watcher seeing it claimed (or the control present), or the stand-in receiving it, by the end. An
    emitted `t` is an upper bound on when a state was seen, so it can place an event before the end.
  - *Outside:* a successful read that began after the end and still found it queued (or the control absent). The
    collector (`probe.py watch`) stamps `acquiredFrom` before each read's first SQL query, and gives each frame the
    `previousSeenFrom` of the read just before it, which returned the previous frame's content. Only those lower
    bounds count. An emitted `t` never proves absence (F1.a), and a frame with an error, or without the inventory in
    question, is never an empty one (F1.b). `judges.mjs` `absentThrough` holds this rule.
- The controls placed are every control a successful frame showed, once per identity (`controlsOn`). A failed read,
  last or not, neither erases one already seen nor adds one only it mentions (F1.b).
- With neither, the condition reads UNKNOWN. A judge then returns the verdict UNKNOWN, which is never a pass and never
  an asserted breach. There is no grace interval: an early claim or receipt with millisecond evidence still fails.

## Review R1-R8, and where each is closed

| item | closed by |
|---|---|
| R1 bounded holds, lawful release | `withheld` judges claims, receipts and steers inside the window only; the healthy models carry the release after it, and the review's lawful-release history passes. F1, the second review's remainder: whole-second stamps are buckets placed by `byEnd`, and the review's same-second release passes |
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
The attempt ran 5f4effad's sources. Since then the F1 repairs changed `judges.mjs`, `self-check.mjs`, `gate.mjs` (its
log line) and the collector `probe.py`; the other eight hashed files are unchanged. The retained frames therefore
carry no acquisition stamps.
- [rejudge.mjs](rejudge.mjs) runs this commit's judges over the retained evidence ([decision2-red/](decision2-red/),
  each file identical as JSON to what the attempt saved).
- All 14 verdicts and reasons are identical to the recorded ones, with nothing UNKNOWN ([rejudge.txt](rejudge.txt)):
  the re-judge delta is 0 of 14.
- Why nothing moved: the receipt's withheld check ran 7 times over 6 named runs, and every claim second lay wholly
  before its window's end (20,763 to 22,179 ms before it). The receipt holds no controls. No straddling bucket arose,
  so no absence witness was ever decisive.
- Changing one recorded verdict makes it exit 1. Both readers are unwired there, so every case whose RED is the reader
still taking the legacy path must fail.

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

- **Self-check** ([self-check.txt](self-check.txt)): 210/210 witnesses pass. Every judge passes its healthy models and
  gives each countermodel its expected verdict (FAIL, or UNKNOWN where the model withholds the millisecond evidence),
  with `pass` agreeing with the verdict. Every page 2 case's words are found in page 2, and every amended case's words
  in its own amendment entry. The F1 witnesses are:
  - the release in the hold's last second, read queued by a read that began after the end: passes;
  - F1.a, a queued frame emitted after the end but read before it: UNKNOWN;
  - a read still queued after the end, recorded on the next frame as `previousSeenFrom`: passes;
  - the release in the next second: passes;
  - a claim in the hold's last second, seen claimed before it ends: fails;
  - a receipt in the hold's last second, before it ends: fails;
  - a claim in an earlier second, seen late, with no receipt: fails;
  - a claim straddling the end with no millisecond evidence: UNKNOWN;
  - for a steer: seen before the end fails; read absent after the end passes; F1.b, an error read after the end
    between an empty inventory and the steer, UNKNOWN; no millisecond evidence, UNKNOWN.
  - for a steer a successful frame showed inside the window (F1.b, the final inventory), on both judges that use it:
    - followed by a failed last read: fails;
    - with no tail: fails;
    - still in a successful last inventory: fails;
    - a steer only a failed read mentions stays unseen: passes;
    - one steer seen in three successful frames is one known control.
- **The collector** ([scripts/tests/test_gate_probe_acquisition.py](../../../scripts/tests/test_gate_probe_acquisition.py)):
  the real `watch` loop, run against a scripted clock and a stand-in `snapshot`, shows three things:
  - every frame's `acquiredFrom` is at or before its read, and its `t` after it;
  - `previousSeenFrom` is the last read of the previous content;
  - an error read is its own frame with no `runs` or `controls`, and the frame after it names the error read.
- **The reviews' histories** ([review-replay/replay.txt](review-replay/replay.txt)). Each review's rows and the judges it
  read are pinned by sha256: the copies are committed with LF line ends and one final newline, and the replay restores
  each original's own line ends and ending before hashing. A one-byte edit stops it with exit 2.
  - *Round 1* (sw3a-source-1792542290852, judged by 88efbf12): 88efbf12 gives the recorded verdict on all ten rows. The
    four defects (lawful release, Q before idle, empty drain, negative prose) are RED on 88efbf12 and GREEN here. The
    six controls keep their verdicts, and a matched last-known control passes. Rows are adapted only by added marks,
    printed with their reasons, and 88efbf12 gives the same verdict on the adapted rows.
  - *Round 2* (sw3a-successor-1792548943247, judged by 5f4effad): 5f4effad gives the recorded verdict on the seven
    graded rows. The four A6 rows are not graded, because the review withdrew their premise
    (INTERPRETATION-CORRECTION-A6.json).
    - The same-second lawful release is RED on 5f4effad and GREEN here.
    - The next-second release, the original lawful release and the genuine early claim keep their verdicts, as do the
      three last-known controls.
    - A matched control (that row less its 20.950 s queued snapshot) reads UNKNOWN here; 5f4effad called it a breach.
    - Since round 3 the same-second row's PASS needs its 20.950 s queued frame to have been read after the end. It is
      adapted with `acquiredFrom` 20.920 s, and its verbatim verdict (UNKNOWN) is printed beside it.
  - *Round 3* (sw3a-f1-1792551632225, judged by ef2264d9): ef2264d9 gives the recorded verdict on all eight rows.
    - The two erroneous PASSes (a queued frame emitted after the end; an error frame taken as an empty inventory) read
      UNKNOWN here.
    - The six matched controls keep their verdicts. The successful post-end empty inventory is adapted with the
      `acquiredFrom` its description gives it (20.920 s), and verbatim it reads UNKNOWN.
  - *Round 4* (sw3a-acquisition-1792553532164, judged by b7824b6a): b7824b6a gives the recorded verdict on all three
    rows. A steer seen in the window and then a failed last read is RED on b7824b6a (PASS) and GREEN here (FAIL). The
    same history with no tail, and with a successful last inventory keeping the steer, fail on both.
  - Each predecessor's judges in place of this commit's make the replay exit 1. The BAD checks, counted over the
    whole replay:

    | judges substituted | BAD by round | total |
    |---|---|---|
    | 88efbf12 | round 1: 4, round 2: 6, round 3: 6 | 16 |
    | 5f4effad | round 2: 2, round 3: 5, round 4: 1 | 8 |
    | ef2264d9 | round 3: 2, round 4: 1 | 3 |
    | b7824b6a | round 4: 1 | 1 |

    The previous README said 5f4effad's judges failed "exactly round 2's two F1 rows". That is round 2's subset only;
    the review counted 7 across the three rounds then.
- **Mutation arms** ([arms/run_arms.py](arms/run_arms.py), [arms/summary.json](arms/summary.json)): 50/50 killed, all
  restored by byte copy and checked by sha256, baseline and after both exit 0.
  - They cover each review item's guard. f01-f12 are F1's: no blanket grace, the wholly-before bucket, each
    millisecond observation for claims and controls, and UNKNOWN as neither a breach nor a pass.
  - g01-g04 are F1.a/F1.b's in the judges: the emitted `t` used as absence, an error frame counted as an empty
    inventory, `previousSeenFrom` ignored, and a frame's own `acquiredFrom` ignored.
  - h01-h03 are the final inventory's: a failed read's controls counted, the known set taken from the last frame
    only, and one control counted once per sighting.
  - p01-p04 are the collector's, judged by its own test: the stamp taken after the read, `previousSeenFrom` wrong or
    never advanced, and an error frame given an empty inventory.
  - An arm counts as killed only when its checker exits nonzero AND a FAIL line names that arm's witness.
  - The replay also exits 1 under 21 arms: a05 a06 a08 a09 a20 a21 a23 a24 a25 f01 f03 f04 f05 f06 f09 f10 f11 f12
    g01 g04 h02.
  - Per-arm captures are kept beside the driver's run, not committed. One arm (f07) missed its anchor on the first
    campaign, and p01 matched twice on this one. Each time the driver skipped the arm before writing anything, the
    anchor was corrected, and the whole campaign was re-run.

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

## Section 6: the qualification apparatus (review sw3a-successor-1792548943247)

Built after the F1 approval at f5191387; judges.mjs, self-check.mjs, AMENDMENTS.md and the CASES table are unchanged.
Witnesses: `scripts/tests/test_gate_apparatus.py` (20 tests after the review of 11d8f3ae; 11d8f3ae had 18, not the 17 it stated).

1. **R2's native discriminator.** The silenced scenario now waits, after the resume, for comms to accept a fresh BUSY
   record, holds busy for one claim pass (FIRST_PASS_MS + 1 s, on the watcher's frame clock), and only then asks for
   idle. It records `freshBusyAccepted` and `idleRequestedAt`. A fixture check (`case-judging.mjs`) refuses env-resumed
   by name, before its judge runs, when fresh and idle coincide, when no fresh busy record was accepted, or when the
   busy phase was shorter than a claim pass. The retained receipt's marks coincide (both 1792546599000, seen
   1792546599943) and are refused. `rejudge.mjs` stays judge-only.
   - **Changed after review of 11d8f3ae.** The claim-pass check had measured `idleRequestedAt`, the request mark. Idle
     can arrive without the request (the stand-in's unreadable-control fallback), so a 7 000 ms observed phase behind an
     11 800 ms mark was admitted. Admission now reads the observed interval, `idleAccepted` minus `freshBusyAccepted`,
     which must be at least FIRST_PASS_MS on comms' clock (`at`) and on the watcher's (`seenAt`). The request mark is
     still recorded and no longer judged. Witnesses: the review's counterexample, and a pass on only one clock, each
     way. Arms c04a-c04c (`window/campaign`). Under this rule, attempt 1 below (29 000 ms observed) would be admitted;
     its refusal was by the old request-mark rule.
   - Development attempt 1 (`development-s6/1-phase-too-short`, comms 36028554 + env 7a855db0, silenced only):
     fresh busy 29 000 ms before idle, but the phase was 9 349 ms on the frame clock. The fixture check refused it by
     name. The scenario had slept on its own clock; it now waits on the frame clock.
   - Development attempt 2 (`development-s6/2-separated`): fresh busy at 1792558068000 (seen ...068380), idle requested
     12 049 ms later, idle accepted at 1792558097000 (seen ...097923). The fixture is admitted; env-resumed reaches its
     judge and FAILs on unwired main, because main claims the queued send during the silence. The separated phase
     discriminates R2 only on an SW5 candidate.
   - Development attempt 3 (`development-s6/3-final-tree`), on 3d82bad5 with the runner merge and the stdout-only Pair:
     - the network and three containers were owned by full bound ids, and two volumes were owned;
     - the timeline (9 frames), stand-in log (187), relay log (22) and lifetime all parsed;
     - idle was requested 12 102 ms after fresh busy, and the two were accepted 29 000 ms apart;
     - cleanup read back complete, and env-resumed FAILs at its judge (main's RED).
   All three are DEVELOPMENT attempts, not qualification.
2. **Exclusive attempt storage.** `attempt-dir.mjs` claims `--out` with a non-recursive `mkdir`, which the operating
   system grants to exactly one caller; any existing folder, empty or not, is refused. Witnesses: a second acquisition
   of one name is refused, and of eight concurrent node processes exactly one wins.
3. **The mutation controller.** `scripts/sandbox/gate/campaign.py` restores each arm's file in a `finally`, checks
   its sha256, writes the arm's receipt before the next arm, and exits nonzero on a red baseline or after-control, a
   missed anchor, a survivor, a checker that cannot start or times out, a controller exception, a restore mismatch or
   an unreadable receipt. It reads `arms/run_arms.py`'s ARMS literal in place, together with `arms/section6_arms.py`.
   - A witness counts only on a failure line (`FAIL…`, pytest `FAILED`/`ERROR`, node `not ok`): node prints
     `# Subtest: <name>` for passing tests too, and the self-check prints the same words after PASS (arm e10).
   - Campaign (`arms/campaign-s6/`): 77/77 killed, campaign PASS, exit 0. The arms are the 50 retained ones, then
     c01-c06, d01-d02, e01-e10 and s01-s09. c01 and d01 put back f5191387's behaviour (no fixture check; recursive
     mkdir) and are killed.
   - Negative control: one arm that survives makes the campaign exit 1 and restores the file
     (`campaign-s6/negative-control.txt`).
   - Full per-arm stdout/stderr stayed in the session scratchpad; the receipts here carry exit, named failure lines
     and the three hashes.
4. **The runner, R1/R2 carried (approved at cfdf15b1, 098/runner-r1r2).** Merged as 558644f1. The variant keeps its
   exports, volume cleanup and entry-point guard; everything else in `scripts/sandbox/env-against-comms.mjs` is
   cfdf15b1's text. The exact delta against cfdf15b1:
   - `export` on LIVE, KEY, redact, log, deadline, run, must, docker, sha, bytesSha, exists, create, cleanup, outcome
     and sleep;
   - cleanup orders a volume after its container (`volume rm -f`);
   - the CLI runs only when the file is the entry point.
   `histories.mjs` cuts the driver at that guard when present (+3 lines); on cfdf15b1's driver it still cuts at the
   CLI line (`runner-merge/histories-cfdf15b1-driver-control.txt`). On the merged driver (`runner-merge/`):
   - self-check: 28 PASS, exit 0;
   - histories `new`: every history matched;
   - cfdf15b1's 11 arms: 11/11 killed, controls GREEN before and after, driver restored.
   The first arms run (`runner-arms-before-boundary-fix.txt`) had RED controls because the cut left the guard's block
   open, so its kills are void.
   - **Integrated after review of 11d8f3ae** (it found R2 not consumed by gate attempts):
     - the runner exports `setPrimaryUntil`, and `cleanup()` lifts the window around itself, so a caller cleaning up
       inside a window (the gate, per scenario) is never cut;
     - `attempt()` opens the window for its own budget (120 min), so R2's admission, cap and late-result rules bound
       every gate command;
     - a verdict reached after the budget is refused before `attempt.json`;
     - `gate.mjs` runs its CLI only as the entry point, and `attempt()` takes `dock` and `budgetMs` for its test.
     Witness: `scripts/tests/gate_attempt_window.mjs` (by `test_gate_attempt_window.py`). The real `attempt()` runs
     against a stand-in Docker whose every call goes through the runner's `run()` as a node child. A 4 s budget
     passes while comms starts (20 s), and three tests check the cut, full cleanup and the refused verdict. The
     control (120 s budget, nothing slow) expects nothing cut and the cases' own verdict.
     Arms (`window/`):
     - w01 (no window) and w02 (no terminal check) are killed, each by its own test (campaign receipts);
     - r01 (cleanup keeps the window) is killed by the cleanup test, run by hand because the campaign mutates the
       gate directory only (`runner-cleanup-arm.txt`).
     cfdf15b1's arm a09 (removing `sandbox()`'s own lift before cleanup) now survives by design: cleanup lifts the
     window itself, so that line is no longer what guarantees it. The other 10 arms are still killed.
5. **Pair reads Docker's stdout as data (runner R1 in `scripts/sandbox/gate/pair.mjs`).**
   - A constructor option `dock` (default: the runner's `docker`) carries every call: the 19 `docker(` calls become
     `this.#dock(`, and the four `create(...)` calls pass it as their `dock`.
   - The nine data reads moved from `.out` to `.stdout`: `#ownVolumes` (list volumes), `api` (text), `snapshot`,
     `timeline`, `standin`, `lifetime`, `#daemonPid` (find the env daemon), `contact` (relay log), and `restartComms`
     (started at).
   - Two reads stay on `.out` on purpose. `start`'s `/listening on/` test reads `docker logs`, which carries the
     container's stderr on its own stderr. The `envLog` tail is a diagnostic.
   - Witness: `scripts/tests/gate_pair_stdout.mjs`, nine node tests, run by `test_gate_pair_stdout.py`. A scripted
     stand-in Docker answers every call with the value on stdout and a benign `WARNING` line on stderr. Arms s01-s09
     each put `.out` back at one site and are killed by that site's test.
6. **Still open: SW3b.** Three preregistered full attempts on one accepted wired SW5/aify-env pair, every failure
   retained, with source-bound images and owned cleanup. The two cases that pass on unwired main need their
   candidate mutations.

## What this does not show

- No candidate exists yet (SW5). Every RED page 2 names is a mutation of the candidate, exercised only once SW5 exists.
- Real Claude and Hermes queueing is not exercised. The stand-in is bounded protocol evidence.
- The builds may fetch dependencies; the internal network isolates the runs only.
