# NOW — working state, 2026-09-03

**Read this first after a compaction.** It is the live state of the work; the long-form task list is
`2026-09-02-master-task-list.md` and the SoC design is `2026-09-02-real-soc-design.md`.

## Standing constraints (do not violate these)

- **DO NOT RESTART OR STOP aify-env.** The operator's team is working on it. Restarting supersedes
  the incumbent and reaps its workers. Ship code, tell the operator, let them choose the moment.
- Publishing a tag is mine to do this time — the operator delegated it explicitly ("you close the
  tag and push it, no need for me").
- All five suites green before every commit: `python -m pytest service/tests -q -p no:randomly -n 8
  --dist loadfile`, `cd mcp/stdio && node tests/run-all.mjs`, `cd service/new_dashboard && node
  --test *.test.mjs`, `cd ~/projects/aify-wrapper && node --test tests/*.test.js`, `cd
  ~/projects/aify-env && npm test`.
- Mutation-prove every fix. Measure rather than assume.

## Where v0.6.1 stands

**Working and proven on real hardware**: aify-env is the process host. It claims spawns, registers
warm agents, runs the workers, streams their consoles, carries input/resize/stop, and reports
liveness — with no aify-comms environment bridge running. Six lanes came up tonight.

### Done tonight (all pushed)

| what | where |
|---|---|
| credential resolution + `bridgeStartedAt` placement | aify-env `91a67c4` |
| `comms_envs`/`comms_spawn`/dashboard answer the CLAIM question, not `status` | aify-comms `c1209fc3` |
| bridge suite 700s → ~130s (bounded pool, longest-first) | aify-comms `b45b00b9` |
| a refused heartbeat says it was refused | aify-comms `b45b00b9`, aify-env `beb009f` |
| `aify-env doctor` reports claiming | aify-env `c1ed5e6`, `85fa07e` |
| service composes the whole launch (`GET /terminals/{id}/launch`) | aify-comms `c40ec1a0` |
| host runs terminal controls | aify-env `251f8cc` |
| POSIX launcher gets its own coreutils on Windows | aify-env `f2b2854` |
| install seal searched for the leak instead of hashing the file | aify-comms `37744ba2` |
| build id could not see `lib/plugins/` | aify-env `9f6b233` |
| subscribe by the runner's id; write/resize/stop via a handle book | aify-env `a56ca9d` |
| liveness reporting + one-worker-per-agent + prompt answering | aify-env `9d3ee87` |
| a dead bridge cannot hold an environment row hostage | aify-comms `80f1cba8` |
| REFUSE a second worker, never kill the live one (reversed my own regression) | aify-env `f91435d` |
| a terminal its host is reporting is not released over a bridge id | aify-comms `fc8d4c52` |

### Done since (all pushed)

| what | where |
|---|---|
| console prompts answered where the screen is CURRENT, not in the route | `274bef7b` |
| `resumePolicy` read from `agents.runtime_state`, gated by `needs_resume_policy` | `274bef7b` |
| **(b) the `aify-comms` command stops being an environment bridge** | this round |
| **(d) docs + skills pass** | this round |

### v0.6.1 remaining

- **(c) The compaction rule.** The seam is BUILT and proven: `answer_for_screen(screen,
  resume_policy=...)`, `needs_resume_policy` gating the query, and `_resume_policy_for_agent`
  reading the agent row. **The RULE is not written, deliberately** -- nobody has captured what
  claude's compaction dialog actually sends. The last matcher written from what a screen "looks
  like" watched its dialog and did nothing with fourteen green tests, because claude emits
  `ESC[1C` where a person sees a space. Capture it with `comms_console_tail` on a worker that is
  actually at that prompt, drop the bytes in `service/tests/data/`, then write the rule.
  The trust dialog is NOT a prompt: `hasTrustDialogAccepted` is per-project state in
  `~/.claude.json`, so write the key rather than answering a dialog.
- **(e) Cut and push the v0.6.1 tag.** `VERSION` already says 0.6.1; `mcp/stdio/version.js`,
  `package.json`, `package-lock.json` and `.claude-plugin/plugin.json` must match, then
  `bash scripts/stamp.sh`, rebuild, re-run `install.sh` (mcp/stdio changed), then tag.

### What (b) deliberately did NOT do

The bridge ROLE is unreachable from any installed entry point, but ~1,800 lines of now-dead modules
remain: `spawn-loop.mjs`, `terminal-control-loop.mjs`, `environment-control-loop.mjs`,
`managed-environment-sync.mjs`, `managed-teardown-sweeps.mjs`, `boot-marker-sweep.mjs`,
`reap-managed-survivors.js`, `terminal-manager.mjs`, plus `IS_ENVIRONMENT_BRIDGE` in
`launch-identity.mjs` and 37 test files that name them. Deleting them touches `server.js`, which
every running wrapper loads as its MCP server -- so it is a v0.6.2 change with its own live
verification, not the last thing done before a tag. **MEASURED before deciding**: nothing is lost
by the removal today. `managed-orphans` reports no delivery loops, `bridge-current` reads
`unknown-all` (no bridge reports a build), `usage/consumption` is empty, and the OpenAI pool is
collected by the SERVICE, not the bridge.

## THE TAG: v0.6.1 WAS ALREADY CUT AND PUSHED, on 2026-09-02

`v0.6.1` points at `a9c963f0` on the remote. **Twenty-eight commits sit after it** -- every fix from
the night of 2026-09-02/03, including the four that decide whether the fleet works at all. So the
instruction "close the tag and push it" was already satisfied before it was given, and the work since
has no tag.

**Not tagged by me, deliberately.** The delegation named ONE tag, and that tag exists; cutting a
different number is a release decision that changes the operator's plan, since `v0.6.2` is currently
reserved in this file for the resident-TUI feature set. Two options, both cheap:

- **ship this as v0.6.2** and move the feature work to v0.6.3 -- the fixes are substantial and the
  published v0.6.1 does not contain them;
- **move `v0.6.1`** to HEAD -- rewrites a published tag, so the operator's call, not mine.

Everything else for a release is ready: `VERSION`, `mcp/stdio/version.js`, both package files and
`.claude-plugin/plugin.json` all read 0.6.1 and agree, the service is deployed and `aify-comms
doctor`'s `service` check reads `build == repo HEAD`, and all three clients are reinstalled.

## sc-coder IS ALIVE, UNADDRESSABLE AND UNRESTARTABLE — the top item

**Found while verifying the orphan fix, on the operator's own fleet.** It is a deadlock, and every
step in it is a component behaving as designed:

1. Something marks a live terminal ended. For `term_1788413610405_82e689c5` the marker is unknown;
   what IS measured is that at 06:47:05 the service appended `reconciled_managed_orphan_worker`
   ("live sidecar but no console PTY = headless orphan; worker killed host-side") -- and that
   reconciler only matches a terminal ALREADY `stopped`/`failed`, so it is a consequence, not a
   cause. The claim in that reason is false: the worker was not killed. It streamed console output
   for another forty seconds.
2. **The worker's own output can never restore it.** `_terminal_status_transition` refuses
   ended -> active by design (`_TERMINAL_MONOTONIC_STATUSES`), which is right for a liveness frame
   and fatal here.
3. The reconciler clears `consoleTerminal`, so the dashboard has nothing to attach to.
4. A restart makes a NEW terminal and a start control. aify-env REFUSES it -- correctly, and this is
   the change that made the deadlock visible instead of destructive: before `f91435d` it would have
   killed the live worker and "recovered" by destroying the session. The refusal names the live
   terminal and its pid.
5. Result: a running claude worker nobody can reach, restart or address. Three failed restart
   terminals from 06:27, 06:33 and 06:39 are the operator hitting exactly this.

**The fix is a re-binding, not a kill.** When a host refuses a start because it already runs a worker
for that agent, the service should adopt the live terminal it names rather than leaving the agent
pointed at a dead row -- the refusal already carries the terminal id and the pid. That is a design
change across both repos and was NOT attempted unattended.

**Do not "fix" it by letting an ended terminal go active again**, and do not let the host stop the
worker: `ec243e9` exists precisely because an end-status orphan rule would have killed this agent.
A terminal the service calls finished while its process is writing is a contradiction to report.

## Known-open, reported, not yet fixed

- **The dev-channels auto-confirm has never fired on the operator's fleet.** It was checked in the
  ROUTE, against a screen that did not yet include the chunk -- and a parked worker sends no later
  chunk, so it never fired once. `274bef7b` moved it into the write path where the screen is
  current, and the service is DEPLOYED (build 274bef7b == HEAD, verified). Still unproven LIVE:
  the next fresh worker is the test. Until then a parked worker needs one `comms_console_input`;
  `comms_console_tail` shows which screen it is on.
- The operator saw an empty/dark dashboard console before the last restart; it cleared afterwards.
  Unexplained — watch for it, do not assume fixed.
- **Claude login expires ~2026-09-05** ("run /login to renew", seen on a worker screen). Every
  managed claude worker starts failing when it lapses. Operator's action.
- 48 registered agents, most last seen in June/July. Dashboard clutter with no retirement path.
- `--channels` may remove the dev-channels prompt entirely, but `claude --help` mentions "channel"
  zero times and the flag parser accepts ANY flag before `--version`, so acceptance could not be
  tested. Do not bet a release on it; investigate in v0.6.2.

## v0.6.2

Headline: **resident TUIs via aify-env, opt-in.** `claude-aify --shared` asks aify-env to host the
PTY and attaches as a client; the default path stays byte-identical to today (exec claude, the
operator's own terminal, zero interposition). Measured cost of the hosted path: 64.6 MB RSS and
504 KB streamed for 14 concurrent PTYs. Then streaming, remote TUI and the herdr-style view in
aify-env are one feature, and a closed terminal stops killing an agent.

Then: A1–A4 (aify-env TUI), B1–B5 (dashboard console, agent browse, doctor in the UI), C1/C3/C5/C6/C7,
D2/D4/D6/D7/D9/D10/D11, and the reviews E1/E2 LAST — a review round adds to the list, so running one
first expands it faster than it shrinks.

## The architecture constraint that governs all of it

The operator is building **aify-dashboard** (cron/tasks, reminders to agents, injection through
aify-wrapper, subservices, project-directory registration, cross-agent mapping) and
**aify-project-graph** will inject project context into agents. So **aify-env and aify-wrapper will
be consumed by services other than aify-comms.**

Consequences, and they are already the reason for several of tonight's decisions:

- **No service-specific knowledge in aify-env or aify-wrapper.** That is why the launch environment
  is composed by the service, why the console-prompt rules are moving out of aify-env, and why the
  launcher resolver stays (a host question) while the screen model goes (a service question).
- **The wrapper's channel model must become N-service.** Today `claude-aify` hardcodes
  `server:aify-comms-channel`. Two more services want a channel each.
- **The plugin seam stays the only place a service is named.** `lib/plugins/<service>/` plus
  `pluginsForServices`; a second service is a second directory, not a second special case.
- PTY hosting, the attach client and the launcher resolver are all harness- and service-agnostic
  already. aify-wrapper renders all four launchers from ONE template, so a per-harness feature is
  written once. Per-harness screen knowledge belongs in aify-comms' runtime adapters, beside
  `session_env_vars` and `console_argv`.
