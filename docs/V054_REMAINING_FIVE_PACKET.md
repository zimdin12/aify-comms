# The five files still over 1000 lines — all blocked on the same kind of decision

**Status:** measured at HEAD, end of the v0.5.4 relocation series. Seven of the twelve goal files are done.
The remaining five are not stalled for lack of work; each is stalled on one decision, and it is the same
decision in a different costume: **a shared mutable store, or a class, that everything else reads.**

Relocation cannot cross that boundary. Every slice in this series worked because the thing being moved had
an owner it could belong to and no state it had to carry. What is left has state at the centre.

## The five, measured

| file | lines | what the bulk is | ceiling by relocation |
|---|---|---|---|
| `app.js` | 4,935 | one module-scope `state` object, **509 references**, touched by 97 of 169 functions | ~4,570 |
| `control_plane.py` | 3,088 | `_compute_live_status_cache`, a 432-line hub nearly every closure reaches | ~3,030 |
| `server.js` | 3,005 | `runDispatchLoop` 449L — but the loops' 27 mutable names are PRIVATE, see below | tractable |
| `hermes-managed-host.js` | 1,845 | `runDeliveryLoop` 619L; **754 lines outside any declaration** | — |
| `pi-session.js` | 1,110 | the `PiSession` class, 960L | ~1,017 |

`pi-session.js` is the clearest case: remove EVERY remaining non-class function and it still sits at ~1,017.
The class alone is 960.

## What is genuinely still extractable, and why I stopped

- **`app.js`: 13 functions, 63 lines** — and even that is generous. This is the TRANSITIVE figure: no
  `state`, no browser global, no `byId`/`api`, AND every app.js function it calls also clean. Spot-checking
  those thirteen finds some reading OTHER module-scope objects (`evaluateFlowGates` reads `flowGates`), so
  the honest number is under sixty lines. The proven harness exists (`extraction-proof.mjs` — put the spans
  back, delete the added import, require byte-identity with the pristine fixture) and six slices have used
  it; there is essentially nothing left for it to move.

  **THIS FIGURE HAS BEEN CORRECTED TWICE, AND BOTH ERRORS RAN THE SAME WAY — overstating what is
  extractable.** The 39/361 count included functions that do not touch `state` DIRECTLY but call `api`,
  `uiConfirm` or `openRunInspector`; "not directly stateful" is not "movable", and movability is the
  question this packet exists to answer. The first version said 370 references / 94 of 175
  functions / 57 pure / ~551 lines. Those came from a scanner carrying the SPREAD BUG this series had
  already found and fixed once: `(?<![\w.])state` misses `...state.x`, because the final `.` of the
  spread satisfies the lookbehind. Every function that spreads state was counted as pure. The corrected
  figures make `app.js` MORE state-coupled than reported, not less — the conclusion below is unchanged and
  the evidence for it is stronger.
- **`server.js`: 6 functions, 51 lines** — corrected, and for the same reason as `app.js` below. The earlier
  "8 functions / ~105 lines" counted module state as `let` bindings plus `Map`/`Set` literals only, so
  `const TERMINAL_MANAGER = new TerminalProcessManager({...})` — a live PTY-owning object with twelve
  readers — was invisible, and its callers looked stateless. `runSingleAgentManagedTeardown` and
  `reportDeadOwnedTerminals`, the two largest of the eight, are gated on it. What remains is one 28-line
  boot sweep and five three-liners across unrelated subjects.
- **`pi-session.js`: ~30 lines** of timeout helpers. The session pool below them is blocked by a real
  circular import.
- **`control_plane.py`: three singletons, 55 lines.** This was the one blocker I had NOT verified myself —
  the note said "the status-cache component, operator-scope, previously ruled", and it was right, but I had
  been treating the whole file as blocked on the strength of it. Measured: 28 top-level functions / 1,538
  lines, with nearly every seed's transitive closure running through `_compute_live_status_cache` (432
  lines, the hub) to produce closures of 578-1,161 lines. Outside it sat a 90-line serializer group, which
  this round extracted into `api_core/records.py` — taking the file 3,181 -> 3,088 and retiring a documented
  borrow shim. What is left outside the hub is 55 lines.

## The server.js loops are NOT interlocked — correcting this packet's own claim

I described those four loops as "one scheduler over one pile of shared state", from their closure touching
27 mutable module names. Measured per name — who WRITES it, who READS it — that is wrong, and unlike the
other corrections in this document, this one runs in the file's FAVOUR:

- **Every `*Busy` re-entrancy flag is written and read by exactly one function.** `dispatchLoopBusy` only by
  `runDispatchLoop`, `spawnLoopBusy` only by `runSpawnLoop`, `terminalControlBusy` only by
  `runTerminalControlLoop`, `managedEnvironmentSyncBusy` only by `syncManagedEnvironmentAgents`,
  `environmentControlBusy` only by `runEnvironmentControlLoop`. Private guards, not coordination.
- **Every `*Timer` is written by its own `ensure*` starter and by `cleanupOnExit`**, which clears them on
  shutdown. That is the single cross-cutting concern, and its shape is ordinary: each loop exports `stop()`,
  shutdown calls each.
- **Only five names in the entire file are written by one function and read by another**, each a tight pair:
  teardown to teardown-sync, bootstrap to heartbeat, heartbeat to payload, shutdown to main, and the two
  spawn-claim counters.

So option C is materially cheaper for `server.js` than stated above. `runDispatchLoop` stays parked on its
own merits — 449 lines on the live claim path — not on a coupling that does not exist.

## What is inside the status-cache hub, since option C turns on it

`_compute_live_status_cache` is 432 lines, async, with **21 awaits and ZERO loops**. It is not an algorithm —
it is a straight-line FACT-GATHERER. The decision it feeds was already extracted: line 1443 is
`effective_status, reason, awaiting_reply = await _decide_effective_status(...)`, living in
`api_core/status_decision.py`. What is left is assembling that call's inputs.

**It has exactly one clean seam, and it is early.** Cutting after L1249 — about 70 lines in, between its two
row-fetching `try` blocks — leaves 8 locals crossing. Every later cut point crosses 14 or more, and the
density only rises:

| point | reads earlier locals |
|---|---|
| `_decide_effective_status(...)` call, L1443 | **22** |
| the managed-session block, L1571 | 7, reaching 64 statements back |
| the final `return {...}`, L1601 | **10**, reaching 65 statements back |

Sixty-seven top-level statements assign 70 local names, and statements routinely reach 40-65 statements back
for them.

**So splitting the hub is expensive, not cheap.** Extracting the first ~70 lines as a "fetch the rows" step
is real and small. Beyond that, any split means passing a twenty-plus-field context object between the
pieces — which is option B's coupling wearing option C's clothes, and worth doing only if the goal is
testability rather than line count. That distinction is the ruling.

## The decision, stated once

For each file the choice is the same shape:

**A. Accept it as out of scope for the 1000-line goal**, on the grounds that a file whose bulk is one
coherent stateful thing — a store, a scheduler, a session class — is not the defect the goal was aimed at.
The goal caught eleven files where unrelated logic had accumulated; these five are not that.

**B. Give the state an owner and inject it.** Mechanical and provable, but it converts module-scope reads
into property reads on a passed object: the same coupling with more ceremony. For `app.js` that is 370 call
sites.

**C. Split the stateful thing itself** — one owner per loop with its own state, a `PiSession` split, a
dashboard store with a real interface. This is the only option that makes any of it independently
testable, and none of these loops or classes has a unit test today. It is a redesign: not byte-identical,
needing its own review standard, and touching the paths that reap workers and claim runs.

## What I am asking

1. **A, B or C — per file, or one ruling for all five?**
2. If **C** anywhere: v0.5.x or v0.6?
3. Should the genuinely clean remainder land regardless? Measured with the widened state definition it is
   **app.js ~46 + server.js 51 + pi ~30 = under 130 lines**, not the ~700 this packet first claimed — and
   most of the app.js remainder has no existing owner, so landing it would mean the single-purpose modules
   this same packet argues against. At that size the
   argument for doing it is test coverage — each becomes callable by a test that cannot reach it today —
   and the argument against is five more single-purpose modules. Your call.

## A pattern in my own measurements, stated because it affects how much to trust the rest

Every figure in this packet has been corrected downward, three times, and each time the cause was a scanner
using a NARROWER definition than the code does:

- `state` references missed `...state.x`, because a spread's final `.` satisfies a `(?<![\w.])` lookbehind;
- "clean" counted functions that avoid `state` DIRECTLY while calling `api`/`uiConfirm`/`openRunInspector`;
- "zero mutable" counted `let` and `Map`/`Set` literals, but not `const X = new SomeClass()` or object
  literals — which is how a PTY manager with twelve readers went unseen.

Each correction made the files look LESS reachable by relocation, so the conclusion has only hardened. But
the numbers here were wrong three times in the same direction, and the ones that remain uncorrected are the
ones nothing has forced me to re-measure yet.

## What I have NOT established

- Whether `app.js`'s `state` can be split by page/domain rather than owned whole. 509 references is a count,
  not a shape; I have not measured how many are confined to one screen's functions.
- Whether `server.js`'s four loops can be separated without a shared scheduler object — several read `*Busy`
  flags another loop sets, and I have not traced whether that is coordination or coincidence.
- ~~Whether `_compute_live_status_cache` can be split at all.~~ **MEASURED — see below.** What remains
  unestablished is whether the four `server.js` loops coordinate through their `*Busy` flags or merely read
  each other's, and whether `app.js`'s `state` splits by screen (509 references is a count, not a shape).
