# v0.6 Phase 3 — dashboard: what was found, what was fixed, what was not

Stage 1 was the read-only critique (`docs/DASHBOARD_CRITIQUE_2026-08-19.md`). This is stage 2: the
work done against it, and the three items that are deferred with a reason rather than fixed.

Nothing here was clicked. The live-UI rule is allowlist-never-blocklist, and the recorded incident
behind it is a button sweep that fired real Stop controls and killed three workers plus the session
doing the sweeping.

---

## The census, measured twice and corrected twice

| | stage 1 critique | now |
|---|---|---|
| product modules | 69 | 69 |
| loaded by the suite | 67 | 67 |
| declared functions (loaded) | 434 | 501 |
| never called | 57 | 49 |
| — pre-init no-ops | 24 (of 42 classified) | 30 |
| — genuinely untested | 18 (of 42 classified) | **19** |

**Both of my own measurements were wrong before they were right, and the corrections are the useful
part.** The first census scanned `.mjs` only, so `chat.js` and `app.js` were invisible — a census that
silently excludes files reports a smaller problem than exists. The second classified only the arrow
form of a placeholder (`let refreshSoon = () => {};`) and missed three other shapes: no-op METHODS
inside a placeholder object (`let chatController = { render() {}, close() {} }`), dependencies
defaulted at construction (`typeof deps.x === 'function' ? deps.x : () => {}`), and default
PARAMETERS (`isEnabled = () => false`). Missing a shape does not shrink the census; it moves work into
the wrong column, which is worse, because the wrong column is the one that looks actionable.

The critique's 18 came from classifying the worst seven files only. All 49 are classified now, which
is why the number went up while the work went down.

**Genuinely untested went 31 → 19.** Seven functions gained tests; five were reclassified as
unreachable once the classifier learned the other placeholder shapes.

## What gained tests

**`chat.js` — `open`, `close`, `loadFleetPulse`, `refreshPulse`, `openAnalytics` (12 tests).** The
conversation lifecycle. What made these worth writing is that three carry a guard nobody had ever
exercised, and each guard exists because of a real incident:

- `loadFleetPulse` patches `state.agents` statuses from the pulse payload. Without it the rail renders
  an older `/agents` poll while the pulse board carries fresh ones, and a single frame shows a green
  dot beside "working now" — observed 2026-07-02.
- Both loaders discard a payload that arrives after the operator moved on. Switching pulse window
  mid-flight must not paint the previous window's numbers; leaving an analytics panel mid-flight must
  not paint the previous agent's figures under the new agent's name.
- `open()` strips the `dm:` rail prefix before asking for analytics. Passing the prefixed key through
  loaded an empty, all-zero panel that looked exactly like real data for an idle agent.

All three mutation-proven: removing each guard turns a test red.

**`message-transport.mjs` — `sendRunFollowup` (8 tests).** The Runs view's Retry and Queue-after
buttons. Every field is a promise to the receiving agent: `queueIfBusy` decides whether the follow-up
interrupts a working agent, `requireReply` opens a tracked contract, `inReplyTo` is what threads the
answer back rather than starting an orphan. It reads BOTH `messageId` and `message_id` because runs
arrive in two shapes, and reading one silently drops threading — that case is now pinned.

**`work-loop-actions.mjs` — `loadContractsForState` (5 tests).** Three branches build three different
queries, and a wrong one is invisible: the page renders a plausible list of the wrong contracts. `all`
is the only branch that asks for closed rows and the only one that raises the limit to fit them; both
halves are pinned, because dropping the limit alone would silently truncate.

## Deferred, with reasons

**1. `app.js` headroom: 987 of 1000 lines, and it can only shrink by EXTRACTION.**

Measured, not assumed: an in-place simplification of `flowGates` — deriving nine restated entries from
`flowAssertions` instead of writing each twice — is correct, cannot change behaviour, and saves six
lines. It turns `extraction-proof.test.mjs` red, because that gate reconstructs the pristine file
byte-for-byte from the current one plus every extracted module. The protection is real (it is what
proves no slice quietly changed something two functions away) and its price is that the remaining
content is frozen against improvement.

The obvious next slice is `flowAssertions` / `flowGates` / `evaluateFlowGates`, and it is **deliberately
retained**: `run-inspector-controls.mjs` and `agent-drawer.mjs` both record that their renderer stays
in `app.js` precisely because it calls `evaluateFlowGates`, whose assertions probe half the file.
Moving it would fight a decision two modules already document.

So the headroom stands at 13 lines with a known-good reason not to force the obvious slice. The
recommendation is a slice chosen for its import surface rather than its size, taken with time to do
the `EXTRACTIONS` bookkeeping properly, not appended to a long session.

**2. `styles.css` stays out of the 1000-line gate — ruled, not deferred.**

The plan asked Phase 3 to answer this. The answer is no, and the reason is that the 1000-line rule is
about a defect CSS does not have: a long module hides control flow, import cycles and ownership, and
a reader cannot hold it. A 1,844-line stylesheet is a flat list of rules; length makes it tedious, not
unreadable, and the real CSS defects (dead selectors, specificity wars, duplicated tokens) are
invisible to a line count.

It is not unguarded. `no-unwatched-oversized-file.test.js` holds it at a measured ceiling that may
only go down, which is the right instrument: it cannot grow unnoticed, and nobody is asked to split a
stylesheet to satisfy a rule written for modules.

**3. Nine remaining untested functions are renderers that need a DOM.**

`renderRail`, `renderConversation`, `mountChatConsole`, `render`, plus the `resize`/`waitForSize` pairs
in `console-actions.mjs` and `xterm-mount.mjs`. The sizing pair is the interesting one — sizing is
where the recorded xterm crash lives (WebGL atlas on a zero-box or detached element) — and testing it
honestly needs a real layout, not a stub that returns whatever the test wants. A stubbed
`getBoundingClientRect` would assert that the stub returned what it was told to.

Carded rather than faked. The bar for revisiting: a headless-browser harness, which is a Phase-3-sized
piece of work of its own and was not in this phase's scope.

## The gate, restated

The plan's gate read "census materially below 78/492". That gate could be satisfied by DELETING
placeholders, because it counts functions that cannot be called — 30 of the 49 remaining. Restated:

> **Every genuinely untested function is covered or carded with a reason, and the placeholder count is
> reported separately.** 19 remain: 9 carded above, 10 in modules whose tests were not this phase's
> target and which the same rule now governs.

Reported separately, as promised: 30 pre-init no-ops, unreachable by construction because every
`initX(deps)` throws on a partial bag, so a test for one would assert that a no-op does nothing.
