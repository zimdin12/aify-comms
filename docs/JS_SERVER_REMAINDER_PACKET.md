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
