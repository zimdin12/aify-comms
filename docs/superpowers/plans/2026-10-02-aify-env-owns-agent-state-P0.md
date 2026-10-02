# aify-env owns the agents' state: P0 contracts

The contracts [the plan](2026-10-02-aify-env-owns-agent-state.md) needs frozen before any P2 code, for the end state
the operator approved (aify-env owns agent state, lifecycle and processes; aify-comms owns messaging and the
delivery decision). Grounds: the v0.8.0 tags, comms 3811a66f, env df319ad, wrapper 5f3d3018.

## C1. What aify-env publishes per agent

One record per agent per publishing instance: the observations and the **one** derived word.

```json
{
  "agentId": "comms-tech-lead", "lifetime": "7f3c9e2a-...", "mode": "resident", "harness": "claude",
  "state": "working", "stateCause": "turn-open",
  "busy": true,
  "process": {"state": "running", "verified": "yes", "pid": 41236, "createdAt": 1790950000101},
  "turn": {"open": true, "startedAt": 1790951000456, "lastEventAt": 1790951300789, "ageMs": 300912, "awaitingInput": false,
           "busyIf": {"strict": true, "verifiedRenewal": true}},
  "screen": {"state": "working", "observedAt": 1790951301000},
  "background": {"shells": 0},
  "lifecycle": {"stoppedByOperator": false, "definition": "valid", "startable": true},
  "runsWith": {"model": {"value": "...", "from": "definition"}, "effort": {"value": "...", "from": "definition"}}
}
```

- `state`: C3's word. `busy`: C3's turn law under the rule the operator chose (P-1); until then it is the strict
  rule, today's.
- `turn.busyIf` carries both answers, so the comparison (C8) can show what P-1 would change.
- `stateCause`: the C3 rule that decided the word, for the explanation a reader shows.
- All times are the host's clock at observation. A receiver judges freshness on its own clock from when it applied
  the publication (C5).

## C2. The reader ledger, in the end state

The census (`evidence/2026-10-02-c2/reader-ledger.md`, 96 readers at 3811a66f, a candidate inventory, not certified
exhaustive) is mapped here input by input, then reader by reader. **Input side**: who owns each fact `derive()` and
the gates read today, after the switch.

| today's input | owner after | where it comes from |
|---|---|---|
| `in_turn`, `awaiting_input`, `turn_busy` (hooks, detectors) | aify-env | C3's turn, from hooks and the screen |
| `turn_busy` from the delivery reporters (`dispatch-loop`, `claude-channel`, `hermes-run-reporting`) | aify-comms | its own run in flight: `dispatch_runs` running, owned by a live delivery bridge |
| `worker_present` (managed), `alive` / `has_live_session` / `bridge_stale` (resident) | aify-env | `process` (running and verified) |
| the delivery half of worker presence (channel sidecar, wrapper child), wake mode, capabilities | aify-comms | its own delivery components: the `deliverable` flag and a note |
| `console_booting`, `spawn_starting` | aify-env | `starting` |
| `disabled` | aify-env for a defined agent (`lifecycle.stoppedByOperator`); aify-comms for an undefined one, for one tag (D8) | |
| `config_defect` | split: harness missing, definition invalid or runtime unknown is aify-env's `misconfigured`; no wake path is aify-comms' `deliverable: false` and a note | |
| `env_reachable` | aify-comms | its receiver's freshness for that publisher (C5) |
| `host_activity`, `background_work` | aify-env | `screen`, `background` |

**Reader side.** Each group reads the view C6 defines:

- **Status servers and pushes** (A1-A17, C1-C2): replaced by `commsView` (C6), one function both the poll and the
  push call. Today's two producers disagree (KNOWN_ISSUES); after the switch there is one.
- **Busy readers** (B9 queueIfBusy, B17 claim, B18, B19 steer bypass, B27, B43, B44): read `holdsDelivery` (C6).
- **Run-correlated readers** (B21 reminders, B26 stranded-reply guard): read the delivery turn (C6), which keeps
  today's run identity and its 120 s freshness. A turn's run id is an aify-comms delivery fact today: the hook
  turn-start writes `''` and does not overwrite a run id a delivery set (`turn_boundaries`). Only the delivery
  reporters, the session start route and the orphaned-run reconciler write one.
- **Readiness and eligibility** (B4 preflight, B10-B16 cold-start and twin, B24-B25 claimer, B28-B32 worker
  hygiene): read `process` and `deliverable`.
- **Counts, compaction and session displays** (B33-B40), the dashboard (D1-D20) and the bridge (E1-E9): read the
  view's word.
- **Manual-stop readers** (B17 `{stopped}`, B30, B33, B34, B41, E5): read the view's `stopped`, which comes from
  aify-env for a defined agent.

**`unknown` is new**, and `is_live_agent_status('unknown')` answers live today. In P7 `unknown` joins
`NON_LIVE_AGENT_STATUSES` for counting (fail closed). The send preflight queues to it (stored, delivered when the
state is known), never refuses it. The dashboard shows a grey chip with the cause. Each of these is listed in C8 as
a deliberate change.

**The discriminator.** P4 adds the ledger as data and a test that re-derives the reader set from source with the
census patterns. A reader not in the ledger, or a ledger row whose reader is gone, fails it. P8 runs it again: no
reader of a deleted input may remain.

## C3. aify-env's derivation and turn law

**The turn.** Events are the runtime hooks (turn-start, turn-end, blocked, unblocked), carrying `firedAtUs`, the
lifetime and the instance. The bridge-side detectors and the turn-busy heartbeat post only to aify-comms, for its
delivery turn, until C7 deletes them: they carry no lifetime and are not aify-env events. Two questions are asked of each, in this order and kept apart
(aify-env `lib/turn-events.mjs`). Today's `accept_hook_event` answers both at once at machine grain, and its
owner-change branch is what let an event naming no lifetime, at 100, replace a bound turn's last event at 200
as "the first of a new owner" and close the turn (review of 591d172f, R1).

**Admission: whose turn may it touch?** Decided by the lifetime the event names and C4's verdicts, never by its
time. Every refusal is counted and logged, and changes no turn.

| incoming event | admission |
|---|---|
| a kind not in the vocabulary | refused `unknown-kind` |
| no usable `firedAtUs` | refused `no-timestamp`: every 0.9 hook and detector stamps it, and an end that cannot be ordered could close a newer turn |
| names no lifetime (a launcher from before 0.9) | refused `unbound`. C4 adopts a resident only from its lifetime record, so such an agent has no turn here. aify-comms' engine keeps deciding for it until the switch, and P7 requires every resident relaunched under a 0.9 launcher first |
| the agent's lifetimes are a conflict (C4) | refused `conflict`, whatever it names |
| names the agent's current lifetime (verified `yes`) | admitted to that lifetime's turn |
| names a lifetime whose verdict is `unknown` | a `turn-end` is admitted to that lifetime's retained turn; any other kind is refused `identity-unknown`, since it would renew or re-anchor |
| anything else: verified `no`, no record, no adoption, another lifetime current | refused `not-current` |

**Ordering: in what order, within that one lifetime?** Today's timestamp rules, against that lifetime's own
last applied event. No event of another lifetime reaches it, so there is no owner-change branch to port.

| admitted event | rule |
|---|---|
| the first event of the lifetime | applies |
| fired later than the lifetime's last applied event | applies |
| the same microsecond, and a `turn-end` | applies (end wins a tie) |
| the lifetime's stored record has no usable last event (a damaged turns file) | refused `unordered-record` |
| otherwise | refused `out-of-order` |

The effects are today's `apply_event`: a start opens a turn and keeps the anchor of one already open, an end
closes it, blocked and unblocked set awaiting-input. The run id stays in aify-comms with its delivery.

The shared table (aify-env `tests/fixtures/agent-state-law.json`) keeps today's answer for every ordering row,
which aify-comms checks against the Python, and aify-env's answer beside it. A row where the two differ names why.
Four do: no timestamp, no lifetime (twice, the second the R1 specimen), and no adoption. They are C8's
class `unbound or unstamped`, named there in advance as a policy change for the operator to confirm.

A runtime exit ends the open turn. **The turn is durable.** aify-env writes each lifetime's turn record to
`~/.aify/env/<instance>.turns.json`, keyed by lifetime, with `startedAt` and the last event. The record outlives
a closed turn, because its last event orders the next one, and goes when its lifetime is `no`. A restarted instance
treats each stored turn by what C4 now says of its lifetime:

| lifetime after the restart | the stored turn |
|---|---|
| `yes` | restored as it was: open, same `startedAt`, same last event; `busy` follows the chosen law |
| `no` (proved gone, reused or a sibling) | closed: the process that owned it has ended |
| `unknown` (probe unanswered, or equality) | **retained, not renewed**: published open with its original `startedAt`, `busy` under the **strict** law only (true until 1800 s from that start, never renewed, whatever P-1 says), state `unknown` with cause `identity-unknown`; no lifecycle action may target the lifetime (C9 refuses `identity-unknown`); it is closed only when the lifetime becomes `no`, or a turn-end for it is applied |

So a new generation publishes the same open turn with the same `startedAt`, and never drops accepted busy evidence,
restarts a ceiling, or extends one just because an identity could not be read. A resident adopted for the first
time, with no stored turn, is `unknown` until its next hook (C4).

**Controls (P2):** a restart with an unknown probe (the turn is retained, strict, and expires at 1800 s from its
original start), a restart after a proved exit (closed), and a restart with `yes` (restored, same anchor).

**Busy** is an **open** turn for which `turn_is_still_live(startedAt, lastEventAt, renewable, now, 1800 s)` holds,
ported unchanged, under two rules that are published side by side (`turn.busyIf`). Open first: a closed turn keeps
its last event to order the next one, and the law, given that event and no start, would hold it for the whole window.
The turn record is in microseconds and the law in milliseconds; aify-env converts in one place (`turnIsBusy`).

- `strict`, today's rule for a hook-owned turn: `renewable` is false, so the turn holds for 1800 s from its start.
- `verifiedRenewal`, P-1: `renewable` is true when the last event came from the agent's current lifetime, and that
  lifetime's process is `running` and `verified: yes`.

`busy` follows `strict` until the operator chooses.

**The word.** First match wins. The screen is fresh while it is within 75 s on aify-env's clock.

| # | condition | state | cause |
|---|---|---|---|
| 0 | a fact outside its vocabulary, or missing | `unknown` | unrecognised |
| 1 | `lifecycle.stoppedByOperator` | `stopped` | operator-stop |
| 1a | a conflict: two verified lifetimes, or one agent from two instances (C4) | `unknown` | conflict |
| 2 | definition invalid, harness not installed, or runtime unknown, and no process that is or may be running (process `unknown`, or running with identity `yes` or `unknown`, may be) | `misconfigured` | config |
| 3 | process `unknown`, or running with its identity `unknown` (C4) | `unknown` | identity-unknown |
| 4 | process running and verified; fresh screen `working` or `blocked` | that word | screen |
| 5 | process running and verified; turn busy | `blocked` if awaiting input, else `working` | turn-open |
| 6 | process running and verified; fresh screen `shell`, or background shells | `shell` | at-prompt |
| 7 | process running and verified | `idle` | at-prompt |
| 8 | process `starting`, within the startup window | `starting` | starting |
| 9 | managed, defined and startable, no process | `available` | startable |
| 10 | otherwise | `offline` | absent |

These are today's precedences: a positive screen sighting outranks the bookkeeping, and an idle or shell screen never
ends an open turn (row 5 is above row 6). Past its window, a `starting` that never produced a process falls to
row 9 or 10, so it cannot hang there. **Out of this tag**, and named so it is not done by accident: ending an open
turn because the screen has read idle for a sustained time.

## C4. A resident is a lifetime record, verified against the OS

The launcher writes `~/.aify/residents/<agentId>.<lifetime>.json` at start:

```json
{"agentId": "comms-tech-lead", "lifetime": "7f3c9e2a-...", "instance": "default", "harness": "claude",
 "pid": 41236, "launcher": "/c/Users/.../claude-aify", "writtenAtUs": 1790950000500123, "herdrPane": "w1:p3"}
```

- `writtenAtUs` is microseconds since the epoch (`$EPOCHREALTIME`); a value below 10^15 is milliseconds and the
  record is refused, since it would read every pid as reused.
- `launcher` is `$0` exactly as invoked. It is matched as one whole argument of the pid's command line, folding
  slashes, case and Git Bash's `/c/` against `C:/`, because Windows reports a Git Bash launcher in its `/c/` form.

- `pid` is the **launcher's own OS pid** (on Git Bash `/proc/$$/winpid`, never `$$`). The launcher lives for the
  session and runs the exit path.
- One file per lifetime, so an exiting launcher removes only its own and can never remove a replacement's.
- `instance` is the aify-env instance the launcher ran under; only that instance adopts it.

**Adoption** asks the OS for the pid's creation time and command line:

| answer | verified |
|---|---|
| created **strictly before** `writtenAtUs`, compared at the finer of the two precisions (Windows creation time is 100 ns; `writtenAtUs` carries microseconds, `$EPOCHREALTIME`), with `launcher` as an argument of the command line | `yes`, and that exact creation time is pinned |
| later sightings: the same pinned creation time | `yes` |
| created at a time **equal** to `writtenAtUs` at the comparable precision | `unknown`: indistinguishable from a pid born just after the write, so it is never adopted, never removed and never acted on |
| created before the write, but `launcher` is not an argument of its command line | `unknown`: more likely a spelling the match does not fold than another process, and a false `no` would remove a live launcher's record |
| gone, or created after `writtenAtUs` or with a different pinned time (a reused pid) | `no`: the lifetime is over, and aify-env removes the record |
| the probe cannot answer (access denied, timeout, no probe, no creation time) | `unknown`: reported, never acted on, never removed |

No tolerance, and no equality. A reused pid is created after the original wrote its record, and the original existed
before writing. Pinning a first sighting proves nothing on its own, so a creation time that cannot be ordered against
the write is `unknown`, not `yes` (comms-senior-dev, reviews of eda03a3d and 6e79bcac, N3). The original is never
removed on an `unknown`, so a live one is never lost.

**More than one verified lifetime** for one agent, or one agentId from two instances: that agent's record is
`state: "unknown"` with cause `conflict`, naming both. Consumers act conservatively until one ends: aify-comms holds
delivery if **either** is busy (C6), every lifecycle request for the agent is refused `conflict` (C9), and nothing is
removed. Neither is picked as current.

**Routing.** Each instance writes `~/.aify/env/<instance>.json` `{url, instance, pid, startedAt}`. A hook reads its
own instance's descriptor (`AIFY_ENV_INSTANCE`) and carries `AIFY_LIFETIME`. An adopted resident's turn is `unknown`
until its next hook; its process is known at once.

**A managed worker's lifetime** is minted by aify-env when it spawns the worker, and passed with `AIFY_ENV_URL`,
`AIFY_ENV_INSTANCE` and `AIFY_LIFETIME`, so its hooks are admitted like a resident's. aify-env holds that process, so
the lifetime is `yes` from the spawn with no record to adopt, and it ends when the process exits or the instance
that spawned it ends. Without this, C3 would refuse every managed worker's events as `unbound`.

**Records are read across instances**, because one agent verified under two instances is a conflict. Only a lifetime
of the reading instance can be its current one, and only that instance retains an `unknown` one.

**Controls (P2):** reused pid, an unmatched launcher, wrong instance, delayed prior-lifetime hook, tied events, access
denied, an old exit beside a new record, adoption with no next hook, two live lifetimes, and a managed worker's
first hook.

## C5. Publication order

At boot, `generation = max(persisted + 1, now_ms)`, written and fsynced to `~/.aify/env/<instance>.generation`
before the first push; a random `incarnationId`; one `publication` counter for every push the incarnation makes,
with one push in flight per destination.

```json
{"kind": "snapshot", "complete": true, "machineId": "win32:stevenz-l", "instance": "default",
 "generation": 1790950000123, "incarnationId": "b1e...", "publication": 42, "agents": [...],
 "removed": [{"agentId": "old-agent", "lifetime": "..."}]}
```

- A snapshot lists every agent the instance publishes, and absence means gone. A failed or partial enumeration is
  sent as `kind: "unavailable"` with a reason; it renews nothing and removes nothing.
- `kind: "changes"` carries the changed agents and `removed` (by lifetime), so a delayed removal cannot end a
  recreated agent.

**Receiver**, keyed by `(machineId, instance)`, validating the whole body and applying it **in one transaction of its
own** (never through a writer that commits inside):

| incoming vs last applied | answer | effect |
|---|---|---|
| none, or a higher `generation` | 204 | apply; a higher generation first drops everything the older one published |
| same generation and incarnation, higher `publication` | 204 | apply |
| same generation and publication, same digest | 200 `{applied: false, reason: "duplicate"}` | nothing; freshness not renewed again |
| same generation with a different incarnation, or same publication with a different digest | 200 `{applied: false, reason: "conflict"}` | nothing; reported |
| lower generation, or lower publication | 200 `{applied: false, reason: "stale"}` | nothing |

A snapshot every 60 s, so a quiet host stays fresh. No applied publication for 180 s makes that publisher's agents
stale (C6). The credential is the service's own `agentState.credentialRef`, under `x-aify-agent-state-key`, sent only
to that destination. A service that names none gets its ordinary key, which is its trust policy, not proof of host
authorship. A non-2xx answer is dropped, never retried.

**The unrecoverable case:** a lost generation file **and** a clock behind the last generation. The receiver reports
that publisher as behind, and never accepts it as current.

**Controls (P2, P4):** an old snapshot after a newer change; removal and recreation; boots A, then B, then a delayed
A; equal and backward clocks; a duplicate; a conflict; a partial collection; a quiet snapshot.

## C6. aify-comms in the end state

```text
commsView(record | none, messaging, deliveryTurn, now) -> {status, statusNote, deliverable, holdsDelivery, runOccupied}
```

One pure function, called by the poll, the push and every reader. Its inputs:

- `record`: the newest applied C1 record for the agent (fresh or stale per C5), or none. A conflict (C4) supplies
  both records.
- `messaging`: the agent's dispatch runs, a reply owed, unread messages, delivery components (sidecar, wrapper
  child, wake path), and, for an **undefined** agent in this tag, the operator stop flag.
- `deliveryTurn`: aify-comms' own record of a turn it delivered (below).

**The word.** First match wins.

| # | case | status | note |
|---|---|---|---|
| 1 | an undefined agent the operator stopped (aify-comms' flag, D8) | `stopped` | "stopped by <actor>" |
| 2 | no record, and an undefined managed agent aify-comms can still cold-start by its own spawn path (D8) | `available` | "not defined in aify-env" |
| 2b | no record | `offline` | "environment not reporting" |
| 3 | a stale record | `offline` | "environment not reporting since <time>" |
| 4 | a conflict record | `unknown` | names both lifetimes |
| 5 | a run aify-comms delivered is running, and the record's state is `idle`, `shell`, `starting` or `available` | `working` | "running <run>" |
| 6 | a record | its `state` (`idle` shown as `online`) | its cause |

Then the messaging notes are appended, and a missing delivery path sets `deliverable: false` with "no delivery path:
<reason>". The word is never changed after row 6.

**Row 5 is the one place a messaging fact changes the word.** It is today's running-run promotion
(`_status_with_dispatch`), on the same set of words: today `available`, `starting`, `online` and `shell`, never a
manual, non-live or blocked status. It stays because a run aify-comms is delivering is work it knows is happening.
The plan's D2 says so. C8 compares it as its own decision, because today's promotion is applied on some paths and
not others (the census, KNOWN_ISSUES).

**The delivery turn**, aify-comms' own, replacing the run half of `agent_turn_state`: `{agentId, open, runId,
touchedAt, startedAt}`. Its merge rules mirror today's single row:

| event | effect |
|---|---|
| a delivery reporter's busy for run R | open, `runId` R, `touchedAt` now, `startedAt` now if it was closed |
| a delivery reporter's clear for run R | closes it only if `runId` is R (today's owner rule) |
| an applied record whose turn advanced (`lastEventAt` moved) while open | `touchedAt` = the application time, as a hook refreshed the shared row |
| an applied record whose turn ended (a turn-end applied in aify-env) | closes it, as a hook turn-end clears the shared row |

**Busy.** `holdsDelivery` is true when any of these holds:

- the fresh record's `busy`; under a conflict, either record's `busy`;
- the delivery turn is open, judged by `turn_is_still_live` on its own anchor, renewable while its delivery bridge
  heartbeats (today's verified renewal);
- a stale record's open turn, held under the **strict** law to `strictExpiry` (below), never renewed.

**Run occupancy is a separate output**, as it is a separate check today. `runOccupied` is true while a dispatch run
for the agent is claimed or running: today's `hasActiveRun` (`dispatchState.activeRun`), the same rows and the same
discard of an unusable run. It is **not** inferred from, or folded into, `holdsDelivery`, because a delivery turn can
close while its run stays running. The gates read the two exactly as they read their counterparts today:

- send-time queue (B9): queue when `runOccupied`, or queued runs exist, or `holdsDelivery`;
- claim (B17): today's active-run checks (driver state, run owner's bridge heartbeat, console owner) are unchanged
  messaging facts, beside `holdsDelivery` where `_turn_busy_holds_delivery` stood; the steer bypass (B19) is
  unchanged;
- reminders (B21): `hasActiveRun` is `runOccupied`, as today.

Row 5 of the word and `runOccupied` read the same run, so a running run both shows `working` and is counted as
occupancy, while `holdsDelivery` may be false. That is today's combination too, and C8 compares the gates with the run
running and not running, the delivery turn closed in both, and steer and non-steer targets.

The run-correlated readers keep today's expressions on these facts:

- **Reminders (B21):** `turn_fresh` is an open delivery turn touched within 120 s, or a record's open turn whose last
  advance was applied within 120 s. `turn_run_id` is the open delivery turn's `runId`, else `''`. "Busy for other
  work" is `turn_fresh and turn_run_id != this run`, unchanged.
- **Stranded-reply guard (B26):** skip while the delivery turn is open with `runId` equal to the run, unchanged.

**The stale anchor (N2).** Every record carries `turn.ageMs`, the turn's age on the publisher's clock when it
published. Each application gives an estimate of the start on aify-comms' clock: `appliedAt - ageMs`. That is never
earlier than the true start, because a publication cannot be applied before it was sent. aify-comms keeps the
**earliest** estimate per turn (`startedAt` identifies the turn), and `strictExpiry` is that estimate plus 1800 s.

The bound, named: a stale turn holds until 1800 s after its start, **plus the smallest delivery delay of any
publication of that turn aify-comms applied**. Today's anchor is aify-comms' receipt of the turn-start hook, so
today's bound is 1800 s plus that hook's delivery delay. Both are 1800 s plus one delivery delay. In C8 the
difference is measured, not assumed: both anchors are recorded per turn, and a turn that holds longer under the new
one is a classified difference. A ceiling never restarts, because a new generation publishes the same `startedAt`
(C3, durable turns), and an estimate can only move earlier.

## C7. Deleted after the switch

From aify-comms:

- the turn and process inputs of `derive()`, and `status_inputs.py`'s gathering;
- `agent_turn_state` (its run half becomes the delivery turn, C6), `agent_status_state`, `agent_hook_order`;
- the turn-start, turn-end and status-event routes. They stay for one tag, accepting and ignoring, for old
  launchers;
- `turnBusy` on heartbeats. The delivery reporters keep their run PATCHes;
- the console-working lease;
- the terminal `activity` frame's status use;
- the background-work route;
- the claude transcript and codex rollout detectors.

The resident hermes gateway turn detector goes too, unless C8 shows a hermes class that only it covers; then it stays
with the gateway host until the next tag. From aify-wrapper: the comms hook commands and the herdr state hook.

## C8. The comparison that admits the switch

For each decision, aify-comms computes today's answer and the C6 answer **in the same evaluation, on the same
rows**, at every change of either input and on every sweep:

| decision | today | end state |
|---|---|---|
| status word | `derive()` via both gather paths, plus the roster gates and promotion | `commsView().status` |
| busy | `_turn_busy_holds_delivery` | `commsView().holdsDelivery`, under `strict` and under P-1 |
| send-time queue | `dispatch_launch` queueIfBusy | the same decision on `holdsDelivery` |
| claim | the `/dispatch/claim` gate, steer and non-steer | the same on `holdsDelivery` |
| worker readiness | `_worker_liveness_for` | `process` and `deliverable` |
| live counts | `is_live_agent_status` | the same on the view's word |
| reminder skip (B21) | `turn_fresh and turn_run_id != run`, or an active run | the C6 expression on the delivery turn and the record |
| stranded-reply guard (B26) | busy row with this run id | the delivery turn open on this run |
| running-run promotion | `_status_with_dispatch` on each path that applies it | C6 row 5, on every path |
| stale-turn ceiling | 1800 s from the service's receipt of turn-start | `strictExpiry` (C6): both anchors recorded per turn |

**Recorded per decision:**
- evaluations (the denominator) and agreements;
- every disagreement, with both sides' raw inputs and the versions of all three repos;
- the classes that were never observed.

**Classification.** Each disagreement is a **bug** (fixed in aify-env or C6, then measured again), an **approved
policy change** (confirmed by the operator, quoting the class and its count) or **unresolved**. An explanation is
not a classification.

**The switch needs:**
- zero unresolved and zero open bugs;
- every policy change approved, P-1 included;
- a deterministic test for each class the fleet did not show: reordered and tied events; a delayed prior-lifetime
  hook; publication loss; stale, duplicate and conflicting publications; a reused pid; an unknown probe; a renewed
  long turn; an expired turn; an invalid timestamp; a stopped agent; an undefined agent.

**Known in advance to differ, so already named as policy changes for the operator:**
- `unknown` (C2);
- `unbound or unstamped` (C3): an event today's service applies and aify-env refuses because it names no lifetime,
  carries no timestamp, or names a lifetime aify-env has not adopted. The shared table's four divergence rows are
  this class; on the fleet it is every resident still on a launcher from before 0.9;
- a resident with no wake handle shows its state with a "no delivery path" note instead of `misconfigured`;
- P-1;
- the pushed and polled status agreeing where today they do not.

## C9. Lifecycle requests (D8)

aify-comms writes, the plugin claims, aify-env executes and reports.

| field | rule |
|---|---|
| `requestId` | idempotency key: the same id again returns the first result and does nothing |
| `agentId`, `action` | `start`, `stop`, `restart` |
| `expectedRevision` | a different current definition revision is refused `revision-moved` |
| `expectedLifetime` | for stop and restart, a different current lifetime is refused `lifetime-moved` |
| `requestedBy` | the operator gate, as for every definition change |

- One open request per agent (a unique partial index); a second is refused `request-open`, naming the first. An agent whose record is a conflict (C4) has every request refused `conflict`; an agent whose current lifetime is unknown (C3, C4) has every request refused `identity-unknown`, start included, since it may be running.
- Durable results: `done` (with the new lifetime), `refused` (reason) or `failed` (reason), each with its time.
- Start and restart run under the store lock (`admitStart`), so a definition change and a start cannot interleave.
- A request no plugin claims is reported by the doctor and never retried by the service.
- Cold-start on a message is aify-comms deciding the wake and asking through this queue.

## C10. The plugin interface

The core hands every plugin one `ports` object and the plugin's own registry entry. JSDoc typedefs in
`lib/plugins/ports.mjs`:

```js
/** @typedef {object} HostPorts
 *  @property {{machineId: string, instance: string, dedicated: boolean, version: string}} host
 *  @property {object} processes  start / stop / list / output of the PTY processes this plugin owns
 *  @property {object} agents     definitions (list, snapshot, applyRequest, admitStart); lifecycle (start, stop,
 *                                restart); state (current(), subscribe(fn) -> unsubscribe)
 *  @property {(entry: object) => Promise<string>} credential   the key for this plugin's own entry, resolved per call
 *  @property {{watchRoots: () => Promise<{roots: string[], problems: string[]}>, cwdRoots: () => Promise<string[]>}} grants
 *  @property {(line: string) => void} log
 */
/** @typedef {object} ServicePlugin
 *  @property {string} name
 *  @property {() => Promise<void>} start      idempotent
 *  @property {() => Promise<void>} stop       idempotent; leaves no timer
 *  @property {() => {problems: string[]}} state   optional keys a doctor reads: claimer, definitions
 *  @property {object} [capabilities]          e.g. agents (the startable list the picker asks)
 */
/** A factory: (ports: HostPorts, entry: object) => ServicePlugin */
```

- **One conformance test** builds every `FACTORIES` product with stub ports, and asserts the shape, idempotent
  start and stop, no timer left after stop, and `state().problems` an array. The boundary gate already keeps
  service names out of the core.
- **Migration.** Today's loose `shared` bag becomes these ports in P2, with the comms plugin moved onto them, and
  `bin/aify-env.mjs` loses its inline assembly. It is at 997 lines, so the ports file is where that code goes.
- **The generic state feed** (C5) is the core's own publisher, not a plugin. A service opts in with
  `agentState` in its registry entry.
