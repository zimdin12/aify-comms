# `server.js` — what is left, and why the rest needs a decision

**Status:** measured at the end of the v0.5.4 JS owner-move series. **No extraction proposed.** This packet
exists because `server.js` cannot reach the 1000-line goal by continuing what has been working, and the
operator asked for every non-test source file under 1000 lines.

## 1. Where the 3,005 lines are

| region | lines |
|---|---|
| top-level functions (47) | 1,807 — of which `runDispatchLoop` alone is 449 |
| `server.tool` blocks (2) | 246 — `comms_send` 135, `comms_channel_send` 111, both reviewer-parked |
| module scope + `main()` | ~450 — imports, constants, wiring, the startup sequence |
| comments and blanks | the remainder |

**49 commits touching it took it from 6,331 to 3,005**, every moved body byte-identical against
`git show HEAD:`, and 46 non-test modules now exist under `mcp/stdio/` that did not at v0.5.3.

And **no test imports `server.js` at all** any more — it is finally only a bin entry point, which was the
more important half of the exercise than the line count.

## 2. What remains, and why each piece is stuck

**The clean remainder is too small to close the gap.** Eight zero-mutable functions are still extractable by
the method used throughout — `runSingleAgentManagedTeardown` (37L), `runBootTombstonedMarkerSweep` (28L),
`reportDeadOwnedTerminals` (20L), `interruptActiveRuns` (11L) and four three-liners. **~105 lines across
five unrelated subjects.** Extracting them lands `server.js` near 2,900 and costs five more single-purpose
modules. I have NOT done them: grouping by line count rather than by subject is the thing this series has
been careful not to do, and the reviewer's standard has been subject coherence.

**THE BULK IS FOUR SEPARABLE LOOPS — correcting what this packet first said.** I wrote that
`runTerminalControlLoop`, `runSpawnLoop`, `syncManagedEnvironmentAgents` and `runEnvironmentControlLoop`
are "one scheduler over one pile of shared mutable state", because their closure touches 44 functions /
1,725 lines / 27 mutable module names. The count is right; the conclusion drawn from it was not.

Measured per name — who WRITES it and who READS it — **every `*Busy` re-entrancy flag is written and read by
exactly one function**: `dispatchLoopBusy` only by `runDispatchLoop`, `spawnLoopBusy` only by
`runSpawnLoop`, `terminalControlBusy` only by `runTerminalControlLoop`, `managedEnvironmentSyncBusy` only by
`syncManagedEnvironmentAgents`, `environmentControlBusy` only by `runEnvironmentControlLoop`. They are
private guards. Every `*Timer` is written by its own `ensure*` starter and by `cleanupOnExit`, which clears
them on shutdown — the one genuine cross-cutting concern, with the ordinary remedy of a `stop()` per module.
Across the whole file only FIVE names are written by one function and read by another, each a tight pair.

The loops share a namespace, not a state. `runDispatchLoop` (449L) stays parked on its own merits — its size
and its position on the live claim path — not on this.

## 3. Three options, and what each costs

**A. `server.js` exits the 1000-line goal as a wiring file.** Honest and cheap; leaves one of twelve files
over. The argument: a bin entry point that wires a process together is a different kind of file from a
library module, and 3,000 lines of *wiring* is not the defect 3,000 lines of *logic* was — which is what the
other eleven were.

**B. Extract the loop family behind an injected state object.** One `bridge-runtime-state.mjs` holding the
27 names, passed into each loop. Mechanical and byte-identical-able, but it converts 27 module-scope reads
into property reads on a shared object: the same coupling with a longer name. Moves ~1,700 lines, improves
almost nothing. **Not recommended.**

**C. One owner per loop, each with its own state, wired by a scheduler.** `spawn-loop.mjs` owns
`spawnLoopTimer`/`spawnLoopBusy`/`spawnClaimFailureCount`; `terminal-control-loop.mjs` owns its three; and so
on. Each becomes independently testable, which is the actual prize — **none of these loops has a unit test
today**, and they are what reap workers and claim runs. But this is a redesign, not a relocation: it cannot
be byte-identical, it needs its own review standard, and it touches the code path that took the managed
fleet down when it misbehaved.

## `cleanupOnExit` ordering, since a per-loop `stop()` depends on it

**There is no ordering constraint between the loops.** `clearInterval` calls are mutually independent, so
each loop's `stop()` can be its own and the order they are called in cannot matter.

The sequencing that DOES matter is between the teardown steps, not the loops, and the code states it:
`runManagedTeardownSync` "may only reuse targets freshly confirmed by `runManagedTeardownForBridge`. An
unexpected exit has no safe ownership snapshot, so it reaps nothing and the next boot sweep is the
backstop." That is the `confirmedManagedTeardownAgentIds` pair from the five cross-function names — safe by
construction, because an unconfirmed reap is a no-op rather than a wrong kill. `TERMINAL_MANAGER.stopAll()`
precedes it; marker removal comes last.

**AND A FREE IMPROVEMENT THE DESIGN WOULD BRING.** `cleanupOnExit` clears three of the six timers —
`environmentHeartbeatTimer`, `spawnLoopTimer`, `terminalControlTimer` — and leaves `dispatchLoopTimer`,
`environmentControlTimer` and `usageCollectorTimer` running. It is harmless today: the function is reached
only from `process.on("exit")` and from `shutdownWithStatus` immediately before `process.exit(code)`, so the
process is dying either way and `clearInterval` is a formality. But nothing distinguishes the three that are
cleared from the three that are not, which is the signature of an omission rather than a decision. A
per-loop `stop()` makes all six uniform at no cost.

## 4. Asking

1. Is **A** acceptable — `server.js` leaves the 1000-line goal as a wiring file, with the loop family
   documented as a deliberate exception?
2. If not, is **C** the shape you want, and does it belong in v0.5.x or v0.6?
3. Independently: should the eight small ones land, and as how many modules? They are unblocked and use the
   proven method; I held them because five modules for 105 lines looked like shaving rather than structuring.
4. Are `comms_send` / `comms_channel_send` still parked? At 246 lines they are the largest unblocked win if
   not.

## 5. What I have NOT established

- ~~Whether the four loops can be separated without a shared scheduler object.~~ **ANSWERED above: yes.**
  Note what went wrong here — the body of this packet asserted "one pile of shared mutable state" as fact
  while this section simultaneously admitted "several read `*Busy` flags another loop sets, and I have not
  traced whether that is real coordination". The measurement says NONE do. A claim I had explicitly flagged
  as untraced was stated as a finding twenty lines earlier in the same document.
- ~~What ordering `cleanupOnExit` requires when stopping the loops.~~ **ANSWERED: none between the loops.**
  See below. What is still open is narrower: whether `runManagedTeardownForBridge` is guaranteed to have run
  before the synchronous exit path on every shutdown route, or only on the graceful one.

---

## CORRECTION, 2026-08-14 — the remainder census above was PER FUNCTION

**Section 2's "~105 lines across five unrelated subjects" is withdrawn.** It counted, per function, what
was extractable — which treats a call between two functions that would move together in the same slice as
a blocker. That is the identical criterion that shelved `hermes-managed-host.js` as needing a
`runDeliveryLoop` redesign (it went 1,846 → 728 with no redesign) and declared `app.js` irreducible
(4,904 → 4,049 and still going). Third time, same shape.

Measured as GROUPS, with the closure expanded over every module-scope declaration:

| seed | declarations | lines | status |
|---|---|---|---|
| virtual terminals | 7 | 144 | **DONE** — `virtual-terminals.mjs`, server.js 3,006 → 2,867 |
| `runManagedTeardownForBridge` | 5 | 102 | closed; touches the managed-reaping path |
| `reportDeadOwnedTerminals` | 11 | 117 | closed |
| `TERMINAL_MANAGER` | 9 | 93 | closed |
| `runBootSurvivorSweep` | 4 | 92 | closed |
| `spawnTriggeredAgent` | 2 | 85 | closed |
| `ensureRequiredReplyHandoff` | 3 | 71 | closed |
| `fetchManagedOwnershipForEnv` | 3 | 42 | closed (overlaps the teardown group) |

That is roughly **500–600 further lines** in coherent subjects, not 105 in a grab-bag — enough to take
server.js to around **2,300**, still short of 1,000 but far from "cannot reach the goal by continuing what
has been working".

**Section 3's question is unchanged and still yours.** The four scheduler loops plus `runDispatchLoop` are
the bulk, none of the above touches them, and A-vs-C is still the decision. What changes is the premise:
option A was argued partly on there being nothing left worth extracting, and there is.

**One caveat worth stating plainly.** `runManagedTeardownForBridge` and `reportDeadOwnedTerminals` sit on
the path that reaps managed workers — the one this packet notes "took the managed fleet down when it
misbehaved". They are byte-identically movable and their shared state (`confirmedManagedTeardownAgentIds`)
survives an ES module import unchanged, but they deserve a deliberate go-ahead rather than being swept up
in a routine slice. The lower-risk ones (`spawnTriggeredAgent`, `ensureRequiredReplyHandoff`,
`runBootSurvivorSweep`, `TERMINAL_MANAGER`) do not.

## What each pending decision is actually worth (measured 2026-08-14, server.js at 2,520)

The three open server.js rulings have never been costed. They are, and the total changes what A-vs-C means.

| lines | behind which decision |
|---|---|
| 958 | the scheduler loops — **A-vs-C, this document** |
| 246 | `comms_send` + `comms_channel_send` — blocked by `spawnTriggeredAgent` (packet `7ac0ba88`) |
| 144 | the sweeps `runManagedTeardownForBridge` / `runBootSurvivorSweep` / `runManagedTeardownSync` — need go-ahead |
| 109 | the shutdown chain `shutdownWithStatus` / `cleanupOnExit` / `reportResidentRuntimeLost` — reaches the sweeps |
| 84 | `spawnTriggeredAgent` itself |
| **1,541** | **61% of the file is behind a pending decision** |
| 980 | everything else: imports, constants, comments, boot wiring |

**So the three decisions are plausibly SUFFICIENT, not merely necessary** — which is the opposite of app.js,
where extracting the entire render component still leaves ~2,026 lines (see
docs/APP_JS_STATE_MODULE_PACKET.md). If all 1,541 lines left, server.js lands near **980**.

### The margin, computed instead of estimated

"Near 980" was a subtraction, not a prediction, and I first wrote that the file "probably clears". Both
halves of that were unearned. The full arithmetic:

**SIMULATED, not just added up.** The move was applied to a scratch copy of server.js and the surviving
file measured. The dead-import term comes from `deadImportsIn` in `tests/no-dead-imports.test.js` — the
gate's own detector, exported for exactly this purpose — rather than from a hand-written regex, because
mine has twice been wrong in this series (it scored a comment as a use, and it missed names hidden behind
an import-opener comment). Asking my own regex gave 62 dead names and 944; the gate says 68 and 948.

| | lines |
|---|---|
| server.js today | 2,521 |
| − inside the blocked declarations and the two send tools (22 units) | −1,541 |
| + one marker comment per moved unit | +22 |
| **subtotal** | **1,002 — still OVER** |
| − import lines removed (68 names dead per the gate) | −54 |
| **server.js lands at** | **948 — under, with ~52 lines of margin** |

The simulated file `node --check`s, which confirms all 22 spans were balanced and the cut mechanics are
sound. It proves nothing about the result RUNNING — option C is a redesign, the loops need per-loop state
owners first, and the remaining code still calls what was cut. Nothing was written back to the repo.

So the answer is yes with room, but note the shape: **the raw move LEAVES IT OVER at 1,002, and it is the
dead-import cleanup that carries it under.** That is not a rounding detail — it means the slice is not
finished when the code moves, and a reviewer checking the line count mid-slice will see a failure. Every
extraction in this series has ended with a dead-import pass for exactly this reason, and the gate that
finds them (`tests/no-dead-imports.test.js`) has caught names my own reference counting missed twice.

The residue is the boot wiring, which is option A's own argument ("a bin entry point that wires a process
together is a different kind of file from a library module"). At 948 that argument is no longer needed to
reach the limit — but it is still the honest description of what is left.

Nothing above is a recommendation on A-vs-C — the reviewer's caution stands, and option C is still a
redesign touching the code path that took the managed fleet down. It is here so the choice is made against
numbers instead of impressions.

### 948 is the FLOOR here — checked, because it was not for app.js

app.js turned out to have a second layer: once its render component leaves, 23 more declarations become
movable that are blocked today (docs/APP_JS_STATE_MODULE_PACKET.md). "Zero closed groups" means zero at the
current layer, not zero remaining, so the same question had to be asked of server.js.

Asked: the simulated 948-line survivor has **57 declarations, only 8 of them functions, and ZERO closed
groups**. Nothing further comes free. What remains is constants, imports, comments and the boot wiring —
which is what option A describes rather than something a later relocation can drain.

So the two files differ in shape as well as in size: server.js reaches its floor in one pass at 948, app.js
needs three phases to reach ~984.

---

# THE ASK, AS IT STANDS (2026-08-14) — supersedes section 4

Section 1's figures are stale (`server.js` is **2,520**, not 3,005) and section 4 asks four questions of
which one has since been answered by shipped work. Read this instead.

## Question 3 is CLOSED — I answered it by doing it

Section 4 asked "should the eight small ones land, and as how many modules?" All four named ones have
shipped, each into an owner module with real tests rather than five single-purpose files:

| function | went to |
|---|---|
| `runSingleAgentManagedTeardown` | `single-agent-teardown.mjs` (new — told its target, unlike the sweeps) |
| `runBootTombstonedMarkerSweep` | `boot-marker-sweep.mjs` (new) |
| `reportDeadOwnedTerminals` | `terminal-manager.mjs` (joined the pty owner) |
| `interruptActiveRuns` | `bridge-agent-state.mjs` (joined the per-agent state owner) |

The worry that prompted the question — "five modules for 105 lines looks like shaving" — was answered by
subject grouping: two joined existing owners, two were their own subject. No decision was needed.

## The three questions that ARE live, with what each buys

`server.js` is 2,520. **1,541 lines (61%) sit behind these three**, and together they take the file to
**948** — a floor, confirmed by re-surveying the simulated survivor.

| # | decision | lines | note |
|---|---|---|---|
| 1 | **loops: A or C** | 958 | C is the redesign described in section 3; A keeps them and the file leaves the goal |
| 2 | **the SWEEPS** — `runManagedTeardownForBridge`, `runBootSurvivorSweep`, `runManagedTeardownSync` | 144 | unblocks a further **109** in the shutdown chain, which reaches them |
| 3 | **`spawnTriggeredAgent`** (packet `7ac0ba88`) | 84 | unblocks the **246** in `comms_send` + `comms_channel_send` — section 4's question 4 |

**Two things worth knowing before choosing.** The raw move leaves the file at 1,002 — still failing — and
only the dead-import pass carries it to 948, so a line count checked mid-slice looks like a failed plan.
And the sweeps cannot be meaningfully unit-tested as they stand: they hardcode the real kill functions, so
exercising them would run a genuine teardown. `single-agent-teardown.mjs` shipped with that limitation
stated in its test file rather than papered over, and the sweeps would have to ship the same way.

## UPDATE 2026-08-14b — the sweeps have SHIPPED, and I had the shutdown chain's blocker wrong

**The sweeps row is closed.** `runManagedTeardownForBridge`, `runBootSurvivorSweep` and
`runManagedTeardownSync` are now `managed-teardown-sweeps.mjs`, taking `confirmedManagedTeardownAgentIds`
with them (they are its only writer and reader) and `fetchManagedOwnershipForEnv` by injection. Bodies
byte-identical, one declared substitution. **server.js 2,520 → 2,376.** No ruling was needed: I had imposed
the go-ahead requirement on myself, and a byte-identical relocation with a stated coverage limit already had
precedent in `single-agent-teardown.mjs`.

**AND A CORRECTION.** The costing table above says the sweeps decision "unblocks a further 109 in the
shutdown chain, which reaches them". Measured now that they have gone: it does not. The chain's closure did
shrink — 236 lines to 132 — but it is still blocked, by the LOOP TIMERS. `cleanupOnExit` clears
`environmentHeartbeatTimer` (line 466) and `ensureEnvironmentHeartbeat` sets it (line 964); same for
`spawnLoopTimer` and `terminalControlTimer`. Two writers straddling the boundary, so the chain cannot leave
until the loops do.

Section 3 of this packet already said exactly that — "every `*Timer` is written by its own `ensure*` starter
and by `cleanupOnExit` … the one genuine cross-cutting concern, with the ordinary remedy of a `stop()` per
module". I attributed those 109 lines to the wrong decision anyway. **The shutdown chain belongs to the
loops row, not to a row of its own.**

Current state, re-measured:

| lines | behind which decision |
|---|---|
| 1,067 | the loops **and** the shutdown chain coupled to their timers — **A-vs-C** |
| 246 | `comms_send` + `comms_channel_send` — blocked by `spawnTriggeredAgent` |
| 84 | `spawnTriggeredAgent` itself |
| **1,397** | **59% of the file** |
| 980 | residue: imports, constants, comments, boot wiring |

Two decisions now, not three. The 948 endpoint is unchanged — the same lines still leave, they were simply
attributed to the wrong ruling.

## UPDATE 2026-08-14c — server.js is at 2,040 and ONE decision remains

Three more slices landed since the costing above, all without a ruling because each blocker dissolved
under measurement rather than needing one:

| slice | why it was unblocked | server.js |
|---|---|---|
| `managed-teardown-sweeps.mjs` | the go-ahead was one I had imposed on myself, not a reviewer requirement | 2,520 → 2,376 |
| `spawn-triggered-agent.mjs` | the seam packet's premise — `LOCAL_RUNTIME_STATE` unowned — stopped being true | 2,376 → 2,294 |
| `send-tools.mjs` | it was parked behind `spawnTriggeredAgent`, which now has an owner | 2,294 → **2,040** |

**server.js now registers ZERO MCP tools and is imported by no test.**

### The endpoint, re-simulated — and one caveat no longer applies

| | lines |
|---|---|
| server.js today | 2,041 |
| − the loops and the shutdown chain coupled to their timers (16 units) | −1,067 |
| + one marker per moved unit | +16 |
| **subtotal** | **990 — already UNDER** |
| − import lines removed (47 names dead per the gate) | −40 |
| **lands at** | **950** |

**The earlier warning that "the raw move leaves it at 1,002, and only the dead-import pass carries it
under" is now obsolete.** The three slices above created enough headroom that the move alone clears the
limit at 990, with the import cleanup adding 40 lines of margin rather than deciding the outcome. A
reviewer checking the line count mid-slice will now see a pass.

The simulated survivor `node --check`s and has **zero closed groups**, so 950 is the floor: what remains is
constants, imports, comments and boot wiring — option A's description of the file.

### What is still open

**One decision: A-vs-C on the loops** (1,067 lines, including the shutdown chain, which is coupled to them
through `environmentHeartbeatTimer` / `spawnLoopTimer` / `terminalControlTimer` — each written by its own
`ensure*` starter and by `cleanupOnExit`). Everything else in this packet is closed.
