# v0.7 review — weak points found, and which are worth doing

Written 2026-08-25 at the close of the review round. Every entry carries the measurement it rests on
and a judgement. **The judgement is the point**: a list of everything wrong is a list nobody acts on.

Fixed items are in git and not repeated here — `git log 1a3de61a..HEAD` has them. This file is what
was found and *not* fixed, plus what was deliberately left alone.

## Shipped this round

**Defer the shared-files fetch while the Files page is hidden. SHIPPED 2026-08-25, `2c2da14b`.**
`/shared` is 113,854 bytes for 388 files, 34,839 gzipped, fetched every cycle whether or not the page
is open. At the default 15s refresh that is 8.0 MB an hour per tab after compression, 23.9 at the 5s
floor. `state.files` is read by the Files page alone. The poll now asks `files-page.mjs` first, and
`navigateToPage` loads the list on open so nothing is ever shown stale.

CORRECTION, and the reason this entry is worth reading. An earlier version of it said the change had
been attempted four times, left the reconstruction between 169 and 828 characters off, and cost more
to land than the bandwidth was worth. **That conclusion was wrong, and it blamed the wrong thing.**
The gate is cheap. A controlled experiment — one line added to one tracked declaration, declared as
`editedSince` and nothing else — passed first time. What had failed every previous attempt was my own
editing: python that matched an anchor by an indentation I had copied out of my own terminal output,
where a display prefix had added two spaces. The same slip broke four more edits while landing this
change, each time as an assertion rather than a wrong result.

The one real piece of knowledge is that `wrapper.dedent` is the prefix reconstruct **adds back**, so a
declared edit on a wrapped item is written with that prefix INCLUDED, not stripped. Both readings of
that are silent: the edit is simply "not found verbatim". `unwrapBody` says so in a comment; I guessed
twice before reading it.

So the standing advice is the opposite of what stood here: an edit to an extracted dashboard module
costs one `editedSince` entry, and the thing to budget is getting the bytes right, not the gate.

## Worth doing, needs an operator decision

**The roster endpoint costs 5.9 ms per agent and is the dashboard's slowest poll slice.**
Measured against the live service 2026-08-25, five samples each: `GET /api/v1/agents/{one}` is 10.2 ms;
`GET /api/v1/agents` for the 47-agent roster is 282.9 ms (471 ms median on an earlier, busier sample,
996 ms worst). The difference is 272.7 ms across 46 extra agents -- **5.9 ms of marginal cost per
agent**, so it scales linearly with fleet size and this fleet is not large.

It is not payload size: `/api/v1/sessions` returns 107,626 bytes in 41.9 ms while this returns 63,965
in 282.9. The whole poll -- ten slices -- sums to 953 ms of medians, and this one endpoint is half of
it.

WHAT I RULED OUT, so the next person does not repeat it. `_has_live_terminal_session` runs per live
managed agent and looked like an unindexed scan; it is not -- `idx_terminal_sessions_agent
(agent_id, status)` covers that query exactly. `_enforce_env_reachable_gate` issues
`SELECT * FROM environments WHERE id = ?` per live managed agent (19 of the 47) for a table holding
2 rows, which is wasteful but cannot account for 5.9 ms each on an indexed primary key.

NOT FIXED, deliberately. Batching those 19 lookups into one preloaded map is obvious and safe, but I
could not measure that it helps: attributing the 5.9 ms needs a profile of the loop against the real
database, and the only honest place to take that is a host where the fleet is idle. Refactoring the
hottest read path in the service on a guess, with no way to verify the result, is how a performance
fix becomes an outage. The measurement is the deliverable; the attribution is the next step.

**A metric the service computes on every stats call and shows nobody.**
`orphan_unread_messages` is 1,889 right now -- unread inbox rows addressed to agents that have since
been removed. `/api/v1/stats` recomputes it on every call (203.0 ms median), a cleanup endpoint
exists (`POST /messages/cleanup/orphan-unread`), and no dashboard source mentions either. So the
residue is counted continuously, never surfaced, and cannot be acted on by anyone who does not read
the JSON by hand. Surfacing it means adding a control that DELETES messages, which is the operator's
call to make, not mine to add unprompted.

SHARPENED, and this is the actionable half. `/api/v1/stats` cannot be deferred the way `/shared` was:
its consumer is `#metrics` in the always-visible topbar, not a page. But the dashboard reads exactly
TWO fields from it -- `dispatch_runs_by_status` and `run_failures_24h` -- while the endpoint computes
24 top-level keys for every call, including a per-agent message histogram over 32,929 messages and the
1,889-row orphan scan above. 203 ms every 15 seconds per open tab, to render two numbers. A narrow
projection for the topbar's two fields is the obvious shape; like the roster, it wants a profile
against the real database before anyone writes it.

While measuring: `shared_size_bytes` is 383,021,022 -- 383 MB across the 388 shared files behind the
`/shared` payload this round already deferred.

**Every agent can read every other agent's hermes gateway credential, and the fix is an
access-control decision rather than a redaction.**
Measured 2026-08-25 against the live service: `/api/v1/agents` returns
`agent.runtimeConfig.gatewayUrl` for 16 agents with the auth token in the query string, on the
endpoint the dashboard polls every 15 seconds and any agent may call. That is more exposure, and more
continuous, than the seven tokens in stored run errors that `9599d802` fixed.

IT IS NOT THE SAME DEFECT, which is why it was not fixed with it. The token in an error message was
decoration -- the message needs the address, never the credential. This one is LOAD-BEARING:
`session-console.mjs` hands `runtimeConfig.gatewayUrl` to `hermesGatewayUrlToHttp`, which pulls the
token out and puts it in the console URL, and a visible TUI in the dashboard console is a standing
hard requirement. Redacting the field breaks the console.

So the question is not "strip it" but "who should receive it": the dashboard needs the token for the
agent whose console is being opened; one agent does not need another agent's. Scoping the field by
caller identity is an auth change on the hottest endpoint in the service, and the fleet is live.
Recorded rather than attempted.

Worth knowing before deciding: the seven tokens already written into dispatch-run errors are still in
the database. New ones stop, old rows do not clean themselves, and purging stored rows is destructive
and not something this round will do.

**RESOLVED, and my previous entry here was wrong: `available` on a TUI-less hermes lane is correct.**
The entry this replaces said the teardown "did not fire" and pointed at `countAttachedSessions`
returning -1. Both were wrong, and I had not read far enough to say either.

What actually happens: the loop reports the gateway dead, the server receives it at
`/agents/{id}/resident-lost`, and for a `session_mode='managed'` agent it deliberately sets
`status='active'` -- which derives `available` -- plus `launch_mode='detached'`, so the next message
cold-starts a fresh session. That is not a leak in the status; it is the FIX for a worse one.
Resting a managed worker at `stopped` was the 2026-07-06/07 defect: the send-gate rejects `stopped`
outright, so a dead-gateway hermes could never wake and a whole team sat unreachable. Pinned by
`test_a_managed_worker_rests_COLD_STARTABLE_not_stopped`.

So `available` on a managed agent means COLD-STARTABLE, never ATTACHED. The dispatch that follows is
supposed to start a fresh session; on the lanes measured 2026-08-25 it produced a gateway whose
visible TUI never attached, which is an operational condition with an operator remedy (relaunch
hermes-aify), not a control-plane defect.

WHAT THE ROUND ACTUALLY FOUND is in `9599d802`: those messages carried the gateway's auth token into
stored run errors and status notes, and two of them told the operator the agent was "self-correcting
off available" while the server was deliberately resting it AT available. The false sentence is what
sent me down this path for most of a round -- prose on a join, describing the resident case and read
as if it covered both.

Two internal comments still make that claim (`hermes-delivery-loop.mjs` ~596,
`hermes-delivery-run.mjs` ~329) and one more sits in `hermes-managed-host.js` ~373. Left alone
deliberately: all three are inside declarations under the byte-identity extraction gate, so a
comment-only fix costs a declared edit each. Worth doing when that file is next opened for a real
change, not on its own.

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
