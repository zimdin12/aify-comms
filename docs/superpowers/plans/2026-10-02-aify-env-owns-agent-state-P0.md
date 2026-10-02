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
  "turn": {"open": true, "startedAt": 1790951000456, "lastEventAt": 1790951300789, "awaitingInput": false,
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
- **Busy readers** (B9 queueIfBusy, B17 claim, B18, B19 steer bypass, B21 reminders, B26, B27, B43, B44): read
  `holdsDelivery` (C6).
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

**The turn.** Events are hooks (turn-start, turn-end, blocked, unblocked), carrying `firedAtUs`, the lifetime and
the instance. Ordering is today's `accept_hook_event`, ported entry for entry and checked by a table run against the
Python statement:

| incoming event | rule |
|---|---|
| no `firedAtUs` | applies, outside the ordering |
| a lifetime other than the agent's current adopted one | refused and logged |
| the first event of a lifetime | applies |
| fired later than the last applied event of that lifetime | applies |
| the same microsecond, and a `turn-end` | applies (end wins a tie) |
| otherwise | refused and logged |

A runtime exit ends the open turn. **Busy** is `turn_is_still_live(startedAt, lastEventAt, renewable, now, 1800 s)`,
ported unchanged, under two rules that are published side by side (`turn.busyIf`):

- `strict`, today's rule for a hook-owned turn: `renewable` is false, so the turn holds for 1800 s from its start.
- `verifiedRenewal`, P-1: `renewable` is true when the last event came from the agent's current lifetime, and that
  lifetime's process is `running` and `verified: yes`.

`busy` follows `strict` until the operator chooses.

**The word.** First match wins. The screen is fresh while it is within 75 s on aify-env's clock.

| # | condition | state | cause |
|---|---|---|---|
| 1 | `lifecycle.stoppedByOperator` | `stopped` | operator-stop |
| 2 | definition invalid, harness not installed, or runtime unknown, and no running process | `misconfigured` | config |
| 3 | process `unknown` (the probe cannot answer) | `unknown` | probe |
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
 "pid": 41236, "launcher": "C:/Users/.../claude-aify", "writtenAt": 1790950000500, "herdrPane": "w1:p3"}
```

- `pid` is the **launcher's own OS pid** (on Git Bash `/proc/$$/winpid`, never `$$`). The launcher lives for the
  session and runs the exit path.
- One file per lifetime, so an exiting launcher removes only its own and can never remove a replacement's.
- `instance` is the aify-env instance the launcher ran under; only that instance adopts it.

**Adoption** asks the OS for the pid's creation time and command line:

| answer | verified |
|---|---|
| created at or before `writtenAt` (millisecond floor, **no tolerance**), and the command line contains `launcher` | `yes`, and that exact creation time is pinned |
| later sightings: the same pinned creation time | `yes` |
| gone, created after `writtenAt` or with a different pinned time (a reused pid), or no launcher in the command line (a sibling) | `no`: the lifetime is over, and aify-env removes the record |
| the probe cannot answer (access denied, timeout, no probe) | `unknown`: reported, never acted on, never removed |

No tolerance, because a reused pid is created after the original wrote its record, and the record's own process
existed when it wrote. A tolerance would admit a pid reused within it (comms-senior-dev, review of eda03a3d).

**More than one live lifetime** for one agent: the newest `writtenAt` with `yes` is current, and any other `yes` is a
conflict, published and never resolved by picking. One agentId from two instances is a conflict too.

**Routing.** Each instance writes `~/.aify/env/<instance>.json` `{url, instance, pid, startedAt}`. A hook reads its
own instance's descriptor (`AIFY_ENV_INSTANCE`) and carries `AIFY_LIFETIME`. A managed worker gets `AIFY_ENV_URL`.
An adopted resident's turn is `unknown` until its next hook; its process is known at once.

**Controls (P2):** reused pid, sibling launcher, wrong instance, delayed prior-lifetime hook, tied events, access
denied, an old exit beside a new record, adoption with no next hook, and two live lifetimes.

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
commsView(record | none, messaging) -> {status, statusNote, deliverable, holdsDelivery}
```

One pure function, called by the poll, the push and every reader. Its inputs:

- `record`: the newest applied C1 record for the agent, or none, or one marked stale by C5.
- `messaging`: the agent's run in flight, a reply owed, unread messages, delivery components (sidecar, wrapper
  child, wake path), and, for an **undefined** agent in this tag, the operator stop flag.

| case | status | note | holdsDelivery |
|---|---|---|---|
| no record | `offline` | "environment not reporting" | the run in flight only |
| a stale record | `offline` | "environment not reporting since <time>" | the run in flight, OR the last record's open turn under the **strict** law: 1800 s from `turnSeenAt` |
| a record | the record's `state` (`idle` shown as `online`) | the cause, plus messaging notes | `record.busy` OR run in flight |
| a run in flight, and the state is `idle`, `shell`, `starting` or `available` | `working` | "running <run>" | true |
| no delivery path | unchanged word | "no delivery path: <reason>" | as above; `deliverable: false` |

The running-run row is today's promotion, and it stays a messaging fact.

**A stale publication keeps the open turn, strictly.** `turnSeenAt` is aify-comms' own clock when it first applied a
record showing that turn open: a new `turn.startedAt`. Today's turn-start route stamps the service's own time too.
A publisher that goes quiet mid-turn therefore holds delivery exactly as a hook-owned turn with no further hooks does
today, up to 1800 s from its start, and never longer, because nothing fresh renews it. Releasing at staleness would
be the early release comms-senior-dev measured in the review of 7ca834ae (R2), and so would be a policy change.

## C7. Deleted after the switch

From aify-comms:

- the turn and process inputs of `derive()`, and `status_inputs.py`'s gathering;
- `agent_turn_state`, `agent_status_state`, `agent_hook_order`;
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

- One open request per agent (a unique partial index); a second is refused `request-open`, naming the first.
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
