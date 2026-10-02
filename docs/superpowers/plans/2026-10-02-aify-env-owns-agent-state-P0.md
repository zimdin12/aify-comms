# aify-env owns agent state: P0 contracts

The contracts the plan ([2026-10-02-aify-env-owns-agent-state.md](2026-10-02-aify-env-owns-agent-state.md)) needs
frozen before any P2 code. Grounds: comms 3811a66f, env df319ad, wrapper 5f3d3018 (the v0.8.0 tags). Written
against comms-senior-dev's review of plan 7ca834ae (R1-R5).

**The shape changed in this revision, and the reason is R2.** The first plan had aify-env publish one display word
and had aify-comms gate delivery on it. Today's delivery law is not a function of the display word: a hook-owned
turn holds delivery to 1800 s from its start, a verifiably renewed turn holds while it is renewed, and a fresh
`working` screen with no open turn shows `working` and does not hold (senior-dev's sealed witness of the actual
`_turn_busy_holds_delivery` and `derive()`, 2026-10-02). So aify-env publishes **typed facts**, and aify-comms keeps
both laws exactly, with their inputs relocated. Nothing in C1-C9 changes a delivery or status outcome on purpose;
each place that would is named as a policy change and left out of this tag.

## C1. What aify-env publishes per agent

One record per agent per publishing instance. Every field is an observation the host makes itself; nothing here
is derived from aify-comms.

```json
{
  "agentId": "comms-tech-lead",
  "lifetime": "7f3c9e2a-...",
  "mode": "resident",
  "harness": "claude",
  "process": {"state": "running", "verified": "yes", "pid": 41236, "startedAt": 1790950000123},
  "turn": {"open": true, "startedAt": 1790951000456, "renewedAt": 1790951300789, "renewable": true,
           "awaitingInput": false, "lastEvent": {"kind": "turn-start", "firedAtUs": 1790951300789123}},
  "screen": {"state": "working", "observedAt": 1790951301000},
  "background": {"shells": 0, "observedAt": 1790951301000},
  "runsWith": {"model": {"value": "...", "from": "definition"}, "effort": {"value": "...", "from": "definition"}}
}
```

| field | values | meaning |
|---|---|---|
| `lifetime` | uuid | one process lifetime of this agent on this instance (C4); a restart is a new lifetime |
| `process.state` | `starting`, `running`, `exited`, `unknown` | `unknown` when the identity probe cannot answer (C4): never read as dead |
| `process.verified` | `yes`, `no`, `unknown` | whether the pid is still the recorded process (C4) |
| `turn.open` | bool | the C3 turn record, after C3's ordering |
| `turn.startedAt` / `renewedAt` | epoch ms | the anchor and the last renewal, the two inputs `turn_is_still_live` takes |
| `turn.renewable` | bool | true while the turn's own process is `running` and `verified: yes` (C3) |
| `screen.state` | `working`, `blocked`, `idle`, `shell`, absent | the core screen observer, managed workers and adopted residents whose screen aify-env holds |
| `background.shells` | int | what the screen or hook reports as still running behind an idle prompt |
| `runsWith` | object or absent | defined agents only: what the next start would use. Configuration, never readiness |

All times are the host's clock at observation. A receiver judges freshness on its own clock from when it applied
the publication (C5), never from these.

**Not published, because aify-env does not own them:** `disabled` (operator stop), `config_defect`, environment
reachability, the delivery sidecar's presence, spawn rows, dispatch runs, unread and reply-owed. These stay
aify-comms inputs (C2).

## C2. The consumer ledger

Built from a source census at 3811a66f (2026-10-02): 17 producers and servers (A), 46 decision readers (B), 4 raw
write paths (C), 20 dashboard readers (D) and 9 bridge readers (E). The full table, with each reader's facts,
distinction and decision, is `evidence/2026-10-02-c2/reader-ledger.md`. This section is what it concludes.

**Bidirectional, input side: what each input's source is after 0.9.** An input either keeps its source, or is
relocated to a host publication **and written by the same function into the same row**, so no reader can tell.

| input (table or function) | today's producer | after 0.9 | written by |
|---|---|---|---|
| `agent_turn_state` / `agent_status_state` from **hooks** (turn-start, turn-end, blocked, unblocked) | hooks to the comms routes | hooks to aify-env, published, applied by the ingest | the hook routes' own handler body (`turn_boundaries` turn-start and turn-end, `post_status_event`), factored into one function that the routes and the ingest both call: `accept_hook_event`, the `agent_turn_state` write with the owner marker `user-prompt-submit`, and `_apply_status_event`. So `_turn_lease_is_renewable` stays strict and `_clear_turn_busy_for_dead_bridges` still skips it |
| the same tables from **bridge detectors** and **delivery reporters** | the bridge | unchanged | unchanged |
| `host_activity` (`terminal_sessions.activity_state`, `activity_reported_at`) | the comms plugin's `activity` frame | aify-env's core screen observer, published | `record_host_activity`, for the agent's live terminal; 75 s freshness on the receiver's clock as today |
| `background_work`, console-working lease, terminal prompt hint | the bridge and the terminal stream | unchanged | unchanged |
| worker presence (`_worker_liveness_for`: terminal, sidecar, wrapper child), resident bridge freshness, wake mode, capabilities, `config_defect`, `env_reachable`, spawn and console booting, `disabled` | aify-comms' own rows | unchanged in 0.9; `process` is compared against worker presence in C8 | unchanged |

**Bidirectional, reader side.** Every reader in the ledger reads one of the rows above or a derived word computed
from them, so each keeps its required distinction by construction:

- the status producers and servers (A1-A17) and the push paths (C1-C2) read only the inputs above;
- the delivery decisions that read the turn (B9 queueIfBusy, B17 claim, B18 busy, B19 steer bypass, B21 reminders,
  B26 stranded reply runs, B27 dead-bridge clear, B43-B44 turn routes) read `agent_turn_state` through the same
  law;
- the readiness and eligibility decisions (B4 preflight, B10-B16 cold-start and twin, B24-B25 claimer, B28-B32
  worker hygiene, B33-B40 session and analytics) read only unchanged inputs;
- the dashboard (D1-D20) and the bridge (E1-E9) read served words and fields whose producers are unchanged.

The one reader whose input changes is `host_activity_for`. Its observation now comes from the core observer
instead of the comms plugin's copy of the same rules. Its rules are herdr's in both places, and they are checked
by porting the plugin's rule table entry for entry.

**The discriminator.** P4 adds the ledger as data (`reader-ledger.json`: reader, facts, distinction, row) and a
test that re-derives the reader set from source with the census's patterns. A reader the derivation finds that the
ledger lacks fails the test, and so does a ledger row whose reader is gone. That is coverage of the set, not of
meaning; the meaning is the table above, and it is reviewed.

**Found by the census, and not changed by 0.9.** These are current behaviour, recorded in KNOWN_ISSUES. Each could
appear in C8 as an old-side disagreement, so each is named here:

- `PATCH /agents/{id}` (`update_agent`) writes any lowercased string to `agents.status` with no allowlist and no
  operator gate. A stored `stopped` is the manual stop that derivation may not argue with, so any key holder can
  stop any agent (read 2026-10-02). The `comms_status` tool uses this route for self-report.
- The dispatch run PATCH writes `agentStatus` when it is in `VALID_STATUSES`, which includes `stopped`. No bridge
  sends it; nothing prevents one from doing so.
- The pushed status (`_broadcast_engine_status`, gather path A3) and the polled one (A4) disagree for a resident
  with no wake handle (`misconfigured` against `offline`). The push also skips the roster gates and the
  running-run promotion.
- The orphaned-run reaper's fail-fast set omits `misconfigured` and still lists the retired `stale`. The agent
  drawer offers Stop for `misconfigured` and `starting`.
- The claim funnel checks only raw `stopped`, while the engine also treats `launch_mode='none'` as disabled.
- The pulse board overwrites the roster's gated status with an ungated one until the next poll.

## C3. The turn law, ported, not reinterpreted

**Ordering** is today's `accept_hook_event`, entry for entry, with the host check expressed by lifetime:

| incoming event | rule |
|---|---|
| carries no `firedAtUs` | applies (outside the ordering), as today |
| names a lifetime that is not the agent's current adopted lifetime | refused and logged (replaces "not the registered host": a delayed hook from a previous process cannot apply) |
| first event of a lifetime | applies |
| fired later than the last applied event of the same lifetime | applies |
| fired in the same microsecond and is a `turn-end` | applies (end wins a tie: a wrong idle is corrected by the next turn-start, a wrong working is not) |
| otherwise | refused and logged |

The port is checked case for case against the Python statement, as a table test run on both sides.

**Liveness** is `turn_is_still_live(started, touched, renewable, now, strict=1800 s)`, unchanged, ported to aify-env
for its own published `turn.open`. What changes is who vouches for a renewal: today `renewable` means "the turn's
bridge row exists, is this agent's, and is heartbeating"; in aify-env it means "the turn's own process is
`running` and `verified: yes`" (C4). A hook-owned turn of a resident aify-env has adopted is therefore renewable
while its process lives, where today a hook-owned turn is strict-anchored. **That is a policy change** (a resident
claude's long turn would hold delivery past 30 minutes), so in 0.9 aify-comms does not consume it: aify-comms
computes its own `renewable` exactly as today (C6), and the host's `turn.renewable` is published for the
comparison (C8) and for aify-dashboard only.

**Screen precedence** is today's `derive()` rule, stated as cases:

| screen (fresh) | turn open | result |
|---|---|---|
| `working` or `blocked` | either | the screen's word is displayed; delivery is unchanged (it reads only the turn) |
| `idle` or `shell` | yes | the turn stands; the screen does not end it |
| `idle` or `shell` | no | displayed (`online` / `shell`) |
| absent or stale (older than 75 s on the receiver's clock) | either | decides nothing |

**Not in this tag, named so it is not done by accident:** ending an open turn because the screen has read `idle`
for a sustained time. It would fix the lost-turn-end cases (claude's Esc interrupt, hermes' non-retryable API
error), and it is a delivery policy change that needs the operator's decision.

## C4. A resident is a lifetime record, verified against the OS

**Records.** The launcher writes `~/.aify/residents/<agentId>.<lifetime>.json` at start:

```json
{"agentId": "comms-tech-lead", "lifetime": "7f3c9e2a-...", "instance": "default", "harness": "claude",
 "pid": 41236, "launcher": "C:/Users/.../claude-aify", "writtenAt": 1790950000500, "herdrPane": "w1:p3"}
```

- `pid` is the **launcher's own OS pid** (on Git Bash, `/proc/$$/winpid`, never `$$`, which is an MSYS pid). The
  launcher lives for the whole session and runs the exit path, so its lifetime is the agent's.
- One file per lifetime, so cleanup needs no compare-and-unlink: an exiting launcher removes only the file named by
  its own lifetime and can never remove a replacement's.
- `instance` names the aify-env instance the launcher ran under (`AIFY_ENV_INSTANCE`, else `default`); only that
  instance adopts the record.

**Verification** is env's `defaultVerify` rule, adapted:

| probe answer | verified |
|---|---|
| the OS creation time of `pid` is at or before `writtenAt` (plus `START_TIME_TOLERANCE_MS`), and its command line contains `launcher` | `yes` |
| the process is gone, or it was created after `writtenAt` (a reused pid), or its command line lacks the launcher (a sibling) | `no`: the lifetime is over; the record is removed by aify-env |
| the probe cannot answer (access denied, timeout, no probe on the platform) | `unknown`: reported, never acted on, never removed |

A reused pid necessarily started after the record was written, because the record's own process was alive to write
it; that is the discriminating fact, as in `orphan-reap.mjs`.

**Several records for one agent** (a crash left one, or two launchers ran): the newest `writtenAt` whose
verification is `yes` is the agent's current lifetime; any other `yes` is a **conflict**, published as such and
never resolved by picking. A receiver that sees one agentId from two instances shows a conflict too.

**Hook routing.** The launcher exports `AIFY_LIFETIME` and `AIFY_ENV_INSTANCE` into the runtime's environment, so
every hook it fires carries both. aify-env writes one descriptor **per instance**, `~/.aify/env/<instance>.json`
`{url, instance, pid, startedAt}`, and a hook reads the descriptor of its own instance. A managed worker gets
`AIFY_ENV_URL` directly. A hook whose lifetime is not the instance's current lifetime for that agent is refused
(C3).

**Adoption with no next hook.** An adopted resident's turn is `unknown` until its next hook; `process` is known at
once.

## C5. Publication order

**Publisher incarnation.** At boot each instance computes `generation = max(persisted + 1, now_ms)`, writes it to
`~/.aify/env/<instance>.generation` and fsyncs before its first publication, and draws a random `incarnationId`.
The persisted term makes it strictly increasing across restarts whatever the wall clock does, including two boots
in one millisecond; the wall-clock term recovers a lost file on a host whose clock is sane. A lost file **and** a
clock stepped behind the last generation is unrecoverable by design: the receiver reports the publisher as behind
(below) rather than accepting it.

**Publication sequence.** Every push from an incarnation carries `publication`, one counter for **all** pushes to
all destinations, and goes out one at a time per destination. Per-agent order is not used for membership.

**Body.**

```json
{"kind": "snapshot", "complete": true, "machineId": "win32:stevenz-l", "instance": "default",
 "generation": 1790950000123, "incarnationId": "b1e...", "publication": 42, "agents": [...],
 "removed": [{"agentId": "old-agent", "lifetime": "..."}]}
```

- `kind: "snapshot"` requires `complete: true` and lists every agent the instance currently publishes; absence
  from it means gone. An enumeration that failed or was partial is never sent as a snapshot: it is sent as
  `{"kind": "unavailable", "reason": "..."}` with the same identity fields, which renews nothing and removes nothing.
- `kind: "changes"` lists changed agents, and `removed` lists ended ones by `(agentId, lifetime)`.
- A recreated agent is a new lifetime, so a delayed removal of the old one cannot end it.

**Receiver rules**, for the key `(machineId, instance)`:

| incoming vs last applied | answer | effect |
|---|---|---|
| no prior | 204 | apply |
| higher `generation` | 204 | replace: everything the older incarnation published is dropped, then apply |
| same `generation`, same `incarnationId`, higher `publication` | 204 | apply |
| same `generation` and `publication`, same body digest | 200 `{"applied": false, "reason": "duplicate"}` | nothing; freshness not renewed again |
| same `generation`, different `incarnationId`, or same `publication` with a different digest | 200 `{"applied": false, "reason": "conflict"}` | nothing; reported (doctor row, dashboard) |
| lower `generation`, or same incarnation and lower `publication` | 200 `{"applied": false, "reason": "stale"}` | nothing |

So the R4 case closes: an old snapshot (publication 7) that omits B arrives after B's change (publication 8) and is
stale, never applied. Application is one transaction: validate the whole body, then write.

**Freshness** is renewed only by an applied publication, on the receiver's clock. A quiet host still sends a
snapshot every 60 s with a new `publication`, so quiet is fresh; a replayed old body can never renew. No applied
publication for 180 s makes the instance's facts stale (C6 says what stale means to each reader).

**Transport.** POST to the service's `agentState.path` with its own credential under header
`x-aify-agent-state-key`. The credential is bound to that destination and sent nowhere else. A service that names
no `credentialRef` gets its ordinary key: that is the service's trust policy, not proof that the host wrote the push.
A non-2xx answer is dropped, never retried; the next snapshot repairs.

## C6. What aify-comms does with it in 0.9

aify-comms ingests into `host_agent_state` and maps the facts onto the **existing** inputs, through the **existing**
paths:

- Turn events the host received from hooks are applied through today's ordering into `agent_turn_state` and
  `agent_status_state`, as the hook routes apply them now. `renewable` is computed by aify-comms exactly as today:
  a hook-owned turn stays strict-anchored at 1800 s from its start. When publication is stale, nothing new arrives,
  which is today's "no new hook" and needs no new rule.
- The screen observation replaces the comms plugin's `activity` frame as the source of `host_activity`, with the
  same 75 s freshness on the receiver's clock.
- `process` feeds the comparison (C8) in 0.9 and replaces no worker-presence input yet. Worker presence keeps its
  delivery half (the channel sidecar is aify-comms' own delivery component and stays its fact).
- The delivery-side turn reporters stay aify-comms producers, unchanged: `dispatch-loop.mjs` and
  `claude-channel.js` `reportTurnBusy`, and `hermes-run-reporting.mjs` `reportTurnBusy`/`clearTurn`. They report a
  run aify-comms delivered, renew through a heartbeating bridge, and move with the hermes gateway tag.
- The bridge turn detectors (claude transcript, codex rollout, resident hermes gateway) stay in 0.9. Each is
  retired only when C8 shows a replacement covers its obligation.
- `unknown` is never a status word aify-comms emits in 0.9, so `is_live_agent_status` and every vocabulary reader
  are untouched. (Its current answer for `unknown` is live, a hazard for any later tag that emits it.)

## C7. The switch, and what it deletes

After the comparison (C8) passes, the launchers stop posting turn events to aify-comms and post only to aify-env;
aify-comms' hook routes stay for one tag for old launchers, then go. Nothing else is deleted in 0.9.

## C8. The comparison that admits the switch

For every decision below, aify-comms computes the current answer and the answer with host-sourced inputs **in the
same evaluation, on the same rows**, at each change of either input and on every sweep:

| decision | function |
|---|---|
| status projection | `derive()` via both gather paths |
| busy | `_turn_busy_holds_delivery` |
| send-time queue | `dispatch_launch` queueIfBusy decision |
| claim | the `/dispatch/claim` gate, steer and non-steer |
| worker readiness | `_worker_liveness_for` |

Recorded per decision: the denominator (evaluations), agreements, disagreements with the raw inputs on both sides,
the versions of all three repos, and the classes never observed. Each disagreement is classified **bug**,
**approved policy change** or **unresolved**; an explanation is not a classification. The switch needs zero
unresolved and zero bug, every approved change confirmed by the operator, and a deterministic test for each class
the fleet did not show: reordered events, a delayed prior-lifetime hook, publication loss, stale and duplicate
publications, a reused pid, an unknown probe, a renewed long turn, an expired turn, an invalid timestamp. The
fleet run is the operator's install and restart, separately authorized.

## C9. Lifecycle requests (D8)

Carried like definition requests: aify-comms writes, the plugin claims, aify-env executes and reports.

| field | rule |
|---|---|
| `requestId` | idempotency key: the same id again returns the first result and does nothing |
| `agentId`, `action` | `start`, `stop`, `restart` |
| `expectedRevision` | the definition revision the request was made against; a different current revision is refused `revision-moved` |
| `expectedLifetime` | for `stop` and `restart`: the lifetime being acted on; a different current lifetime is refused `lifetime-moved` |
| `requestedBy` | the operator gate applies, as for every definition change |

One open request per agent (a unique partial index); a second is refused `request-open` naming the first. Results
are durable rows: `done` (with the new lifetime for start and restart), `refused` (reason), `failed` (reason), each
with its time. aify-env takes the definition store lock (`admitStart`) for start and restart, so a definition
change and a start cannot interleave. A request whose plugin never claims it is reported by the doctor, never
retried by the service.
