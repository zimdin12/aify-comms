# v0.7 review — weak points found, and which are worth doing

Written 2026-08-25 at the close of the review round. Every entry carries the measurement it rests on
and a judgement. **The judgement is the point**: a list of everything wrong is a list nobody acts on.

Fixed items are in git and not repeated here — `git log 1a3de61a..HEAD` has them. This file is what
was found and *not* fixed, plus what was deliberately left alone.

## Shipped this round

**The dashboard tracked whether realtime was connected and showed it nowhere.**
`state.realtimeConnected` had FOUR writers in `realtime-socket.mjs` and no reader anywhere -- the
only other mention was its declaration in `state.mjs`. When the WebSocket dropped, the dashboard fell
back to the 15-second poll and the connection chip went on reading `live`, tooltip "All data
refreshed": true of the poll, and read by an operator as "updates are arriving as they happen".

The chip had three states -- `reconnecting` (service unreachable), `live`, `N stale` (a slice two
cycles old). All three answer freshness. None answered whether realtime was working, which is a
different question with the same symptom-free failure.

It now has a fourth: `polling`, amber, "Realtime updates are disconnected. The view refreshes on the
poll instead." Amber rather than green because the view behaves differently from how it looks; not
`reconnecting`, because the data IS current and the service IS reachable. The two existing warnings
still outrank it -- losing the roster and a two-cycle-stale slice are older, more specific complaints.

FOUND BY LOOKING, not by reading. Loading the page in a browser and asking whether the socket was up
is what exposed a flag with no consumer; four rounds of source scanning over this same file did not.

THE SERVER IS BLIND TO THE SAME THING, and that half is not fixed. `WSManager` in `service/ws.py`
has `active_count()` and `online_agents()`; neither has a caller anywhere in production code. So
nothing on the service side can answer "is any dashboard actually connected", just as nothing on the
client side could answer "is my socket up" until this round.

Exposing `active_count()` on `/health` is a one-line addition and would make the question answerable
from outside the browser -- which is where an operator asks it. Not done here because it changes a
response shape, and every other shape change this session has been left as the operator's call. Worth
taking together with the chip, since the two halves answer the same question from opposite ends.

One thing it surfaced on the way: `state.mjs` defaults `realtimeConnected` to FALSE, true only once
the socket opens. So the chip now reads `polling` for the first paint of every session until the
WebSocket connects. That is accurate rather than wrong, and worth knowing before someone reports it as
a regression.

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

**Terminals cannot be enumerated through the API, which is why terminal-level questions keep
needing the database.**
Every route is keyed by id -- `GET /terminals/{id}` plus input, output, resize, stop, report-dead and
the two control routes. There is no route that LISTS them. To ask "which terminals exist", "how many
are in `stopping`", or "what did this environment have open", you must already know the ids.

This is not a theoretical tidiness point; it blocked three separate questions in one night:

* The operator's incident -- what stopped two workers in the same second -- ended at "the
  terminal_events rows would say, and there is no way to read them in bulk".
* Bounding the batch-stop reconciler's worst case needed the count of rows in `terminal_sessions`
  with `status='stopping'`. Unreachable, so that risk is recorded as unmeasured in the review dossier.
* Two attempts to approximate it failed for DIFFERENT reasons, which is the tell that the data is
  genuinely not exposed rather than merely awkward: `/api/v1/sessions` carries `terminalStatus` on the
  AGENT-SESSION row (a different table from the reconciler's predicate), and its `terminalId` column
  is empty on all 100 rows right now, so the id-based fallback has nothing to walk.

A `GET /api/v1/terminals` with a status filter would answer all three. It is a new route rather than a
shape change, so it breaks nothing -- but it is still an API addition, and every API decision this
session has been left to the operator. Worth pairing with the `events` opt-in question, since both are
about making terminal state readable without a database client.

**What the dashboard poll actually costs, measured from the browser rather than the source.**
Loaded the live dashboard in an isolated browser context and read its network log -- the first honest
picture of the cycle, after three earlier attempts that used source scanners and were wrong each time.
One poll, 2026-08-26:

| bytes | share | endpoint |
|---|---|---|
| 414,690 | 29.2% | `/spawn-requests` |
| 362,094 | 25.5% | `/messages/recent` |
| 300,154 | 21.1% | `/messages/inbox/dashboard` |
| 113,854 | 8.0% | `/shared` |
| 82,620 | 5.8% | `/sessions` |
| 70,840 | 5.0% | `/dispatch/runs` |
| 63,937 | 4.5% | `/agents` |
| 5,042 | 0.4% | `/environments` |
| 2,400 | 0.2% | `/stats` |
| 1,507 | 0.1% | `/contracts` |
| 1,477 | 0.1% | `/settings` |
| 1,113 | 0.1% | `/channels` |
| **1,419,728** | | **one cycle -- 5.4 MB/min, 325 MB/hour per open tab at the 15s default** |

This re-ranked the work. `/shared`, fixed earlier today, is 8% of the problem. `/spawn-requests` is the
largest single item and had grown 3.5x since it measured 118,424 bytes a few hours earlier, because the
fleet spawns all evening -- a reminder that a payload measured once is a payload measured at one moment.

`/spawn-requests` is now page-gated like `/shared`: its only reader is the Environments table. Together
the two gated slices are 37% of the cycle.

THE MESSAGES PAIR IS THE REMAINING 47%, AND IS NOT A CLIENT FIX. `/messages/recent` is 83% `body`
(296,038 of 358,177 bytes) and already carries a `preview` field beside it. But bodies are genuinely
read: `chat-render.mjs` renders the open conversation from `m.body`, and `chat-select.mjs` searches
across them. Serving previews in the list and bodies for the open conversation means a `fields=`
parameter or a second endpoint -- an API change, and the search behaviour would need somewhere to go.
Worth doing, worth designing first.

Settings, channels and contracts are 1-1.5 KB each. Not worth touching, named here so nobody spends a
round on them.

**Three independent caps bound terminal_events, and one of them is justified by prose about another.**
Corrected 2026-08-26: an earlier version of this entry named two. There are three.

| where | value | what it does |
|---|---|---|
| `api_core/events.py` `_TERMINAL_EVENT_CAP` | 500 | the WRITER's amortised prune, every 200 inserts |
| `reconcilers/terminal_history.py` `keep_events_per_terminal` | 200 | the sweep's retention |
| `routers/terminals.py` | 200 | what the detail endpoint returns |

None references another. The writer's comment justifies its number in prose -- "terminal_events ... is
only ever read back LIMIT ~200" -- which is a claim about a constant in a different module, stated
approximately, so the stale-value gate added in `392a2c99` cannot check it (it matches
`NAME = number`, not "~200").

NO DEFECT TODAY, and that is the finding rather than a complaint. The tightest cap wins, the ordering
is writer(500) > sweep(200) = read(200), and every layer is generous enough for the one below it. The
risk is entirely in movement: raise the read limit past 200 and the sweep silently truncates the
answer; raise the sweep past 500 and the writer does. Recorded so the next person changing one knows
there are two others and that the only thing tying them together is a comment.

The ordering fix in `d2538e26` is what makes this survivable: the endpoint now returns the NEWEST rows
under whatever cap it has, so a mismatch costs history rather than costing the recent events that
explain a death.

**36% of the console's polled payload is an events array nothing reads.**
Measured on the live console fetch `GET /terminals/{id}?cols=100&rows=28`, the request the dashboard
polls while a console is open: 133,878 bytes total, of which the `events` array is 48,116 across 200
rows. The console renderer uses `terminal.snapshot` and never touches `events` -- `xterm-mount.mjs`
reads `data.terminal.snapshot` and `fresh.terminal.snapshot`, and no dashboard or bridge source
mentions the key at all.

The rows are not useless -- they are the only way to ask what a terminal did, and this round used them
to investigate the operator's incident. They are just not what the CONSOLE needs, and it is the console
that fetches this endpoint on a timer.

NOT CHANGED, because it is a response-shape change rather than an internal one. Making `events` opt-in
(`?events=1`) would cut a third off a hot payload, and `test_api_v2_regressions.py` pins the key's
presence today -- that test failing is the point, not an obstacle, but the decision belongs to whoever
can weigh an API consumer outside this repo breaking. The ordering half of the same query WAS fixed,
since which 200 rows come back is not part of the contract.

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

ATTRIBUTED AND PARTLY FIXED, 2026-08-25 (`5c45ab44`). The profile the entry above deferred was taken
against a SYNTHETIC 50-agent database rather than the live one, which turned out to be enough: the
shape reproduces (marginal cost per agent rises with roster size) and the attribution transfers. One
roster call issues 285 SQL statements at 50 agents, and cProfile puts the time in asyncio event-loop
machinery and socket I/O rather than in SQL -- every `await db.execute` is a hop to aiosqlite's worker
thread and back, 5,730 of them across five calls. That is why an indexed primary-key lookup still
costs milliseconds, and it means the number that matters is ROUND-TRIPS, not query plans.

The three repeats, per roster call at 50 agents:

| count | statement |
|---|---|
| 66 | `SELECT environment_id FROM agent_sessions WHERE agent_id = ?` |
| 66 | `SELECT * FROM environments WHERE machine_id = ? ORDER BY last_seen DESC` (a two-row table) |
| 58 | `SELECT * FROM agents WHERE id = ?` (rows the handler already holds) |

Two of the three are now fixed. `_enforce_env_reachable_gate` takes the row its caller already has,
and the roster hands every gate call one request-scoped `environments_by_machine` cache, since that
lookup depends on machine_id alone and a fleet's agents share a host. Per roster call at 50 agents:

| | statements | `SELECT * FROM agents WHERE id = ?` | `SELECT * FROM environments WHERE machine_id = ?` |
|---|---|---|---|
| before | 285 | 58 | 66 |
| after the row fix | 235 | 8 | 66 |
| after the cache | 186 | 8 | 17 |

RETRACTION, and it matters more than the fix. `5c45ab44`'s message quotes a harness median of
43.2 ms -> 28.5 ms at 50 agents. **Do not trust that number, or any wall-clock A/B taken on this
host.** Measured immediately afterwards: the SAME code, five independent builds, produced 44.3, 47.2,
46.4 ms in one batch and 22.4, 22.7, 24.5, 24.0, 24.8 ms in the next. Eleven percent spread inside a
batch and a factor of two across them -- because this machine is running the live fleet, so anything
timed here is timed against whatever the agents happen to be doing. The before/after samples in that
commit were taken minutes apart and the difference between them is indistinguishable from load.

What survives is the count, which is derived from the code rather than the clock and reproduces
exactly: 285 -> 186 round-trips, and each is an event-loop hop to aiosqlite's worker thread. That is
the honest claim. The wall-clock consequence needs an idle host, and the entry above already says the
same thing about the live measurement it started from.

The other two are left. Both want a per-request preload -- resolve every agent's owning environment
once instead of per agent -- which is a real change to how the gate obtains its inputs rather than one
extra parameter, and it should be measured against the live database before it lands. The harness that
took these numbers is worth rebuilding when someone picks it up: build N agents through the real
registration endpoint, then count `aiosqlite.core.Connection.execute` calls per request.

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

**The cross-language constant census: 19 service constants are named from JS, 5 carry a timing
relationship, and two of those were enforced only by a comment.**
Measured 2026-08-25 by scanning `service/**` for named constants and `mcp/stdio` + `service/new_dashboard`
for files that mention them. The vocabulary ones (`AGENT_STATUSES`, `RUNTIME_ALIASES`,
`NOTIFIABLE_EVENTS`, `MODEL_PLACEHOLDERS`, ...) are already bound by the twins census. The interesting
class is the TIMING pairs, where a bridge cadence has to stay inside a service window:

| service constant | bridge side | headroom | gate |
|---|---|---|---|
| `CONSOLE_WORKING_LEASE_SECONDS` 20s | idle re-probe 16s | 1.25x | added `4f47f616` |
| `ACTIVE_RUN_BRIDGE_STALE_SECONDS` 120s | `TURN_BUSY_HEARTBEAT_MS` 30s | 4x | added `9933246b` |
| `TURN_BUSY_BACKSTOP_SECONDS` 30min | hermes-env.mjs | n/a | already `test_turn_busy_delivery_ceiling.py` |
| `TURN_BUSY_STALE_SECONDS` 120s | `REPULSE_MS` 45s | 2.7x | none |
| `MAX_WAIT_S` 25s | server.js long-poll | not measured | none |

NOT GATING THE LAST TWO, and the reason is not laziness. `REPULSE_MS` has 2.7x of headroom and is
`Math.max(5000, env)` -- a floor with no ceiling, so an operator setting
`AIFY_HERMES_TURN_REPULSE_MS=200000` would exceed the 120s window silently. A test can only pin the
DEFAULT, which is already comfortable; it cannot police the override, and pinning the default would
read as protection the operator does not actually have. The honest fix there is a ceiling in the
`Math.max` expression itself, derived from the window, which is a behaviour change to hermes turn
detection and wants an owner. `MAX_WAIT_S` needs its bridge-side counterpart measured before anyone
can say what the relationship even is.

THE SHAPE WORTH REMEMBERING. Both gated pairs were stated correctly in a code comment and enforced by
nothing -- "the re-probe interval must stay BELOW that lease", "a turn longer than that window is
reaped as a dead bridge". Prose on a join is where this repo's defects keep living, and a comment that
states an invariant is a test that has not been written yet. The heartbeat one also needed the literal
moved out of `server.js` first: a constant inside a bridge entrypoint is untestable by construction,
because importing the entrypoint to read it starts a bridge.

**The managed-claude status flap happens on a bridge that HAS both of #224's fixes.**
Reported live 2026-08-25: sc-designer went `working` -> `online` -> `working` mid-task, then
`available` when it finished, then back to `working` on the next message.

RETRACTION FIRST. The previous version of this entry said the running bridge was executing pre-fix
code, on the reasoning that the installed `terminal-runtime.js` was written at 07:43 while the bridge
process started at 04:53. That inference is wrong: an install being newer than the boot proves the
DISK changed, not that any particular fix is missing from the running process. `cf6ef25` is from
2026-06-18 and had been installed for two months.

The instrument for this already exists and I ignored it. Every bridge reports its build sha on
registration, which is what `aify-comms doctor`'s `bridge-current` compares -- it is in the
environment row's `metadata.bridgeBuild`, and reading it settles the question in one call. The live
environment reports **`bridgeBuild=579dd546`** (today, 01:47). `cf6ef25` is an ancestor of it, and
`git show 579dd546:mcp/stdio/terminal-runtime.js` contains `consoleKeepaliveIdleReprobeTicks` three
times. The running bridge has BOTH of #224's fixes.

So the symptom is a residual neither fix covers, and the candidate is measurable. The idle re-probe
fires every `consoleKeepaliveMs` x `consoleKeepaliveIdleReprobeTicks` = 4000 x 4 = **16 s**, against
`CONSOLE_WORKING_LEASE_SECONDS` = **20 s**. That is 1.25x of headroom -- while
`console-working-timing.test.js` holds the PULSE path to two full intervals and justifies it as "the
normal case on a busy host rather than an exceptional one". The re-probe path needs MORE headroom than
the pulse path, not less: a nudge has to reach the PTY, the console has to repaint, and only then does
a pulse POST, all inside the same lease. On a host where wall-clock timing was measured varying by a
factor of two under fleet load, four seconds is thin.

A gate now pins the direction (`re-probe interval < lease`), watched failing at 6 ticks. TIGHTENING it
-- 4 ticks to 2, 16 s to 8 s -- is the obvious fix and is NOT taken here: it is a tuning change to the
live status path, the churn argument in the code comment would need re-checking at the new cadence,
and it wants validating against a real agent by someone who can watch one.

The `available` half is NOT a bug and that part of the previous entry stands. A managed agent whose
worker exits rests cold-startable and keeps reading `available` so the next message starts a fresh
session, pinned by `test_a_managed_worker_rests_COLD_STARTABLE_not_stopped`. sc-designer opened six
sessions between 15:18 and 17:21: six cold starts, not six failures.

**`agent_sessions.process_id` is the environment bridge's pid, not the worker's.**
Measured: pid 206288 appears on 48 sessions across 8 different agents, and its command line is
`server.js --environment-bridge`. `terminal_sessions.process_id` IS the worker's pid -- two columns
with the same name meaning different things, one table apart.

Not fixed, and worth being explicit about why: nothing is broken by it. The safety check in
`terminal_lifecycle.py` that matches a supplied `processId` against the stored one reads the TERMINAL
column, which is the per-worker one; the dashboard never displays the session column at all. It is a
trap for whoever reads the API next, and it caught me for several minutes while answering the report
above -- "one process, six sessions" is a compelling and completely wrong story if you do not check
what the pid names.

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

**A roster takes ceil(N/8) polls to settle after a bulk registration, and the 8 has no rationale
written down.** `LIST_AGENTS_REFRESH_LIMIT = 8` bounds how many expired live-status entries one
`GET /api/v1/agents` recomputes. It is the only constant in its block of `tuning.py` with no comment
saying why, and it is not exposed as a setting.

MEASURED 2026-08-26, 25 identically-registered managed agents on one host, polling the roster
repeatedly with no other activity:

    poll 1: available 8,  online 17
    poll 2: available 16, online 9
    poll 3: available 24, online 1
    poll 4: available 25

Exactly 8 per poll, converging on the 4th. So an operator watching a dashboard after a mass restart
sees a roster that is part-stale for ceil(N/8) polls -- at the default 15s that is about a minute for
25 agents and roughly three minutes for 100. The mix is convergence, not disagreement, and an agent
reading `online` there is a not-yet-recomputed registration value rather than a wrong answer.

NOT CHANGED. Raising the cap trades dashboard settling time for CPU on the hottest read path, and I
have measured the first and not the second, so picking a new number would be choosing the half I can
see. Worth noting that the threading fix in this round makes each refreshed agent cheaper -- two
environment lookups per agent became a shared one per request -- so the trade is better than it was,
which is an argument for re-measuring it, not for guessing.

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

**Two of the four env names that select this service are not declared to the shared registry.** The
bridge resolves its endpoint from `CLAUDE_MCP_SERVER_URL` / `AIFY_SERVER_URL`, and `SERVER_URLS` also
takes `CLAUDE_MCP_FALLBACK_URLS` / `AIFY_SERVER_FALLBACK_URLS`. Only the first pair is exported as
`ENDPOINT_ENV_NAMES` and written into `~/.aify/services.json`, and a runtime's per-server MCP env block
is key-scoped -- so the fallback pair is INHERITED from whatever launched the runtime.

PROVEN, not read: this repo's declaration run through aify-wrapper's own `mcpEntriesFor()` returns the
per-server env block as exactly `["CLAUDE_MCP_SERVER_URL", "AIFY_SERVER_URL"]`, with neither fallback
name in any block.

NOT FIXED, and the reason is the fix itself. `endpointEnv` binds every declared name to the service's
endpoint VALUE, so declaring the fallback pair would set the fallback list to the primary URL --
dedupes to nothing -- while silently overriding the operator's documented opt-in ("Set
AIFY_SERVER_FALLBACK_URLS / CLAUDE_MCP_FALLBACK_URLS to opt into any non-loopback fallback
explicitly"). Nothing in the repo produces those vars: not `install.sh`, not a wrapper template. Their
only use today is an operator setting them by hand, which is exactly what declaring them would break.
One live documented feature traded for one hypothetical is the wrong side of that deal.

WHAT WOULD CHANGE IT: a second registered service. `httpCall` iterates `[ACTIVE_SERVER_URL,
...SERVER_URLS]` and LATCHES `ACTIVE_SERVER_URL` to the first URL that answers, so an inherited
fallback pointing at another service becomes that process's endpoint for the rest of its life. The
comment above `defaultFallbackServerUrls` records this class happening once already -- fallbacks
"silently failed a local bridge over to a developer's shared server".

Gated by `service-carriers-the-registry-does-not-declare.test.js`, which fails when a NEW
service-selecting carrier appears in either resolver and hands whoever added it the trade-off above.
Mutation-proven three ways: a new undeclared carrier, the primary pair re-typed by name, and the CLI
writing an entry from anything other than the shared list each fail their own test by name.

## Open questions this round could not settle

**Why two managed claude workers stopped in the SAME SECOND, 2026-08-25 18:52:55Z.**
Operator-reported: the sc- team looked lost, two runs sat `delivered / reply=awaiting` with reminders
firing, and no agent read `online`. Most of that turned out to be the system behaving correctly. What
does not have an explanation is the timing.

WHAT IS ESTABLISHED, from live rows rather than inference:

* `term_1787683898449_0938b55a` (sc-claude) and `term_1787683959637_a53b46af` (sc-designer) both have
  `stoppedAt = 2026-08-25T18:52:55Z`. To the second.
* Their spawns were created 18:51:38 and 18:52:38 — 77 s and 17 s before that shared instant. Different
  ages, same death. That is one event reaping both, not two workers failing.
* Six cold-starts across the two agents (18:18, 18:31/32, 18:51/52) all settled `failed`.
* The environment bridge did NOT restart: `bridgeId` is still `5fdddb0f-489b-...` and
  `metadata.bridgeBuild` still `579dd546`, the same instance running hours earlier. A superseded bridge
  reaping its managed workers is the usual cause of a simultaneous stop and it is ruled out here.
* The system RECOVERED on its own. sc-designer holds an `attached` terminal created 19:17:51 and reads
  `online`; `available` on the others is cold-startable, not lost.

WHAT WAS RULED OUT, so nobody re-runs it:

* The headless-orphan reaper (`reconcilers/managed_workers.py`) is a CONSEQUENCE, not the cause. It only
  fires when the last non-virtual terminal is ALREADY `stopped` or `failed`; it kills the orphaned
  sidecar afterwards. It cannot stop a live console.
* `cols: 0, rows: 0` on the dead terminal rows is NOT a 0x0 PTY. The healthy live terminal carries the
  same values with `renderedCols/Rows = 100/28`; it is an unset column, not a dimension.
* The delegated spawn does not lose its terminal dimensions. `start()` builds its spec from
  defaulted locals, so `startDelegated` receives 100x28 and never falls through to aify-env's own
  120x30 defaults. The two paths would disagree if it did, and it does not.

A CANDIDATE WITH A MECHANISM, found 2026-08-25 by censusing every writer that stops a terminal.
Eleven functions in the service move a terminal to `stopped` or `failed`. Ten append a terminal event
saying what happened. The eleventh, `_reconcile_stuck_terminal_and_session_rows`, did not -- and it is
the ONLY one that closes terminals with a set-based UPDATE:

    UPDATE terminal_sessions SET status = 'stopped', stopped_at = COALESCE(stopped_at, ?)
    WHERE status = 'stopping' AND datetime(updated_at) < datetime('now', ? || ' seconds')

One statement, any number of rows, every one stamped with the SAME `stopped_at`, recording nothing but
a count in the reconcile summary. That is the exact signature of the incident: two terminals of
different ages sharing a death instant, with nothing terminal-level to read.

It is a candidate, not a conclusion. It only closes rows already in `stopping` past the grace window,
and whether those two were in that state cannot be recovered now. What IS settled is that the one path
capable of a simultaneous multi-terminal stop was the one path that left no trace, which is why the
question was unanswerable rather than merely unanswered. Fixed: each closure now carries a reason on
the row and an event naming the reconciler, so the next occurrence identifies itself.

WHAT WOULD SETTLE IT: the terminal_events rows for those two ids around 18:52:55, which record who
asked. There is no read endpoint for them, so this needs a query against the database rather than the
API. That is the first thing to look at, not another read of the reaper.

**A managed shell can still convert its agent to resident.** The JS `normalizeSessionMode` fails
toward `resident`, so only a literal `sessionMode:"managed"` is refused. Known, reported, awaiting an
operator ruling — untouched here. The Python side is gated by
`test_session_mode_vocabularies_stay_apart.py`; the bridge side is not.

**A default parameter referencing a name its module never declares** parses cleanly and throws on the
first real call. One shipped this round and was caught by `doctor-actually-runs.test.js`. A regex sweep
produced 47 hits across 162 files with the two most credible both false positives, and the behavioural
version — import every module and call every export — is unsafe here, because the bridge exports
heartbeat starters and reapers. No safe precise instrument exists for it today.
