# v0.7 review — weak points found, and which are worth doing

Written 2026-08-25 at the close of the review round. Every entry carries the measurement it rests on
and a judgement. **The judgement is the point**: a list of everything wrong is a list nobody acts on.

Fixed items are in git and not repeated here — `git log 1a3de61a..HEAD` has them. This file is what
was found and *not* fixed, plus what was deliberately left alone.

## Worth doing, needs an operator decision

**Defer the shared-files fetch while the Files page is hidden.**
`/shared` returns 113,854 bytes every poll — 388 artifacts, metadata-only and capped at 2000 by a
2026-07-03 fix, so bounded but the fourth-largest payload in the cycle. `state.files` has exactly two
readers and both are the Files page. At one poll per 15s that is roughly 26 MB/hour per open tab for a
panel that is usually not on screen.
Not done because it is a trade, not a cleanup: deferring means the page can show data up to one poll
old when it is opened, unless a navigation hook forces a load — and that hook lives in
extraction-tracked code. Which side to err on is a product call.

**The codex console input is named only by a placeholder.**
`session-console.mjs`, inside the form marked `data-action="codex-console-send"`. A placeholder is
erased once the field has content, and this one doubles as a state message, so the field's announced
name changes with the thread. One attribute fixes it. Deferred only because that module is
extraction-tracked and a one-attribute edit there costs a declared `editedSince` cycle — worth doing
alongside the next intentional change to the file. Recorded in KNOWN_ISSUES.md.

## Worth knowing, not worth doing

**`mcp/stdio/pi-session.js` is 993 lines against a 1000-line gate.**
Seven lines of headroom, and tighter than either file CLAUDE.md named as the watch-item. The gate is a
red test, not a silent failure, so nothing is at risk — but the next small edit there goes red for a
reason unrelated to that edit. Splitting it is real work; being ambushed is not.

**The dashboard DOM is 8,394 elements, 91% of it in hidden pages.**
Measured against LCP 131 ms, TTFB 2 ms, CLS 0.02, and a hidden page took ZERO mutations across a full
poll cycle — the render is signature-gated, so it is large but idle. Lazy-rendering pages would be a
large change for no measured gain.

**126 requests per cold load, 60-plus of them unbundled ES modules.**
Chrome's trace puts render-blocking savings at 0 ms. Bundling would buy nothing measurable here.

**`service/new_dashboard/fixtures` is not excluded from the container-rebuild staleness count.**
Same class as the two exclusions that WERE added this round, but measured: 0 fixture-only commits in
the last 300, and 1 commit touching fixtures at all. Adding an exclusion would widen an opt-out list
for zero benefit, and a wider exclude list is the false-green direction that list's own comment argues
against.

**`agents.capabilities` is a `list[str]`; `agent_sessions.capabilities` is a `dict`.**
One field name, two shapes. Consistent per table in live data — every agents row a list, every
agent_sessions row a dict — and nothing mixes them. The COUNTS move while the fleet works (288
session rows when first measured, 291 an hour later); what does not move is that no row of either
table has ever held the other shape. A gate here would only assert that types are types.

## Left alone on purpose, with the reason recorded in code

**`terminateProcessTree`'s callers keep an unreachable `catch`.** Its own last act is
`proc.kill(signal)` on the object a fallback would retry, and in the self-protect branch a REACHABLE
fallback would kill the bridge, its parent or a sibling worker — the guard exists to prevent exactly
that. The `try` stays so a future throw cannot escape into a timer callback.

**`openAiUsageVerdict`'s success branch is unreachable from production.** usage-collector.js calls the
API and returns `ok` directly, reaching the predicate only after a failed call. Wiring it up would move
the success decision into a function whose remaining job is classifying failures.

**Three poll-cycle catches are kept as defence in depth**, exempted BY NAME in
`dead-error-reactions.test.mjs` so the exemption cannot quietly widen.

## Open questions this round could not settle

**A managed shell can still convert its agent to resident.** The JS `normalizeSessionMode` fails
toward `resident`, so only a literal `sessionMode:"managed"` is refused. Known, reported, awaiting an
operator ruling — untouched here. The Python side is gated by
`test_session_mode_vocabularies_stay_apart.py`; the bridge side is not.

**A default parameter referencing a name its module never declares** parses cleanly and throws on the
first real call. One shipped this round and was caught by `doctor-actually-runs.test.js`. A regex sweep
produced 47 hits across 162 files with the two most credible both false positives, and the behavioural
version — import every module and call every export — is unsafe here, because the bridge exports
heartbeat starters and reapers. No safe precise instrument exists for it today.
