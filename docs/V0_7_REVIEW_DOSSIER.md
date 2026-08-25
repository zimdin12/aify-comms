# v0.7 review dossier — the 27 commits of 2026-08-25/26

Written because the review is the bottleneck, not the work. Everything below is committed, pushed and
green on all five suites, and **none of it is running**: the container serves `1a3de61a` (56 commits
behind, measured 2026-08-26) and the environment bridge serves `579dd546`. Nothing here has been read by anyone but its
author.

**13 of the 27 commits touch production code, 3 touch only tests, and 11 are documentation.**
Classified by what each commit's files actually are, not by its subject line — and the two disagree
once: `4f47f616` is titled `fix(status):` and changes no production code at all (a test and a
retraction). A reviewer scanning subjects would budget time for it that it does not need, so read the
file list, not the verb.

The docs commits need reading for accuracy but cannot break anything, and four of them are retractions
of earlier claims in this same series — those are the ones worth reading first if you want to
calibrate how much to trust the rest.

## Deploying is two separate things, and they are not interchangeable

| half | commits | what it needs |
|---|---|---|
| service + dashboard | 10 | container rebuild (`service/**` and `service/new_dashboard/**` are COPY'd into the image) |
| bridge | 4 | `install.sh` to copy `mcp/stdio` into `~/.aify-comms`, **and** a restart of every wrapper — a running bridge executes the copy it loaded at boot |

`225afafc` is in both halves — it changes `terminal-runtime.js` and `terminal_diagnostics.py` together
— which is why the two rows sum to 14 rather than 13. Counted from git rather than from memory: the
first draft of this table said 8 and 5, and was wrong in both cells.

A bridge restart reaps managed workers. That is why `bridge-current` is red on purpose.

## Review order, riskiest first

### 1. The roster read path — `5c45ab44`, `43188723`, `f7d64900`

Three commits, same three files (`registration_gates.py`, `managed_env.py`,
`routers/agents/identity.py`), each removing repeated database round-trips from `GET /api/v1/agents`:
285 → 235 → 186 → 137 per call at 50 agents.

**Why this is top of the list.** It is the hottest read path in the service and it feeds
`_enforce_env_reachable_gate`, which decides whether a managed agent reads `offline`. A wrong answer
here is a status flip on your dashboard, not an exception. The changes are: pass the agent row the
caller already holds; cache the environments-by-machine lookup per request; preload every agent's
session-environment binding in one query.

**What I would check.** The preload in `load_session_environment_by_agent` replaces a per-agent
`ORDER BY last_seen DESC LIMIT 1` with one query plus a Python loop keeping the first row per agent.
`test_session_environment_preload_matches_the_query.py` compares the two against the same data,
including a dead session that is NEWER than every live one — but it is my test of my own change, and
"the map matches the query" is exactly the property I would have assumed if I had not written it down.

**~~Least confident about~~ — CLOSED, `test_the_roster_never_writes_what_it_caches.py`.** The
request-scoped caches are correct only if nothing changes their subject during the request. That
originally rested on a reading of the handler: its write phase runs before the caches are built. A
reading is not a guarantee, and nothing stopped a later edit from moving a write into the per-agent
loop, where one agent's answer would depend on whether it was reached before or after.

Now asserted end-to-end: across the whole roster request, no INSERT, UPDATE or DELETE touches
`environments` or `agent_sessions`. Stronger than "no writes after the cache was built", and easier to
read. Proven by mutation — a write injected into the per-agent loop fails it by name. Two positive
controls guard it, because every assertion in that file is an absence: one that the spy sees the
request at all, one that the gates actually ran, since a request that never consults the caches would
satisfy the write assertion while proving nothing.

### 2. The batch terminal stop — `c8dd39e3`

`_reconcile_stuck_terminal_and_session_rows` closed stuck-`stopping` terminals with one set-based
UPDATE and recorded nothing. It now selects the ids first so each closure carries a reason and an
event. **This changes one statement into three per sweep**, and the sweep runs on the reconcile loop.

**What I would check.** The change converts an O(1)-statement operation into O(N): the old bulk
UPDATE closed any number of rows in one statement, and the new form issues a SELECT, an UPDATE and one
event INSERT per closed terminal. There is no LIMIT, so one sweep drains the whole backlog.

**Still open, and I want to be exact about why.** I tried to bound the worst case and measured the
wrong noun: `/api/v1/sessions` reports `terminalStatus` on the AGENT-SESSION row (0 of 100 in
`stopping` right now), while the reconciler predicates on `terminal_sessions.status`, a different
table with no list endpoint. The proxy is suggestive and is not the population in question, so it does
not settle anything. What is known: the function's own docstring records a PTY stuck `stopping` for 17
days, so the state does accumulate.

If a reviewer wants this bounded without measuring first, the pattern already exists in this repo --
`reconcilers/terminal_history.py` drains in chunks with a `max_chunks` ceiling and lets the periodic
sweep finish the job. A `LIMIT` here would be the same shape. I did not add one, because adding a
ceiling for a backlog nobody has measured is how a tuning constant enters a codebase without a reason
attached to it.

### 3. The delegation seam — `740c7d06`

`EnvClient` short-circuited every 204 to success **before** comparing against its declared expected
status, which made the declaration decorative for the routes that use it — `write()` and `resize()`
both said 200 against a server that answers 204. The short-circuit is gone so `expected` governs.

**Why it matters more than it looks.** Removing it is behaviour-preserving only because every
204-returning route now declares 204. If a route is added that answers 204 and declares something
else, it will now fail where it previously passed silently. That is the point, and it is also the
risk.

### 4. The bridge changes — `9599d802`, `9933246b`, `225afafc`

- **`9599d802`** stops gateway auth tokens reaching the control plane. Seven distinct tokens were
  sitting in stored dispatch-run errors, readable by any agent. Every message builder now redacts.
- **`9933246b`** moves `TURN_BUSY_HEARTBEAT_MS` out of `server.js` so a test can hold it against the
  server's `ACTIVE_RUN_BRIDGE_STALE_SECONDS`. Same value, new home.
- **`225afafc`** also strips never-inherited markers at the aify-env delegation boundary.

**What I would check.** `9599d802` touches five bridge files including `hermes-delivery-loop.mjs`. The
redaction is a pure function and well tested, but the loop is the largest subsystem here and I changed
message construction inside it.

### 5. The dashboard — `f485781c`, `9c83415e`, `5069006c`

- **`f485781c`** — `starting` agents sorted *below* offline and stopped ones in the chat rail, because
  the sort rank was a hand-written map missing that status. Now derived.
- **`9c83415e`** — `/spawn-requests` (414,690 bytes, 29% of a 1.4 MB poll cycle) is fetched only while
  the Environments page is open. **Read the slot handling**: the slice stays in the `allSettled` array
  and resolves to `null`, because `ok(i)`/`val(i)` index it by position.
- **`5069006c`** — the connection chip now says `polling` when the WebSocket is down. **It will read
  `polling` on first paint of every session** until the socket connects; that is accurate, not a
  regression.

### 6. Diagnostics wording — `98f57004`, `d2538e26`

Lowest risk. A dead TUI's last line is no longer reported as its cause of death, and the terminal
detail returns the newest 200 events rather than the oldest. Both came out of your incident.

## The four retractions, if you want to calibrate

`823493a6`, `4f47f616`, `4233d21e` and the timing retraction inside `43188723` each withdraw a claim
made earlier in this same series — a wrong diagnosis of your status flap, a wall-clock measurement
taken on a loaded host, an entry that named two constants where there are three. They are in the
history deliberately. If you read nothing else, these show which kinds of claim in the other commits
deserve the most scepticism: anything timed, and anything inferred from a file's mtime.

## Three things waiting on your decision

1. **`events` opt-in on the terminal detail** — 48,116 of a 133,878-byte response the console never
   reads. A response-shape change, and `test_api_v2_regressions.py` pins the key.
2. **`active_count()` on `/health`** — one line; makes "is any dashboard connected" answerable from
   outside a browser. Pairs with `5069006c`.
3. **The messages list/body split** — `/messages/recent` is 83% `body` (296,038 of 358,177 bytes), and
   the pair is 47% of the poll. Bodies are genuinely read for the open conversation and for search, so
   this needs designing, not trimming.
