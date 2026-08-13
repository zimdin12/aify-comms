# app.js — the measured ceiling, and the one decision that lifts it

**Status:** decision packet. Nothing here is implemented. `service/new_dashboard/app.js` is 4,903 lines
and is one of three files still over the 1,000-line limit after v0.5.4 cleared the other nine.

**Measured 2026-08-13 against the current file**, not carried forward from an earlier note. An earlier
note in project memory said app.js had "246 functions, ZERO exports" and was at a relocation ceiling;
both halves were stale — it has 164 top-level functions and 20 import lines pulling in already-extracted
`.mjs` cores. Re-measure before trusting any number below.

## Why relocation stops at ~4,700

| functions | count | lines |
|---|---|---|
| WRITE `state` (assign, push, splice, set, delete) | 26 | 1,094 |
| READ `state` only | 70 | 1,557 |
| touch `state` at all | **96** | **2,651** |
| touch neither `state` nor the DOM | 23 | 206 |

`state` is a single module-scope object literal declared at lines 65–108 with 30 top-level keys
(`agents`, `runs`, `sessions`, `environments`, `chat`, `settings`, `terminalOwners`, `activeXterm`, …).

A module extracted from app.js cannot import `state` back, because app.js is where `state` lives —
that is the upward import the series forbids everywhere else, and here it would also be a cycle. So the
only functions that can move today are the 23 that touch neither `state` nor the DOM: **206 lines,
averaging 9 lines each**. Extracting all 23 leaves app.js at roughly **4,700**.

That is the ceiling. It is not a judgement about effort; it is what the dependency graph permits.

## CORRECTION 2026-08-14 — the claim above is WRONG, and this is what a prototype showed

**Everything from here down was written from a static count of which functions touch `state`. I then
built the move in a scratch copy and measured it. Moving `state` does NOT unblock 2,651 lines.**

Prototype: lift the 44-line `state` declaration into `state.mjs`, import it back, re-measure.

| after the move | functions | lines |
|---|---|---|
| free to move | 28 | **202** |
| still blocked by the DOM | 14 | 125 |
| **still blocked by calls to sibling app.js functions** | **122** | **3,103** |

Before the move, 23 functions / 206 lines were free. After it, 28 / 202. **`state` is not the binding
constraint — the internal call graph is.** 122 of 164 functions call another function defined in
app.js, and no static count of `state` usage could have revealed that, because I never asked what
*else* blocked them.

### What the call graph actually looks like

21 connected components, and one of them is almost the whole file:

* **141 functions, 3,085 lines in a single component** — `_refreshImpl`, `renderAll`, `renderSettings`,
  `agentForSession` and everything they reach, transitively.
* 20 small components totalling 345 lines (2–3 functions each, e.g. `activityItems` +
  `renderActivityFeed`, `pastedImageName` + `uploadPastedImage`).

### SECOND CORRECTION, same day — the numbers above describe the PROTOTYPE, not the current file

The component sizes in this section were measured on the scratch copy with `state` already removed. I
then quoted them as if they applied to app.js as it stands, which is the exact conflation this
correction was written to name. Measured again on the REAL file, under ONE criterion — touches no
`state`, no DOM, and calls no app.js module-level name:

* **11 functions, 67 lines** are movable today, individually.
* **1 self-contained component** (`pastedImageName` + `uploadPastedImage`, 22 lines).

Not ~345, and not the ~206 in the original table either — that earlier figure allowed calls to
app.js-scope helpers like `byId`, which are themselves blockers. Three different numbers for "what can
move" appeared in this document because three different criteria were used and none was stated. This
one is stated.

So the honest options are:

1. **Move the 11 free functions and the one component** — about 89 lines, taking app.js from 4,903 to
   roughly **4,814**. Real, unblocked, and nowhere near the limit. They also share no subject, so they
   would land in a grab-bag module, which every other slice in this series refused to create.
2. **Break the 141-function component** — a redesign of the dashboard's render flow, not a relocation,
   and not a v0.5.x-shaped task.
3. **Leave app.js on the allowlist** with this measurement attached.

Option 1 is available without any decision from the operator. It is not recommended on its own: it
buys 1.8% of the file at the cost of a module with no coherent subject.

Moving `state` is still *worth doing on its own merits* — a shared mutable object with 26 writers
deserves an owner and an identity gate — but it should be proposed as that, not as the thing that
unblocks the file.

### Why I got it wrong, recorded because the mistake is reusable

I measured the constraint I was looking for and stopped. "96 functions touch `state`" is true; "those
96 are blocked BY `state`" does not follow, and one prototype would have shown it — as it did, once I
built one instead of arguing from a count. The same series has three other entries in this class:
stripped-text offsets read as line numbers, a spread-blind regex, and a reachability regex that ran
past the end of a statement. A measurement that confirms the hypothesis you started with deserves the
same suspicion as one that refutes it.

## The decision (as originally written — read the correction above first)

**Move `state` to its own module** (`service/new_dashboard/state.mjs`), exporting the object. app.js
and every extracted module then import it, and the 96 state-touching functions — 2,651 lines — become
relocatable by subject in the normal way.

This is the "factory conversion" the oversized allowlist entry already names. It is a **design change,
not a relocation**, which is why it is a packet rather than a slice.

### What makes it safe to attempt

* `state` is a plain object, not a closure variable. Per the standing design rule, module-scope state
  can have an owner; closure-captured state cannot. This one qualifies.
* The identity discipline already exists on the Python side and has caught a real fork this series
  (`_listen_events`, where two copies would have made `comms_listen` hang silently). The JS analogue is
  the same: exactly one module may declare `state`, and every reader must get that object by identity.
* `service/new_dashboard/extraction-proof.mjs` already provides reconstruction equivalence — put the
  extracted spans back, delete the added import, require byte-identity with the pristine fixture. It
  has proven three app.js slices and takes a new entry per slice.

### What makes it risky

* **`state` is mutated from 26 functions.** A second copy would not raise; the dashboard would render
  from one object while events updated another, and the symptom would be stale panels rather than an
  error. The `_listen_events` incident is the precedent for how invisible that is.
* **app.js is loaded as `<script type="module">` in the browser.** Import order and cycles behave
  differently from Node; the existing `.mjs` cores are pure, so none of them has exercised a shared
  mutable module yet.
* **The dashboard has no DOM-level test.** `app.js` is reachable only by source-regex tests, which
  cannot fail on wrong logic — the repo's own note says so. So the safety net for a `state` move is the
  reconstruction proof plus manual verification, not a behavioural suite.

### Suggested order, if approved

1. `state.mjs` exporting the object; app.js imports it. **No other change.** Prove with reconstruction
   equivalence + a new gate asserting exactly one module declares `state` and readers share it by
   identity (the JS analogue of `test_process_global_identity.py`).
2. Verify the dashboard by hand — read-only, per the standing rule that live-UI testing never fires
   controls.
3. Only then begin moving state-touching functions by subject, one slice each, as the Python side did.

## The alternative

Leave app.js on the allowlist with this measurement attached. The entry currently reads
"reviewer-ruled relocation ceiling; remaining reduction needs factory conversion" — accurate, and now
quantified: 206 movable lines against 2,651 blocked.

## The other two files, for completeness

* **`mcp/stdio/server.js` — 3,005.** Packet accepted as measurement
  (`docs/JS_SERVER_REMAINDER_PACKET.md`); awaiting operator scope.
* **`mcp/stdio/hermes-managed-host.js` — 1,845.** Measured 2026-08-13: 8 top-level functions covering
  1,092 lines, 754 lines outside any of them, and **959 comment lines — 52% of the file**.
  `runDeliveryLoop` alone is 619 lines and its 7 seams are arrow-function consts declared INSIDE it,
  capturing its locals. The five extractable helpers total ~130 lines, reaching ~1,715. Clearing it
  needs `runDeliveryLoop` split, which is a redesign of the delivery loop.
