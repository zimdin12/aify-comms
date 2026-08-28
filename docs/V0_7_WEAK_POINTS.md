# v0.7 review — weak points found, and which are worth doing

Written 2026-08-25 at the close of the review round. Every entry carries the measurement it rests on
and a judgement. **The judgement is the point**: a list of everything wrong is a list nobody acts on.

Fixed items are in git and not repeated here — `git log 1a3de61a..HEAD` has them. This file is what
was found and *not* fixed, plus what was deliberately left alone.

## What actually needs you, ranked

NINETEEN genuine decisions; everything else in the file is a recorded judgement that needed no ruling.
This list exists because the file is 2,038 lines and a decision buried on line 900 is a decision nobody
makes -- and because the list itself proved the point: it stood at eight for a full day of rounds while
six more decisions were being written below it.

1. **The API and dashboard are unauthenticated and not bound to loopback**, and 16 of 47 agents
   return a live hermes gateway token through `GET /api/v1/agents`. Measured, not inferred: no API
   key in `.env` so the middleware is never installed, listeners on `::` rather than `::1`, and the
   token is LOAD-BEARING for the dashboard console so it cannot simply be redacted. Bind the port,
   set a key, or scope the credential -- only the third fixes the field itself.

   **RE-MEASURED 2026-08-26 evening, and the number is unchanged for a reason worth knowing.**
   `9599d802` stopped storing gateway auth tokens in the control plane, and it worked: the standalone
   token field is gone, and the 13 agents carrying anything named `*token*` now carry only
   `gatewayTokenEnv`, which is an environment-variable NAME and not a credential. But 16 of 47 agents
   still embed `ws://...?token=<live token>` inside `gatewayUrl`, and `GET /api/v1/agents` is
   unauthenticated. The credential moved out of its own field and stayed in the URL beside it.

   MEASURE IT WITH A RECURSIVE WALK. `gatewayUrl` is NESTED, so a scan over each row's top-level keys
   returns a clean zero and reads exactly like a fix. That zero was produced during this
   re-measurement and believed for a minute; the walk over every string value in the payload, with a
   positive and a negative control in the same run, is what corrected it.

   That is worth stating plainly because a reader glancing at `9599d802` would reasonably conclude the
   exposure was closed. Half of it was. The remaining half is the load-bearing half -- the dashboard
   turns that ws URL into the console's "Open in new tab" target -- so it is still the same decision,
   with the same three options, and no smaller than it was.
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

   It is larger than one method. `WSManager` has a whole agent-addressed half -- `online_agents()`,
   `notify_agent()`, and the `?agent_id=` parameter on `/ws` -- and **nothing in this repo connects
   that way**. Measured 2026-08-26 by widening past the bridge to every `/ws` reference repo-wide:
   every client is the dashboard's own `new WebSocket(`${wsOrigin}/ws`)`, with no agent id, and the
   only connection that supplies one is `test_main_websocket_auth.py`. So the two events sent through
   `notify_agent` -- `new_message` from `channel_send.py:218` and `messages.py:274`, `dispatch_request`
   from `dispatch.py:233` -- are delivered to an audience that is empty. **The decision is whether
   `/ws?agent_id=` is an intended external-client API or dead weight**, and it is yours because
   deleting a public endpoint on my own judgement is not a bughunt finding. Nothing is broken either
   way: an event nobody receives costs one dictionary lookup.
8. **The codex console input is named only by a placeholder**, which typing erases.

ADDED 2026-08-26, in the rounds after this list was first written. Each is recorded in full further
down; these are the one-line versions, because the front of the file is the only part anyone reads.

9. **A terminal control writes `terminal_sessions.status` WITHOUT passing the allowlist.**
   `_terminal_status_transition` refuses any status outside `TERMINAL_SESSION_STATUSES` and exists for
   that reason since 2026-08-16; the two UPDATEs in `_apply_terminal_status_from_control` bypass it
   entirely. Routing them through would ALSO start refusing writes the monotonic guard rejects, which
   changes live behaviour on a control path -- a decision, not a repair. This is the `lost` incident's
   shape: an unrecognised status is invisible to every `WHERE status IN (...)` sweep.
10. **`agent_sessions.spawn_spec_id` and `spawn_request_id` are FOREIGN KEYs whose DEFAULT is `''`.**
   NULL is exempt from a foreign key; `''` is not, and no row has id `''` -- so an insert that OMITS
   them takes a default guaranteed to violate the constraint, and SQLite names no column in the error.
   All three production writers name them, so nothing is broken; the question is whether to migrate
   the defaults to NULL. It cost me two debugging cycles inside one session.
11. **`/messages/recent?limit=80` is 294,226 bytes -- 66% of every refresh cycle** -- fetched on every
   page at a default 15s interval, roughly 1.18 MB per minute per open tab. It cannot simply be
   page-gated: `buildHandoffPacket` reads the same store for message BODIES and is reached from an
   agent action, so gating it would produce an EMPTY packet with no error. The safe version makes that
   function fetch on demand, which means making an operator-facing action async.
12. **Five attribution columns a rename leaves pointing at a tombstoned name.** `requested_by` (three
   tables), `handled_by` and `removed_by` all demonstrably store agent ids and none is repointed. They
   sit in the rename gate's `UNRESOLVED` bucket, and the argument runs both ways: an audit trail should
   say who acted at the time, but every OTHER reference to that identity moves in one transaction.
   `removed_by` sometimes holds the literal `"api"`, so a repoint has to tolerate a non-agent value.
13. **`HermesSingleShotController` is imported by nothing.** No mode routes to it and no replacement
   exists; its only reference outside its own file was a comment claiming it was wired, now corrected.
   Delete the module, or wire the mode it was extracted for. Cheap either way -- it is here because
   deleting product code is not mine to do.
14. **`agent_sessions.status` and `dispatch_runs.status` have no vocabulary gate**, while
   `terminal_sessions` and `environments` both do, leaving three f-string status literals across three
   files unguarded. Preventive rather than urgent: building two more gates is a larger commitment than
   repairing a scan, and the agents gate deliberately scopes itself out of judging other tables.

15. **FIXED 2026-08-26** (aify-env `0043275`, aify-comms `963352db`), and the write-up below stands
   because it records the reasoning. **One correction, measured after the fix**: on WINDOWS an
   external kill reports `(1, null)`, not a null code, so `1 ?? 0` left it as 1 and the coercion never
   destroyed the case this fleet actually suffers. It destroyed every NULL code -- a signalled death on
   POSIX, and a kill aify-env issues itself anywhere. The repair is still right and still worth having;
   the claim that it explains these particular drops was mine and was too strong. **Not deployed**:
   aify-env must restart, which reaps the live fleet.

   The original entry: **a signal-killed DELEGATED terminal is recorded as "exited with code 0".** aify-env drops the
   signal at `runner.mjs:284` (Node's `close` event is `(code, signal)`) and coerces the resulting null
   code to 0 at `runner.mjs:185`, so the exit frame carries a manufactured clean exit and no signal.
   Every hop on the aify-comms side is correct and none of it helps. The fix is small on both sides but
   changes the exit frame's shape, which is a wire contract between two repos on a LIVE tier. Until it
   is made, the exit code this review added is trustworthy for a local pty and only for a
   self-terminating delegated process -- and sc-architect, the death that prompted the feature, was
   delegated.

16. **The dashboard still cannot tell you why a console died, and it is where you looked first.** The
   service records the exit code and signal, and as of this round every terminal payload carries them.
   The Console tab of a dead session does not read a terminal payload: it renders from the SESSION
   row, which says `This session is stopped -- no live console` and nothing else. `agent_sessions`
   has `terminal_status` but no exit columns, so closing this means either joining `terminal_sessions`
   into the sessions query -- a per-request join for a field read only when something died -- or
   having the dead-console branch fetch `GET /agents/{id}/console`, which already returns the cause,
   the exit phrase and which store answered. The second is cheaper and needs no schema change, and it
   puts one extra request on a branch that only renders for a dead session. It is a UI shape call, so
   it is yours: today the answer exists and only an MCP tool shows it.

17. **Stopping ONE ConPTY can kill every other agent's worker on the host, and the fix shipped today
   only removes the automatic trigger.** node-pty 1.1.0, `lib/windowsPtyAgent.js:133-150`: under
   ConPTY, `kill()` forks `conpty_console_list_agent`, calls `GetConsoleProcessList`, and then
   `consoleProcessList.forEach(pid => process.kill(pid))` -- it kills every pid attached to that
   console, not only the child being stopped. aify-env's `stop()` calls `child.kill()`, so any stop can
   take siblings with it.

   MEASURED LIVE on 2026-08-26: seven managed workers died in two clusters, 10:12:51-56 and
   10:26:00-01, with lifetimes from 4m41s to 10m12s -- so not an age limit, everything alive at once.
   Each cluster opens with one process ending on its own, which is exactly when the reaper finds a dead
   entry and calls stop(). aify-env's own process survived both, which is what a console-group kill
   looks like from outside.

   `2bac2c7` stops the REAPER doing it: a pid the sweep has just proved dead is now released rather
   than stopped, and releasing kills nothing. That removes the unattended trigger. An EXPLICIT stop --
   a dashboard Stop, a teardown -- still goes through `child.kill()` and still carries the exposure.

   TWO CANDIDATE FIXES, and both are yours because both change how a live host stops a terminal.
   Pass `useConptyDll: true` when spawning, which routes `kill()` down the branch that does not
   enumerate the console at all; or on win32 stop a terminal by tree-killing its own pid and let the
   exit event drive node-pty's teardown, never calling `kill()`. The first is one line and depends on
   the DLL being available on this host; the second keeps the current backend and changes the stop
   path. I did not pick one: getting it wrong means Stop stops working, on the machine your fleet
   runs on.

**If you spend attention on only three, spend it on 1, 9 and 11**: the first is exposure, the second is
a failure shape this codebase has already paid for once, and the third is the only one carrying a
measured number large enough to feel.
Everything else in this file is recorded with a judgement and needs nothing from you. That sentence was
FALSE between the rounds of 2026-08-26 and this edit: six decisions were added below it while it went
on claiming there were none -- the same defect this review spent the day finding in code, a summary
that stopped covering its own subject.

18. **Nothing collects an orphaned managed worker while the bridge is running, and the control plane
    cannot see one.** A delivery loop is `nohup node hermes-managed-host.js run <agent>` -- detached on
    purpose, so it outlives its launcher. The only thing that reaps it is the survivor sweep at bridge
    BOOT, so a loop orphaned mid-session accumulates until the next relaunch. Measured on the operator's
    host 2026-08-26: six alive, oldest 96 minutes, each holding a hermes gateway.

    **The invisibility is the interesting half.** Its agent reads `available` -- correctly, since
    `available` means "no live channel sidecar" -- while its `lastSeen` refreshes every few seconds,
    because the orphan itself is heartbeating. *The liveness signal that would prove it dead is the one
    the orphan keeps emitting.* An operator reading the dashboard sees "not running" beside a process
    that is running, which is exactly what the operator reported that day, in those words.

    **Half done, and the remaining half is yours.** `aify-comms doctor`'s new `managed-orphans` check
    now NAMES them, read-only. What it deliberately does not do is kill: a periodic reaper is a
    process-killer running unattended against ownership rules that have already been wrong twice in
    this repo's history, and the blast radius of a wrong answer is somebody's live session. Adding one
    is a decision, not a repair. The cheap alternative is that nothing changes and the orphans clear at
    the next bridge relaunch, which is what happens today.

19. **The POSIX branches of the kill and reap paths have never run, and cannot be run here.** Sixteen
    bridge modules branch on `process.platform`; twelve have no test that forces the other side.
    `runtimes-process.js` is the one that matters: its POSIX half kills a process GROUP (`kill(-pid)`)
    and walks `descendantPids` via `ps`, and none of that executes on Windows. The operator plans a
    Linux deployment and has a macOS user, so this is not hypothetical.

    **What is NOT worth doing: more reading.** I read the branch and it looks right, and that sentence
    is worth nothing -- `defaultListProcesses` also looked right for a whole release. A test asserting
    "it calls `killPid(-pid)`" would be a location pin: it proves a line was written, which is the
    class of test this repo has already converted away from once.

    **What IS worth doing: one Linux CI job that runs the three suites.** That is the only instrument
    that can distinguish a correct POSIX branch from an untested one, and it answers all twelve modules
    at once rather than one hand-written assertion at a time. Until it exists, the honest status of the
    POSIX reap path is ASSUMED, not proven -- and the Windows half of that same file spent v0.5.4
    silently enumerating nothing, which is what an unexercised path buys you.


## Checked and found HEALTHY, 2026-08-26 — do not re-walk these

A review round that only records defects teaches the next reader nothing about where NOT to look. Each
of these was investigated far enough to be certain, against the live system, and each cost real time.

| what | measurement | verdict |
|---|---|---|
| **231 spawn failures since 2026-08-24, many blaming a console line that explains nothing** | normalised the error text (each embeds a unique terminal id, so a raw GROUP BY produced one group per row and hid the shape). 172 quote a console line as the cause; the recent ones quote `✻Cogtated for 27s`, bare `5` / `40` / `8`, and a box-drawing tool-call tree | ALREADY FIXED, and the residue predates the deploy. `terminal_diagnostics` gained its decoration guard in `d0abee57` (2026-08-26), which IS an ancestor of the running build `45045505` (built 2026-08-27T19:12:27Z). Since that build deployed there have been 5 spawn failures and **ZERO** quote a console line. The latest bad cause is 2026-08-27T19:07:32 -- five minutes before the deploy. |
| **`/stats` reports `dispatch_reply_pending = 118` while zero replies are owed** | the query counts `status IN ('completed','failed','cancelled')` with no result -- rows that are FINISHED and never answered, a total that only grows. Open reply-owing runs: **0** | NOT A LIVE DEFECT, because nothing reads it: the field is in the /stats dead-field ledger, so no operator has been shown 118 outstanding obligations. The NAME contradicts the query, which is a trap for whoever wires it to a tile, so the query now carries a comment saying what it counts and where the real numbers live. Renaming an emitted field is a response-shape change and was not taken. |
| **DATE EVERY LIVE FINDING AGAINST THE DEPLOYED BUILD** | three rounds running, an alarming live number turned out to predate its own fix: the 62 `missing_reply` contracts (fixed 2026-07-20), these spawn causes (fixed 2026-08-26, deployed 2026-08-27T19:12), and the diagnostics residue | METHOD, not a defect. `GET /version` gives the running sha and build time; `git merge-base --is-ancestor <fix> <running>` settles whether a fix is live. Rows written by an older build look identical to rows written by the current one, and the database keeps both for ever. |
| **Settings the operator can change that nothing reads** | 43 keys in `DEFAULT_SETTINGS` against 494 product files; exactly ONE name appears only in its own declaration | NOT A DEFECT. `console_auto_confirm_claude_dev_channels` is documented in place as "retained for settings-response compatibility only", and its sibling `..._compaction` says the bridge decides at process start from `AIFY_AUTO_CONFIRM_COMPACTION` and does not poll the service. The comment claims Dashboard Next does not expose a no-op toggle; verified -- neither dashboard mentions either key. A documented inert setting whose documentation is accurate. |
| **The overdue tile counts from a page** | `summary-tiles.mjs` counts `state.contracts.filter(c => c.overdue)`, and the base refresh fetches `/contracts?limit=80`. Peak reply-required runs requested in any single HOUR, over 5,724 ever: **49**. Open right now: **0** | NOT WORTH FIXING at this scale, and say so with the number. Concurrent open contracts would have to exceed 80 for the tile to under-count, and the busiest hour on record requested 49 -- most of which were answered within minutes. The endpoint's own `summary` is computed over the same page, so moving the derivation server-side would change no number. The mitigation already exists: a capped page sets `truncated`, and the Work Loop prints a partial-scan notice. **What would change this answer:** a sustained fleet where more than 80 replies are owed at once. |
| **The 62 contracts stuck in `missing_reply`** | all 62 requested in July 2026; only ONE has a reply from the target, dated 2026-07-03; the rule letting a `request`-typed reply close a contract landed 2026-07-20 (`7f5711dd`); of the 8 requested after that date, **0** have a target reply; **0** unanswered since 2026-08-01; **0** reminders ever sent for any of them | FIXED ALREADY, and the rows are residue. Before 2026-07-20 a reply typed `request` did not close the contract it answered, so an answered obligation stayed open and a reminder fired for it -- reproduced exactly in run_1783039967791: the target replied at 00:56:35 while the run was open, the contract never closed, and a reminder went out at 01:07. The fix is covered by `test_linked_request_closes_reply_contract_without_reminder`. The residue is inert: `status='completed'` keeps it out of the reminder sweep. |
| **Channel messages that reach nobody** | 667 channel rows: 179 canonical (no recipient) and 488 per-member copies. 22 canonical rows produced ZERO copies | BY DESIGN, all 22 explained. 21 are `_system` join/leave notices, which have no recipients. The 22nd is an agent posting to a channel whose only member is itself. `echoes` looked like four lost messages until its members' join times turned out to postdate them by two hours. The canonical rows ARE read -- they are the transcript. |
| **The unread badge disagrees with the inbox** | six live agents, counts from 1 to 97, badge vs `/messages/inbox` | AGREE EXACTLY. Both key on `to_agent = ? AND no read receipt`, and the inbox computes its count with the LIMIT stripped -- the thing `/contracts` got wrong and this did not. |
| **Failed steers lose the message** | 137 failed dispatch controls; of the 136 with a source message, **0 rows missing and 0 unread** -- every one has a read receipt | NO. The steer is a fast-path injection into a live turn; when the run ends first the message falls back to normal delivery and lands. 96 of the 137 are "the run ended before the steer landed". |
| **Dangling references across the tables** | 3,578 messages to agents that no longer exist, 3,897 from them, 1,759 orphan read receipts, 104 dispatch_runs targeting a missing agent | BY DESIGN. Removing an agent keeps their history. The one that would matter -- an OPEN run targeting a missing agent -- is **0**. |
| **Tombstoned agents resurrect** | 71 tombstones joined against `agents` | **0** resurrections. |
| **Dispatch runs stuck open** | 21,528 completed, 181 failed, **1** `delivered` | Healthy. The one open row is a reply contract, which reminders own. |
| **The service is erroring** | 12h of container logs: `REQ-ERROR`, `REQ-5XX`, `DB-LOCK` | **0 lines.** 709 reconciles (one per ~61s, correct), 20 WS connections, 4 WAL-checkpoint warnings (3 at 1071-1754ms). |
| **The dashboard drops realtime events** | service broadcasts **51** event names; the dashboard names 5 | ALREADY GATED. `realtime-dispositions.mjs` gives every event a declared disposition and defaults to refresh; `realtime-dispositions.test.mjs` fails on a name with no entry. A first count of 45 was MY undercount -- a one-line `broadcast("x")` grep. Their scan is the authority. |
| **MCP tools declare parameters nothing reads** | 32 tools with a zod shape | **0** unread parameters, and now gated by `every-tool-parameter-has-a-reader.test.js`. |
| **`/messages/recent` ships bodies nobody needs** | 135,603 bytes, 67.2% `body` | NEEDED. DM conversations render from that list with no second fetch. |

TWO INSTRUMENTS THAT DID NOT EARN THEIR KEEP, recorded so the next person does not rebuild them:

- **Strict-subset SQL siblings** (statements against one table whose WHERE conditions are a subset of
  another's). It generalises a real defect found this round, and produced **64 candidates** of which
  the two highest-signal ones -- the same file pair as the real defect -- are both legitimately
  different questions. The rest are scoping predicates, not guards. A gate would need ~60 exemptions,
  which is not a gate.
- **Fields a JS consumer reads that Python never emits.** First run 339 findings; after fixing a
  per-file "locally defined" set that made cross-module state look unknown, **187** -- still dominated
  by vendored xterm, DOM builtins, and fields that pass through the service as opaque JSON and are
  therefore never named in Python at all. The class is real (it caught the `spawnSpec.metadata`
  defect) but not mechanically separable at this signal-to-noise.

**THE METHOD THAT WORKED, stated because it is the transferable part.** Every defect found in this
round came from OBSERVING THE RUNNING SYSTEM -- the live accessibility tree, a measured HTTP payload,
the container's own log, the live database -- and none from scanning source. Both source scans above
produced noise. When a round stalls, go and look at the thing running.

## The mass-worker-death investigation, and what it has RULED OUT

Unsolved. Written down because eight hypotheses have been eliminated with evidence and re-walking any
of them would cost the same day twice.

**The shape.** Managed workers die in SAME-SECOND CLUSTERS -- five at 10:12:51-56, seven at
12:06:37-39, eight around 13:12 -- across BOTH runtimes at once, on win32:stevenz-l. Lifetimes inside a
cluster vary from 4m41s to 10m12s, so it is not an age limit: whatever is alive dies together.

**Ruled out, each by its own evidence:**

| candidate | how it was eliminated |
|---|---|
| aify-env's reaper killing already-dead pids | fixed in `2bac2c7`, DEPLOYED, deaths continued |
| the 5-minute stale-run reaper | every run in the 12:06 window finished `completed`, not one `failed` |
| `worker_idle_close_enabled` | off, and it stamps `Auto-closed: idle longer than...` into `error`; every dead row had an empty error |
| the bridge ordering a teardown | its log carries a teardown line for individually-stopped agents and NOTHING for any cluster |
| aify-env crashing or restarting | same pid across every cluster |
| the aify-env TUI | no stdin handling at all; a read-only view |
| a kill by image name | no `taskkill /IM` anywhere in the three repos; every kill path is by pid |
| `managedClaudeMaxTurns` | exported from `runtimes.js` and called by nothing -- a dead knob, not a limit |

**TWO DISTINCT SHAPES, which is why it reads as inconsistent.** The operator suspected two bugs before
the instrument could show it, and the panel now does:

* `exited 1` through the child's own close event, eight at once. **On Windows an externally terminated
  process and a program that returned 1 report the same `(1, null)`** -- measured here. The code alone
  cannot separate them.
* `no exit reported`, which appears only when the entry was REMOVED by `stop()` or the reaper. A
  process that ends on its own always arrives through the close event carrying a code or a signal.

**The instrument, and where it lives.** aify-env `83cbb71`, `aa4ec41` and `da4dc9d` put a per-death
record on `/health` and a RECENT EXITS panel in the TUI: id, pid, label, time, exit code, signal, WHICH
PATH removed it, and the process's last 200 characters with the terminal chrome stripped. aify-env is
the ONLY tier carrying it -- the service (`1a3de61a`) and the environment bridge (`579dd546`) on that
host are days behind and drop the fields before anything stores them.

**How to read the next one.** An empty `lastOutput` across a whole cluster means abrupt external
termination and the cause is not in aify-comms. A provider or gateway error there means the runtime,
and the answer is upstream of this project entirely.

## Shipped this round

**Two defects in the console renderer, one class, and the second was found by walking the fleet's own
streams rather than by another report.** pyte dispatches private-parameter CSI sequences as if they
were ordinary ones. Measured across seven live terminals, 304,604 characters:

    407x  CSI > 4;2 m   XTMODKEYS (modifyOtherKeys)   -> dispatched as SGR 4: UNDERLINE ON, forever
    406x  CSI < u       Kitty keyboard, pop flags     -> PRINTS A LITERAL `u` into the screen
    406x  CSI > 1 u     Kitty keyboard, push flags    -> inert
      1x  CSI > 0 q     cursor-style query            -> inert

The first is what the operator screenshotted: a full-width rule under every row of a live console. On
one 70,871-character stream containing ZERO real SGR sequences, all 5,722 cells came out underlined
while `screen.default_char` did not. The second lands on the HERMES agents -- 120, 109 and 177
occurrences in three live streams -- and is why the claude screenshot showed only the underline.

**The honest half: only the underline was observed reaching a screen.** The injected `u` is proven in
isolation and overwritten by later repaints on all three live streams that carry it. Removing a
sequence the emulator cannot parse is right either way; claiming both halves were caught in the wild
would not be.

The rule is now by PREFIX rather than by final character. `<`, `>` and `=` mark capability
negotiation and pyte implements none of them, so enumerating finals would leave the next one to be
discovered the same way. `?` stays out: pyte does implement `?...h` / `?...l`, and 12,861 of them
crossed the sanitiser in that sample carrying the alternate screen and cursor visibility.

**A claude generating for seven minutes read as `online`.** Same screenshot, second arrow. The footer
`✻ Concocting… (7m 29s · ↓ 27.6k tokens)` matched none of the working rules -- no "esc to interrupt",
no `for 21s` -- and a glyph-based rule cannot work at all here, because claude repaints the spinner
CELL with an absolute cursor move between frames, so the glyph and the footer text are never adjacent
in the flattened tail. The brackets carry the specificity instead: an elapsed timer and a live token
counter together, which is the discrimination the subagent-row rule already makes.

**A killed worker told its requester the terminal had "stopped".** The run-closing sentence was built
from the status alone, and the bridge reports `stopped` for every ending that is not a spawn failure.
The exit code and signal sat in the same row, written moments earlier in the same request.

**And two defects in my own new tests, both caught by controls rather than by luck.** A fixture
captured through a cp1257 console lost all 129 spinner glyphs and sent me hunting an encoding bug in
the product that did not exist -- the raw HTTP response had 161 intact sequences throughout. And git
was rewriting the captures on the way in: the committed blob of one held 46 FEWER carriage returns
than the file captured, which on Linux would have made a test whose whole claim is "this is the real
capture" assert on an edited one, silently.


**The exit columns had a SECOND reader and it was still dropping them.** `GET /agents/{id}/console`
was the reader I built and tested. `_terminal_session_to_dict` is the one almost everything else gets
-- `GET /terminals/{id}`, the console start and stop payloads, the virtual-terminal ensure, the
session-ops rows the dashboard renders -- and it serialised `status` and `error` and nothing about how
the process ended. Every one of those consumers still answered "stopped". Nothing was red: the suite
was green, the feature's own eight tests were green, and the column had data in it. Found by walking
the readers rather than by anything failing, which is the only way this class ever surfaces. Fixed,
with two tests watched red against the old serialiser -- one of which has to assert the KEY IS
PRESENT rather than that its value is null, because an absent key and a null value read identically
through `.get()`.

**One test's terminal output was arriving in the next test's database, and nothing was failing.**
`_base.setUp` clears two process-globals with a comment each saying why -- the derived live-status
cache and the settings cache -- because production has one process and one database while the suite
keeps the process and gives every test a fresh database. `TERMINAL_OUTPUT_WRITES` is the third and
nobody reset it. A POST appends to a per-terminal deque and schedules its flush through `call_later`,
which has not run when the request returns; the database is deleted and the deque is not. Measured
directly: a terminal seeded with `output=''` read back nine `[terminal exited]` markers and
`outputSeq: 11`, none of which its own test wrote. Two independent fields, same story.

The direction matters. A test whose own bytes go missing fails loudly. The silent case is the
opposite -- a test handed output it never produced, concluding a write path worked. The proof needs
two tests sharing one terminal id, because a single test cannot observe a leak between tests, and it
was watched red with the reset disabled: the second test read the first's bytes verbatim.

**The bridge-to-service body contract now has a gate, and today it is clean.** The only thing between
the host bridge and the container service is a JSON body, and pydantic's default `extra` is "ignore",
so a key the route's model does not declare is discarded with no error on either side. Measured: 98
write call sites across `mcp/stdio` and its two subdirectories, 88 with a body the scan can read, all
88 resolving to a route that exists, ZERO undeclared keys. The zero is the finding -- and it is only
worth having because the scan carries both controls: a declared key must be recognised and an
impossible name must be reported, in the same run.

Two things about that measurement are worth keeping. Comments are stripped file-wide before any walk,
because an apostrophe inside a comment opens a string for the argument parser and swallows the rest of
the call -- which is how `if`, `catch` and `const` arrived as body keys on the second run. And the ten
call sites whose body is a VARIABLE are named in the gate with the builder that produces each, rather
than silently skipped: a blind spot written into the source beats a clean number that reads as full
coverage. The five of those whose route binds a model were checked by hand.

**Auditing my own feature found that its headline promise is FALSE for one important case, because of
a defect one tier down.** I told the operator that a terminal death would now explain itself. For a
delegated terminal killed by a SIGNAL it will say "exited with code 0" -- a clean exit -- which is
worse than the silence it replaced.

The chain was traced end to end across two repos, and it is correct until the last hop:

    aify-env  child.on("close", (code) => finish(code))     runner.mjs:284
              Node's close event is (code, SIGNAL). The signal is discarded AT THE CALL SITE.
    aify-env  stream.exitCode = code ?? 0                    runner.mjs:185
              Node gives code === null for a signal kill. `?? 0` turns that into a CLEAN EXIT.
    aify-env  response.write("event: exit
data: " + JSON.stringify({ code }))
              The frame carries `code` and no signal -- there is nothing left to carry.
    comms     finish(Number(payload?.code ?? 0))             env-client.mjs:191
    comms     _handleExit(id, state, { code, signal: null }) terminal-runtime.js:374
    comms     exitReport(detail) -> exitCode: 0              terminal-exit-report.js

The PTY path drops it the same way at `runner.mjs:278`, using `event?.exitCode` and ignoring
node-pty's signal.

**Everything on the aify-comms side is right**, which is what makes this worth writing down rather
than patching blind: `exitReport` refuses a non-numeric code, keeps `exitCode` and `exitSignal` as
separate fields precisely because a signalled process reports a null code, and the column defaults to
NULL so "nobody said" stays distinct from "exited 0". None of that helps when the value arriving is a
0 that was manufactured two hops earlier.

**THE FIX SPANS TWO REPOS AND CHANGES A WIRE CONTRACT**, so it is the operator's: aify-env would keep
the signal from `close(code, signal)`, stop coercing null to 0, and add a `signal` field to the exit
frame; aify-comms' `env-client` would read it and pass it through the detail it already builds. Both
halves are small. The seam between them is live, and a protocol change on a live tier is not something
to make quietly.

**Until then the limitation is disclosed rather than implied.** A LOCAL pty death records a true code
and signal today -- that half works. A DELEGATED death records a code that is only trustworthy when
the process exited of its own accord, and sc-architect, the death that prompted the whole feature, was
delegated.

**A slack ceiling in my own test, and an audit whose instrument measured the wrong thing.** Continuing
the self-audit: the lesson from the vacuous analytics fixture generalises -- a mutation proving the FIX
is load-bearing does not prove the FIXTURE can distinguish the case the test names -- so it was worth
asking of every test written today.

**The audit's own instrument was weak, and saying so is the point.** It scanned for control LANGUAGE
("positive control", "proves nothing") across the 31 test files added recently and flagged one of mine
as having none: `terminal-exit-report.test.js`, ten tests, zero mentions. Reading it, the substance was
there all along -- `assert.ok(calls.length >= 4)` is an anti-vacuity control whatever it is called. The
scan measured vocabulary and reported it as presence.

**But reading it found a real weakness underneath.** That test asserts "every exit path in the runtime
supplies something to report", and its scan matches a BRACE-LITERAL detail argument. A call site
passing a variable matches nothing and is skipped in silence -- while `>= 4` against a real 5 leaves
room for exactly one path to disappear with nothing going red. That is the slack this repo's own size
gates refuse: the MEASURED value, never a comfortable margin above it.

Replaced with two instruments required to AGREE: the detail-reading scan, and a second count of the
call sites by the call itself. A path this file cannot read now fails rather than being quietly
excluded from the guarantee above it. Proven by making one call site pass a variable -- the test then
reports "5 exit paths exist but only 4 have a detail this test can read".

**The cross-check immediately caught my error rather than the code's.** My first counter matched a bare
`_handleExit(`, which also matches the method DEFINITION, so it reported 6 against 5 and failed on a
disagreement of its own making. Narrowed to the call form, the two agree at 5. That is what a second
instrument is for, and it is more useful than the assertion it guards.

**I audited my own work from today and found a vacuous test I had shipped hours earlier.** Three
rounds had produced no defect in other people's code, and the one defect I did find was in my own
document -- so the least-reviewed code in this repo is what I wrote today. That is where I looked.

`test_analytics_resolves_the_fleet_once.py` proves a per-machine cache returns the same answers as an
uncached lookup. Every fixture in it registered ONE environment on ONE machine. The cache is keyed by
`machine_id`, so a single-host fixture passes against a cache that ignores its key entirely and hands
back whatever it cached first -- **a wrong answer rather than a missing one**, which is the harder kind
to notice.

**It took three attempts to write a test that could see the mutation**, and each failure was a
different flavour of the same mistake:

1. Compared the live-state result's `environmentId`. That key does not exist -- it is `environment_id`
   -- so the lookup returned nothing, fell through to `status`, and compared two identical maps.
2. Compared `environment_id` correctly. Still passed: that field does not come from this resolver at
   all, so the mutation is invisible at that level.
3. Compared what the RESOLVER itself returns, per agent, in call order. This one fails against the
   key-blind cache and passes when it is restored.

The fixture also gained a control that would have caught all three: it asserts the agents resolve to
MORE THAN ONE distinct environment, because a fixture where every agent resolves identically cannot
tell a per-machine cache from a key-blind one -- which is the only thing it exists to tell.

**The optimisation itself was correct throughout.** This changed no product behaviour: it was verified
correct for multi-host BEFORE the test was written, and the test was written because being correct by
accident is not the same as being pinned. What was wrong was my evidence, twice, in a file whose
subject is measuring things properly.

**A live-data probe, and the two numbers on that dashboard that look like defects and are not.** The
gate seam was worked out, so this round changed instrument entirely: read the RUNNING system for rows
in states the code says are impossible. Today's most valuable finding came from live evidence rather
than from reading, so it was worth trying again.

Three checks, no defect -- and the two alarming-looking figures explained with their thresholds, which
is the durable part:

**`dispatch_runs_by_status` shows 8 `delivered`, three of them 10.0h, 15.2h and 19.0h old with
`requireReply=false`.** That reads as the stranded-row shape this review fixed twice in code: a
non-terminal status nothing closes. It is not. Two reconcilers cover `delivered`, and the second one
names this exact case -- "require_reply=0 runs older than `stale_hours` (info-only, no reply expected,
should have been auto-completed)" -- with `stale_hours` defaulting to 24 and the sweep calling it
without an override. All three are INSIDE the window. Caught mid-flight, not stranded.

**`dispatch_reply_pending: 115` is a historical tally, not a backlog.** It counts runs that are
already terminal (`completed`/`failed`/`cancelled`), required a reply, and never received one: 115 of
21,535 lifetime runs, 0.53%. Nothing is waiting on those.

**And two clean joins on live rows:** all 47 registered agents hold a status inside
`VALID_STATUSES`, and every session carrying a terminal id agrees with that terminal's own status --
though only 2 sessions currently carry one, so that check has thin signal and is reported as such
rather than as reassurance.

**A round that found no new defect, which is itself the finding.** Seven consecutive rounds widened a
gate; this one checked three more and they were sound. Recorded with the reasoning so the next pass
does not re-walk them, and so the run of clean results is visible rather than inferred:

| gate | question asked | answer |
|---|---|---|
| `test_comments_do_not_cache_a_stale_value.py` | does its scan recurse, and can a TEST's constant shadow a product one? | `rglob` recurses, so subdirectories are covered. The `setdefault` collection does not exclude tests, so a name declared in both could resolve to the test's value -- measured: 70 product constants, 27 test constants, **ZERO** names declared in both with different values. Latent, not live |
| `handler-imports.test.mjs` | is `WATCHED` narrower than the modules it should cover? | its own docstring records this scope being corrected once already, and the complementary standard -- a test that CALLS every export -- is the gate widened last round, whose dashboard backlog is empty. No gap provable |
| `test_environment_upsert_columns_agree.py` | (previous round) | sound in scope and coverage |

**And one hunt that came back nearly empty, deliberately reported.** The orphaned controller suggested
a class: prose asserting a structural relationship the imports do not have. A first scan over the
bridge returned 17 candidates and was mostly noise -- "moved to X" MEANS the code left, so not
importing it is correct, and `test_moved_to_comments_are_true.py` already covers that verb. Worse, the
scan was single-line and would have MISSED the case that prompted it, whose claim wraps across two
comment lines: the hunt for narrow scans had a narrow scan.

Corrected to the real shape -- a claim naming several modules where some are imported and one is not --
it returns **exactly one** instance across the whole bridge: the one already found by reading. A gate
for a one-instance class whose instance is already declared is not worth its load, so none was added.

**What WAS worth doing: the false sentence is gone.** `hermes-controller.js` said mode-specific
implementations live in two files and imported one. The comment now says what is true and names the
open decision, because a comment asserting a wiring the imports lack is how a reader concludes a code
path exists and builds on it.

**An empty backlog that was reporting on 236 modules while 32 were out of scope -- and an orphaned
controller behind it.** Seventh instance, same method: derive each gate's population independently
instead of trusting its list.

`every-export-is-named-by-a-test.test.js` holds `UNTESTED_EXPORT_BACKLOG`, and that list was EMPTY --
which reads as "every export is named by a test". Its `MODULE_DIRS` is two directories read
NON-recursively. Deriving the population found 268 non-test JS modules, of which **32 sat outside**,
including `mcp/stdio/adapters/` (7) and `mcp/stdio/controllers/` (11) -- the per-runtime product code,
not helpers.

Measured independently before touching the gate: 24 exports in those two subdirectories, **three named
by no running test**. Widening `MODULE_DIRS` made the gate report exactly those three, which is the
agreement that makes both measurements trustworthy.

Two are untested-but-live (`createCodexLegacyTimers`, `resolveActiveCodexThread` -- three
non-declaring references each). **The third is ORPHANED**, which is a different problem:
`HermesSingleShotController` is imported by nothing. Its only reference outside its own file is a
COMMENT at `hermes-controller.js:23` listing it as a mode-specific implementation -- and that file
imports `HermesManagedController` alone. No mode routes to it, and no other single-shot controller
exists to have replaced it. The prose asserts a wiring that the imports do not have.

**Deleting product code is yours, so it is recorded rather than removed.** A test naming it would
prove a class nothing constructs still works, which is not worth writing. The scope stays an explicit
list rather than becoming a recursive walk: `mcp/stdio/scripts/` is a developer tool and
`mcp/stdio/tests/` holds fixtures, and a gate that demands the wrong thing gets weakened rather than
obeyed.

**One gate checked and found sound this round**, recorded so nobody re-walks it:
`test_environment_upsert_columns_agree.py` reads one helper, and that is the CORRECT scope -- it is the
only writer of `environments` doing an upsert, the other three are single-purpose targeted UPDATEs, and
all 15 table columns are written by the upsert it reads. No gap in either direction.

**The single-worker gate could not see the one file most likely to break it.** Sixth instance of the
class, found by the method that is now routine: census the gates that pair a declaration with a scan,
then derive each scan's population independently instead of trusting its list.

`test_single_uvicorn_worker.py` forbids `--workers > 1`, and that invariant is load-bearing --
`_LIVE_STATE_CACHE` is a process-global dict, so a second worker gets its own copy and the derived
agent status silently diverges. The gate walks files by suffix: `.yml`, `.yaml`, `.sh`, `.py`, plus the
exact name `Dockerfile`.

Deriving the population -- every file in the tree that mentions uvicorn -- returned 32, of which 22
were outside that filter. Nearly all are prose or caches. **One is a launch file:**
`docker-compose.override.yml.example`, whose suffix is `.example`. A compose OVERRIDE is exactly where
an operator adapting the example would add `--workers`, and it already carries a commented-out uvicorn
command line inviting exactly that edit.

**Proven rather than argued.** Planting `--workers 4` in that file and running the gate:

| scanner | result |
|---|---|
| old filter (suffix + exact `Dockerfile`) | **PASSES** -- a false green on the invariant |
| widened filter | fails, naming the file |

Fixed by shape rather than by adding a filename: a template suffix (`.example`, `.template`,
`.sample`, `.dist`) is stripped and the inner suffix re-tested, and `Dockerfile*` now matches variants
like `Dockerfile.dev`. Neither a Dockerfile variant nor a `.sh.example` exists today -- which is the
argument for doing it now, since a gate guarding a silent-corruption invariant should be complete
before the file that breaks it arrives. The widening is bounded by its own test: `.env.example` and
`README.md` must stay OUT.

**The parity gate could not see the tools most worth declaring -- fourth instance of one class.** I
went looking for drift between the two MCP transports and found that a gate already exists and already
declares all 14 stdio-only tools with reasons. That is the THIRD time this review has rediscovered
something the operator had already adjudicated, and the right answer each time was to stop.

What was genuinely open was narrower. `transport-parity.test.js` asserts "every SSE tool also exists
in stdio" -- its own words for the accident that matters most -- and its scan matches
`^async def (comms_[a-z_]+)`. SEVEN tools sat outside that prefix, reachable over a LIVE transport
(`/mcp/sse` answers 200 on the running service), absent from stdio, and declared nowhere:

| tool | where |
|---|---|
| `list_containers`, `start_container`, `stop_container`, `gpu_status`, `container_logs` | `service/sse/container_tools.py`, registered at `mcp/sse_server.py:97` |
| `service_info`, `service_health` | decorated in the transport itself |

Two of them are destructive. The gate whose subject is undeclared difference could not see them,
because the scan read one shape of NAME while its assertion claimed completeness -- the same failure as
`broadcast(` vs `notify_agent(`, a POST body vs a spread, and `ast.Constant` vs an f-string.

Fixed by widening the scan to every tool name, declaring all seven with reasons, and adding a control
that fails if the wide scan ever stops being wider than the narrow one. My own first derivation --
reading `TOOLS` tuples -- found only five; the widened gate found `service_info` and `service_health`
that I had missed, which is the gate doing to me what it now does for everyone.

Two stale numbers corrected in passing, both measured rather than guessed: the file's own header said
"twenty tools are implemented in BOTH" against a measured 22, and my first comment said five tools sat
in the gap when the gate then found seven.

**A cross-vocabulary literal in eleven terminal filters -- pinned, NOT deleted, and the operator had
already ruled on the member once.** Third application of the AST hunt, this time to a different class:
a status column filtered against a literal its own vocabulary does not contain, which is the `lost`
incident's actual cause.

Eleven SQL clauses compare `terminal_sessions.status` against `recovering`. That value is not in
`TERMINAL_SESSION_STATUSES`, `_terminal_status_transition` REFUSES anything outside that set, and
nothing writes it -- so the clauses are inert. `recovering` IS a real status of the other table:
`_LIVE_SESSION_STATUSES` holds it for `agent_sessions.status`, where most of the tree's `recovering`
comparisons live and are entirely correct.

**The member was adjudicated in `44299eb6`**, whose subject reads "`recovering` is live but not
active, on purpose -- do not unify them" and which says outright: "I found it with my own near-miss
scan and had to establish it was intentional; the next reader running that scan will find it too."
That is exactly what happened, three rounds later, to me. Nothing here unifies anything.

**Pinned rather than deleted, and the reason is the failure mode.** Removing the literal from eleven
clauses changes no behaviour and risks being wrong about one of them. The trap worth closing is the
reader who sees `recovering` in a terminal filter, concludes a terminal can be recovering, writes it
-- and has the write SILENTLY DROPPED, because the transition returns "" rather than raising. The new
gate derives every literal compared against `terminal_sessions.status`, requires each to be a terminal
status or a DECLARED foreign one with a reason, and fails if a declared one stops appearing so the
list cannot rot. It also asserts the transition still refuses each declared literal -- if that ever
changes, the clauses become live and the declaration is wrong.

**Three instrument corrections on the way, all mine.** The first scan compared against
`TERMINAL_SESSION_STATUSES` alone and reported 20 offenders, most of which were other tables' columns
in joined queries. Attributing by qualifier cut it to eleven. Then `UPDATE terminal_controls` -- whose
subquery mentions terminal_sessions -- had its OWN unqualified `status` attributed to the terminal
vocabulary, because the alias map read FROM and JOIN but not UPDATE. Each correction made the gate
narrower and correct rather than merely quieter.

**The same class again, found by hunting it rather than tripping over it.** Last round's dispatch
finding gave the shape a name -- a value normalised for a comparison and then used RAW for the write
-- so this round searched the service tree for it with the AST rather than waiting for the next one.

The first pass returned 83 hits and was useless: it counted the normalising assignment's own source as
a "raw use". Narrowed to the shape that actually costs something -- the raw value reaching a DATABASE
WRITE while a normalised twin exists in the same function -- it returned TWO. One was a
self-reassignment (`dispatch_mode = dispatch_mode.lower()`), a false positive. The other was real.

`_apply_terminal_status_from_control` computes `terminal_status_norm` and uses it for exactly one
thing: the end-status membership check. The two UPDATEs beneath it bound the UNNORMALISED value four
times, and both statements compare that same parameter against lowercase literals:

| binding | consequence of a mixed-case value |
|---|---|
| `terminal_sessions.status = ?` | stored verbatim, so every reaper's `status IN (...)` misses the row |
| `stopped_at = CASE WHEN ? IN (...)` | never stamped, so nothing can age the row |
| `agent_sessions.terminal_status = ?` | stored verbatim |
| `owner_mode = CASE WHEN ? IN (...)` | never returns to `managed` -- the session stays owned by a console that has gone |

Four consequences from one missing `.lower()`, against one for the dispatch run. Three of the six
tests fail against the old bindings; the whitespace one passes both ways and says so in its own
docstring, because `.strip()` was already applied and a test that cannot discriminate the fix should
not be read as proving it.

**No live defect today, measured:** the bridge sends four `terminalStatus` literals across live
sources -- attached, failed, running, stopped -- all lowercase.

**Recorded and NOT fixed: this path bypasses the status allowlist.**
`_terminal_status_transition` refuses any status outside `TERMINAL_SESSION_STATUSES` and returns the
normalised value; it was added on 2026-08-16 for exactly that reason. These two UPDATEs do not go
through it, so an undeclared status could reach the column here. Routing them through would ALSO
start refusing writes the monotonic guard rejects, which is a live behaviour change on a control
path -- a decision, not a repair. **That one is yours.**

**A dispatch run's status was written raw while everything downstream assumed lowercase.** Found by
pulling the thread I left last round: I recorded that `dispatch_runs.status` has no vocabulary gate,
and went to check whether a real defect sat behind that absence. One did.

`update_dispatch_run` lowercased the REQUESTED status for its monotonic guard and then wrote the RAW
one. Three consumers each test that written value against a lowercase literal:

    params.append(effective_status)                     the column every reconciler queries
    if effective_status == "running"                    stamps started_at
    if effective_status in _DISPATCH_TERMINAL_STATUSES  stamps finished_at and settles the run

A status of `Completed` passed the guard (which compared `completed`), was written verbatim, matched
neither check, and then matched no reconciler either -- every dispatch sweep selects on lowercase
(`dispatch_lifecycle.py:88, 367, 400`, `dispatch_queue.py:258, 337`). The row is finished to its
caller and unfinished to the system: `require_reply` never settles and cleanup never deletes it. That
is the `lost` incident's exact shape on a table with no gate to catch it.

**No live defect today, and the reason is measured rather than assumed:** across `mcp/stdio` with
`tests/` and `fixtures/` pruned, the bridge sends exactly five status literals on
`/dispatch/runs/{id}` -- completed, delivered, failed, queued, running -- and all five are lowercase.
(The first pass of that census walked the fixtures directory and attributed three of them partly to a
pristine pre-extraction snapshot; re-measured against live sources only, the set is identical.) What made it worth fixing anyway is that `status` is `Optional[str]` with no
validator, the bridge is host-side and routinely a different build from the service, and the guard one
line above already lowercases -- the author expecting case to vary in the same expression that then
did not handle it.

Four of the nine tests fail against the old write and five pass, which is the right split: the five
are the controls, the lowercase path and the monotonic guard, and normalising must not buy its
correctness by weakening that guard.

**One defect class appeared three times in a day, so I went looking for the rest of it.** The shape:
a gate scans for producers using one call pattern, another pattern exists, and the gate reports
honestly about what it reads and silently about the rest -- which is indistinguishable from having
checked.

The three that turned up on their own: `realtime-dispositions.test.mjs` read `broadcast(` and not
`notify_agent(`; `test_terminal_status_vocabulary.py`'s bridge scan read a POST body and not a spread;
and the same file's ROUTER scan accepted only `ast.Constant` SQL, so an f-string `UPDATE` was
invisible. `service/reconcilers/terminal_runs.py:356` writes `status = 'stopped'` inside an f-string
-- it has to be one, the WHERE clause interpolates a placeholder list -- so a real writer proves the
shape is in use. No live defect (`stopped` is already a member), but the guarantee that file claims,
that a new status fails here by file and value BEFORE it can strand a row, did not hold for that
shape. Fixed; the router census goes 16 files to 17.

Then I checked the siblings rather than assuming they shared it:

| gate | same limitation? | real gap today | action |
|---|---|---|---|
| `test_environment_status_vocabulary.py` | yes, `ast.Constant` only | **ZERO** f-string writes to `environments` | left alone -- no measurement supports a change |
| `test_stored_statuses_are_canonical_or_normalized.py` | no -- scans RAW TEXT, so f-strings are visible | none | none needed |
| `agent_sessions.status`, `dispatch_runs.status` | n/a | 3 f-string literals across 3 files | **no vocabulary gate exists for either table** |

That last row is a coverage question, not a blind spot, and it is a DECISION rather than a fix:
`agent_sessions` and `dispatch_runs` have no status vocabulary gate at all, so nothing catches a new
literal on either. Building two more is a larger commitment than repairing a scan, and the agents gate
explicitly scopes itself out of judging other tables' vocabularies on purpose.

**Nothing recorded HOW a terminal ended, and the bridge knew.** This closes the half of the sc-claude
question I could not answer on 2026-08-26: the console tail could show what the agent had been DOING
when it stopped and nothing whatsoever about the stopping.

node-pty hands the bridge `{exitCode, signal}`. `terminal-runtime.js` spreads both into the exit
detail on ALL FOUR exit paths -- the PTY (line 257), the DELEGATED aify-env process (374), the piped
child (446) and a forced stop (836). `terminal-manager.mjs` then read `detail.error.message`, posted
an output marker and a status, and dropped both numbers. `TerminalOutputRequest` had nowhere to put
them anyway, and `terminal_sessions` had no column for them. Four components, and the data died at
the third.

The delegated path mattered here specifically: sc-architect was a delegated terminal, so a fix that
only covered the local PTY would have missed the case that prompted it.

**NULL IS NOT ZERO, and that is the whole design of the two new columns.** Zero is a clean exit and
the most common value there is. A column that cannot tell "exited cleanly" from "nobody told me"
answers the question wrongly rather than not at all. So: the migration defaults to NULL, the model
types `exitCode` as an int rather than coercing a string, the route tests `is not None` rather than
truthiness, and the bridge OMITS the field instead of sending null. Four places, and a truthiness
test at any one of them would have destroyed exactly the most common case. The console tail keeps the
three answers distinct -- killed by a signal, exited with a code, or nothing recorded.

The exit is written straight to the row rather than through the output write queue. That queue exists
to COALESCE a high-frequency stream, and an exit is reported once; threading it through the pending
state would complicate the hot path to carry a field it forwards unchanged. Writing it separately
also means a later output chunk cannot blank it -- bytes still arrive after the exit POST on a busy
terminal -- and the queue's UPDATE names only output, seq and status, so it cannot clobber these
columns. Both properties are pinned by tests.

**The terminal-status census had the same blind spot, and caught me with it.** Extracting the exit
body into `terminal-exit-report.js` moved two status literals out of `terminal-manager.mjs`, and
`test_terminal_status_vocabulary.py` went red: its frozen census said that file sends
`{attached, failed, stopped}` and the scan now found only `attached`.

The gate was right and its scan was incomplete. It reads two shapes -- a `POST .../output` body and
`_pushTerminalFrame`'s second argument -- and the body is now SPREAD from a helper, which is a third.
Left alone it would have reported the exit vocabulary as GONE rather than moved, which is the same
failure the realtime dispositions gate had this morning: a scan that reads one shape of producer
reports honestly about that shape and silently about the rest.

Fixed by teaching it to follow the spread and attribute the literals to the module they live in, so
the census stays DERIVED rather than gaining a hand-listed exception. The frozen list was then
re-measured with the gate's OWN scan rather than copied out of the failure message -- which mattered:
a looser rule I tried first (any object literal carrying both `output:` and `status:`) reported two
extra files, and both were false positives -- `terminal-control-loop.mjs`'s `completed` is a CONTROL
status that sets `terminalStatus` separately, and `hermes-apiserver-client.js`'s is an RPC result.
Three different objects, one careless pattern.

Its positive control moved with the ternary rather than being deleted: the property being checked is
that a two-literal ternary is read whole, and that ternary now lives in the helper. Left pointing at
the old file it would have been a control over nothing, passing on a scan that had stopped reading
ternaries entirely.

**Only the bridge's own exit report fills these columns, and that is correct.** A terminal can also
be marked dead by a reconciler that noticed the process is gone -- the orphan reaper did exactly that
to sc-claude at 01:29 and 01:30. Those paths never OBSERVED an exit, so they leave both columns NULL,
which reads as "nobody told me" rather than inventing a code. A death nobody watched still cannot
explain itself; what changes is that a death the bridge watched now does.

**Still not answered: WHY those two PTYs died.** This records the exit code from now on; it cannot
retrofit one onto a death that already happened. sc-claude and sc-architect remain unexplained, and
the next one of these will explain itself.

**The console tail told the operator nothing was recorded, while 14,773 characters sat one store
over.** This one was found by the operator asking a live question -- why did sc-claude die -- not by
reading code, and the endpoint that exists to answer exactly that question answered it wrongly for the
agent next to it.

`comms_console_tail` reads `terminal_sessions.output`. A terminal's bytes also persist as
`terminal_events` rows. Usually the column is the fuller of the two. Measured on two real deaths,
2026-08-26, characters per store:

| agent | `terminal_sessions.output` | `terminal_events` rebuilt |
|---|---|---|
| sc-claude | 63,423 | 10,913 |
| sc-architect | **18** | **14,773** |

sc-architect's eighteen characters are its own `[terminal exited]` marker. Non-empty, so the
`.strip()` gate passed it, the tail rendered a line saying only THAT it ended, and the operator read
"(nothing was recorded)" -- while the events held the screen it died on, ending on
`Search Files("build_terrain_heat_source")` and a note about tuning a test timestep to recover green.

This is the endpoint whose own docstring exists because of this exact shape: "the cause of a failed
managed hermes launch sat in `terminal_sessions.output` for 2.5 hours ... The bytes were never
missing; nothing would serve them." It recurred one store over, which is the argument for
`says_what_it_was_doing` over `.strip()`: **non-empty is not informative**.

Fixed by falling back to the events when the column says nothing, naming the store that answered in
`recordedFrom`, and reading the events ONLY on that path so the common case costs no extra query.
`[terminal failed] <error>` is deliberately not treated as self-reporting -- that marker carries the
reason after it, and filtering it would delete the most useful line a dying terminal writes.

The MCP tool says when it recovered. `comms_console_tail` renders `r.output`, so the service fix
alone would have shown the recovered text with no indication of where it came from -- a field with no
reader, which is the defect this round has caught in other people's code twice. The tool now prints
"Recovered from the terminal's recorded events; the output column held only its exit marker" on that
path and NOTHING on the ordinary one, because a line that appears every time trains its reader to skip
it. A bridge carrying this change will meet a service that predates it, so an absent `recordedFrom`
reads as the ordinary case rather than as a recovery; that skew is pinned by a test.

**Scope, stated so this is not read as more than it is.** `_prune_terminal_history` expires the
output column and the events on the SAME 24-hour schedule (`ended_output_ttl_hours` and
`terminal_event_ttl_hours`, both 24), so the fallback does not extend how far back a death can be
explained -- it fixes the case WITHIN that window, which is when anyone actually asks. The pruner did
not cause sc-architect's near-empty column either: that terminal was 42 minutes old, so nothing had
expired. Its eighteen characters are all the column ever received.

**The same handler also called a dead terminal idle.** With both stores silent it fell through to
"has no live console (it lazy-starts on a message)" -- which describes an agent waiting by design, not
one whose terminal ended. A silent death is itself a finding and now reads as one.

**A schema trap found while writing that fixture, and NOT fixed.** `agent_sessions.spawn_spec_id` and
`spawn_request_id` are nullable FOREIGN KEYs whose column DEFAULT is the empty string. NULL is exempt
from a foreign key; `''` is not, and no `spawn_specs` or `spawn_requests` row has id `''` -- so an
insert that OMITS those columns takes a default guaranteed to violate the constraint, and SQLite
reports `FOREIGN KEY constraint failed` naming no column. All three production writers name both, so
nothing is broken today. **The decision is whether to migrate the defaults to NULL**, and it is yours:
it is a schema change against a live database, which is not something to do unilaterally while the
fleet is running. Pinned meanwhile by a test that derives the offending columns from the schema, so a
newly-added one is named rather than left to ambush the next writer.

**FOUR callers derive one status, and none of them found the others.** The reconcile sweep, the
roster and the analytics page all resolve the same two questions per agent -- which environment owns
it, and which environment its session runs in -- and each answered them once PER AGENT rather than once
per pass. `fab4204c` fixed the roster months ago with a request-scoped dict. Nothing carried that to
the other two, because nothing named the shape: the fix lived in the roster's own handler as a local
variable, so it read as a detail of listing agents rather than as a rule about the derivation.

Both are fixed this round, and both were measured the same way -- counting `aiosqlite` execute() calls,
never wall clock, because wall clock on this host is unusable (the same code timed 44-47ms and then
22-25ms minutes apart; the live fleet is the load).

| path | before | after | at 47 agents |
|---|---|---|---|
| `_run_dispatch_reconcile_once()` | 17N + 44 | 12N + 46 | 843 -> 610 |
| `GET /api/v1/analytics` (cold cache) | 16N + 79 | 11N + 81 | 831 -> 598 |
| `GET /api/v1/analytics/pulse` (cold cache) | 16N + 6 | 11N + 8 | 758 -> 525 |

The pulse board was found only BECAUSE the analytics page was fixed first: once the shape had a name,
the fourth instance was one grep away. That is the argument for naming a shape rather than fixing an
endpoint -- three rounds of reading this code did not surface it, and the fix that did took a minute.

Every model is exact at four measured fleet sizes, not fitted. Analytics costs two extra fixed queries
for five fewer per agent, so it wins from a single agent.

The analytics measurement had to be taken on a COLD live-state cache and that distinction matters: a
warm request costs a flat 79 and was never the problem. What this removes is the spike paid by
whichever request happens to arrive first after the cache expires -- an arbitrary caller, on a
single-worker SQLite service whose lock contention has its own DECISIONS.md entry.

My first attempt at the analytics numbers reused one fixture across fleet sizes and the agents
accumulated, which reported a 16-per-agent slope from a fleet that was not the size the loop said it
was. The figures above come from a fresh database per size with the agent count read back from the API
and printed beside each measurement.

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
has `active_count()` and `online_agents()`; neither has a caller anywhere in production code, and
`online_agents()` reads a map that only a `?agent_id=` connection can populate -- which nothing in
this repo makes. (`analytics.py` has a LOCAL variable of the same name, which is not this method; the
first scan that conflated them would have reported a consumer that does not exist.) So
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

### The Work Loop re-enrols runs whose sender explicitly opted out of a reply

TWO PLACES ANSWER "IS A REPLY OWED" AND THEY DISAGREE. The send path normalises once, at creation:
`_dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type))`, so the
message TYPE supplies a default only when the caller said nothing and an explicit `requireReply=false`
survives into `dispatch_runs.require_reply`. `mcp/stdio/send-tools.mjs` documents that false as
intentional fire-and-forget for exactly `request`/`review`/`error`. `reply_expectation.py` states the
reason: collapsing the two "would lose the difference between 'did not ask' and 'asked for false'".

`_contract_list_query` then re-derives obligation from type and priority anyway:

    r.require_reply = 1
    OR r.message_type IN ('request','review','error')
    OR (r.priority IN ('high','urgent') AND r.message_type NOT IN ('info','response','approval'))

So a sender who deliberately opted out is enrolled in the Work Loop and chased for a reply. The tool
promises one thing and the reminder machinery does another.

CENSUSED 2026-08-27 on the live database, because the size of the change is the whole question. Runs
selected ONLY by the legacy clauses -- `require_reply = 0` and pulled in by type or priority:

| type | priority | status | runs |
|---|---|---|---|
| error | normal | completed | 68 |
| error | urgent | completed | 29 |
| error | high | completed | 24 |
| request | normal | completed | 17 |
| request | high | completed | 8 |
| review | high | completed | 8 |
| request | normal | failed | 1 |

**155 in total, spanning 2026-06-02 to 2026-08-26, and ZERO still open.** That is the number that
decides how this is done: narrowing the clause to `r.require_reply = 1` changes nothing live today.
There is no reminder in flight to cancel and no queue to drain, so the migration risk dev was right to
raise does not currently exist. It is a policy ruling, not a data problem — and it will stop being free
the moment an opted-out `error` run is open, which the table above says happens routinely.

`error` is 121 of the 155, so the practical question is concrete: **should an `error` sent with
`requireReply=false` be chased for a reply?** The send contract says no. The Work Loop says yes.

NOT CHANGED, deliberately. It is the operator's ruling on what the Work Loop is for, and a
one-character SQL edit would flip a user-visible behaviour on a live fleet without anyone deciding it.
Provenance for legacy rows is not recoverable — `require_reply = 0` cannot distinguish "asked for
false" from "omitted before the normalisation existed" — so if the ruling needs that distinction it has
to be declared as typed absence rather than inferred.


**Five attribution columns a rename leaves pointing at a tombstoned name -- newly VISIBLE, and the
ruling is yours.** `test_agent_rename_covers_every_agent_reference.py` claims completeness over agent
references and measured them with a hardcoded set of six column NAMES. Three more names demonstrably
store agent ids, found by deriving the population two ways rather than trusting the list:

- every FOREIGN KEY into `agents` -- 7 columns, all already covered by the six, so this direction was clean
- every column whose WRITER stores an agent id -- which found `requested_by` (`req.from_agent`),
  `handled_by` (`agentId`, from the bridge) and `removed_by`

Widening the set made five pairs visible, now sitting in that file's `UNRESOLVED` bucket:
`terminal_sessions.requested_by`, `terminal_controls.requested_by`,
`environment_controls.requested_by`, `dispatch_controls.handled_by`, `agent_tombstones.removed_by`.

**The argument runs both ways, which is exactly why it is not mine to settle.** Leaving them keeps an
audit trail that says who acted at the time; repointing them keeps one identity consistent across the
single transaction meant to move every other reference together. `removed_by` complicates it further
by sometimes holding the literal `"api"`, so any repoint has to tolerate a non-agent value -- which is
part of the decision rather than an obstacle to it.

They are in UNRESOLVED rather than LEFT_BEHIND deliberately: nobody has DECIDED about them, they were
invisible, and blessing them quietly is what that bucket exists to prevent. Whichever way it goes, each
becomes a LEFT_BEHIND line with a reason or a REPOINTED pair, and the test fails until it does.

**A correction to my own record while confirming this.** I reported `terminal_sessions.agent_id` as an
open rename bug earlier today, from a memory note written on 2026-08-15. It was RESOLVED on 2026-08-19
(v0.6 Phase 4) and the gate records the resolution in its own bucket. The note has been corrected; the
gate was right and my note was eleven days stale.


**The dashboard's biggest payload is 294 KB every 15 seconds, and one function is why it cannot
simply be page-gated.** Measured against the RUNNING service, bytes per refresh slice:

| slice | bytes |
|---|---|
| `/messages/recent?limit=80` | **294,226** |
| `/sessions?limit=80` | 80,224 |
| `/agents` | 63,748 |
| `/environments` | 5,042 |
| `/contracts?limit=80` | 2,569 |
| `/stats` | 2,409 |
| `/settings` | 1,477 |

That one slice is 66% of the cycle. The interval is `dashboard_refresh_seconds`, default 15 and
floored at 5, so it is ~1.18 MB per minute per open tab, and it is fetched on EVERY page.

`state.messages` is the DM store. Its readers are the chat surface -- select, render, click handlers,
message actions -- plus the message inspector. The unread badges elsewhere do NOT read it; they come
from the `agents` payload (`a.unread`), which is why gating looks safe at first glance.

**IT IS NOT SAFE, and the blocker is `buildHandoffPacket`.** It reads `state.messages` for message
BODIES to assemble a handoff packet, and it is reached from `openContinueForm` -- an agent action, not
a chat one. Page-gating the fetch would leave it reading an empty store and producing an EMPTY PACKET
with no error: the silent-degradation shape this review has spent the day removing, reintroduced to
save bandwidth.

**THREE CHEAPER ANGLES WERE TRIED FIRST AND ALL LAND ON THE SAME BLOCKER**, so nobody needs to
re-explore them:

- *Drop a duplicated field.* Per-field measurement of the live payload: `body` is 260,784 bytes
  (85.8%), `preview` 22,267 (7.3%), `subject` 9,875, everything else under 2 KB. All 80 messages carry
  both `body` and `preview`, and in all 80 the preview is a PREFIX of the body (median 244 chars
  against 2,772) -- which looks like pure duplication and is not. `chat-select.mjs:123` prefers
  `preview` for the conversation-list subtitle, so removing it falls through to `body` and renders a
  2,772-character subtitle. Both fields are load-bearing, for different surfaces.
- *Ask the endpoint for less.* `recent_messages` does `SELECT m.*` and exposes only `limit`; there is
  no field selection to use.
- *Add one.* A `?bodies=false` parameter is additive and safe -- and would have no consumer, because
  the dashboard still needs bodies for `buildHandoffPacket`. A parameter with no reader is the defect
  this review keeps finding in other people's code.

The dashboard already has the right tool: `shouldLoadForPage` gates `/spawn-requests` and FAILS OPEN
when the page element or classList is missing, which is the correct guard direction. The safe version
of this change gates the fetch AND makes `buildHandoffPacket` fetch on demand. It has exactly one
caller, so it is contained -- but that caller is synchronous and renders a form from the packet, so it
means making an operator-facing action async. **That is a behaviour change on a live UI, which is
yours rather than mine.** The measurement above is what makes it worth your attention: 66% of every
poll, for a store two surfaces read.


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

### The dashboard refresh bundle, measured end to end — and why none of it is worth changing

496,173 bytes across ten endpoints in one refresh, measured 2026-08-27 against the live service. Kept
here because the next person to ask "what is slow" should not have to re-derive it, and because every
candidate it produced was REJECTED for a stated reason rather than for lack of looking.

| bytes | share | endpoint |
|---|---|---|
| 155,832 | 31.4% | `/spawn-requests` |
| 128,631 | 25.9% | `/messages/recent` |
| 99,122 | 20.0% | `/sessions` |
| 64,496 | 13.0% | `/agents` |
| 37,784 | 7.6% | `/dispatch/runs` |
| 5,042 | 1.0% | `/environments` |
| 2,380 | 0.5% | `/stats` |
| the rest | 0.6% | `/settings`, `/channels`, `/contracts` |

**`/agents` latency is not a finding.** It read 5,345ms on the first pass, which looks alarming beside
`/spawn-requests` at 50ms for three times the bytes. Five consecutive calls: 2321, 3209, 2258, 428,
142ms — a 23x spread with a descending trend, which is cache warming plus fleet contention, not a cost.
Its round-trip count is FLAT in fleet size by design (it caps the recompute per request; 51 at both 20
and 40 agents, per `test_the_status_refresh_is_not_n_plus_one.py`). Wall-clock on this host is
unmeasurable while the fleet is live, and this is what that looks like when you check instead of quote.

**`spawnSpec` is 48,532 bytes, 40% of the largest endpoint.** Its only dashboard consumer is
`inspector-forms.mjs:141`, which reads `record.spawnSpec.metadata` — 16% of it. More concretely, 11,012
bytes are a VERBATIM copy of a field on the same row: `workspace`, `environmentId`, `agentId`, `mode`
and `runtime` are identical on 100 of 100 rows. (`id` and `updatedAt` differ on all 100 and are the
spec's own; `createdAt` is identical on 32 and differs on 68, so it is not safe to treat as duplicate.)
NOT ACTED ON: 11KB is 2.2% of the bundle, and dropping emitted fields from a public-ish endpoint is a
contract change. The reviewer's ruling on `/stats` applies unchanged — in-repo non-use does not prove
external non-use, and a cheap payload does not authorise a break.

**`/messages/recent` ships `body` (87,743 bytes, 75%) AND `preview` (13,848) for the same 80 rows**,
which looks like the same field twice. It is not. `body` is rendered by `chat-render.mjs:148` and
searched by `chat-select.mjs:151`; `preview` is PREFERRED over it at `chat-select.mjs:123` when a
message has no subject. Both have consumers and neither can be dropped.

So the honest total: of 496KB, the changes actually available are a 2.2% contract break and a 0.5%
contract break. Neither is worth having. The bundle is large because the dashboard genuinely shows that
much, not because it is wasteful.


**One dashboard poll costs 134 database round-trips, and `GET /agents` is 98 of them.** Counted with
an `aiosqlite.core.Connection.execute` spy, per slice of the poll bundle, with a twelve-agent fleet
seeded through the real registration route. The rest: `/stats` 20, `/sessions` 8, `/contracts` 2,
`/environments` 2, and one each for `/messages/recent`, `/dispatch/runs`, `/spawn-requests` and
`/settings`. Round-trips rather than milliseconds, because wall-clock on this host is unmeasurable
while the fleet is working -- the same code timed 44-47ms and then 22-25ms minutes apart.

**`GET /agents` is NOT linear in fleet size, which is the answer to the question I went in with.**
Four agents cost 54 statements and twenty-four cost 98 -- 2.2 per agent, not the 12 per agent the
twelve-agent reading suggests. `LIST_AGENTS_REFRESH_LIMIT = 8` caps the per-request status
recompute, and the selection is missing-from-cache first then oldest `refresh_after`, so a large
fleet round-robins rather than starving its tail; the 60s reconcile sweep runs the same refresh
unbounded as a backstop. That was the failure I expected to find and it is not there.

**The remaining lever is real and I did not pull it.** Of the 98, seventy-two are the eight-agent
status refresh at nine per-agent queries each -- `agent_turn_state`, `agent_console_signal`,
`bridge_instances`, `agent_sessions`, two `dispatch_runs` reads and two `terminal_sessions` reads,
one row at a time. Batching them the way `environments_by_machine` and `session_environment_by_agent`
already are would take the poll from 134 to roughly 70. The reason it is not this round's commit:
those nine queries live in seven different `api_core` modules, each owning one, and threading
prefetch maps through all seven touches the status engine while the fleet is live on it. It is a
decision about appetite, not a repair.

**And one duplicate inside that, deliberately left.** `_managed_console_is_booting` runs TWICE per
refreshed agent in one request -- once from `status_inputs` and once from `status_decision`, two
paths that both need the answer -- at two queries a call, which is the `16x` in the measurement.
Memoising it would save 8 of 134 round-trips, 6%, in exchange for threading a cache through the two
most safety-sensitive modules in the service. Not worth it; the number is here so nobody has to
re-measure to reach the same conclusion.

**Showing the exit code in the dashboard too -- NOT worth it, and the reason is where the data
would have to travel.** The two new columns camelCase onto `GET /terminals/{id}` for free, so a
dashboard that fetched a terminal could display them today. It does not fetch one: the dashboard
reads a terminal's end state off the SESSION object (`session.terminalStatus` in
`session-console.mjs:95`), and there is no dead-terminal detail panel to put an exit code in.
Surfacing it there means threading two more fields onto the session payload and inventing a place to
render them -- new surface, for a display-only gain on a question `comms_console_tail` now answers
directly. Revisit if a terminal detail view ever exists for its own reasons.

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

### Dead CSS: 415 class names swept, and the three ways the sweep lies

Prompted by finding `.attention-strip .button-row` styled for a row that no renderer emits. If one
selector could outlive its markup, others could, and `styles.css` is the second-largest non-test file
in the repo.

**RESULT: two dead classes out of 415.** `.console-embed-open` and `.console-embed-hint` had exactly
ONE occurrence each repo-wide -- their own rule. They arrived in `514129a1` with a "hermes iframe
offline caption + open-in-new-tab escape hatch", and the markup was rewritten while the CSS stayed. The
wider `console-embed` family is very much alive, which is what made them survive a casual look.

**The raw scan flagged 18 CLASS NAMES; 16 were false positives and 2 were real.** The arithmetic, so
the population closes: FIFTEEN were caught as composed by the automated check -- six `c-*`, four
`toast-*`, two `p-*`, two `t-*` and `xterm-viewport`. THREE survived it, and of those `.critical` was a
false positive found only BY HAND. That leaves the two deleted. 15 + 1 + 2 = 18.

(`.attention-strip .button-row`, the dead rule that prompted all this, is NOT one of the 18 and never
could be: it is a compound SELECTOR whose class names are both alive. Different population, different
instrument -- which is exactly why a class-name sweep cannot find that kind of death and a DOM query
can.)

The three ways a false positive arose, and every one would
have been reported as dead by an instrument nobody controlled:

  * PREFIX COMPOSITION. `c-answered`, `p-urgent`, `t-review`, `toast-error` and their families never
    appear as literals -- `ui.js:22` builds `toast toast-${tone}`. Detected by searching for the
    prefix immediately before a template hole.
  * WHOLE-VARIABLE COMPOSITION. `.critical` is applied by `analytics-page.mjs:99` as
    `class="usage-pool-card ${sev}"`, where the class IS the variable. The prefix check cannot see
    this; only knowing the value can.
  * THIRD-PARTY DOM. `.xterm-viewport` is created by the xterm library. Our source will never mention
    it and it is not dead.

**A live-DOM sweep is worse, not better.** Querying every selector across all seven pages reported 280
of 789 as unmatched -- but almost all are state-dependent (`.app-shell.nav-collapsed`,
`.metric[data-tone="warn"]`, `.attention-strip .contract` while the Work Loop is clear). Reaching those
states means clicking a live operations UI, which this project has an incident about. The static scan
plus manual verification of survivors is the safe instrument; the DOM sweep is only useful for
CONFIRMING a specific suspect, which is how the original dead selector was proven.

Both controls that matter: a class known to be used must not be flagged, and a class known to be
composed must be detected. The first version of this sweep had neither, and a bash `grep` written with
BACKTICKS INSIDE A DOUBLE-QUOTED STRING silently ran command substitution instead of searching -- it
returned nothing and looked like a clean result.

Not worth repeating on a schedule: 2 findings for 415 names, both cosmetic. Worth repeating when a
feature's markup is rewritten, which is exactly how both of these were orphaned.


### The storage layer: every table has readers, and every join holds

MEASURED 2026-08-27 against the live database, because "is anything accumulating" is the question a
25-table SQLite service invites and nobody had answered it with numbers.

**No table is written and never read.** Counting `FROM`/`JOIN` against `INSERT [OR ...] INTO`/`UPDATE`/
`DELETE` across 493 production files, every table with writes has readers. The first pass reported
`settings` as never written, which was the SCAN: `INSERT OR REPLACE INTO` does not match `INSERT INTO`.
That is why the corrected version asserts `WRITE("settings") > 0` as a control before reporting
anything -- a write-detector that cannot see the commonest upsert form will call a live table dead.

**`agent_live_state` really is vestigial**, as CLAUDE.md says. Last write `2026-06-18T05:42:48Z`, the day
the cache moved in-memory, and zero production DML -- no SELECT, INSERT, UPDATE or DELETE names it,
after excluding schema, tests and fixtures. It is NOT unreferenced: `service/schema.py:46-58` still
creates the table and its index, which is what "retained for schema compat" means. The matches a naive
grep finds are otherwise all the helper `invalidate_agent_live_state`, a function name rather than a
table.

**Referential integrity holds where it matters.** Orphan counts across the main relationships are zero:
read_receipts→messages, agent_sessions→agents, terminal_sessions→agents, spawn_requests→spawn_specs,
dispatch_events→dispatch_runs, terminal_events→terminal_sessions, channel_members→channels.

104 `dispatch_runs` point at a `target_agent` with no `agents` row, and that is CORRECT: all 104 target
one of 9 agents that were removed WITH a tombstone, and the count of runs whose target is in neither
table is 0. Removal writes a tombstone and keeps the run history.

The residue is history, not breakage. NINETEEN DISTINCT SENDER IDS have neither an agent row nor a
tombstone, accounting for 273 message rows between them -- two different denominators, kept apart
because mixing them is how a small population reads as a large one. `_system` is a pseudo-sender the
query should have excluded and accounts for 21 of those ROWS; `dashboard` is the operator; the rest are
test agents from May 2026 plus `manager-bot` (117 rows) and `claude-main` (89 rows) from
2026-08-07..13. Nothing reads a sender expecting a row -- `agentForSession` returns `{}` rather than
undefined precisely so callers can read `.status` off a stranger.

**`environment_controls` is 93% failed (380 of 407) and that is the drain working**, not a fault: every
failure is a `stop` aimed at a superseded bridge ("target bridge never claimed" / "no longer current").
The trend confirms it -- 254 in May, 50 in June, 18 in July, 16 in August. It is also the only
control/event table not pruned (April→August), where `terminal_controls` holds a 2-hour window and
`dispatch_events` three days. At ~100 rows a month that is decades from mattering, so it is recorded
rather than fixed.


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
| dashboard API calls | all 52 `api()` paths vs all 103 declared routes | every one resolves; the two apparent misses were my normaliser -- `/messages/inbox/dashboard` binds "dashboard" to `{agent_id}`, and `/agents/{id}/{path}` is a dynamic dispatcher |
| destructive UI actions | all 8 destructive `data-*` dispatch targets traced to the function that performs the write | every one calls `uiConfirm` first, the shared-file delete with `tone: 'danger'`. Two apparent gaps were the delegation trap: the confirm sits one call deeper than the handler the dispatcher names |
| icon-only buttons | all 164 `<button>` elements in the 78 non-test dashboard sources | ONE unlabelled -- fixed, and gated by a derived scan rather than a pin |
| realtime event dispositions | all 51 broadcast names vs the disposition table AND its gate | the table is complete; the GATE was not -- see below |
| aify-env spawn seam (Phase 8) | the request AND the response, both directions, across two repos | CLEAN. aify-comms sends `{service, launcher, args, cwd, env, label}`; aify-env's `startProcess` consumes exactly those six. aify-env returns `{id, pid, terminal, service}`; aify-comms reads three, and `service` is an echo of what it sent. The one asymmetry: `pty: started.handle.terminal === true` cannot distinguish ABSENT from false, so a version-skewed aify-env would produce a spurious "no pty" line -- visible and harmless, worth knowing, not worth changing |
| agent registration | every key the five registration modules build vs `AgentRegister`'s 23 fields | CLEAN. The two apparent extras were correctly scoped: `appServerUrl` is nested inside `runtimeConfig` (a declared field), and `runtimeState` goes to its own `PATCH /agents/{id}/runtime-state` with its own model. The model ignores extras by default, so an actual stray key WOULD be dropped silently -- which is why this was worth checking rather than assuming |
| `terminal_output` WS payload | all 5 fields the service broadcasts vs what the dashboard reads | `seq` IS sent (`terminal_write_queue.py:255`), so the console's dedup and gap-resync are live rather than silently disabled -- which is what a missing `seq` would have produced, since `Number.isFinite(seq)` simply skips both. `status` rides along with no reader in that branch, but `terminal_stopped` is its own event and defaults to refresh, so the case is covered twice rather than not at all |
| dashboard accessibility sweep | live regions, empty states and control naming, across all 78 non-test sources | SOUND, and better than expected. Toasts announce (`role=alert` for error/warn, `role=status` otherwise, chosen deliberately in a comment); five other live regions cover the api chip, settings, the console stream and the session-changed banner; empty states are a convention (`nothing here` x7 plus specific ones). Two apparent gaps were my grep: a literal-markup pattern cannot see `setAttribute('role', ...)`. Only two real defects existed and both are fixed above -- one unlabelled glyph button of 164, and the prompt dialog's unnamed input |
| hot write paths | round-trips through the four highest-frequency writes, at three fleet sizes | ALL CONSTANT. `POST /dispatch/claim` 9, agent heartbeat 9, `turn-start` 18, environment heartbeat 3 -- identical at N=4, 12 and 24, so none carries an N+1. turn-start is the heaviest and its 18 are 18 DISTINCT statements, so there is nothing duplicated to remove either. Nothing to optimise on the paths a bridge actually hammers |
| bridge write bodies | every identifier lowercased anywhere in a file, against every value sent raw in an httpCall body | ZERO. The defect class fixed twice on the service side does not exist on the bridge. The first run of this scan reported zero because its pattern skipped the path argument and parsed NO bodies at all -- the control caught it, and the corrected scan parses 85 bodies before reporting the same zero |
| paginated limits | all 16 numeric query params in `service/routers/`, and each unclamped one traced to its consumer | 12 clamped at the route by `Query(..., ge=, le=)`; the other four are clamped where they are USED (`lines` at `max(1, min(int(lines or 40), 200))`, `cols`/`rows` by the snapshot view's `max(20, min(..., 500))`) or harmless (`offset`, which SQLite floors at 0) |

**The realtime gate could not see half its own producer.** `realtime-dispositions.test.mjs` exists to
compare the dashboard's disposition table against the events the service actually sends, and it reads
the producer's own source rather than a copy -- which is the right design. Its scan matched
`broadcast(` only. `ConnectionManager` has two senders that put an IDENTICAL frame on the wire:
`broadcast()` writes `{"event", "data"}` to every connection, `notify_agent()` writes the same shape to
one agent's socket. So two real events -- `new_message` from `channel_send.py:218` and
`messages.py:274`, `dispatch_request` from `dispatch.py:233` -- were invisible to every assertion in
the file, and the "49 distinct names" figure quoted in three places was the blind scan's number, not
the producer's.

The consequence is not a missed refresh: an unclassified name already defaults to refresh, which is
the fix that file shipped. It is that the two GHOST tests would REJECT a correct declaration. Proven
by mutation before touching anything -- declaring `new_message` in `IGNORED` failed the suite 9 pass /
1 fail with `ignored but never broadcast: new_message`, a message that is false and that points its
reader at the wrong side. The event is real; the scan was not looking.

Fixed by teaching the scan the second sender, with two tests that go red without it (watched: 10 pass /
2 fail with the scanner blinded, 12 / 0 with it). The counts in both files now say 51 and say which
sender each half comes from.

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
