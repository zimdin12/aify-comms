# Dashboard critique — v0.6 Phase 3, stage 1 (finding only)

**Read-only pass.** Nothing here was fixed; nothing was clicked. The live-UI rule is allowlist-never-
blocklist, and the recorded incident behind it is a button sweep that fired real Stop controls and killed
three workers plus the session doing the sweeping. This pass reads source and coverage output only.

**Measured 2026-08-19**, and every number below was cross-checked two ways before it was written down —
because three numbers I published earlier in this release were wrong from scripts I ran once and quoted
as fact.

---

## Headline: the census overstates the gap by more than half

| | |
|---|---|
| Product modules | 69 |
| Loaded by the suite | 67 |
| Declared functions in loaded modules | 434 |
| Reported uncovered | **57** |

`docs/V0.6_PLAN.md` records **78 never-called of 492**. That figure is stale — the suite has grown since
— and both figures share a defect: **they count functions that cannot be called.**

Examining the seven worst files (42 of the 57):

| | Count | What it is |
|---|---|---|
| Pre-init no-op placeholders | **24** | `let refreshSoon = () => {};` at module scope, replaced by `initX(deps)` |
| Genuinely untested | **18** | real behaviour with no test |

**The placeholders are unreachable by construction, not by neglect.** Each module declares no-op defaults,
then `initAgentSessionActions(deps)` — and its siblings — **throw** on a partial bag:

```js
const missing = REQUIRED.filter((k) => deps == null || deps[k] == null);
if (missing.length) throw new TypeError(`initAgentSessionActions requires ${missing.join(', ')}`);
```

So once init has run the defaults can never be invoked, and init cannot half-run. Writing tests to
"cover" them would assert that a no-op does nothing.

**Consequence for the Phase 3 gate.** The plan's gate says "census materially below 78/492". That gate is
measuring the wrong thing: ~57% of what it counts is unreachable by design, so it can be satisfied by
deleting placeholders rather than by testing behaviour. **Recommend the gate be restated as "the 18
genuinely untested functions are covered or carded", with the placeholder count reported separately.**
That is a reviewer decision, not something to change unilaterally.

---

## F1 — `chat.js` is the real target (7 of the 18)

`close`, `open`, `loadFleetPulse`, `refreshPulse`, `markConversationRead`, `onSelectionChange`,
`openAnalytics`.

This is the chat surface: opening and closing a conversation, marking it read, reacting to a selection
change, and the fleet-pulse/analytics panels. It is the single largest genuinely-untested cluster in the
dashboard, and it is user-facing behaviour rather than plumbing.

**Why it matters beyond coverage:** `markConversationRead` writes read state. A read-marking bug is
silent — the operator sees an unread badge that never clears, or worse, one that clears without the
message being seen — and nothing else in the suite touches it.

---

## F2 — `message-actions.mjs` render path (4 of the 18)

`render`, `renderConversation`, `renderRail`, `mountChatConsole`.

The rendering entry points for the conversation, the rail and the embedded console. Untested rendering is
usually low-severity, with one exception worth naming: `mountChatConsole` attaches a terminal, and the
xterm mount path already has a recorded crash class (WebGL atlas on a zero-box or detached element) that
`xterm-mount-handlers` guards against. Whether this path reaches those guards is unverified.

---

## F3 — `console-actions.mjs`: `resize` and `waitForSize` (2 of the 18)

Both are sizing logic, and sizing is where the recorded xterm crash lives. `waitForSize` in particular is
a wait — waits are where "flaky" usually means a budget below its own cost, and this repo has paid for
that twice.

---

## F4 — `agent-session-actions.mjs`: `submitAgentEdit` (1 of the 18)

The only genuinely untested WRITE in the list. It submits an agent edit; everything else in that file's
uncovered set is a placeholder or a render. A write with no test is a different risk class from a render
with no test.

(`agent-session-actions.mjs#v` is NOT a measurement artifact, which is what I assumed before checking:
it is `const v = (id) => byId(id)?.value?.trim() || '';` — a real field-reader declared INSIDE
`submitAgentEdit`. It is uncovered for exactly one reason: its enclosing function is. That makes it the
same finding, not a second one, and it drops the actionable count in this file from 2 to 1.)

---

## F5 — `app.js` is invisible to the census, and so is its exclusive dependency

`app.js` and `page-titles.mjs` are the only two product modules the suite never loads. `page-titles.mjs`
is **not** dead — `app.js` imports it — it is invisible because its only importer is.

`app.js` is not untested in the ordinary sense: `extraction-proof.test.mjs` reads it as TEXT and
reconstructs it byte-for-byte. But no test ever *executes* it, so its ~19 declarations contribute nothing
to the 434 and are absent from the 57. **Any statement of the form "the dashboard has N uncovered
functions" excludes app.js entirely**, and that should be said whenever the number is quoted.

---

## F6 — the two counting methods disagree, and the gap is the finding

Regex over source: **481** named functions. V8 across the suite: **434** declared. The 47-function gap is
modules V8 never saw — because V8 can only report on code that was loaded.

This is the same class as the bridge census counting one file four times under `?cacheBust=` query
strings. **A coverage tool reports on what ran, not on what exists**, so "uncovered" from V8 always means
"uncovered among the loaded", and a module nobody imports scores zero uncovered functions while being
entirely untested. Any future census should report loaded-vs-total alongside the uncovered count.

---

## What this pass did NOT examine

Stated so the gap is visible rather than implied:

- **The 15 uncovered functions outside the top seven files.** Only 42 of 57 were classified.
- **Anything about how the dashboard LOOKS or behaves in a browser.** No page was opened. UX critique,
  layout, responsiveness and accessibility are untouched by this pass, and the operator asked for "hard
  critique and polish" — this delivers the first half of the first word only.
- **`styles.css` (1,844 lines)**, which is out of the 1000-line gate's scope by decision and whose
  inclusion is an open reviewer question.

---

## Recommended Phase 3 order, if approved

1. Restate the gate (above) so it measures behaviour rather than placeholders.
2. `chat.js` — 7 functions, user-facing, includes read-state writes.
3. `console-actions` sizing + `mountChatConsole` — the xterm crash-adjacent paths.
4. `submitAgentEdit` — the one untested write.
5. Classify the remaining 15 uncovered functions.
6. Only then the UX pass, which needs the operator awake since it is the half that requires opening the
   dashboard.
