# v0.7 review — weak points found, and which are worth doing

Written 2026-08-25 at the close of the review round. Every entry carries the measurement it rests on
and a judgement. **The judgement is the point**: a list of everything wrong is a list nobody acts on.

Fixed items are in git and not repeated here — `git log 1a3de61a..HEAD` has them. This file is what
was found and *not* fixed, plus what was deliberately left alone.

## What actually needs you, ranked

Eight of the entries below are genuine decisions; the rest are recorded judgements that needed no
ruling. This list exists because the file is a thousand lines and a decision buried on line 400 is a
decision nobody makes.

1. **The API and dashboard are unauthenticated and not bound to loopback**, and 16 of 47 agents
   return a live hermes gateway token through `GET /api/v1/agents`. Measured, not inferred: no API
   key in `.env` so the middleware is never installed, listeners on `::` rather than `::1`, and the
   token is LOAD-BEARING for the dashboard console so it cannot simply be redacted. Bind the port,
   set a key, or scope the credential -- only the third fixes the field itself.
2. **A DM survives a transient blip and a channel message does not.** `/channels/{name}/send` has no
   `clientNonce`, and the index that protects the DM path does not cover the channel row's NULL
   `to_agent`. A schema decision, not an edit -- and the honest first move is a counter, since nothing
   measures how often a channel send fails.
3. **Nothing lists terminals.** Every terminal route is keyed by id, which blocked four separate
   questions in this round alone, including "what stopped these two workers in the same second".
4. **48,116 of a 133,878-byte console payload is an events array nothing reads.** A response-shape
   change that an existing regression test pins, so it needs someone who can weigh breaking a
   consumer.
5. **Three independent caps bound `terminal_events`, and one is justified by prose about another.**
6. **`/stats` computes 24 keys in 20 SQL round-trips so one page can show two of them**, and the
   obvious page-gate is blocked by a render gate that reads the same field. See the entry for why it
   was not simply done.
7. **`active_count()` is defined and called by NOTHING.** Verified 2026-08-26: it exists at
   `service/ws.py:25` with zero callers anywhere in the service, and `/health` returns only
   `build, ntfy, status, version`. So "how many dashboards are connected" is unanswerable without
   opening a browser -- which is exactly why I could not size the sequential WebSocket broadcast this
   round. One line on `/health` would give the method its only consumer.
8. **The codex console input is named only by a placeholder**, which typing erases.

Everything else in this file is recorded with a judgement and needs nothing from you.

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

**A superseded bridge could set a turn it was not allowed to clear. SHIPPED 2026-08-26, `c71b0fe4`.**
`/turn-end` has refused a superseded bridge since WS-4a; `/turn-start` never read the body at all, so
the same detector's SET was honoured while its CLEAR was refused -- a one-way ratchet toward
`working`. Its 45s KEEP-FRESH re-post also refreshed `turn_updated_at`, the column the 30-minute
ceiling measures, so the backstop meant to catch a latched turn never fired. Eight tests, three
watched red first, four mutations.

**The roster's environments cache served one of the two phases that needed it. SHIPPED 2026-08-26,
`fab4204c`.** Measured against a claim I had already published: 17 machine lookups per request at 50
agents, 16 of them from the live-state refresh, which runs before the cache was created. 137 -> 121
round-trips per call; the poll cycle 173 -> 157.

**The inbox was a fallback in the code and not one on the wire. SHIPPED 2026-08-26, `106e8e18`.**
`/messages/recent` wins whenever it returns messages, so the inbox response was fetched and discarded
every healthy cycle -- 300,154 bytes uncompressed, 105,673 gzipped. Now a real loader that reports its
own failure, with the positional slot preserved.

**A console that came back without a terminal said nothing. SHIPPED 2026-08-26, `e128cf11`.** aify-env
answers `terminal: true|false` on every spawn so the caller can tell; the flag reached
`startDelegated`, became `pty`, and died one line short of `[terminal attached pid=N]`. The attach
line now names the cause, in the console the operator is already watching.

**Two service-selecting env carriers are read by the bridge and not declared to the registry. GATED
2026-08-26, `9ae4037d`.** Not a fix -- a decision gate. Declaring them would override the operator's
documented fallback opt-in, so the test fails when a NEW carrier appears and hands whoever added it
the trade-off.

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

RE-VERIFIED 2026-08-26 with a widened search, because a scoped one would have missed a reader: the
only `.events` reads anywhere in the dashboard or bridge are `state.inspector.events` and
`run.events`, both DISPATCH-RUN events from a different endpoint. Nothing reads the terminal detail's
array, and the control holds -- `terminal.snapshot` is read in `console-actions.mjs` and
`xterm-mount.mjs`. The byte figures cannot be re-measured today, because nothing lists terminals and
the id from that incident is gone -- which is decision 3 in the list at the top of this file.

The rows are not useless -- they are the only way to ask what a terminal did, and this round used them
to investigate the operator's incident. They are just not what the CONSOLE needs, and it is the console
that fetches this endpoint on a timer.

NOT CHANGED, because it is a response-shape change rather than an internal one. Making `events` opt-in
(`?events=1`) would cut a third off a hot payload, and `test_api_v2_regressions.py` pins the key's
presence today -- that test failing is the point, not an obstacle, but the decision belongs to whoever
can weigh an API consumer outside this repo breaking. The ordering half of the same query WAS fixed,
since which 200 rows come back is not part of the contract.

**LARGELY ADDRESSED, and the timings below rest on a method I later retracted.**
Two corrections to my own entry, in the order they matter:

1. THE NUMBERS ARE UNRELIABLE ON THIS HOST. Everything below is wall-clock against the live service,
   and wall-clock here is dominated by the fleet's own load: the SAME code measured 44-47 ms and then
   22-25 ms minutes later. I retracted a published speed-up for exactly this and moved to counting SQL
   round-trips, which are deterministic and attributable. Read the millisecond figures as "this felt
   slow once", not as a rate.
2. THE COST IS LARGELY GONE, measured the way that survives load. `GET /api/v1/agents` went from 285
   round-trips per call at 50 agents to 97, and it is now FLAT -- 97 at 20 agents and 97 at 50 --
   because the refresh is capped at 8 and every per-agent lookup in the gate loop is batched
   (`5c45ab44`, `43188723`, `f7d64900`, `fab4204c`, `e34de257`, `ea150ba3`). The "scales linearly with
   fleet size" claim below is no longer true of the code.

What remains true and useful is the ruled-out list at the end: the index that already covers
`_has_live_terminal_session` is worth knowing before anyone re-investigates.

The original entry, kept because its ruled-out section is still the useful part:

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

RE-MEASURED 2026-08-26, a day later and on the same running service: 16 of 47 agents in the roster
carry a `?token=` in `runtimeConfig.gatewayUrl`. The same count on a different day makes this a
standing exposure rather than a momentary artefact of whoever happened to be registered.

THE SCOPE IS WIDER THAN "ANY AGENT", and this is the part that needs the operator rather than a
commit. Three facts, each measured on 2026-08-26 rather than inferred:

* `service/main.py` installs its auth only under `if config.api_key:` -- and no `*_API_KEY` line is
  set in `.env`, so `APIKeyMiddleware` is never added. A `GET /api/v1/agents` with no credentials
  succeeds; that is how the counts above were taken.
* The container publishes `"${SERVICE_PORT:-8800}:8800"` with no host-IP prefix, and
  `Get-NetTCPConnection -LocalPort 8800` shows listeners on `::1` AND `::` -- the wildcard, not
  loopback only.
* That endpoint returns the tokens.

So the reachable set is not "registered agents" but "whatever can open port 8800 on this host". What
that resolves to depends on a host firewall this check cannot see, so the honest statement is that
nothing in the SERVICE narrows it.

THE DASHBOARD IS THE SAME SHAPE, checked while there: `service/new_dashboard_app.py` adds only
`GZipMiddleware` -- no auth of any kind -- port 8801 listens on `::` like 8800, and `GET /` returns
200. Its markup carries `data-default-api-port="8800"`, so a browser that reaches it then queries the
API described above. The two together are an operational console over the fleet rather than a
read-only leak.

THE OPERATOR'S CALL, not a code change, and the options differ in cost rather than in difficulty:
bind the published port to 127.0.0.1, set an API key, or give the dashboard console a scoped
credential so the field stops needing to carry a live one. The third is the only one that fixes the
field itself; the first two shrink who can ask.

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

**A DM survives a transient blip and a channel message does not.** `/messages/send` carries a
`clientNonce` (`send-tools.mjs` generates a `randomUUID()`), `isRetriableRequest` gates the retry on
that nonce being present, and the server collapses a retry to the original message by
`(from_agent, client_nonce)`. `/channels/{name}/send` has none of it: no nonce in the body, no
retriable rule, and the route inserts into `messages` without the column.

That asymmetry meets a failure mode this codebase documents in its own poll comment -- "The
single-worker service can transiently drop a request under poll load" -- so the two paths behave
differently on exactly the event both were built to survive. It is not silent: the tool returns
`isError: true` and the agent is told. It is still a message that needed one retry and did not get it.

WHY THIS IS NOT A CHEAP FIX, which is why it sits here rather than being done. Adding a nonce
client-side WITHOUT server support would make a retry double-post, which is worse than the current
failure. Server support means the route accepting the field, short-circuiting on a prior
`(from_agent, client_nonce)`, and carrying the column through BOTH inserts -- `channel_send.py`
writes a channel row with no `to_agent` and then a row per recipient. The uniqueness that protects
the DM path is an index on `(from_agent, client_nonce, to_agent)`, and SQLite treats NULLs as
distinct, so the channel row's NULL `to_agent` is not covered by it. That is a schema decision, not
an edit.

WHAT WOULD SETTLE THE PRIORITY: how often a channel send actually fails. Nothing counts it today --
the error goes to the calling agent and nowhere else -- so the first move is a counter, not a nonce.

**BOTH SHIPPED. The reconcile sweep re-asked per agent two things the roster already batched.** With the
environments cache landed, one sweep costs `45 + 15N` round-trips -- measured exactly at N=5, 20, 25
and 50 (120, 345, 420, 795). It runs every 60 seconds and is UNCAPPED: the roster refreshes at most
`LIST_AGENTS_REFRESH_LIMIT` (8) live states per call, this one passes `limit=None` and recomputes
every agent. So this is the load that grows with the fleet.

THE COEFFICIENT'S NOUN, because it is easy to carry the number somewhere it does not belong: 15N was
measured on uniformly MANAGED CLAUDE agents. The per-agent cost is not one number -- measured
separately on the same fixture, adding agents to an empty roster:

    empty roster sweep       44-45 round-trips (varies by one with fixture state; the
                             four-point fit gives an intercept of 45)
    +20 MANAGED claude        15.1 per agent
    +20 RESIDENT claude       10.2 per agent

The floor is 45 DISTINCT statements with no duplicates -- one per reconciler concern -- so the fixed
half is irreducible without removing a reconciler, not a batching opportunity.

Managed costs about half again what resident does, which follows from the path:
`_managed_owning_environment_row` returns early for a resident agent and the channel-sidecar probe is
managed-only. So a mixed fleet lands between 10N and 15N by its mix, and `45 + 15N` is the
worst-case shape rather than a prediction for the real roster. Count against a representative mix
rather than multiplying.

The uncapped-ness is DELIBERATE and not a defect: this sweep is the backstop that keeps every agent's
status fresh when no dashboard is polling, which is exactly what a cap would break.

Per-agent coefficients, measured at N=20 by counting `aiosqlite` execute() calls:

| multiple | statement | roster precedent |
|---|---|---|
| ~~2.0N~~ DONE | `SELECT environment_id FROM agent_sessions WHERE agent_id = ?` | `f7d64900` -- now preloaded in the sweep too |
| ~~1.0N~~ DONE | `SELECT * FROM agents WHERE id = ?` | `5c45ab44` -- the batch now hands over the row |
| 2.0N | `SELECT last_seen FROM bridge_instances WHERE agent_id = ?` | none |
| 2.0N | `SELECT created_at FROM terminal_sessions WHERE agent_id = ?` | none |

The session binding SHIPPED: the sweep now builds the preload once and threads it, and the roster's
map moved above its refresh phase for the same ordering reason the environments dict did. The sweep's
shape went 44 + 17N -> 45 + 15N -> 46 + 13N, each step trading one fixed query for 2N per-agent ones,
all three models exact at four points. At 50 agents that is 894 -> 696 round-trips per pass.

The agent-row re-read SHIPPED too, and cost nothing: the batch already read every agent to sort by
staleness, so widening that one `SELECT id FROM agents` to `SELECT *` and handing each row over
removes 1.0N without adding a query. The optional parameter falls back for the other caller,
`_compute_agent_status`, whose own row cannot safely be passed through -- `_compute_live_status_cache`
reads seven columns and that row's provenance is not audited.

Final shape: 46 + 12N, against 44 + 17N at the start of the round. At 50 agents a pass is 894 -> 646,
27.7% fewer round-trips; the roster is 105 -> 97 at N=20, exactly its 8-agent refresh cap.

What is left is the 4.0N with no precedent, below.

NOT DONE IN THE SAME COMMIT, deliberately: each is its own threading change through the same three
signatures, and shipping them separately means a regression names which one. The remaining 4.0N
(`bridge_instances`, `terminal_sessions`) have no precedent and are harder than they look. Attributed
at N=6, the sidecar probe's 2.0N is NOT one function asking twice -- it is
`managed_workers.py:296` and `channel_delivery.py:305`, two different reconcilers each asking once per
agent from its own pass. Sharing an answer between them means crossing the leaf-module boundary the
reconcilers were deliberately split along, so it is an architecture question rather than a cache.
(`channel_delivery.py:214` and `dispatch_queue.py:351` each already keep a per-loop `sidecar_cache`,
which is the same idea at the scope where it does not cross that boundary.)

A MEASUREMENT TRAP worth recording with them, because it cost me a wrong number first: calling
`GET /api/v1/agents` to count agents before a sweep REFRESHES live states, so the sweep then skips
them as already fresh and reports 64 round-trips instead of 345. Measure the sweep with the
live-state cache cleared and without touching the roster, or the comparison is against a sweep that
did not run.

**One status derivation asks "is this console booting?" twice for the same agent, and the obvious fix
is already rejected on the record.** Measured at N=6 in a sweep, attributing each call to its true
caller (excluding the file that DEFINES the probe, which is what made my first two attributions point
at the function's own line): `_managed_console_is_booting` runs 2.0N, from
`status_inputs.py:533` inside `_compute_live_status_cache` and `status_decision.py:234` inside
`_decide_effective_status` -- which `_compute_live_status_cache` itself calls at line 398. Same
function, same agent, same question, twice.

IT IS NOT UNCONDITIONAL REDUNDANCY, which is why this is a note rather than a fix. The two probes sit
behind different conditions and coincided because every agent in the fixture was managed with no live
worker and a reachable environment. And `_decide_effective_status`'s own docstring already weighed the
tempting change and refused it: "Hoisting it would make this a pure function of plain values and
trivially testable, and it would also add a database query to EVERY status computation on a hot path."

BOTH OBVIOUS SHAPES CONFLICT WITH A RECORDED DESIGN DECISION, which is why this is left alone rather
than merely deferred:

* HOISTING is refused by `_decide_effective_status`'s own docstring -- it "would add a database query
  to EVERY status computation on a hot path". The probe is on a late branch precisely so it does not.
* A LAZY MEMO carried in `StatusFacts` is refused by that class's docstring: it is FROZEN and holds
  "facts about a moment, already read from the database by the caller". A deferred read is not a fact
  already read, and putting one there would let the decision trigger its own query -- "folding them in
  would make a frozen container a lie" is the argument the class already makes about a different
  member.

So the remaining shape is a memo threaded as a separate parameter alongside the three IN/OUT
accumulators, on the hot path that serves every status, for 1.0N -- about 50 round-trips per sweep at
50 agents, 7.7% of the post-fix pass. That is a design addition rather than a threading change, and it
is not worth it at that price. Recorded so the next person weighing it starts from the two refusals
rather than rediscovering them.

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

**/stats computes 24 keys in 20 SQL round-trips so one page can show two numbers.** Measured
2026-08-26 against the running service and by counting `aiosqlite` calls:

| | |
|---|---|
| keys returned | 24 |
| SQL round-trips per call | 20 (12.7% of a 157-round-trip poll cycle) |
| response size | 2,400 bytes -- so this is a ROUND-TRIP cost, not a bandwidth one |
| keys any consumer reads | 2 (`dispatch_runs_by_status`, `run_failures_24h`) |
| where they are read | `summary-tiles.mjs` `renderDiagnosticsSummary`, which writes `#diagnostics-summary` |
| where that element lives | inside `<section id="page-diagnostics">` -- one page |

So the same shape as `/spawn-requests`, which this round page-gated. **It was NOT gated, and the
reason is a coupling that the spawn-requests slice did not have.** `app.js:107` uses
`state.stats.dispatch_runs_by_status !== undefined` as a RENDER GATE for the runs section, so a
page-gated `/stats` would leave `state.stats` empty off the Diagnostics page and change whether that
section renders -- a behaviour change wearing the clothes of an optimisation. Gating it needs that
gate rewritten first, which is a different change with a different risk.

Two smaller things found in the same trace, both left alone:

- `app.js:346` passes the WHOLE `state.stats` object as the render-memo signature for
  `renderMetrics`, which reads `state.agents` and `state.contracts` and none of `state.stats`. Any of
  the 24 counters moving re-renders tiles that cannot have changed because of it.
- Only 2 of the 24 keys have a reader anywhere in the dashboard. The other 22 are a public API
  surface (`/api/v1/stats` is advertised in `meta.py`), so trimming them is a breaking change and an
  operator decision rather than a cleanup.

**`updateStaticLinks()` cannot do anything, and its test manufactures the element that would let
it.** The whole body is `const legacy = byId('legacy-dashboard-link'); if (legacy) legacy.href = ...`.
That id appears NOWHERE in any HTML in the repo -- only in the lookup itself, in the pre-extraction
fixture, and in `static-links.test.mjs`, which builds a stub element with that id and then asserts the
href was set. The function is called at boot from `boot-wiring.mjs:371` and has been a no-op since the
markup lost the link.

It is obsolete rather than merely unwired: `/api/v1/dashboard` is now a RedirectResponse
(`meta.py:61`, live check returns 307), so the link's destination bounces to the page the link would
have been sitting on.

Found by censusing every id the dashboard's JS looks up against every id that exists -- 110 lookups,
103 static ids, 22 built at runtime, three unaccounted, two of those false positives (one element is
created in JS, one id is built as `set-${key}`).

NOT REMOVED. Deleting it means touching `extraction-proof.test.mjs`, whose plan declares
`updateStaticLinks` with a marker comment in app.js; that gate is built for MOVES, and a deletion is a
different kind of edit to teach it. Four dead lines are not worth reopening a byte-identity gate for
without a reason to be in there anyway.

The part worth carrying forward is not the dead function, it is the test: it fabricates its own
subject, so it proves the function works on an element that production does not have. Same shape as
the interrupt attribution this round already retracted: six green tests, all of them exercising the
pure builder rather than the call site that never ran.

**A live bridge's turn-start is still attributed to `user-prompt-submit`, which keeps the row out of
the dead-bridge sweeper.** `/turn-start` hardcodes `turn_bridge_id = 'user-prompt-submit'` for every
caller, and `_clear_turn_busy_for_dead_bridges` skips `('', 'user-prompt-submit')` ON PURPOSE
(`dispatch_lifecycle.py:219`, and `claim_gating.py:288-292` explains why): a hook-driven turn has no
owning bridge whose liveness could be tested, so there is nothing for that sweeper to check.

The three sibling paths all use the real id. `/turn-end` guards on it, and the heartbeat path records
it when it sets (`turn_busy_signal.py:62`) and guards ownership when it clears (`:83`). `/turn-start`
alone treats every caller as the hook -- including the claude, codex and hermes detectors, which each
send `bridgeId`, `turnRuntime` and `source` on every post.

CONSEQUENCE, stated against the reader rather than in the abstract: if the bridge that started a turn
dies, that turn is not cleared by the sweeper built for exactly that case. It waits for the 30-minute
`TURN_BUSY_BACKSTOP_SECONDS` ceiling instead.

NOT FIXED IN THIS ROUND. Recording the real id changes which rows a reaper touches, and that reaper
kills in-flight state -- a different blast radius from refusing a stale write, which is what the
supersession guard shipped alongside this note does. It also interacts with the carve-outs in
`bridge_registration.py` (complementary sidecar/wrapper-child pairs) in ways worth measuring before
changing. The two halves were found together and are deliberately not shipped together.

`turnRuntime` and `source` are discarded on the same path. `turnRuntime` is harmless today -- the
handler derives runtime from the agent row and the two agree -- but `source` is the only thing that
could ever distinguish the three detectors from the hook in a stored record, and nothing keeps it.

**Two of the four declared status-event kinds have no emitter, and the endpoint that could emit them
is called only by tests.** `status_engine.EVENT_KINDS` declares
`("turn_start", "turn_end", "blocked", "unblocked")`. Censused across the repo: every
`_apply_status_event` caller emits `turn_start` or `turn_end` and nothing else, and
`POST /agents/{id}/status-event` appears in `service/tests/` and nowhere in the bridge or the service.

This is unused capability rather than a defect, and worth knowing precisely because the obvious worry
is wrong: `blocked` IS reachable. It comes from `status_inputs.py:127`, where `awaiting_input` is
`awaiting_stored or (in_turn and _agent_awaiting_input(...))` -- a terminal-text hint -- so the status
the dashboard renders has a live producer that is not the event.

One consistency gap goes with it: `AgentStatusEventRequest.kind` is a free `str`, so an unrecognised
kind is folded by `apply_event` into no change while still being recorded as `last_event`. That is the
same shape this repo already closed for terminal status in `75ea52dc` (undeclared statuses refused
rather than passed through). Low value while the endpoint has no production caller, which is why it is
recorded rather than done.

**A detector turn-start now reads `bridge_instances` twice in one request.** Measured at steady state:
a hook post (no body) issues 11 SQL round-trips including ONE
`SELECT superseded_by FROM bridge_instances`, and a detector post issues 12 including TWO -- the
supersession guard's own lookup plus one the status derivation was already doing. The guard's cost is
that single indexed primary-key read, once per 45s per working resident agent, which is why it shipped
as-is. Collapsing the pair would mean threading a value across two phases of the handler for one
indexed read, and the phases have different owners.

**The poll-cycle byte numbers in this round's commits are UNCOMPRESSED, and the running build is why.**
Measured 2026-08-26 against the live service, which reports build `1a3de61a`: a response to
`/messages/recent?limit=80` comes back with no `content-encoding` header at all, because the gzip
commit (`13255b62`) postdates the running container by design -- nothing in this round is deployed.

So "300,154 bytes" for the inbox slice is a true statement about the service as it runs today and a
misleading one about the service after a rebuild. Both figures, so a later reader can tell which they
are holding:

| | ten slices | after the two gates | saved per cycle |
|---|---|---|---|
| as running (no gzip) | 1,305,436 B | 590,174 B | 715,262 B (54%) |
| gzipped at -6, modelling post-deploy | 294,403 B | 148,489 B | 145,914 B (49%) |

The inbox slice alone is 300,154 B uncompressed and 105,673 B gzipped. The PROPORTION barely moves --
54% against 49% -- which is the part that survives the deploy, and the absolute byte figure is the
part that does not.

What compression does not touch at all: the SQL round-trips and the JSON serialisation behind each
slice. `/stats` is 2,400 B uncompressed and 983 B gzipped, and 20 SQL round-trips either way.

**The console capability gate asks the BRIDGE whether it has a PTY, and under Phase 8 the PTY is
opened by aify-env.** The environment heartbeat advertises `terminal` and `pty` from
`bridgeTerminalSupported()`, which is `!!require("node-pty")` inside the bridge process
(`terminal-runtime.js`). `console_capability_gate.py` reads those two fields and refuses console work
with a message that names the cause: node-pty is not installed or built "for that bridge".

Delegation moved the work and the capability check stayed where it was. aify-env runs its own
independent `terminalSupport()` on its own node-pty, so the two tiers can disagree, and both
directions are wrong:

* bridge HAS node-pty, aify-env does not -> the gate allows it, aify-env falls back to pipes, and the
  operator gets a console that renders no TUI with nothing saying why.
* bridge LACKS node-pty, aify-env has it -> the gate refuses a console that would have worked.

THE CORRECT ANSWER IS PUBLISHED AND UNREAD, in two places. aify-env's `/health` returns
`terminals: {available, reason}` with a comment that is the whole argument: "Stated rather than
inferred. A consumer that has to work out whether it got a terminal from output that looks slightly
wrong is a consumer that will get it wrong." And every spawn response carries `terminal: true|false`.
`EnvClient.health()` exists in this repo and has ZERO callers.

NOT FIXED. Making the heartbeat advertise the delegated tier's capability means calling aify-env on a
path that runs constantly, so it needs a caching policy and an answer for "aify-env is unreachable" --
which is a different fact from "aify-env has no PTY", and collapsing them is how this class of bug
started. That is a design decision, not a repair.

WHAT DID SHIP is the cheap half: the attach line now states when a console came back without a
terminal, so the degradation is legible at the moment it happens even while the gate upstream is still
asking the wrong tier. It reports what actually occurred rather than what was predicted, which is the
half that cannot be wrong.

**The hook detector refuses to guess `~/.hermes`, and its only caller hands it that guess.**
`scripts/hook-installed.sh` exits 2 rather than defaulting hermes' config root, and says why: "a guess
here answers 'no hook' for the one client whose path is not derivable... Unresolved is unanswerable,
and unanswerable is not 'no'." `install.sh` resolves the root first and passes it -- and
`hermes_config_root()` ends with `printf '%s' "$HOME/.hermes"`, so it always returns something.

Each component is right on its own. Composed, the property is lost: the detector's exit 2 is
unreachable from install.sh, and a hermes host whose real root is elsewhere gets a confident "no hook"
derived from a path nobody checked. `install.sh:2831` would fold exit 2 into `_hook_present=false`
anyway, so the distinction has no consumer even if it fired.

MEASURED ON THIS HOST, which is why it is recorded rather than fixed: `HERMES_HOME` is set to
`AppData\Local\hermes`, so `hermes_config_root` takes its first branch and resolves correctly --
the guess is never reached. Both `~/.hermes/config.yaml` and the AppData config exist, and NEITHER
contains the `notify-check` marker, so there is no hermes hook here to preserve or lose either way.

The reachable window is narrow: hermes installed, `HERMES_HOME` unset, `hermes config path` failing,
AND a hook registered somewhere other than `~/.hermes`. Editing a 2,978-line installer to close that
is not the trade, and the detector's own tests already pin the behaviour it is responsible for
(`test_hermes_refuses_to_guess_because_its_root_is_not_derivable`).

Worth knowing as a SHAPE more than as a bug: a guard that refuses to guess is only as good as its
callers' willingness not to guess for it.

**The installed bridge copy has CRLF where the repo has LF, and a future content check would call
that a difference.** Comparing `~/.aify-comms/mcp/stdio/*` against the checkout on 2026-08-26: 16
files differ, 15 of which git also reports as changed since the install marker
(`.aify-version`, sha `1a3de61a`). The sixteenth is `aify-service-endpoint.mjs`, and
`diff --strip-trailing-cr` shows its content is IDENTICAL -- 12,202 installed bytes against 11,959 in
the repo, the difference being 243 carriage returns.

Harmless today: `bridge-installed` compares the marker sha, not content, so nothing looks. It matters
because `doctor-predicates.js` argues content comparison is "strictly stronger than the bridge check"
where it does use one for skills, which is an open invitation to do the same for the bridge. Whoever
takes it must strip CR first or the check reports a permanently-stale file that is not stale --
crying wolf on the one instrument whose whole job is to be believed.

Checked while there: all 17 installed skill files are byte-identical to the checkout, so the skills
check is accurate here and this is a bridge-only artefact.

**Why a run sits at "delivered / reply expected" with a fixed number of reminders and then goes
quiet.** The operator asked this about two sc- runs. Reminders are bounded by AGE, not by count:
`_run_contract_reminders_once` filters with
`AND datetime(r.requested_at) >= datetime('now', -contract_stale_hours)` (default 24), so a run stops
being reminded once it is a day old. Nothing counts reminders and gives up, and nothing closes the run.

So the state the operator saw is the contract behaving as specified and simply not being fulfilled:
the reminders stopped because the run aged out, and the run stays open because a reply never arrived.
Not a defect -- but "reminders stopped" and "we gave up on this" look identical from the dashboard,
and only the first is true.

NOT CHANGED. Whether an unfulfilled reply contract should auto-close, or say "no longer reminding"
rather than "reply expected", is a policy question for the operator rather than a repair. The cheap
half -- distinguishing "still chasing" from "aged out" in what the dashboard shows -- would need the
age comparison the sweep already does, and belongs with whoever decides what the second state should
be called.

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

## Audited and found sound, so nobody re-walks them

Negative results, listed once so a reviewer knows where the evidence already is. Each was checked by
reading the producer AND the consumer, or by constructing the case.

| join | how it was checked | result |
|---|---|---|
| dispatch claim | 5 bridge fields vs `DispatchClaimRequest` | all declared; `bridgeKind` sent by both named sidecars |
| terminal output / report-dead | payloads vs models, and `req.reason` traced to its write | every field consumed |
| spawn-request claim + 3 PATCHes | payloads vs `SpawnRequestClaim` / `SpawnRequestUpdate` | all declared; `capabilities` and `telemetry` read in `running_spawn.py` |
| terminal-control claim + update | payloads vs models, readers traced across modules | all five consumed; `terminalStatus` via `terminal_control_status.py` |
| aify-env expected-status contract | all six `EnvClient` declarations vs aify-env's routes | all six agree; `subscribeOutput` validates its response |
| realtime dispositions | server broadcast names vs client handling | fails OPEN to `refresh`; gated by a producer-derived test |
| skill tool names | 36 in the skill vs 36 registered | exact match, and already gated |
| route wiring | 44 route-declaring modules vs their aggregators | all reachable; `/channels/{n}/send` confirmed live |
| sweep step ordering | recovery-before-reaping pairs | holds, and `test_reconcile_sweep_ordering.py` gates each pair with its incident |
| send preflight | constructed both `misconfigured` paths and ran it | both refused, by `_agent_execution_mode` and the channel gate |
| terminal `cols = 0` | both readers | handled deliberately (`or 100`, and inference) |
| dashboard markup | labels, duplicate ids, dead lookups, dead data-attrs, nav/page/title, focus, empty states | one dead function found (recorded); the rest clean |
| form buttons | every `<button>` inside the 4 `<form>`s | all 5 declare a `type`, so none implicitly submits |
| stale capabilities | the recorded deadlock's remedy | present: one-time backfill (`db.py:263`) plus read-time correction in `_row_capabilities` |
| agent deletion | all 13 `agent_id` tables vs cascades, explicit deletes and the handler | nothing orphans -- see below |
| runtime adapters | all 5 adapters vs the 8 base members that throw unless overridden | all 5 implement the 6 JS-side ones; `wrapperName`/`consoleCommand` are deliberately server-side ("owned by the Python adapter package") and NO JS caller reaches those stubs |
| MCP tool surface | all 36 tools: schema keys vs what the handler reads | none reads an undeclared name; three registration patterns, and `from` is INJECTED from process identity rather than declared, so a caller cannot spoof the actor |
| lexical timestamps | every producer and every comparison in `service/` | correct BY DESIGN -- see below |
| dead schema state | every column of all 25 tables, write-shaped vs read-shaped references | exactly ONE unread column, in `agent_live_state` -- the table CLAUDE.md already calls vestigial, and that claim is accurate: its only real references are the CREATE, its index and a comment (everything else is a FUNCTION whose name contains the phrase) |
| share / unshare | actor, ownership, idempotence, file unlink order | mandatory actor fails closed; file unlinked BEFORE the row, so a failed unlink is retryable |
| channel join / leave | both handlers | symmetric on membership; historical unread is kept, which is defensible |
| paginated limits | all 16 numeric query params in `service/routers/`, and each unclamped one traced to its consumer | 12 clamped at the route by `Query(..., ge=, le=)`; the other four are clamped where they are USED (`lines` at `max(1, min(int(lines or 40), 200))`, `cols`/`rows` by the snapshot view's `max(20, min(..., 500))`) or harmless (`offset`, which SQLite floors at 0) |

**Timestamps are compared LEXICALLY in SQL on purpose, and that is safe here.** It looks like the
classic bug, and this repo has paid for that class before, so it is worth writing down rather than
re-investigating. `service/clock.py` states the contract: "UTC, second resolution, `Z`-suffixed -- the
format every timestamp column in this service stores and every comparison assumes. Changing it is a
data migration, not a formatting choice."

Measured across `service/**` (non-test): 21 comparisons wrapped in `datetime()`, 6 bare textual ones,
and every one of the 9 timestamp-producing `strftime` calls uses the identical
`%Y-%m-%dT%H:%M:%SZ` -- including `_iso_from_ms`. So the bare six are correct, because there is only
one shape to compare.

The drift risk is a SECOND producer, which is the shape that found six defects in the install audit.
There are exactly two `isoformat()` calls and neither reaches a column: `_timestamp_sort_key` is an
in-memory ordering key whose own docstring says it "is not a trust boundary" and points decisions at
`_parsed_timestamp`, and the other is a COMMENT in `reconcilers/terminal_controls.py` warning that
"isoformat() adds sub-second" precision, beside a line that uses the canonical `strftime` instead.

MY FIRST SCAN OF THIS WAS WRONG and its control caught it: the guarded-comparison counter read ZERO,
which is impossible in a codebase that uses `datetime()` 21 times. The regex could never match a
column sitting INSIDE `datetime(...)`. A control that cannot fire reports a clean sweep exactly like
a real one.

**Agent deletion, in full, because a direct-FK census gets it wrong.** Seven tables cascade from
`agents(id)`. `channel_members` and `bridge_instances` are deleted explicitly by
`_remove_agent_record`. `terminal_sessions` has an `agent_id` column with NO foreign key and still
cannot orphan: it cascades TRANSITIVELY through `session_id -> agent_sessions(id) -> agents(id)`, and
`session_id` is `NOT NULL`, so the chain always fires -- which is what `unregister_agent`'s own
comment says ("cascades agents -> agent_sessions -> terminal_sessions -> terminal_controls").
`agent_tombstones` is retained on purpose. What genuinely survives is history: `read_receipts` (which
cascade from `messages`, not agents) and `spawn_specs` / `spawn_requests`, none of which can
resurrect an agent because every consumer joins `agents`.

SIX of my own leads died here rather than in a commit, which is the number worth carrying: a
scoped grep that missed a reader one delegation away, a stack attribution that returned the helper's
own frame, a "hand-written duplicate" that was two different data shapes sharing a name, a "gap"
between two status lists that is a documented distinction, and a `misconfigured` status the preflight
refuses through a different gate. The instrument was wrong every time, never the code. The sixth was this one: a census that looked only for a DIRECT foreign key and called four tables orphans, when the cascade it needed runs through a second table.

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
