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

## THIRD CORRECTION — the checker itself was wrong, and all three files are now measured the same way

Four figures for "what can move out of app.js" appeared in this document — 206, 345, 89, 116 — each
from a different criterion, none of them stated. The last was produced by a checker that stripped
STRING LITERALS BEFORE COMMENTS, so an apostrophe in prose ("can't have mismatched", "agent's
messages") opened a phantom string that ran to the next real quote and swallowed the code between.
That is why `renderAnalyticsPage` was reported as free when its next line calls `byId` and reads
`state.analytics.data`. Caught by reading three of the ten results by eye before publishing; all three
were obviously wrong.

`scratchpad/js_free_fns.py` now strips comments first and states its criterion at the top:

> a function is FREE if, after stripping comments then string literals, no identifier in its body is a
> module-level name declared in the same file, and it touches none of `state`, `document`, `window`,
> `localStorage`, `fetch`.

Deliberately OVER-inclusive: it can only refuse a function that was in fact movable, never approve one
that is not.

### All three remaining files, one criterion, 2026-08-14

| file | lines | free functions | free lines | reaches |
|---|---|---|---|---|
| `service/new_dashboard/app.js` | 4,904 | 6 | 28 | ~4,876 |
| `mcp/stdio/server.js` | 3,006 | 7 | 88 | ~2,918 |
| `mcp/stdio/hermes-managed-host.js` | 1,846 | 3 | 67 | ~1,779 |

**None of the three is meaningfully reducible by relocation.** All need a redesign of the structure
that holds them together: app.js's 141-function render component, server.js's module-scope bridge
state, hermes's 619-line `runDeliveryLoop`.

**RETRACTION.** On the strength of the wrong 532-line figure I suggested the reviewer's ordering
(app.js first, server.js last) deserved revisiting because server.js looked far more tractable. It is
not — 88 lines. The ordering ruling stands, and its stated reason was never tractability anyway: it is
that server.js is the live MCP bridge every agent connects through.

## The other two files, for completeness

* **`mcp/stdio/server.js` — 3,005.** Packet accepted as measurement
  (`docs/JS_SERVER_REMAINDER_PACKET.md`); awaiting operator scope.
* **`mcp/stdio/hermes-managed-host.js` — 1,845.** Measured 2026-08-13: 8 top-level functions covering
  1,092 lines, 754 lines outside any of them, and **959 comment lines — 52% of the file**.
  `runDeliveryLoop` alone is 619 lines and its 7 seams are arrow-function consts declared INSIDE it,
  capturing its locals. The five extractable helpers total ~130 lines, reaching ~1,715. Clearing it
  needs `runDeliveryLoop` split, which is a redesign of the delivery loop.

---

## FOURTH CORRECTION, 2026-08-14 — the ceiling was an artefact of the criterion, and `state` has moved

**Everything above concludes app.js is "not meaningfully reducible by relocation". That conclusion is
withdrawn.** It was measured per function, and per-function measurement counts a call between two
functions that would move together in the same slice as a blocker. Under that rule any cohesive cluster
reads as welded in place, and the more cohesive it is, the more immovable it looks.

This is the third time in this series the same error has produced the same wrong verdict. It said
`hermes-managed-host.js` needed a redesign of its 619-line `runDeliveryLoop` before it could be split;
measured as a group, that file went 1,846 → 728 in two slices on 2026-08-14 with no redesign at all. The
packet even names the distinction — "a function that is not free ALONE may still be free WITH the ones it
calls" is written in `scripts/js_free_functions.py` — and then does not apply it here.

**A second error compounded it: browser globals were counted as blockers.** The checker treats `document`,
`window`, `localStorage` and `fetch` as dependencies because it was written to find PURE, unit-testable
functions. For a relocation that is the wrong question — app.js is loaded as `<script type="module">` and
an extracted `.mjs` runs in the same browser with the same globals. A DOM-touching function relocates
fine; it is merely not pure. Conflating "impure" with "immovable" removed most of the file from
consideration before the call graph was even consulted.

### What the group measurement actually shows

Closure of each function over its callees, blockers counted as app.js module-scope names only, split into
names the group would take WITH it (read by nothing else) and names it shares with the code left behind:

| seed | functions | lines | shared blockers |
|---|---|---|---|
| `renderAll` | 54 | 1,484 | `apiBase`, `byId`, `chatController`, `state` |
| `setPage` | 25 | 931 | `apiBase`, `byId`, `runFrom`, `state` |
| `mountChatConsole` | 15 | 769 | `apiBase`, `byId`, `state` |
| `mountXtermForTerminal` | 10 | 476 | `apiBase`, `state` |
| `loadContractsForState` | 10 | 150 | `apiBase`, `byId`, `state` |
| `renderContracts` | 8 | 125 | `byId`, `state` |
| `codexConsoleConnect` | 4 | 104 | `codexConsoleConnections` |

The binding constraint was never a 141-function knot. It is a handful of shared leaf names, and `state`
is in almost every row.

### Done: `state` now has an owner

`service/new_dashboard/state.mjs`, exporting the 44-line declaration byte-identically. Proven by the
existing reconstruction harness (a new `EXTRACTIONS` entry) and by `state-identity.test.mjs`, which is the
JS analogue of the Python `test_process_global_identity.py`: exactly one module may declare `state`, app.js
must import rather than declare it, and every importer must get the same object with mutations visible
across imports. That last part is the property the 26 mutating functions depend on, and its failure mode is
silent — two objects, no error, panels that never update.

Safe because `state` is a `const`: never reassigned, only mutated. An ESM export is a live binding to one
object.

### What is now unblocked, and in what order

`byId` (a one-line `document.getElementById` wrapper) and `apiBase` (a template string over `apiOrigin`)
are the two remaining shared leaves. Both want neutral owners — `byId` belongs with the other DOM helpers
in `ui.js`; `apiOrigin`/`apiBase`/`resolveApiOrigin` form their own small subject. Note that
`resolveApiOrigin()` runs at module load and reads `location`, `localStorage` and `document`, so its module
cannot be imported in Node without those globals being installed first; its test must set them before a
dynamic import. That is worth doing rather than routing around — the function has four branches and no
tests today.

After those, the subject slices in the table above are ordinary relocations.

### The reusable lesson

The earlier correction in this document already says "I measured the constraint I was looking for and
stopped". This is the same mistake one level up: having found that `state` was not the *only* blocker, I
concluded the file was blocked, without asking whether the *other* blockers were real or artefacts of how
I was counting. **Before accepting any "not reducible" verdict, state the criterion and check it against a
case you have already disproven.** This series has one on file.

## FIFTH CORRECTION, 2026-08-14 — a slice was written, proven, and REVERTED; the closure tool was wrong

The fourth correction above is right that group measurement unblocks app.js, and two enablers plus the
first subject slice have shipped on the strength of it. But the tool it was measured with had a defect
worth recording, because the failure it produced passed every gate this repo has.

**The closure walk expanded FUNCTIONS only.** A `const` the group would take with it was reported as
"owned" without its initializer ever being read. So `renderRunInspector` measured as a 192-line group
needing nothing from app.js, and `flowGates` came along as owned — while `flowGates`' entries call
`flowAssertions`, which probes half the file (`connectRealtimeSocket`, `renderSessionWorkspace`,
`createSpawnRequest`, …). The extracted module referenced a name that did not exist in it.

It was written, the reconstruction proof passed byte-identically, `node --check` passed, and it was
reverted only because the module was read by eye afterwards. **`node --check` cannot catch this**: an
unresolved identifier in an ES module is a runtime ReferenceError, not a parse error.

Worse, the tempting repair — move `flowAssertions` too — would have failed *silently*. Its probes are
`typeof someName === 'function'`, and `typeof` on an undeclared name does not throw; it returns
`"undefined"`. Every flow gate would have quietly evaluated false. No test in the repo asserts on them.

Measured correctly, `renderRunInspector` reaches **138 declarations / 2,532 lines**.

### What replaced the tool

Four hand-rolled parsers gave four different answers before this was settled — brace counting over raw text
(HTML template literals moved the depth counter, 121 of 164 functions found), masking strings before
comments (an apostrophe in prose opened a phantom string, down to 19), and adding regex-literal handling
(nested templates inside `${…}` closed the mask early, 58 still lost). The survey now imports
`declarationSpan` from `extraction-proof.mjs` — the same function the proof itself depends on — and
**gates itself**, asserting the span table covers every top-level function before printing any number.

Two rules came out of it, both now in project memory:

* Scan identifiers **without** stripping string literals. Over-inclusive can only refuse a movable group;
  under-inclusive approves an immovable one, which is what happened here.
* After extracting, compute which of app.js's **imported** names the new module now references. The
  subject survey answers only what a group needs from app.js's *declarations*, so a module can read
  "needs NOTHING" and still be full of unresolved sibling imports. This is the JS analogue of the Python
  symtable undefined-name sweep, and it is now run on every slice before the suites.

### Where `apiBase` went

The third and last leaf is not a script but a ruling: see **`docs/APP_JS_APIBASE_PACKET.md`**.

---

## SUPERSEDED 2026-08-14 — the ceiling it measures no longer exists

This packet opens "app.js is 4,903 lines" and argues **"Why relocation stops at ~4,700"**. Both are
overtaken: app.js is **3,612**, reached entirely by relocation, with no render-flow redesign and no
ruling. Its per-file measurements are kept below as history — they were honest when taken — but do not
plan from them.

What actually moved the number, after this packet was written:

* **Measure GROUPS, not functions.** A call between two declarations that travel in the same slice is not
  a blocker. This packet's own fourth correction says so; the numbers above it predate that.
* **`docs/APP_JS_APIBASE_PACKET.md` is superseded too.** Its options A/B/C assumed unblocking needed a
  harness change. It did not: the reconstruction plan's `marker` already accepts multiple lines, each
  verified verbatim before removal (exactly how `importLine` is treated, and that is executable code), and
  `export let` is a live ESM binding, so a moved body keeps a byte-identical `apiBase` identifier.
* **An in-degree survey finds the names worth attacking.** `scratchpad/hubs.mjs`, built on the
  reconstruction proof's own `declarationSpan` and gated on covering every top-level function. `api` was
  one name with 74 call sites; extracting it turned three refused groups into clean ones.

**The remaining wall IS real and is now measured rather than asserted.** Zero closed groups; six hubs
(`closeInspector`, `setPage`, `renderContracts`, `openRunInspector`, `resyncActiveConsole`,
`renderSessionConsole`) each pull in ~75 declarations / ~1,586 lines because all of them reach `refresh`.
That is one mutually-recursive render orchestrator, and `chatController` is its dependency-injection
wiring site rather than movable logic. Breaking it is the render-flow redesign — out of v0.5.x scope.

### The number the redesign decision actually turns on (measured 2026-08-14)

Relocation cannot clear this file even in principle, and it is worth stating as arithmetic rather than as
a judgement. `scratchpad/shape.mjs` (same `declarationSpan`, same coverage gate):

| | app.js 3,612 | server.js 2,520 |
|---|---|---|
| inside declarations | 2,244 (62%) | 1,460 (58%) |
| **code OUTSIDE any declaration** | **720 (20%)** | **436 (17%)** |
| comments outside | 409 (11%) | 370 (15%) |
| imports / blank | 240 | 255 |

The 720 lines are the delegated event handler and the init sequence — top-level statements, not
declarations, so nothing in this series' machinery can move them: there is no span to relocate and no
name to import back. Extracting the ENTIRE `refresh` component (~1,586 lines) would leave app.js at
roughly **2,026** — still twice the limit.

So clearing app.js needs two decisions, not one: the render-flow redesign for the component, AND a shape
for the top-level init/handler block (most likely an exported `init(...)`/`installHandlers(...)` a thin
entry point calls). Neither is a relocation, both are out of v0.5.x as it is currently scoped. The same
shape applies to server.js's 436 lines, which is its process wiring — and is the substance of option A in
docs/JS_SERVER_REMAINDER_PACKET.md ("a bin entry point that wires a process together is a different kind
of file from a library module").

### CORRECTION: it is ONE decision, not two (attempted and measured 2026-08-14)

I previously reported that clearing app.js needs two decisions — the render-flow redesign for the
component, AND a separate shape for the ~720 lines of top-level init/handler code. **That was wrong, and I
found out by trying it rather than by reasoning further.**

Those 720 lines are not loose init code. **413 of them are the body of ONE inline registration**,
`document.addEventListener('click', (event) => { … })` — the delegated router every click in the dashboard
passes through, and the single largest unit in the file. The rest are much smaller: keydown 22, toggle 4,
and 43 short runs.

I extracted it. The body needed NO substitution at all — the arrow's body sits at indent 2, and
`export function onDocumentClick(event) {` puts it at indent 2, so not one line changed. app.js went
3,612 → 3,202. Then I measured what the body actually references:

> **44 names that app.js itself declares** — `refresh`, `refreshSoon`, `closeInspector`,
> `openRunInspector`, `renderContracts`, `chatController`, `loadAnalytics`, `markConversationRead`, … —
> an upward import, and a cycle.

So the click router is not adjacent to the render component; it IS part of it. Every branch dispatches into
the orchestrator. The same holds one level down: the 22-line keydown handler needs `closeInspector`, whose
own closure reaches `refresh`.

**Consequence for the decision:** there is nothing to rule on separately. Breaking the render component
brings the handlers with it, and until that happens no part of the top-level block can leave. The earlier
framing invited a ruling on a question that does not exist.

Reverted in full — app.js byte-identical, module deleted. A typed `rewrap` mechanism I added to the
reconstruction proof for this (framing lines declared, body still from the module, result still compared to
the fixture — strictly narrower than the free-form `(before, after)` pairs the apiBase packet proposed) was
also reverted: with nothing using it, shipping it would be speculative machinery, and the rule in this repo
is that a mechanism names the artifact it retires.

### Approving the redesign is NECESSARY BUT NOT SUFFICIENT (simulated 2026-08-14)

The "~2,026 remains" figure earlier in this packet was a subtraction. Subtractions have been wrong twice
in this series, so the extraction was applied to a scratch copy and the survivor measured, with the
dead-import term taken from `deadImportsIn` in `mcp/stdio/tests/no-dead-imports.test.js`:

| | lines |
|---|---|
| app.js today | 3,613 |
| − the whole `refresh` component (74 declarations) | −1,578 |
| + one marker per moved declaration | +74 |
| − dead import lines | **−0** |
| **remains** | **2,109 — 2.1× the limit** |

Two things to take from it, both the opposite of server.js.

**The subtraction UNDER-reported by 83 lines.** 74 markers is not a rounding error at this scale.

**ZERO imports go dead, where server.js shed 54 lines across 31 whole blocks.** The reason is the 720-line
top-level block that stays: it uses those imports. And it cannot simply move with the component — it is
not declarations, so there is no span to relocate.

So the earlier framing of the choice — "approve the render-component redesign, or accept app.js stays over
1000" — was wrong in the same way the two-decisions framing was. **Approving the redesign does not clear
this file.** Extracting the largest coherent unit in it still leaves twice the limit. Clearing app.js means
splitting the dashboard entry point across several files, which is a different and much larger piece of
work than "break the render component", and it is not a v0.5.x-shaped task.

The honest options are therefore: accept that app.js stays over the limit for 0.5.x with this measurement
attached to its allowlist entry, or scope an entry-point split as its own project.

### The path DOES exist — three phases, measured, landing at ~984

"Not a v0.5.x-shaped task" was accurate but unhelpfully vague, so here it is costed. Each figure below is
from applying the previous phase to a scratch copy and re-surveying it, not from arithmetic on this page.

| phase | what | app.js after |
|---|---|---|
| — | today | 3,613 |
| **1** | the `refresh` component — 74 declarations, 1,578 lines, **+74 markers** | **2,109** |
| **2** | the action handlers the component was blocking — 23 declarations, 433 lines, +23 markers | **1,699** |
| **3** | the 720-line top-level init/handler block becomes `init()` / `installHandlers()` calls | **~984** |

**Phase 2 is the interesting one: it is ordinary relocation, and it does not exist until phase 1 lands.**
Sixteen groups (`handleRunInspectorControl` 107 lines, `submitContinue` 69, `openRunConsole` 54,
`requestBulkDiagnosticAction` 53, `openMessageThread` 49, …) are all blocked TODAY only because they reach
the component. Once it leaves they need nothing but already-extracted siblings. That is why the wall looks
absolute from here and is not: the survey can only see one layer at a time.

**Phase 1 is not one module.** 1,578 lines cannot become a single file without creating a fresh violation,
and the component is mutually recursive, so it has to be cut somewhere. THAT is the redesign — not the
extraction itself.

**Phase 3 needs a proof mechanism that does not exist yet.** The top-level block is statements, not
declarations, so there is no span to relocate. Either the reconstruction proof learns a declared shape for
it (a typed `rewrap` was written and reverted this round — it works, see the correction above) or the
pristine fixture is re-baselined and the series' "pure file split" claim is explicitly closed off at that
point. Both are reviewer calls.

**The ~16-line margin is thin and should not be treated as a pass.** Dead-import relief will help — phase
2's own import surface shows several names going unused — but it is not counted above, and phase 1's split
may need shared helpers that add lines back. The honest claim is "reaches roughly the limit", not "clears
it comfortably".
