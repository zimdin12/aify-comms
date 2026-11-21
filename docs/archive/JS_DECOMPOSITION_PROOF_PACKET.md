# JS decomposition — proof packet

**Status:** approved by the reviewer with amendments; first slice landed. Measured on `7a767baf`,
**corrected twice since — see §3.1.** The headline number in the first version of this document was
wrong by 4.8×, and both corrections came from running a check rather than reading the code.

The reviewer's standing ruling is that JS is last and needs a reviewed proof packet before any
extraction, and specifically that it must not start on "tests pass". This document is that packet. It
answers the nine requirements in order, with numbers taken from the tree rather than from expectation —
a distinction that matters here because the operator's original framing of the JS problem, which I
repeated, was wrong in a way only measurement caught (see §1.1).

---

## 1. Inventory

Measured with brace-matched top-level declaration scanning and export-marker extraction, not `wc`.

| file | lines | fn decls | arrow consts | class methods | exports | module system |
|---|---|---|---|---|---|---|
| `mcp/stdio/server.js` | 6331 | 103 | 17 | 214 | **11** | ESM |
| `service/new_dashboard/app.js` | 5082 | 192 | 29 | 199 | **0** | ESM |
| `mcp/stdio/hermes-managed-host.js` | 3017 | 19 | 14 | 81 | **28** | ESM |
| `mcp/stdio/pi-session.js` | 1300 | 12 | 0 | 110 | **8** | ESM |

Total: **15,730 lines**, more than the entire remaining Python oversized surface.

### Current test relationship — the number that decides the order

| file | tests that IMPORT and execute it | tests that only READ its source |
|---|---|---|
| `server.js` | 3 | 27 |
| `app.js` | **0** | 4 |
| `hermes-managed-host.js` | 3 | 10 |
| `pi-session.js` | **0** | 2 |

A source-reading test cannot fail on wrong logic — only on changed text. So `app.js` and
`pi-session.js` have **no executable coverage of their own contents at all**, and `server.js`'s 27
source-readers are guarding text, not behaviour.

### 1.1 Correction to the stated diagnosis

The operator and I both described the JS problem as "we do not even have good standards". That is not
what the measurement shows, and the corrected version changes what the fix is.

`app.js` cannot be imported by a test at all:

```
$ node -e "import('./app.js')"
ReferenceError: location is not defined
```

It is not untested because nobody wrote tests. It is untested because **importing it executes
module-scope browser code**, and there is no seam at which a test could get hold of a function. The
export count of 0 is a symptom of that, not an oversight.

The scale of the blocker is small, which is the useful part:

- 328 module-scope statements out of 5,082 lines
- module-scope browser-global touches: **7** `document`, **4** `localStorage`, **1** `window`

So `app.js` is ~94% function bodies behind a ~12-line import barrier. Extraction does not need to fix
that barrier: **a new module containing extracted functions has no module-scope browser code at all**,
so it is importable and testable on day one while `app.js` itself stays exactly as unimportable as it is
today. That is the seam, and it is why extraction and testability are the same task here.

---

## 2. First target

**`service/new_dashboard/app.js`.** Agreeing with the reviewer's preference, and measurement supports it
rather than merely not contradicting it:

- blast radius is dashboard-only — no bridge, no MCP session, no live agent path;
- it is the only file with **zero** import-based tests, so every extraction converts untestable lines
  into tested ones;
- **the pattern is already established in this exact directory**, which makes this continuation rather
  than new architecture:

| existing module | lines | exports | own test |
|---|---|---|---|
| `notify.mjs` | 187 | 11 | yes |
| `cli-resume.mjs` | 102 | 4 | yes |
| `sessions-list.mjs` | 100 | 4 | yes |
| `inspector-refresh.mjs` | 77 | 4 | yes |
| `terminal-input.mjs` | 70 | 6 | yes |

`app.js` already imports all five. There is nothing to invent — the question is only which functions go
next.

`server.js` stays last, as ruled: it is the live MCP bridge, it is loaded at client startup, and a
mistake there takes out the fleet rather than a page.

---

## 3. Seam candidates — pure file-splits only

A function is extractable only if it is **transitively** free of browser globals. Direct freedom is not
enough: `renderRunInspector` touches no `document` itself but is worthless in a test if something it
calls does.

Call graph over the 192 top-level functions, `document`/`window`/`location`/`navigator`/`localStorage`/
`sessionStorage`/`alert`/`history`/`fetch`/`WebSocket` as impurity seeds, propagated to fixpoint:

```
192 top-level functions
 44 touch a browser global directly
103 transitively impure (44 seeds + 59 reached through calls)
 89 TRANSITIVELY PURE — 1,090 lines
```

**That figure was wrong. The real number is 229 lines.** Two constraints were missing, both found by
running the extraction rather than by reading the analysis — see §3.1 before quoting any number from this
section.

### 3.1 Two corrections to the extractable surface

**(i) Inline HTML handlers.** `app.js` renders markup containing `onclick="foo()"` attributes, and those
resolve against the GLOBAL scope, not module scope. Moving such a function into a module makes the
attribute reference a name that no longer exists — the button silently stops working, with nothing to
catch it, because the dashboard's only app.js tests read source text.

```
55 of 192 functions are referenced from inline on*= handler attributes
24 of them are in the transitively-pure set (265 lines) and CANNOT move
```

**(ii) Module-scope aliases and mutable state.** The first purity pass seeded on browser global NAMES. It
missed `byId`, a module-scope `const byId = (id) => document.getElementById(id)`, and it missed the 14
module-scope `let`/`var` bindings including `state`. So functions like `renderDiagnosticsSummary` and
`renderUsageConsumption` were classified pure while calling `byId(...)` and reading `state`. I found this
while extracting them: reading the bodies I was about to move showed the DOM access the detector had not.

Re-measured with aliases and module-scope mutable bindings as impurity seeds, and inline-bound clusters
excluded:

```
192 top-level functions
131 transitively impure
 61 transitively pure (532 lines)
 18 of those inline-bound
 31 MOVABLE — 229 lines
```

**229 lines of 5,082.** So pure splitting takes `app.js` to roughly 4,850, not 3,990. The ceiling
conclusion below is unchanged in direction and much stronger in degree, and this is why the operator
decision in the open question is now unavoidable rather than merely advisable.

The largest movable clusters, all verified free of inline references:

| lines | cluster | public root |
|---|---|---|
| 49 | `settingsFieldHtml` + `themePreviewTilesHtml` | `settingsFieldHtml` — **EXTRACTED, slice 1** |
| 23 | `applyRenderedWidth` | `applyRenderedWidth` |
| 19 | `environmentRoots` + `environmentStartCommand` | both |
| 19 | `renderEventBody` + `renderRunEvent` | (none — internal) |
| 12 | `selectedDiagnostics` | `selectedDiagnostics` |
| 11 | `lookup` | `lookup` |

**229 lines is the extractable surface of app.js under a pure-split rule** (the 1,090 figure above is
retained, struck through by §3.1, because a packet that silently swapped its own headline number would be
the "documentation inherits the intention" failure this project keeps catching). That is the honest ceiling
without behavioural rewrites, and it is ~4.5% of the file. Getting `app.js` under 1,000 lines is therefore
**not achievable by pure splitting** — stated up front rather than discovered at slice four. It would need
either DOM-parameterising the render layer (a behavioural
change, out of scope for 0.5.x) or splitting the impure half into modules that are still only
source-testable (which buys structure but no coverage, and is the thing this packet is supposed to
avoid).

### Proposed first slice

The largest coherent cluster inside the pure set is HTML rendering — functions that take data and
return markup strings:

```
22 render* functions, 420 lines, all transitively pure
```

Proposed as **`service/new_dashboard/render-cards.mjs`** or split by page if the reviewer prefers
smaller units. Candidates include `renderRuns` (35), `renderContractBoard` (29), `renderSpawnRequests`
(28), `renderSessionStatusFilter` (28), `renderSessionActivity` (27), `renderActivityFeed` (23),
`renderDiagnosticsSummary` (21), `renderRuntime` (20), `renderDiagnosticsBulkToolbar` (19),
`renderUsageConsumption` (17).

These are the ideal first extraction: a function that returns a string is testable by calling it and
asserting on the string, with no DOM, no fixture, and no mocking.

**Explicitly NOT in the first slice:** `renderRunInspector` (53) and `openIdentityDirectory` (49) are
transitively pure but sit at the top of call chains reaching 4+ other functions each; they should follow
their callees, not lead. Same bottom-up rule as the Python side.

---

## 4. Reconstruction equivalence

The Python side proves a move with AST+byte identity against `git show HEAD:`. JS has no stdlib parser
here, so the equivalence is defined textually and mechanically:

1. **Extracted spans are byte-identical.** Each moved function's source span, brace-matched from column
   0, must be identical between `git show HEAD:service/new_dashboard/app.js` and the new module, modulo
   exactly one declared change: the prepended `export ` keyword. That substitution is declared per
   function and is the only permitted difference.
2. **Reconstruction.** Textually re-insert each extracted span at its original offset in the new
   `app.js`, delete the added import statement, and require the result to be **byte-identical to
   `git show HEAD:service/new_dashboard/app.js`**. This is the JS analogue of the extract-method gate's
   inline-back proof: it proves nothing else in the file moved, including whitespace.
3. **Untouched spans.** Implied by (2) but asserted separately so a failure says which of the two
   happened: every line of `app.js` outside the extracted spans and the added import block is
   byte-identical to HEAD.
4. **No barrel.** The new module exports exactly the functions extracted into it — no re-exports of
   anything else, no `export *`.

The reconstruction script will be committed alongside the slice, not run ad hoc, so the next extraction
re-uses a proof rather than reproducing an argument.

---

## 5. Real unit tests

Per extracted module, a `*.test.mjs` beside it that **imports and calls** every export, following the
`doctor-predicates.js` and `terminal-input.mjs` pattern. Minimum bar per export:

- one call asserting the real return value for a representative input;
- one degenerate input (`null`/`undefined`/empty array/empty string) — the class of defect that source
  tests structurally cannot see;
- for HTML-returning functions, an assertion on the escaping of untrusted fields, since these render
  agent-supplied text into markup.

Source-regex tests may remain as supporting evidence. They are not the proof, and the existing
`app.test.mjs` family stays untouched so the slice cannot be credited with coverage it did not add.

---

## 6. `node --check`

Run on every touched JS file: the new module, the modified `app.js`, and any test file. Non-negotiable
and cheap; it is the JS equivalent of `py_compile`, which has caught a corrupted `def` line in this
series once already.

---

## 7. Suites

Per slice, all three, before the commit:

```
python -m pytest service/tests -q
cd mcp/stdio && node tests/run-all.mjs
cd service/new_dashboard && node --test *.test.mjs
```

The dashboard suite is the one that must grow: current baseline **194 passing**, and a slice that adds
exports without raising that number has not met §5.

---

## 8. Blast radius labels

Declared per extraction, in the commit:

| label | meaning | files |
|---|---|---|
| `dashboard-only` | a browser page; failure is visible and non-destructive | `app.js` |
| `runtime wrapper/session` | one agent's runtime session; failure strands that agent | `pi-session.js`, `hermes-managed-host.js` |
| `bridge/server live path` | loaded by every MCP client at startup; failure takes out the fleet | `server.js` |

The first slice is `dashboard-only`. Anything labelled `bridge/server live path` additionally requires
`install.sh` + a wrapper relaunch to actually take effect, per CLAUDE.md — a JS slice there is not
deployed by a container rebuild, and claiming otherwise would repeat the deploy-verification failure
`aify-comms doctor` exists for.

---

## 9. Export-surface review

Stated per export, in the commit: **what it is, who imports it, and why it is not a barrel.**

The rule: an export exists because a *named consumer* needs it — `app.js` calling it, or a test
executing it. "It might be useful" is not a reason, and a module whose export count equals its function
count with no consumer for most of them is a barrel wearing a module's name.

Anti-pattern guarded against explicitly: extracting 22 functions and exporting all 22 while `app.js`
calls only 9 of them would create 13 exports whose only consumer is the test that exists to justify
them. Where that happens the function stays private to the module and is tested through its public
caller.

---

## Open question for the reviewer

§3 establishes that `app.js` cannot reach 1,000 lines by pure splitting: 1,090 of its 5,082 lines are
transitively pure. The operator's target is every file under 1,000.

I am not proposing a way around that, and I am not going to quietly redefine the goal. The options, as
I see them:

- **(a)** Extract the 1,090 pure lines, land `app.js` at ~3,990, and record the measured reason the rest
  cannot follow without behavioural change. Honest, incomplete against the operator's number.
- **(b)** As (a), then propose DOM-parameterising the render layer as its own **behavioural** tag
  outside 0.5.x, which is what would make the remainder testable and splittable.
- **(c)** Split the impure half by subject as well, accepting modules that are still only
  source-testable. Meets the line target; contributes nothing to the actual problem, and is the
  "structure without coverage" outcome this packet argues against.

My recommendation is **(a) now, (b) proposed separately**, and explicitly not (c) — the line count is a
proxy for the real goal, and (c) optimises the proxy at the expense of the goal. That said, the target is
the operator's to set, and this is flagged before any extraction rather than after.

**Reviewer ruling (received):** (a) now, (b) as a separate later proposal, (c) rejected for this track
unless the operator explicitly says the numeric target beats the testability goal. The reviewer also ruled
that the first pure slice does not need to wait for operator escalation, because it improves the real goal
and forecloses nothing — but that no claim may be made that `app.js` will reach 1,000.

**After §3.1 this is sharper than when the ruling was given.** At 1,090 lines, option (a) would have taken
app.js from 5,082 to ~3,990 — a real dent. At the corrected 229, option (a) lands it at ~4,850, so the
operator is being asked to accept that `app.js` stays ~4.8× over the threshold for the whole of v0.5.x.
That is the decision, stated plainly, with the measurement behind it.

---

## Slice 1 — landed

`service/new_dashboard/settings-fields.mjs`: `settingsFieldHtml` exported, `themePreviewTilesHtml` private.
49 lines. Dashboard suite **194 → 219**.

What the slice actually bought, which is the argument for the whole track: the first executable assertion
ever run against a line of `app.js` **found a latent defect**. `settingsFieldHtml` interpolates `item.key`
into `id="..."` and `for="..."` with no `esc()`, while the neighbouring `data-setting-key` IS escaped — so a
key containing a quote injects arbitrary attributes. It is not reachable today (`SETTINGS_SCHEMA` is a
hardcoded const of developer-authored literals) and v0.5.x is structural-only, so it is PINNED as current
behaviour with its reachability recorded, and reported for its own behaviour tag. No source-reading test
could have found it.
