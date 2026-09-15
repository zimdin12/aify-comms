# One live instance per agent, per host

Status: BUILT 2026-09-15 in aify-wrapper 0.6.5 and aify-comms 0.6.8; aify-env unchanged. The design
below is kept as written; **"As built" at the end records where the build departed from it and why**,
and is the authority where the two disagree. Not yet proven on the live fleet: that needs the
operator's restart (step 5).

## The operator's rule, and the decision that bounds it

> "we should never allow 2 of same agent to run basically (resident or managed, doesnt matter)."

Asked how, the operator chose on 2026-09-14:

- **Leftovers are always killed** before an instance of agent X starts. A leftover is a process that
  belongs to X but whose owner is already gone.
- **A live X is replaced only on an explicit start**: a person runs the launcher, or the dashboard
  Start/Restart, or an agent's `comms_restart`. An automatic start (a message cold-starting a lane,
  the queued-run backstop) is refused and reported, as aify-env does today.

The second rule is not caution for its own sake. Replace-on-every-start was built and reverted:
`aify-env/lib/plugins/aify-comms/terminal-controls.mjs:412` records four working sessions destroyed in
ten minutes on 2026-09-03, when a message woke an idle lane and the host killed the live worker for a
replacement that then parked on a first-run dialog.

## What broke today, measured

mc-senior-dev refused every message after the operator restarted `herdr-aify env`:
`Session 20260715_001441_960b8f already has a live owner (tui, pid 59544, running 3h43m)`.
pid 59544 was the previous generation's `hermes dashboard --port 9272` host, detached, parent gone,
holding hermes' own session lease (`%LOCALAPPDATA%\hermes\runtime\active_sessions.json`). Two more
agents had the same leftover (graph-senior-dev 8822, comms-senior-dev 9341, one since 2026-09-12).

Why nothing reaped it, read from the code:

1. `aify_hermes_kill_prior` (hermes-aify.sh.in:582) reaps by `pkill -f` and `lsof`. From Git Bash
   neither sees native Windows processes, so on Windows the gateway reap never ran.
2. Its fallback `hermes-daemon-cli.js stop` -> `stopDaemon` kills whatever listens on
   `agentEndpoint(agentId).port`, the HASH port, while the gateway sat on the PERSISTED port (9272).
3. `stopDaemon` then clears the port marker regardless, destroying the only record of 9272; the next
   `resolveGatewayPort` found 9272 busy and chose 9273. The marker files show exactly that at 21:48.
4. The `--tui --resume <id>` matcher never matched: the lease holder was the dashboard host.

The general gaps (subagent map, spot-verified): claude's kill-prior is managed-only and needs a resume
handle; codex's detached app-server is orphaned by a hard kill; pi has nothing; aify-env's refusal is
in-memory per daemon and blind to residents; no runtime has an agent -> live-process record.

## Design

### An agent lease on disk, written by the launcher

`~/.aify/agents/<safe-agent-id>.json`, host-local, owned by **aify-wrapper** because every runtime,
resident or managed, starts through its launchers:

```json
{ "version": 1, "agentId": "mc-senior-dev",
  "instance": { "pid": 36200, "startedAtMs": 1789401902000, "runtime": "hermes",
                "mode": "managed", "intent": "start",
                "attached": [ { "pid": 113008, "startedAtMs": 1789398806000, "kind": "gateway" } ] } }
```

- **Claim** (launcher start, before the runtime): read the lease under an exclusive lock file.
  - Recorded instance pid alive **and** its OS start time matches -> it is LIVE.
    `intent == "replace"`: kill its tree and every live attached tree, wait for them to go, continue.
    Otherwise: exit 75 with one line naming the live pid. Nothing is killed.
  - Recorded instance dead -> each attached pid that is alive with a matching start time is a
    LEFTOVER: kill its tree. Then continue.
  - Write our own record.
- **Attach**: a process that starts something detached, or the runtime itself, adds its pid to the
  current instance (`AIFY_AGENT_LEASE=<pid>:<startedAtMs>` is exported by the launcher so a child can
  name the instance it belongs to). Callers: hermes `ensure-host` for the gateway host; codex-aify for
  its app-server; the aify-comms MCP bridge for its PARENT (the runtime process), which covers every
  runtime's main process without each launcher learning its pid.
- **Release**: the launcher's EXIT trap removes the record if it is still ours. A hard kill skips the
  trap, and that is fine: the record then describes a dead instance and the next claim reaps its
  leftovers. That is the whole point of writing it down.
- **PID reuse fails closed**: a pid is only ever killed when its OS start time matches the recorded one
  (the rule `aify-env/lib/orphan-reap.mjs` already enforces with a tolerance). Unverifiable -> left
  alone and reported.

### Start intent, carried from the service

A launcher run by a person in a terminal is explicit: `intent = "replace"` unless
`AIFY_SESSION_MODE=managed`. A managed launch takes its intent from the service:

- New column `spawn_requests.start_intent` (`'start'` default, `'replace'`).
  - `'replace'`: `POST /spawn-requests` with `createdBy == "dashboard"`; `session_restart.py`
    restart/recreate (dashboard or agent `comms_restart`).
  - `'start'`: dispatch cold start (`dispatch_start.py`), the queued-run backstop, agent `comms_spawn`.
- `GET /terminals/{id}/launch` adds `AIFY_START_INTENT` to the overlay, joined through
  `agent_sessions.spawn_request_id`. A session with no request -> `start`.

The live data is why this needs its own field: `spawn_requests.created_by` holds `dashboard` (415),
`sc-manager` (317), `queued-run-backstop` (67)... and a cold start records the SENDER's agent id, so a
message waking a lane is indistinguishable from that sender spawning it deliberately.

### Hermes, now, independent of the lease

`stopDaemon` / kill-prior are fixed to (1) read the PERSISTED port marker, not the hash port; (2) kill
the listener's process TREE; (3) clear the port marker only when nothing is left listening; (4) also
kill the process holding hermes' own session lease for the agent's session id, when it is hermes and
not in the caller's tree. This collects leftovers that predate the lease file, on every platform.

## Order of work

1. **aify-wrapper**: `lib/agent-lease.mjs` (pure plan + fs/probe edges, injected) and
   `bin/aify-agent-lease.mjs claim|attach|release`; tests incl. pid-reuse and lock contention.
2. **aify-wrapper templates**: claim/release in claude-, codex-, hermes-, pi-aify; codex attaches its
   app-server; exit 75 on refusal never blocks an ordinary launch when the helper itself fails
   (fail OPEN on helper errors, CLOSED on kill decisions).
3. **aify-comms service**: `start_intent` column + the four writers + `AIFY_START_INTENT` in the launch
   overlay; tests at the route.
4. **aify-comms bridge**: MCP bridge attaches its parent; hermes `ensure-host` attaches the gateway;
   the `stopDaemon` persisted-port + lease-holder fix.
5. Suites, mutation proofs, pin bump, operator restart, then a live check: restart `herdr-aify env`
   twice with a hermes and a claude worker running, and confirm no refusal and no leftover.

## Not in scope, named

- Cross-HOST duplicates (the same agent on two PCs). The lease is host-local; `session-handles` in the
  doctor still reports shared conversations fleet-wide.
- aify-env's in-memory second-worker refusal stays as the first line for managed starts inside one
  daemon; the lease is what survives a daemon restart and sees residents.
- `aify-env/lib/kill-tree.mjs` and `orphan-reap.mjs` answer part of the same question. aify-env does not
  depend on aify-wrapper today, so step 1 carries its own small implementation; converging them is a
  follow-up, recorded here so the second copy is known.

## As built, 2026-09-15

Where the build differs from the design above, each measured or read rather than decided on paper.

- **The launcher's pid is `/proc/$$/winpid` on Git Bash, not node's parent.** Measured: under Git Bash
  node's `process.ppid` is a short-lived MSYS stub (launcher winpid 88808; three runs gave ppid 130368,
  58336, 19600). `exec` keeps the launcher's winpid as the runtime's parent, and `/proc/$!/winpid` is
  the native pid for `&` and `nohup`. `bin/aify-lease.sh` carries this once for all four templates.
- **`AIFY_AGENT_LEASE` is the launcher pid alone**, not `pid:startedAtMs`. The attach helper reads the
  start time itself, and the claim compares it with the record.
- **A Linux zombie counts as gone.** A killed but unreaped process still answers `kill(pid, 0)` and keeps
  its `/proc` entry, so `isAlive` reads the `/proc` state and treats `Z`/`X` as dead. The real-process
  tests failed under WSL until it did.
- **One process table decides a claim.** A claim reads the table once, and every recorded process's
  identity, the launcher's ancestry and every tree a stop ends come from it. When a record names a
  process that is still running and the table cannot be read, the start is REFUSED (75, retryable).
  Two earlier designs were wrong. The first proceeded and carried unverifiable entries into the new
  record, which let a later automatic start kill a live instance's gateway. The second proceeded and
  wrote nothing, which left a live launcher off the record so the next automatic start ran as well.
  Both came from separate probes (start times, liveness, the table) failing on their own. Every entry
  records `seenAliveAtMs`, which identifies it without a start time: a process holding that pid which
  started no later than that moment is the one that was seen. An entry an older build wrote with
  neither is `unknown`. It never blocks, and nothing of a live one is stopped. A pid the table does not
  list is gone, whatever the entry recorded.
- **The start-time tolerance is 2 s, not 30 s.** hermes' own record of a live process and CIM
  differed by 0.4 ms (measured 2026-09-15). At 30 s, a pid reused within half a minute read as ours.
- **A start inside the live instance is refused.** That covers a launcher that inherited the
  instance's `AIFY_AGENT_LEASE`, or whose ancestor is the instance. Replacing would end its own
  ancestor, and letting it run would be a second instance. No ancestor of the claimer is ever
  stopped. The service unsets `AIFY_AGENT_LEASE` in every managed launch (`NEVER_INHERITED`): a host
  started from an agent's shell would otherwise make every start of that agent read as nested.
- **Only a launch that NAMES its agent replaces a live one; a shell inside a session names nobody.**
  Found live on 2026-09-15, after this shipped: the resident Herdr server had been started from inside
  comms-tech-lead's Claude Code session, so every pane carried `AIFY_AGENT_ID=comms-tech-lead` and its
  `CLAUDE_SESSION_ID` (read from the server's process environment). A bare `claude-aify` typed into a new
  pane started as comms-tech-lead, was read as a person at a terminal, and replaced the live instance.
  The operator then registered that window as general-manager, and two agents held one conversation.
  The nested refusal above did not fire: the server predated the lease, so it carried no
  `AIFY_AGENT_LEASE`, and the instance it came from had long since restarted. Three changes in
  aify-wrapper: an identity that is not `--aify-agent`/`--agent-id` on the command line only starts
  (`startIntent`, `--identity`); a launcher whose environment carries `AIFY_AGENT_LEASE` or
  `CLAUDE_CODE_CHILD_SESSION` first unsets that session's id, mode, intent and conversation
  (`lib/inherited-session.mjs`); and `herdr-aify` starts its server without any of them. `CLAUDECODE`
  is deliberately not a marker: nothing strips it from a managed launch, so a host started inside Claude
  Code would make every worker forget the identity and mode it was given.
- **The lock names its holder** (pid, moment, nonce). A waiter re-asks every 2 s whether the holder is
  alive. The lock is taken over when the holder is gone, or it is older than 2 minutes, or it names no
  holder and is older than 10 s. Takeover is an atomic rename. A start still waiting after 60 s is
  REFUSED (75), not let through. A claim removes only its own lock, and one that lost the lock
  mid-claim writes no record.
- **Trees and ancestry are read from the process table**, not `taskkill /T` or a group signal. A child
  is followed only if it started after its parent, and an ancestor only if it started before its
  child (the same millisecond counts), because Windows never clears a stale `ParentProcessId`. A query
  that failed or timed out is no table (a timed-out CIM query printed 139 of 838 rows). A zombie is not
  listed. The stop wait treats a failed probe as a process still running.
- **claude claims before its managed reap**, and a `--shared` launch runs neither. The reap ran first
  and could end a live instance that the claim would then refuse.
- **The hermes reap stops only what the agent OWNS.** That means a port no other agent's marker claims
  (its persisted port, else its hash port), and a session lease only when no other agent's session
  marker names that session. `stopDaemon`'s port kill skips a port another agent claims. The agent's
  own marker is kept while anything listens on its port, even when another marker names it too.
- **The gateway attach takes its agent from ensure-host**, and never joins a lease inherited from
  another agent's launcher.
- **A blank requester is START.** Only an explicit `dashboard` requester replaces.
- **The intent is stamped on the TERMINAL row, not joined through the session.** `terminal_sessions.start_intent`
  is written by every insert path. A spawn request's terminal copies the request's intent
  (`running_spawn.py`). Consoles and virtual terminals are `start`. `GET /terminals/{id}/launch`
  always sets `AIFY_START_INTENT`. `replace` comes from: a spawn request created by the dashboard
  (explicitly `dashboard`; a blank creator is `start`), `session_restart.py` restart/recreate, and the dashboard session Start
  button.
- **A resident launch's intent comes from the mode.** `startIntent({explicit, mode})`: an explicit
  `AIFY_START_INTENT` wins. Otherwise managed means `start` and anything else means `replace`. The
  launcher unsets `AIFY_START_INTENT` after the claim, so a shell the agent opens later cannot pass
  it on. Switching an agent from resident to managed is an ordinary `start`.
- **The MCP bridge does NOT attach its parent.** That was dropped. Every runtime's main process is in
  its launcher's process tree (Windows) or process group (POSIX). The real-process tests showed that
  `taskkill /T` and a group kill reach a grandchild, so the launcher's own record already covers the
  runtime. Only DETACHED children escape, and those are attached by name: codex's app-server and
  hermes' gateway host (`agent-lease-attach.mjs`, from `ensure-host`, only for a gateway it started).
- **Codex attaches its app-server and releases in `cleanup()`.** Claude claims above its `--shared`
  branch, so a shared launch claims nothing. Hermes claims at the start of its gateway branch and
  attaches the delivery loop. Pi claims before `exec`.
- **The hermes fix is `hermes-prior-reap.mjs`, and only kill-prior runs it.** `stopDaemon({reapPrior})`
  is called with `reapPrior: true` from `hermes-daemon-cli.js stop`, and a sidecar's own stop never
  reaps. The planner stops gateway host trees on the persisted or hashed port, plus hermes' session
  lease holder when its recorded start matches the OS and its command line is hermes. It never stops
  the caller's ancestry. The port marker is cleared only when nothing still names that port. If the
  process table cannot be read, the marker is kept.
- **A stop never enters another agent's lease.** The tree walk treats every pid another agent's record
  names as a boundary, so replacing one agent cannot end a process a second agent attached.
- **Release keeps the record while an attached process is still running** (or cannot be probed). A
  launcher that exits leaving its detached gateway behind hands the next start something to find.
- **A Herdr restore carries `--aify-start-intent=start`.** The launcher strips the flag before the
  runtime sees it and never records it in the replay argv. A reboot restoring panes is automatic, so
  it must not replace an instance some other path already brought back.
- **kill-prior reaps only for a launcher holding the lease.** `hermes-daemon-cli.js stop` passes
  `reapPrior` only when `AIFY_AGENT_LEASE` names a pid. An older launcher, or one whose helper failed,
  has not established that no live instance runs, so it stops nothing by port or session.
- **A failed process listing is no table.** The reap lists processes strictly: a query that failed,
  timed out or printed nothing throws, so the port reads as held and the marker stays. The other
  agents' port claims are read strictly too: an unlistable marker directory or unreadable marker means
  the reap owns no port.
- **The gateway joins the lease the moment it is spawned**, not once it is ready. A gateway that never
  comes up still runs, detached, and the next start has to find it.
- **A handoff of an agent to itself replaces.** `comms_compact` into the same agent id stores REPLACE
  (`start_intent_for_spawn`). Stored as START, the live worker it names would refuse its own
  successor. A handoff to a different agent is a start.
- **Refusal is exit 75, and a helper failure exits 0 with a warning.** A broken helper costs the
  guarantee, never the launch. Kill decisions still fail closed. A lock still held by another start
  after a minute is a refusal, not a helper failure.

- **An instance that ends leaves nothing running, however it ends** (operator, 2026-09-15: "orphan
  processes suck"). `release` now STOPS what the instance attached before giving the lease up, instead of
  keeping the record while it ran. Every claim also starts a **watch**
  (aify-wrapper `lib/agent-lease-watch.mjs`): a detached process outside the launcher's tree that, once
  the instance is gone or its pid reused, runs `AgentLease.collect`. Collect stops the attached processes
  and every child the instance left running. A claim over a dead instance stops those children too.
  Children are found by the dead pid as their parent, and on Linux also by process group: Linux
  re-parents them to init, and under WSL the killed launcher's runtime was found by neither parent nor
  tree until the group was read. Both are bounded by the instance's life, so a reused pid's children are
  never taken.
- **A hermes gateway carries its agent's lease pid as `HERMES_PARENT_PID`**, which arms hermes' own
  parent-death watchdog, so it exits with the launcher. It is pid only: hermes' start-marker check needs
  millisecond equality and treats a mismatch as conclusive. Found live the same day: after every agent
  was stopped, `hermes update` run from an Administrator terminal had relaunched mc-senior-dev's orphaned
  gateway ELEVATED. hermes relaunches serves whose spawner is dead; with a live parent pid it refuses to
  run instead (read in `update_cmd_windows.py`, not observed).
- **An elevated process has no readable command line**, so `gateway-orphans` said no gateway ran while
  netstat showed the socket, and kill-prior could not see it. Both now read the socket table
  (`listening-ports.mjs`). A port an agent's marker claims, held by a process whose command line cannot
  be read, is reported as `unidentified`; kill-prior keeps the marker and names the pid. Neither can stop
  an elevated process.
- **Test files run on their own leaked hermes markers into the real %TEMP%.** The runner seals TEMP, but
  `node --test` on one file did not, and five files wrote `aify-hermes-*` markers for ids like
  `sc-hermes`. They import `tests/_sealed-temp.mjs` first now; a probe running each of the 64
  hermes-related files alone in an empty TEMP found five leaking before and none after.

Four independent reviews shaped the points above: 12 findings on the first build, 11 on the first fix
round, 7 on the second, and a fourth, end-to-end review of both repos. What was deliberately left:

- The queued-run backstop meeting a live but deaf instance is refused. That is the operator's policy
  for automatic starts. aify-env has no respawn loop, so nothing retries in a loop.
- The dashboard "Start console" route is START.
- A dashboard spawn-spec assignment stores the column default, START.
- `seenAliveAtMs` is compared with no tolerance. A clock stepped backwards (VM resume, a large time
  sync) after a pid was recorded could let a pid reused after the step read as ours.
- The takeover's rename-then-restore leaves a microsecond window for two holders. The loser writes no
  record, but what it already stopped stays stopped.
- claude's managed reap (`reap-managed-claude.js`) does its own process matching after a successful
  claim.
- Two hermes port markers naming ONE port make that port nobody's, so a leftover gateway on it is never
  stopped by either agent's reap. The next launch moves one agent to a new port. The doctor's
  `gateway-orphans` row reports such a gateway. Deciding which agent owns it needs evidence the markers
  do not carry.
- The sidecar's own teardown (`hermes-channel.js`, `reapPrior: false`) still clears the gateway markers
  without checking the port, as before this work.
- `requestedBy: "dashboard"` is a string any caller can send. An agent that names itself the dashboard
  gets a replace. Refusing it in `comms_spawn` and `comms_compact` was dropped: nothing authenticates
  the dashboard, so the refusal would move the spoof rather than end it.
- `stopDaemon` still kills by this agent's hash port when no other marker claims it. That port is where
  this agent's own daemon listens, so an unmarked process there is taken as this agent's.

Evidence: the wrapper suite ran on Windows and under WSL, including `an-agent-runs-once-per-host`
with real processes and `a-launcher-holds-the-agent-lease` against the rendered launchers. In
aify-comms, `test_a_start_says_whether_it_replaces_a_live_instance.py` checks the routes, and
`kill-prior-collects-what-a-previous-hermes-left.test.js` runs the real `stop` against real processes.
Mutants reddened the start-intent chain (8 of 8), the reap (8 of 8) and the ensure-host attach call
site (2 of 2) in the first build. After both review rounds, a mutant per fix reddens its test: 22 of 22
in aify-wrapper and 11 of 11 in aify-comms; after the third, 27 of 27 in aify-wrapper; after the fourth,
14 of 14 in aify-comms. One of those 14 survived at first: the reap's default listing losing `strict`
was unobservable until the listing's spawn became injectable. **PASSES IN TESTS, not PROVEN**: no live hermes, claude or codex agent has been
restarted through the new launchers yet. Also ASSUMED: that codex honours the hook trust
`install.sh` writes at runtime (only the hash formula is proven).
