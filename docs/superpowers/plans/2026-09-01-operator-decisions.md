# Operator decisions, 2026-09-01 — answers to the eight open questions

Written so the answers survive a compaction. Each item records WHAT THE OPERATOR DECIDED, what is
still open inside it, and what has actually been done.

---

## 1. Dashboard is reached from ANOTHER MACHINE. Key is to be `banana`, and the dashboard should ASK.

**Decided.** Remote access is real, so binding loopback is off the table permanently — it would cost
the operator their own access. `API_KEY=banana`.

**New requirement:** the dashboard should PROMPT for the key rather than requiring
`<endpoint>/?api_key=<value>` in the URL bar. Type it once, get in.

Today the only browser path is the query-param exchange added 2026-08-30: visiting `/?api_key=…`
trades it for an HttpOnly cookie that Dashboard Next on :8801 also sends (cookies ignore ports).
A prompt is a small page served on 401 that POSTs the key and sets the same cookie.

**NOT DONE, AND DELIBERATELY NOT DONE YET.** Setting `API_KEY` on a live fleet 401s every client that
has not been reinstalled with it — `install.sh --with-api-key` writes it to `.env` AND passes it to
each client it installs, so every machine needs `install.sh` re-run. Doing that while agents are
mid-work repeats today's mistake in a different colour. Sequence it when the fleet is idle: build the
prompt first, then set the key, then reinstall clients, then verify with `aify-comms doctor`.

## 2. ONE shared secret across the three components — NOT per-agent.

**Decided, and it overrules my recommendation.** aify-env, aify-wrapper and aify-comms all know one
secret, set once at install, stored in a file under `~/.aify`. Same value on every side, so they can
authenticate each other.

Most of this EXISTS: the credential carrier (`0534c17e`, `1de5703e`, `54e76284`) already writes a
key through `aify-env credential set` (stdin, never argv), stores it in aify-env's own credential
store, and publishes a value-free `credentialRef` into `~/.aify/services.json`. The residual work is
listed in the roadmap: `credential set --stdin`, the reader wired into advertisement with live
reload, typed states in `/health` and doctor, immediate revoke on 401/403, and an installer rotation
receipt.

**The one thing this does NOT solve, recorded so nobody is surprised later.** A shared secret proves
"you are part of this fleet". It does not prove "you are allowed to act AS agent X". So
`PATCH /agents/{id}/runtime-state` with no route-local claimant check, and
`routers/agents/config.py:146` skipping the liveness query when `current_bridge` is empty, remain
open — any fleet member can still act for any agent. The operator has chosen membership
authentication; per-agent authority is a separate, later question. It is NOT closed by this.

## 3. Spawn runtime validation + queued reap — THINK HARDER FIRST.

**Not decided.** The operator wants the design argued before anything is built. See the analysis
section below.

## 4. The claude session hook should point at the REPO, not the installed copy.

**Decided, and it REVERSES what shipped today** in `e8856126` + aify-wrapper `4dfd39c`, which moved
the hook onto `@@BRIDGE_DIR@@` (the `~/.aify-comms` native copy) along with the bridge, channel and
reaper.

Nothing is live yet — the change only takes effect when `install.sh` is re-run — so reverting is
cheap right now and gets more expensive after the next install. Pros and cons below.

## 5. The terminal write path IS an issue — investigate further.

**Not decided.** The operator's instinct is that ~870x write amplification matters. Analysis below;
my earlier "leave it gated" was too quick.

## 6. `comms_send`'s description: UNSLOP it rather than delete the duplication.

**Decided, and it is better than either option I offered.** I framed it as keep-or-cut 855 B of
reply-contract prose. The third option is to tighten all 2,636 B, which can save more than the cut
would AND keep the contract an agent needs when its skill copy is stale.

## 7. Freeze-ledger launchable literals: proceed as recommended.

**Decided.** Merge the THREE launchability sites (`dispatch_start.py:124`,
`managed_pty_for_dispatch.py:73`, `undeliverable_queued_runs.py:232`) onto `LAUNCHABLE_RUNTIMES`.
Leave the TWO turn-tracking sites (`console_input_queue.py:67` and `:105`) and give them their own
name — they answer "does this runtime track an active turn", which the contract does not model.
Then lower the ledger's frozen count from 5 honestly rather than by decree.

## 8. Scope, and the dashboard/TUI question.

All nine rows are the v0.6.1 roadmap, so yes, this was the planned scope. Rows 1, 2 and 6 were all
worked: row 2 shipped, rows 1 and 6 were measured and are blocked on decisions rather than on effort.
Full table under "Row state" below.

---

# The analysis the sections above point at

## 3. Spawn validation and reap — the recommendation CHANGED after argument

**The original proposal was: validate `runtime` on creation AND reap unclaimable `queued`
spawn_requests, both server-side. The reap half is now recommended AGAINST, and today supplied the
counterexample.**

Between roughly 09:34 and 09:39 on 2026-09-01 this environment had no aify-env, because one was
killed and the operator had not yet started theirs. Spawns queued in that window were waiting for an
environment that was coming back. **A queued-spawn reaper with a short threshold would have destroyed
them**, and would have been right by its own rule and wrong in fact. A reaper cannot distinguish "no
environment can ever serve this runtime" from "the environment is temporarily down", and this repo
generates the second case routinely.

| option | what it catches | what it costs |
|---|---|---|
| validate on creation | a typo, at the door, with an immediate legible error | contract-launchable is not the same as available on THIS host. Validating against what environments ADVERTISE is more correct and would false-refuse during any outage. So validate against the CONTRACT only. |
| standing reaper | eventually everything, including runtimes no environment serves | needs a timing threshold that cannot tell an outage from an impossibility. See above. |
| **bound the claim** | **every cause: typos, dead environments, outages** | **almost nothing** |

**The real defect is `_has_claimable_spawn_request`.** It answers "a bridge is going to spawn this
worker" from a `queued` row of ANY age, and its own docstring states that assumption plainly. An
arbitrarily old queued request is not evidence that anybody is coming, so every dispatch to that
agent sits queued behind a promise nothing is keeping. Bounding that claim by freshness fixes the
stranding for all causes at once, and is the same anchor shape Row 8's ceiling already uses.

**RECOMMENDED: bound the claim; add contract validation at creation as a cheap second net; clean up
existing stuck rows ONCE rather than installing a standing reaper.**

## 4. Hook source — repo versus installed, with the costs named

**REPO** (the operator's preference). Edits take effect with no reinstall, and there is one copy that
cannot drift from the checkout. It costs: the launcher breaks on any machine without the checkout at
that path; the launcher again carries TWO deploy models, which is the exact thing `e8856126` merged
into one; and `aify-comms doctor`'s `bridge-installed` tells you to re-run the installer while the
hook has ALREADY changed, so the check reports a state that is not true.

**INSTALLED** (what shipped today). One deploy model for every launcher line, security fixes flow on
reinstall, and it works with no checkout present. It costs a reinstall every time the hook is edited.

**The load-speed argument does NOT apply to this operator.** The native copy exists because the repo
often sits on a slow 9p/WSL2 bind mount where the bridge takes ~5s to load against ~0.3s native.
This checkout is at `C:\Docker\aify-comms`, native NTFS. So the usual tiebreak is absent here.

**THIRD OPTION, and the one worth taking: default to the installed copy, prefer the repo when it is
present.** One branch, both properties, and doctor stops lying. Awaiting the operator's pick.

## 5. Terminal write path — the operator is right and the earlier verdict was too passive

The stored tail is rewritten WHOLE on every flush: 64KB written for a median real chunk of 75 bytes,
about 870x amplification. At up to ~40 flushes/sec a single busy agent drives on the order of
**2.6 MB/s of SQLite writes for ~15 KB/s of actual output**; five active agents is roughly 13 MB/s.
That lands on a SINGLE writer, behind one global lock, on the same database file every dashboard poll
reads. This repo's `database is locked` 503s came from exactly this kind of pressure, which is why
"leave it gated" understated it.

**A cheap intermediate was under-weighted: LOWER THE 64KB TAIL CAP.** The live screen already serves
rendering; the stored tail is a seed plus a parse source for idle-prompt hints, and those read the END
of the output. If 8KB serves them, that is an 8x cut with no architectural change and no re-sequencing.
**Measure that before the larger change.** The larger change is unchanged: move the two status-path
readers off the stored tail FIRST, then write it lazily.

## 6. `comms_send` — unslop rather than cut

`comms_send`'s description is 2,636 B. The framing offered was keep-or-cut 855 B of reply-contract
prose. **Tightening the whole thing can save more than the cut would while keeping the contract**,
which matters because an agent whose skill copy is stale gets the contract only from here. It is the
most safety-relevant description in the set, so it gets a careful pass, not a quick one.
`tools/list` costs ~7.9k tokens per agent per turn, so bytes here are paid on every turn by every
agent.

---

# Row state, 2026-09-01

| row | state |
|---|---|
| 0 | 73/73 reviewable READ. **38 of 111 are MINE and cannot be closed by me** — the v0.6.0 tag needs an independent reviewer, not more work from me. |
| 1 | Dashboard terminal: MEASURED and GATED, BLOCKED on decision 2. The 870x above, and `/ws`'s Origin check CANNOT authorise terminal input — omitting `Origin` is the documented way bridges connect, proven live (no Origin -> 101, `evil.example` -> 403). Wiring keystrokes there today lets anything reaching :8800 type into an agent's terminal. |
| 2 | Dashboard UI/UX: **DONE AND SHIPPED.** Three dead CSS rules, all failing because a media query adds NO specificity: `#page-files.active` written BELOW its narrow override (Files clipped, while its byte-identical `#page-sessions.active` twin worked purely because it was written above); the `<=760px` `.run-row` single-column layout dead under a later unbounded `<=980px` rule; `.chat-shell.compact` at (0,2,0) beating the (0,1,0) mobile rule, so compact chat scrolled sideways on a phone. Plus triage tiles that announced as buttons, took focus, and did nothing on Enter. Gated by `css-cascade.test.mjs` (a real resolver, not a grep) and `every-role-button-is-keyboard-operable.test.mjs` (population DERIVED from markup). |
| 3 | T1 FIXED and gated. #1 declined (855 B, not ~1,600). #4 recommend-KEEP. #5 JS half gated. **#6/#7/#8/#9 WRITTEN BUT UNCOMMITTED AND UNVERIFIED** — the five suites were killed mid-run. #2 (actor on four lifecycle verbs) and #3 (description-parity gate) NOT STARTED. |
| 4 | F3 closed, F5 re-anchored. OPEN: F4 (ownership auth, now decision 2), F6 (`keyEnv` binds nothing), F7 (the ws call-site test catches bare `(WebSocketDisconnect, Exception)` and never asserts close 1008). |
| 5 | DONE AND SHIPPED (`e8856126` + aify-wrapper `4dfd39c`) — and decision 4 above may REVERSE it. |
| 6 | aify-env TUI: items 1 and 2 were **already done** before this work started; item 1 measured — differential rendering writes **0 bytes on an unchanged frame and 133 on a one-line change, against 4,687 for the old full repaint**, 97-100% saved. Raw mode and `ConsoleSession` in. Item 3 (colour) scoped only; it needs a state-versus-appearance census before any design. |
| 7 | Research only. The feed-in item is ALREADY SATISFIED: no numeric subject-length rule exists in always-loaded prose, and `references/teamwork.md:111` already carries both the rule and its measurement (2,021 subjects over 200 chars, worst 1,834). Implementing it as written would DELETE a measurement. |
| 8 | Both halves fixed, verified live. |

## Still open beyond the eight

- The credential carrier residual (folded into decision 2).
- **v0.6.0 tag** — needs an independent reviewer for the 38 commits that are mine.
- Duplicate session handles — report-only by design; the mechanism producing them is still unknown
  and two traced explanations were disproved against hermes' own source.
- The removal defect: every deletion leaks a process.
- 28 historical commits carrying AI-attribution trailers. Recommendation stands: leave history alone.

---

# Why starting aify-env kills the running fleet

**TWICE ON 2026-09-01, five agents each time, four common to both.** `09:34:15Z` (mine, from
importing `bin/aify-env.mjs` to check its imports resolved) and `14:06:23Z` (the operator's own
deliberate start, 26 seconds after it booted). Operator: "during my aify-env start all agents were
killed again moments ago. 3 of them were midwork i think."

**EVERY STEP BELOW IS CONFIRMED IN aify-env's OWN SOURCE. It is BY DESIGN, not a broken guard.**

1. The new daemon binds and gets `EADDRINUSE` (`bin/aify-env.mjs:383`).
2. It ASKS the holder over `/health` whether it is an aify-env, and if so **STOPS the predecessor**.
   `:384` records the ruling: "TAKE OVER, rather than refuse. Operator ruling: starting the
   environment means this one serves."
3. The predecessor exits. The new instance takes the port.
4. `reapLeftovers()` runs, DELIBERATELY after the port is held (`:436`, guard at `:159-166`).
5. Every `~/.aify/env-processes.json` entry names the predecessor's pid as `owner` -- now dead,
   because step 2 killed it.
6. `planOrphanReap`'s owner guard skips only an owner that is present AND ALIVE, so nothing is
   skipped. The workers are reaped with their children (`:168-174`).

`bin/aify-env.mjs:386-387` states the intent outright: "The predecessor's processes are not abandoned
-- they are in the record, and this instance reaps from it after the port is ours, which is precisely
why the reap moved." `lib/orphan-reap.mjs`'s header carries the requirement it serves: "processes
managed by aify-env die with it."

**DO NOT "FIX" THE OWNER GUARD. It is working.** By the time it runs, the owner really is dead.

**TWO DIFFERENT INCIDENTS -- do not confuse them.** On **2026-08-26** a TEST daemon on `--port 0`
reaped the live fleet, because the old guard was "reap only once you HOLD THE PORT" and an ephemeral
port is always free. **FIXED** by adding `ownerIsAlive` and sealing `AIFY_ENV_PROCESS_RECORD` to a
temp file in both real-aify-env bridge tests. **2026-09-01 is OPEN and different**: a real
supersession on the real port, where the owner is genuinely dead and no guard can catch it.

**DISPROVED, do not re-run it.** The hypothesis that a missing `owner` field disabled the guard is
wrong: all four ledger entries held `owner: 253040`. `/health` reports `owner=None` because it does
not project the field. Read the ledger, never the health projection.

**THE FIX DIRECTION IS ADOPTION, NOT REAPING.** The incoming instance KNOWS it stopped the
predecessor -- it did so itself at step 2 -- so it can rewrite those entries to its own pid and keep
the processes running. Reaping is right only when the owner died on its own. The operator's ruling was
that starting means this one serves; adoption satisfies that without destroying work.
`owned-processes.mjs:98` already reasons about entries "a second instance must not touch".
**NOT IMPLEMENTED, and not to be implemented without the operator saying so.**

**The operator's stated gap, which this creates:** "i cannot see who is running aify-env and is
actually any of the agents midwork", and "i had to start because i needed to see what is going on."
Observing requires starting, and starting destroys what is being observed.

---

# Before disturbing ANY shared infrastructure: check whether anyone is working

**The operator's correction, and it is better than the rule it replaced.** Asking permission every
time puts the burden on them. The right move is to CHECK, and only then ask if the answer is unclear:
"more correct would be to check if anybody is working etc. and when did they last work, when was last
message sent in system".

This read-only probe answers it. It touches nothing and starts nothing.

```bash
docker exec aify-comms-service python -c "
import sqlite3, datetime
c = sqlite3.connect('file:/data/aify.db?mode=ro', uri=True); c.row_factory = sqlite3.Row
now = datetime.datetime.now(datetime.timezone.utc)
def age(ts):
    try: return (now - datetime.datetime.fromisoformat((ts or '').replace('Z','+00:00'))).total_seconds()
    except Exception: return None
busy = c.execute('SELECT agent_id,turn_started_at,turn_updated_at FROM agent_turn_state WHERE turn_busy = 1').fetchall()
live = c.execute(\"SELECT agent_id,status,updated_at FROM terminal_sessions WHERE status IN ('running','active','starting','attached')\").fetchall()
runs = c.execute(\"SELECT target_agent,status,requested_at FROM dispatch_runs WHERE status IN ('queued','claimed','running','delivered')\").fetchall()
print('mid-turn', len(busy), 'live terminals', len(live), 'runs in flight', len(runs))
"
```

**Read it as: any agent mid-turn, or any terminal written to in the last couple of minutes, means
NOT SAFE.** Measured at 17:2x local on 2026-09-01 it returned 3 mid-turn (one 39s into its turn),
4 live terminals with writes 1-73 seconds old, and 46 runs in flight.

**Column names, because guessing them wastes a round:** `agent_turn_state` has `turn_busy`,
`turn_started_at`, `turn_updated_at` and NO `updated_at`; `terminal_sessions` has `created_at`,
`updated_at`, `stopped_at` and NO `started_at`; `messages` has `timestamp`, `from_agent`, `to_agent`.

**KNOWN INSTRUMENT DEFECT:** `messages.timestamp` did NOT parse with the `fromisoformat` helper above
and printed `?`. The last-message age is therefore UNANSWERED by this probe — do not quote it until
the format is checked and the parse fixed. Everything else in the probe was verified against live rows.

---

# Working state at compaction, 2026-09-01

- **aify-comms HEAD `e8856126`, pushed.** SIX files modified and UNCOMMITTED: the Row 3 #6/#7/#8/#9
  description batch (`mcp/stdio/dispatch-tools.mjs`, `mcp/stdio/self-record-tools.mjs`,
  `service/sse/channel_tools.py`, `service/sse/management_tools.py`,
  `service/sse/shared_file_tools.py`) plus the roadmap. **The five suites were killed mid-run, so
  this batch is UNVERIFIED. Run all five before committing it.**
- **aify-wrapper `4dfd39c`, aify-env `f7f6b5f`, both CLEAN and pushed.**
- **The loop is STOPPED.** It was stopped deliberately after the incident and has not been re-armed.
- **aify-env is the OPERATOR'S**, pid 253040, started 17:05:57 local. Not mine, not to be touched.
