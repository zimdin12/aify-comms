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

## The decision

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
