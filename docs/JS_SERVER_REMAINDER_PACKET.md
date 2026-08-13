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

**The bulk is one interlocked family.** `runTerminalControlLoop`, `runSpawnLoop`,
`syncManagedEnvironmentAgents` and `runEnvironmentControlLoop` close over **44 functions / 1,725 lines / 27
mutable module names** — every poll timer, every `*Busy` re-entrancy flag, the claim-failure counters, the
teardown-confirmation set, the virtual-terminal map. They are not four functions that sit near each other;
they are one scheduler over one pile of shared mutable state. `runDispatchLoop` (449L) is the same shape and
you parked it for that reason.

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

## 4. Asking

1. Is **A** acceptable — `server.js` leaves the 1000-line goal as a wiring file, with the loop family
   documented as a deliberate exception?
2. If not, is **C** the shape you want, and does it belong in v0.5.x or v0.6?
3. Independently: should the eight small ones land, and as how many modules? They are unblocked and use the
   proven method; I held them because five modules for 105 lines looked like shaving rather than structuring.
4. Are `comms_send` / `comms_channel_send` still parked? At 246 lines they are the largest unblocked win if
   not.

## 5. What I have NOT established

- Whether the four loops can be separated **without** a shared scheduler object. I measured the closure and
  the mutable set, not the ordering constraints: several read `*Busy` flags another loop sets, and I have not
  traced whether that is real coordination or incidental.
- Whether any of the 27 mutable names is read from outside the family. If some are, they want owners first —
  the same sequencing that unblocked `comms_register`.
