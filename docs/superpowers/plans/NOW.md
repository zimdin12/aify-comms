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

### In flight, uncommitted right now

**Console prompts moved from aify-env to the service** — the operator's steer, and correct for the
multi-service future. State:

- `service/api_core/console_prompts.py` — NEW. Rules matched against the pyte-RENDERED screen
  (`render_live_screen`), then `plain_text()` strips the SGR codes pyte re-emits. Verified against
  the real capture in `service/tests/data/claude-dev-channels-prompt.raw.txt`.
- `service/routers/terminals.py` — `_answer_console_prompt_if_any` creates an `input` control.
- aify-env's copy DELETED (`lib/plugins/aify-comms/console-prompts.mjs` and its test/fixture).
- `service/tests/test_the_service_answers_a_parked_console.py` — NEW.

**THREE TESTS RED, all mine, all small:**
1. `test_the_service_answers_a_parked_console.py::test_the_REAL_capture_renders_...` — the control
   asserts against `screen` but must assert against `plain_text(screen)`. The rule itself passes.
2. `test_append_terminal_output_split_is_inert.py` — my `terminals.py` edit needs an `EDITED_SINCE`
   entry. Same mechanic as `test_environment_heartbeat_split_is_inert.py`: take the inserted range
   from a `difflib` opcode against `git show HEAD:<file>`, declare it as `(added_text, "")`.
3. `test_no_dead_imports.py` — `console_prompts.py` imports `Any` and defines `DOWN`, neither used
   yet. Remove them (DOWN belongs with the compaction rule when that lands).

### v0.6.1 remaining after those

- **(a) DONE** — `comms_remove_agent` orphan closed by the 404 reconcile in aify-env's control pass:
  a liveness touch that 404s means the service no longer has that terminal, so the host stops the
  process. State-based, so it covers remove, cascade, restore-from-backup, everything.
- **(b) Remove the `aify-comms` environment-bridge command.** `mcp/stdio/spawn-loop.mjs` and
  `terminal-control-loop.mjs` are now dead code. **`mcp/stdio/server.js` MUST keep loading** — every
  running wrapper is an MCP client of it. Verify with `node --check` AND by importing, and run all
  five suites; the bridge suite reads `install.sh` and `service/` too.
- **(c) The trust dialog is STATE, not a prompt.** `hasTrustDialogAccepted` is persisted per project
  in `~/.claude.json` (verified by reading it). Write the key for the spawn's workspace at install
  or spawn time instead of answering a dialog. Compaction stays a live rule and needs
  `resumePolicy` — that is why it must live in the service.
- **(d) Docs + skills pass.** Every doc still says the bridge starts workers. That exact staleness
  cost eight days. Files: `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TARGET_ARCHITECTURE.md`,
  `docs/PHASE8_STATUS.md`, `docs/AIFY_ENV_BOUNDARY.md`, both skill mirrors, every `install.*.md`.
  Skill edits are gated by `skill-size-ratchet.test.js` (a ceiling may only go DOWN — pay for a
  raise) and `test_skill_mirror_parity.py` (byte-identical mirrors).
- **(e) Cut and push the v0.6.1 tag** once (a)–(d) are done and the suites are green.

## Known-open, reported, not yet fixed

- **The dev-channels auto-confirm has never fired on the operator's fleet.** It is now service-side
  and untested live. Until proven, a fresh worker parks and someone presses Enter by hand
  (`comms_console_input`). `comms_console_tail` shows which screen it is on.
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
