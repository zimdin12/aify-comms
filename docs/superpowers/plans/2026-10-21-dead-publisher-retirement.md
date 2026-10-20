# Retiring a dead publisher's key: owner-authorized, exact-key (design, 0.9.8 pre-switch)

Asked for by the SW1 verdict (`APPROVE_SOURCE_SW1_803EDE80_RESTART_RESTORE_WITH_SWITCH_HOLD`, message
1792531860902-a0541994). It is a design only: nothing is built, swept or cleaned up. The wired switch stays on HOLD
until a resolution is adopted and reviewed.

## The problem, and what this does not change

comms deployed 803ede80 and restored 12 publisher keys for `win32:stevenz-l` at start. 10 of them are `herdr-<invocation>`
keys whose daemons are dead (appendix A). Under SW2's rule, an agent is known only when every holder is fresh and no
publisher on its machine is unreadable (`operational_view._busy`). A dead key never becomes fresh again. A restored one
with no row for an agent makes that agent unreadable too. So after the switch, every send to those agents would queue.

A `herdr-<invocation>` key is single-use: `readInstanceContext` refuses an invocation whose `claimed.json`, `ready.json`
or `owned-processes.json` already exists, and `publishInstanceReady` creates them with `wx`. Nothing ever publishes
under a dead one again, so it never heals the way `default` and the residents key do through a successor's higher
generation.

**The truth table stays as it is.** Unknown stays unknown. The fix removes a key only when its owner tier proves it
dead. It does not reinterpret silence.

## The mechanism

**Who retires.** The aify-env holding this home's residents role, one process per `AIFY_HOME` by an OS-held lock
(`ResidentsRole`, `lib/residents-role.mjs`). Each retirement runs inside `enter()`, and its post rechecks `live()`, as
the role's other effects do. Two live daemons therefore never both act: the second has no role.

**What it retires.** One exact key, `(machineId, "herdr-<uuid>")`, read from that key's own files under this home:
- the descriptor `~/.aify/env/<key>.json` (`parseDescriptor`: `{url, instance, pid, startedAt}`);
- the generation `~/.aify/env/<key>.generation`;
- the invocation root `~/.aify/herdr/invocations/<uuid>/` (`claimed.json`, `owned-processes.json`).

Never `default`, the residents key, or its own key.

**The death proof.** Every part must hold. Any read that fails, any probe answering `alive: null`, or any disagreement
leaves the key unproven, and nothing is sent.
1. **The daemon process.** The descriptor's pid equals `claimed.json`'s pid. `ProcessProbeCache` /
   `answersFrom` (`lib/process-probe.mjs`) then answers `alive: false`, or `alive: true` with a `createdAtUs` later than
   the descriptor's `startedAt` (a reused pid). This is the OS's answer about a process, not silence on the wire, so
   a hung daemon (alive, not publishing) is never proven dead.
2. **The herdr owner.** The invocation's `ownerEndpoint` (`\\.\pipe\aify-herdr-owner-<uuid>`, fixed by
   `readInstanceContext`) refuses a connection with "does not exist". Any other error reads unknown. Only a connect
   is made; nothing is written to the pipe.
3. **The owned work.** The invocation's `owned-processes.json`, read STRICTLY (see below). Every entry's pid
   is `alive: false`, or `alive: true` with a creation time after the entry's `startedAt`. Each entry was written by
   `recordStarted` from `Runner`, the only spawner of managed workers.
4. **The fence.** The generation file parses, and its value is the one the retirement numbers above (step 4 of
   the next section). A file that is missing or unreadable is unproven.

`readOwned` degrades a corrupt or unreadable file to `[]` on purpose, so that a daemon still starts. **The proof
needs its own strict reader**, where a corrupt or unreadable file means unproven. Read through `readOwned`, a lost
file would prove an empty population.

The proof is Windows-only, as the probe is. On Linux `alive` is always `null` until a finer creation time exists,
so nothing there is retired.

## The body, the fence and races

The body is built by a pure function of `(machineId, key, savedGeneration, provenPids)`:
`{kind: "retired", machineId, instance: key, generation: savedGeneration + 1, incarnationId: "predecessor:<key>",
publication: 1, reason: "predecessor-ended", inputs: {pids: "<sorted comma list>", proof: "<sha256 of the proof
facts>"}}`. It is serialized canonically.

- **Late frames from the dead key.** Every frame it numbered carries `generation <= savedGeneration` (`advanceGeneration`
  saves before the first push). `_order` refuses each as `stale` against the retired cursor at `savedGeneration + 1`.
  **The cursor row is the fence, and it is kept.** There is no forget and no delete: a removed cursor would let
  an old frame re-create the key.
- **A saved generation behind comms' cursor** (a lost file recovered from the clock). Its body is `stale`, so nothing
  is retired: a fail-closed refusal. It is reported, never retried higher. Inventing a generation is not proof.
- **Two retirers anyway** (a role handover between two sweeps). The bytes are identical, so the second is a
  `duplicate`. A different build with a different serialization gets a `conflict`. Both leave the first retirement
  in place.
- **The proof goes stale while the post is in flight.** The window is one sweep. The death proof is about processes
  that are already gone: a dead pid cannot come back, and a reused one is caught by its creation time.

## comms: one reason, and one gate of its own

`RETIREMENT_REASONS` (`service/api_core/agent_state_shadow.py`) gains `predecessor-ended`. comms admits a body with
that reason only when both of these hold:
- **(a) The key is not fresh.** Its cursor's last receipt is older than the receipt-freshness window (the 180 s of
  operator decision 2, the same clock SW2 reads). A key that is still publishing is never retired, whatever env
  proved. This is a second, independent check: comms watching the receipt stream, beside env asking the OS.
- **(b) The proven population covers the key's last-known rows.** Every non-null `process.pid` in the key's
  last-known rows is in `inputs.pids`. Rows env never proved refuse the retirement.

A refusal applies nothing, and the reason is recorded. After an accepted body, `_retire` runs exactly as B5 does.

Every copy that changes together:
- comms `RETIREMENT_REASONS` and its validation;
- env: a new pure builder beside `AgentStatePublisher.retire` (`lib/agent-state-publisher.mjs`), which keeps
  `"shutdown"`;
- the cross-repo wire fixtures `service/tests/data/g6_agent_state_wire.mjs` and `test_agent_state_receiver.py`,
  which import env's publisher.

aify-wrapper holds no copy of this vocabulary: the same grep finds the env and comms copies and nothing in the
wrapper. The rule [env schema change needs every copy] still applies: merge the env change only after the comms and
wrapper suites run green with `AIFY_ENV_REPO` set to the candidate.

## The reviewer's counterhistory, answered

> Misretiring a live busy key beside a fresh idle peer lets a claim proceed. Removing an unreadable key can release
> peer-only agents.

That is true, and nothing here makes misretirement safe. The design makes misretirement require **two independent
wrong answers at once**:
- env's OS probe says the pid is gone or reused, while the process lives;
- comms sees no receipt within the freshness window, while the publisher is alive.

A live publisher that is busy keeps publishing, which fails (a). A hung daemon fails proof part 1. Releasing peer-only
agents is the intended effect, and it is allowed only for a key with no living process, owner or owned work. Any
agent still running is listed by the live key (the managed host lists every defined agent no resident claims), and
that key becomes the sole judge.

## Witnesses and controls (each must be watched RED first)

- **env proof:**
  - each of the four parts flips the verdict to unproven alone (dead pid but owner alive; owned entry alive;
    corrupt `owned-processes.json`; probe `null`);
  - a reused pid proves death;
  - a body built twice is byte-identical.
- **comms:**
  - a fresh key's `predecessor-ended` body is refused;
  - an uncovered pid is refused;
  - an accepted body fences a later frame of the old generation (`stale`) and keeps the cursor;
  - a `shutdown` body still behaves as B5 does.
- **Negative control:** the live key and the residents key are never candidates, even when every file is readable.
- **Live population (pre-switch ledger).** Appendix A is the population. The switch is not activated while any key
  in it is unretired. Each retirement is shown by the live mirror's view, not inferred from the sweep's log.

## The one-time cleanup

There is no separate path. Once env and comms ship this, the role holder's next sweep retires each proven key in
appendix A, and each unproven one stays and is named. This is not authorized until this design is adopted and the
code is reviewed.

## Appendix A: the live population (read-only, 2026-10-20T21:31Z start log; pids not re-probed for this doc)

- Restored on `win32:stevenz-l`:
  - the live `herdr-c0912a9d…` (pid 84272);
  - `residents-1212b94bbe48`;
  - 10 dead keys: `herdr-` f17d5dd4, 9821d407, 78cfcc61, 069e3559, b68089e7, 20ecafd8, 9add1a1b, 70d0e9be, ff4ad8b8,
    0d278add.
- Read-only, the `owned-processes.json` of f17d5dd4 still names pid 29316 (`claude-aify`, owner 73960). The other
  nine read `[]`.
- If pid 29316 is alive with its original creation time, that key stays unproven until the worker is stopped. That
  is the operator's action.
