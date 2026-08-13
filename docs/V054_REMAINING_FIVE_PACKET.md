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
| `control_plane.py` | 3,180 | the status-cache component (operator-scope, previously ruled) | — |
| `server.js` | 3,005 | four poll loops over 27 mutable module names; `runDispatchLoop` 449L | ~2,900 |
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
- **`server.js`: 8 zero-mutable functions, ~105 lines** across five unrelated subjects. Five single-purpose
  modules to shave 105 lines is grouping by line count, not by subject.
- **`pi-session.js`: ~30 lines** of timeout helpers. The session pool below them is blocked by a real
  circular import.

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
3. Should the genuinely clean remainder land regardless? Measured transitively it is **app.js <60 +
   server.js ~105 + pi ~30 = under 200 lines**, not the ~700 this packet first claimed. At that size the
   argument for doing it is test coverage — each becomes callable by a test that cannot reach it today —
   and the argument against is five more single-purpose modules. Your call.

## What I have NOT established

- Whether `app.js`'s `state` can be split by page/domain rather than owned whole. 509 references is a count,
  not a shape; I have not measured how many are confined to one screen's functions.
- Whether `server.js`'s four loops can be separated without a shared scheduler object — several read `*Busy`
  flags another loop sets, and I have not traced whether that is coordination or coincidence.
- Anything about `control_plane.py`'s status-cache component beyond the standing operator-scope ruling.
