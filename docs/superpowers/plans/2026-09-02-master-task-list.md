# The one list, 2026-09-02

Everything outstanding: what the operator asked for, what I found, and what is done. One file so a
task cannot be lost between a compaction and a work order.

**Reviews are LAST on purpose.** A review round ADDS to this list, so running one before the list is
worked expands it faster than it shrinks. The operator's rule.

**Rules for this file.** An item leaves only when it is DONE, DROPPED with proven reasoning, or
CHANGED with the reason recorded. "Probably fine" is not a state. Every claim of done names the
evidence.

---

## 0. FIRST, by operator instruction 2026-09-02

**THE TAGS, as the operator scoped them 2026-09-02:**

| tag | contents | bar |
|---|---|---|
| **v0.6.1** | T1 (tests) + T2 (all 3 SoC steps) + T3 (skills/docs review) + T4 (install overhaul) | a WORKING version: manually tested, proven working, and the INSTALL proven correct |
| **v0.6.2** | every other improvement, all review fixes, and what the EXTERNAL review of v0.6.1 finds | the improved and reviewed one |

*"t1 - optimizing our tests and fixing separation of concerns should be in 0.6.1 (it had to be in
0.6.0 actually)... 0.6.1 needs to be working version of this (manually tested and prooven working and
prooven install to be correct)."*

**v0.6.1 IS CUT AND PUSHED at `b7d77fdf`, 2026-09-03**, force-moved from `a9c963f0` exactly as this
paragraph said it would be: the tag ACCUMULATES and is re-cut when T1-T4 land and are manually
proven -- the operator's *"make tag now, but all fixes... should all be added to this tag later
on."*

Manual proof was a release gate here, not a nicety, and it earned its place twice over: the night of
2026-09-02/03 produced SEVEN separate cases where every automated check was green and the thing did
not work -- the prompt answerer that never fired, the liveness frame that wrote nothing, the guard
whose input nothing wrote, the `claimed` spawn nothing aged out, the orphan rule that would have
killed a working agent, the leaked carrier a permissive fake hid, and a deploy-delta tool that
reported "no change" for every update because its input was CRLF.

What was proven by hand before the tag moved: six managed lanes with no bridge running; a bare
`aify-comms` exiting 2 on the operator's PATH; `redeploy.sh` end to end across three clients with
the delta reporting honestly; the deployed build reading `b7d77fdf == repo HEAD`; and the operator's
four agents still running through all of it.

- **T1. Optimize the test stack. TAG: v0.6.1** (the operator moved it back on 2026-09-02: *"t1 -
  optimizing our tests and fixing separation of concerns should be in 0.6.1"*). **PREMISE TESTED
  2026-09-02, and it did not hold; PARALLELISM DELIVERED 2026-09-03.**
  **RESULT: 22 minutes -> 2m50, 5,281 passing, nothing deleted.** `pytest-xdist` is installed and
  `-n 8 --dist loadfile` is the invocation; 16 workers buys a further 14s and is not worth the
  contention. One real blocker was found and fixed: a `subTest(status=<enum>)` label cannot cross
  execnet's process boundary, so it passed alone and failed in parallel with a traceback naming
  the serializer rather than the file (`79b878c4`).
  **STILL OPEN under T1:** make the parallel invocation the DOCUMENTED default -- CLAUDE.md still
  prescribes the serial command, so the win is available and not yet taken by anyone but me.
  The census the method called for now exists: 487 python files, 4,656 test functions. The dominant
  file, `test_api_v2_regressions.py`, holds 386 of them in 15,822 lines with NO docstring -- and
  breaks into **248 distinct two-word subjects, 180 of which have exactly one test**. The largest
  cluster is nine. That is not redundancy; it is 248 unrelated subjects in the default destination.
  Deleting there removes tests that each prove something different.
  **The cost is wall time, not count.** ~18 minutes for python, on ONE of this machine's 32 cores.
  `pytest-xdist` is not installed. Parallelising removes no coverage at all, which is a better answer
  than deleting tests that catch things. Two obstacles to establish first: the installer tests race
  each other (two false reds on 2026-09-02) and the live-status cache is a process global.
  **First cut done**, and it is the model: `test_install_carries_the_key_to_the_environment_tier`
  ran the real installer TWICE to ask two questions about one recorded behaviour. Now once per
  class -- 58s to 29.4s, same three assertions, sandbox seal re-verified.

  **PARALLEL SAFETY, measured 2026-09-02** -- the question that decides whether the 32-core win is
  real. Of 487 python test files: 20 touch the process-global live-status cache (4%), 87 spawn a
  process or bind a socket (18%), 7 read the operator's home directory (1.4%). So roughly 82% are
  safe as they stand, and the unsafe minority is IDENTIFIABLE rather than diffuse -- which is what
  makes this tractable.
  **The shape:** `--dist loadfile`, so a file's tests stay on one worker and class-level state is
  preserved; the process-global cache is then no worse than today, because it is already one process
  per run. The genuine hazard is two files on DIFFERENT workers touching one external resource --
  the real `~/.claude.json`, the service registry, a fixed port -- which is the 7 plus part of the
  87, and is handled by pinning those to a single worker group.
  **NO LONGER BLOCKED.** `pytest-xdist` is installed and the payoff is measured rather than
  estimated: 22 minutes to 2m50 at `-n 8 --dist loadfile`, with no coverage removed.

- **T1 (original framing).** *"5272 tests !?!?!?!?!? add optimize tests as your first work item
  ... I am sure that there are many duplicates and pointless tests. you have created all of them so be
  sure to unify into end to end tests and make that tests stack more optimal."*
  Counts today: python 5272 (+10367 subtests), bridge 412 suites, dashboard 1515, aify-wrapper 177,
  aify-env 830. A full sweep costs ~20 minutes for python alone, which is why suites get skipped and
  why three runs were lost today to editing mid-run.
  **How to do it without deleting coverage**, in this order:
  1. MEASURE before cutting. Group by what a test PROVES, not by name: the repo's own rule is that a
     second test of the same property is cost without coverage. A census of assertions per property
     is the input, and it does not exist yet.
  2. Kill duplicates by MUTATION, not by reading. Two tests are redundant when the same mutation
     fails both; that is checkable and a name similarity is not.
  3. Prefer ONE end-to-end test over N unit tests only where the units have no independent failure
     mode. Where a unit can fail alone -- every predicate this repo extracted precisely so it could
     fail in a test rather than in production -- collapsing it loses the thing that made it safe.
  4. The suites that ROT are the ones nobody runs: aify-wrapper and aify-env are in nobody's ritual
     and drifted 158->177 and 496->830 unnoticed. Faster suites are worth most there.
  **The trap to avoid**, and it is the repo's own history: several tests here exist because a green
  suite hid a live defect. Any cut has to name which mutation still fails after it.

- **T2. `aify-comms` the COMMAND should not exist** (operator, 2026-09-02): *"we moved to aify-env and
  that old command should be totally removed."* `docs/TARGET_ARCHITECTURE.md:51` agrees --
  "Nothing else. No `aify-comms` command" -- and its table says that command "goes when Phase 8
  flips (open item 2)". Item 2 reads "DONE 2026-08-25: delegation is ON". **The condition was met
  eight days ago and the removal never happened.**
  **What blocks it, precisely:** Phase 8 moved spawn EXECUTION to aify-env and left spawn CLAIMING
  with the bridge. `/spawn` requires `metadata.bridgeLastSeen`, which `routers/environments.py:349`
  writes only for a request carrying a `bridgeId` -- and only a bridge sends one. aify-env's
  advertisement deliberately preserves that field rather than refreshing it, and the comment says
  why: "without the split, a host with no bridge reads `online` off aify-env's beat and accepts
  spawns nothing can claim." That split is CORRECT given a bridge must claim. Nobody took over
  claiming.
  **So the decision is: does aify-env become the claimer?** The operator's stated model says yes --
  *"I only run aify-env in directory, so it can spawn managed in that directory... it uses global api
  key to connect with aify-comms container... and aify-env spawns aify-wrappers."*

  **DONE 2026-09-03, aify-comms `10a202f2`.** aify-env's `aify-comms` plugin took over claiming and
  was PROVEN ON REAL HARDWARE first -- six managed lanes up with no bridge running at all -- which is
  the condition `TARGET_ARCHITECTURE.md` had already set. The command now starts nothing: `doctor`,
  `--check`, `--version`, `--help`, and anything else exits 2 naming aify-env. Gone with the exec:
  the root parser, the workspace-root resolver, the env exports, the start-up banner, and the baked
  API key (which existed only because the BRIDGE could not reach its own service). Two `export` lines
  survive as the labelled install record `scripts/installed-delegation.sh` reads back.

  **Proven on the host, not just in tests:** a bare `aify-comms` exits 2, the delegation record still
  round-trips through the real reader, and all three clients are reinstalled with `skills-installed`
  and `bridge-installed` green. Mutation: restoring the exec reddens three of nine new tests.

  **DELIBERATELY NOT DONE, and it is a v0.6.2 item.** Deleting them touches `server.js`, which every
  running wrapper loads as its MCP server. Measured before deciding: nothing live is lost by the
  removal -- `managed-orphans` reports no delivery loops, `bridge-current` reads `unknown-all`,
  `usage/consumption` is empty, and the OpenAI pool is collected by the SERVICE.

  **THE REACHABILITY MAP, measured 2026-09-03 and pinned by
  `mcp/stdio/tests/the-bridge-cluster-has-one-way-in.test.js`.** NINE modules, not the eight this
  entry listed until now -- `single-agent-teardown` was missing, and it is reached from
  `terminal-control-loop.mjs` alone, so it is as deletable as the rest and was simply not looked for:

  | module | imported by |
  |---|---|
  | `spawn-loop`, `terminal-control-loop`, `environment-control-loop`, `managed-environment-sync`, `managed-teardown-sweeps`, `boot-marker-sweep` | `server.js` |
  | `terminal-manager` | `server.js`, `terminal-control-loop` |
  | `single-agent-teardown` | `terminal-control-loop` |
  | `reap-managed-survivors` | `managed-ownership`, `managed-teardown-sweeps`, `single-agent-teardown`, `terminal-control-loop` |

  Every path ends at `server.js`, at call sites gated on `IS_ENVIRONMENT_BRIDGE` (lines 773, 803, 845,
  859, 875, 889, 899). **So the deletion is one operation, not nine**: make that flag permanently
  false, cut its call sites, and the whole set falls away together. `managed-ownership` is the only
  member `server.js` imports for its own sake and is the one to settle first. 1,802 lines measured,
  which is the "~1,800" this entry always claimed -- note `reap-managed-survivors` is `.js`, not
  `.mjs`, and a scan assuming the extension silently drops its 480 lines, the largest of the nine.

  **BUT THE FLAG IS NOT ONLY THE CLUSTER'S, and that is the correction that matters.** Fourteen
  product files name it; three READ it from outside the cluster, and all three read it NEGATED:

  - `auto-registration.mjs:84` -- `if (IS_ENVIRONMENT_BRIDGE) return;`
  - `bridge-main.mjs:41` -- `if (!IS_ENVIRONMENT_BRIDGE && ORIGINAL_PARENT_PID > 1)`
  - `resident-runtime-lost.mjs:41` -- `if (!IS_ENVIRONMENT_BRIDGE && REMOTE_AGENT_STATE.size === 0)`

  (`loop-gate.mjs` names it in a comment only.) So retiring the flag is not purely a deletion: it
  SIMPLIFIES THE RESIDENT PATH, which is the path every running wrapper takes. The simplification is
  inert in practice -- no bridge starts today, so all three already evaluate the resident way -- but
  it is a live edit to live code and belongs in the deletion's verification, not in its footnotes.

  **Two counts, not one, and they were being conflated.** 37 test files name one of the nine MODULES;
  7 name the FLAG. The flag's 7 are the ones that redden on the day it goes.

  **AND ONE MODULE THE DELETION MUST NOT TAKE.** `server.js` calls `usage-collector.js` from a
  bridge-gated block, so a sweep of "what only the environment bridge uses" reaches for it -- and it
  is LIVE: `doctor.js` and `usage-preflight.js` both import `checkOpenAiUsageAccess` from it for the
  `usage-openai` check. Only the two CALL SITES go; the module stays. It nearly went unnoticed
  because `server.js` imports it ALIASED (`{ collectOnce as collectUsageOnce }`), so asking who
  exports `collectUsageOnce` finds nobody -- the name exists on that one line and nowhere else. Any
  scan keyed on an exported NAME is blind to every aliased import in the tree; `server.js` has two.
  The gate is keyed on the PATH and asserts both facts.

  **The capability really is safe to drop, measured live rather than remembered.** `GET /usage`
  returns exactly ONE pool, `openai-chatgpt-codex`, updated 2026-09-03T10:30:07Z -- and the SERVICE
  produced it: `service/routers/usage.py` calls `collect_openai_pool` on read, falling back to a
  bridge-posted pool only when that fails. There are no usage tables in the database at all
  (`usage_cache` is in-memory), and no bridge has posted one. So the bridge collector contributes
  nothing to what the endpoint serves today. **The first search for this returned a false ABSENCE**:
  grepping the service for `collect_usage` / `usage_collector` / `poll_usage` found zero and read as
  "the service collects nothing", when the function is called `collect_openai_pool`.

  **OPERATOR DECISION, and it is why the deletion stopped here rather than proceeding.** Per-agent
  CONSUMPTION is a different story from the quota pools, and it is the one thing the bridge's removal
  actually retires. `service/new_dashboard/analytics-page.mjs` reads `/usage/consumption`; the ONLY
  writer of that data is `collectConsumptionOnce`, called from the bridge-gated block in `server.js`
  and from nowhere else. Live right now: `{"by_agent":{},"by_model":{},"by_source":{},"totals":{...
  0}}` -- all zeros, because no bridge runs. So the analytics panel is ALREADY empty and deleting the
  bridge does not break it; it makes it permanent.

  Three ways forward, and the choice is a product one rather than a cleanup:
  1. **Move the collector to aify-env.** It is the tier with host credentials that reads the rollouts,
     which is what this collection needs, and the plugin seam is where a service-specific collector
     belongs. Restores the panel.
  2. **Retire the panel with the bridge.** Honest, and stops a dashboard page promising data nothing
     produces.
  3. **Leave it.** The panel keeps reading zeros, as it has been.

  Nothing here should be deleted until that is answered -- removing the call sites first would leave
  `collectConsumptionOnce` a tested, working, uncallable implementation, which is the exact defect
  this project found four times in two days. `usage-collector.js` STAYS regardless: the doctor imports
  `checkOpenAiUsageAccess` from it.

  **The first measurement of this was WRONG, which is why the gate matches both quote styles.** A grep
  for `from "./boot-marker-sweep` reported ZERO importers and nearly justified deleting a module
  `server.js` imports on line 116 with single quotes. A reachability claim is the kind that gets acted
  on destructively; the instrument has to be right before the conclusion is.

  **The gate exists because the risk is re-entanglement, not decay.** Code imported by exactly one
  caller is an afternoon's deletion; the same code with a second caller is a refactor nobody
  schedules, and that is how dead code becomes permanent. It fails if anything outside the cluster
  starts importing it. Mutations: making the scan quote-sensitive again reddens the quote test;
  adding one stray import in `aify-service-endpoint.mjs` reddens two.

- **T3. Review and update ALL skills and docs** (operator, 2026-09-02). After the SoC move, most of
  what the docs and skills say about component boundaries is wrong -- CLAUDE.md, ARCHITECTURE.md,
  TARGET_ARCHITECTURE.md, PHASE8_STATUS.md, both skill mirrors, and every install guide. The
  TARGET_ARCHITECTURE error that hid T2 for eight days is the reason this is a v0.6.1 item rather
  than housekeeping: a document that says a thing is done is how the thing stops being checked.

  **DONE 2026-09-03, aify-comms `10a202f2`.** CLAUDE.md, README, ARCHITECTURE, TARGET_ARCHITECTURE,
  PHASE8_STATUS, AIFY_ENV_BOUNDARY, BRIDGE_SETUP, all five install guides and both skill mirrors.
  Every one of them told a reader to run `aify-comms` to connect an environment, in shell-command
  form. Both ratchets were PAID DOWN rather than raised -- `install.sh` 3019 -> 2977 and
  `dispatch-bridges.md` 27131 -> 26968 -- because the removal took prose with it: the fleet-death
  entry no longer has to teach that a bare `aify-comms` is dangerous, since it refuses.

  One gate caught a false pass while this landed: `install.opencode.md` satisfied "names aify-env's
  installer" only because it contained `install.sh` for aify-comms. Every guide now names aify-env's
  own installer explicitly.

- **T4. The installer, rebuilt for three components** (operator, 2026-09-02): *"install stuff has to
  be good also, it should have changed almost totally, because installing is now including 3
  components and each repo has its own install instructions, also install should ask stuff if not set
  before, install should be also used for updating."*
  Three requirements, each with a defect behind it:
  1. **THREE COMPONENTS, each repo owning its own instructions.** aify-comms, aify-env and
     aify-wrapper install separately today and only aify-comms has a real installer.
  2. **ASK when a value is not already set.** This is C4, and today proved why: a key that existed in
     `.env` never reached aify-env, and the operator was never prompted for one. An installer that
     silently proceeds with a missing value is how that happened.
  3. **THE SAME PATH UPDATES.** Re-running the installer is already the documented remedy for a stale
     bridge, and today it was ALSO the remedy that changed nothing. Update must be a supported verb,
     not a reinstall that happens to work.
  The bar is the operator's: *"proven install to be correct"*, manually, not by a green suite.

  **PROGRESS 2026-09-03.**
  1. **Three components: DONE for reporting.** `scripts/components.sh` reports all three with their
     versions and names each repo's own installer for the ones that are absent; `install.sh` prints
     it. It NEVER RUNS WHAT IT MEASURES -- presence is `command -v`, the version is read out of the
     installed package's `package.json` -- because a bare `aify-env` starts the host tier and reaps
     the predecessor's workers. PAID FOR: `install.sh` is on a ratchet that may only go DOWN, so the
     rendering moved into the reader and a duplicated "Verifier installed" block was folded away;
     the file sits at exactly 2977.
  2. **Ask when unset: LARGELY ALREADY TRUE, and this needed measuring rather than building.**
     `SERVER_URL` is prompted when unset and interactive. The notification hook is MAINTAINED once
     opted into -- `--with-hook` decides whether to install one that is absent, never whether to
     keep one that exists -- via `scripts/hook-installed.sh`. `scripts/api-key.sh` distinguishes
     ABSENT (a valid deployment) from ERROR and CONFLICT, and `install.sh` aborts on the latter two
     rather than writing a keyless config that looks like a host which never set one. aify-env's
     own installer prompts for credentials. What remains is a WSL/second-host pass: none of this has
     been exercised on a machine that does not already have all three components.
  3. **Update as a verb: DONE, and it now proves itself.** `redeploy.sh` ended by printing
     "wrappers refreshed", which is a claim about what it ATTEMPTED -- and this repo's opening line
     is that every deploy path fails silently. It now captures the verifier's verdicts BEFORE the
     refresh and again after, and reports what the update broke, what it fixed, and what was already
     failing. `scripts/deploy-delta.sh` names no individual check: a hand-kept list of "checks that
     answer whether a deploy took" would miss every check added later, which is exactly how four
     scanners hardcoded the doctor's filename. Nine tests, three mutations.

     **It nearly shipped with the bug it exists to catch.** On Windows the verifier's output is CRLF,
     so the parsed state was `ok` with a trailing carriage return and matched neither branch: every
     comparison printed NOTHING and exited 0. A tool whose whole job is to say what changed,
     reporting "no change" for every update, silently. It looked right in a hand-check because
     `sed -i` had normalised the fixture between one case and the next. `test_CRLF_AND_LF_AGREE`
     pins it.

---

## A. aify-env TUI

Asked 2026-08-24, re-reported unmet 2026-08-25, still unmet 2026-09-02. This is the oldest unmet ask
in the file and the one the operator has raised most often.

- **A1. Show the managed agents that are running.** *"i wanted to have some tui for aify-env. so i can
  see all managed agents that are running etc."* (08-24 20:19), then *"i still do not see anything
  under agent, i should se sc-manager and stuff like that correct?"* (08-25 04:46).
  **Why it never worked:** the TUI lists aify-env's OWN process record. Since Phase 8 the managed
  hermes agents were spawned by the aify-comms BRIDGE, not by aify-env -- which is why they survived
  aify-env going down -- so they were not in that record. The TUI has never asked aify-comms
  anything. This was an unbuilt join, not a display bug.

  **DONE 2026-09-03** (aify-env `d78696b`), and it needed NO CODE. The blocker was the premise: this
  tier is now the process host for every managed agent, so they are in its own record. Rendered from
  the operator's live `/health` the same day:

      PROCESSES 4 owned
        ID       PID     AGENT      SERVICE     IO   UP     TITLE
        cf63-p1  215208  sc-lead    aify-comms  pty  2h40m  Claude Code
        cf63-p2  252812  sc-tester  aify-comms  pty  2h39m  Claude Code
        cf63-p3  195104  sc-critic  aify-comms  pty  2h39m  Claude Code
        cf63-p6  161124  sc-coder   aify-comms  pty  1h5m   Claude Code

  **It came right by consequence, not by construction, so the JOIN is now pinned.** `tui.test.js`
  proved the view renders an agent column from a snapshot the test supplies; `health.test.js` did not
  mention `processes` at all. A payload that stopped carrying `label` would blank the AGENT column for
  every agent with both files still green. Seven tests now drive the real `GET /health` and DERIVE the
  required fields from the renderer's own accessors, so a new column fails on the day it lands rather
  than rendering a dash for ever -- which has already happened here once, with `uptimeMs`.

  One false alarm worth recording: the first render read `up -` for every row and looked like a
  defect. It was my probe -- I built the snapshot from `/processes`, which carries `startedAtMs`, and
  the dashboard reads `/health`, which derives `uptimeMs`. Checked before reporting.
- **A2. Control a managed agent's TUI directly from aify-env** (09-02). The operator names
  [herdr](https://github.com/ogulcancelik/herdr) as the model: a persistent headless server plus a
  TUI client that attaches to REAL terminals, not redraws.
  ~~**Open design question, settle before building:** whose PTY is it?~~ **SETTLED BY EVENTS
  2026-09-03: aify-env owns every managed PTY, because it starts them.** The bridge owned none by
  then; there was nothing left to reconcile.

  **DONE, and it was already built** (aify-env `5dc1ebd`). `ConsoleSession` owns the selection and
  the follower, `OutputFollower` streams `/processes/:id/output`, `composeConsole` renders the pane,
  and `aify-env tui` POSTs keystrokes back to `/processes/:id/input` -- attach, not redraw, which is
  the herdr property the operator asked for.

  **PROVEN LIVE:** following `sc-lead` on the operator's host returns its real claude TUI, 170 frames
  in eight seconds.

  **What was missing was the proof, exactly as with A1.** Every console test used hand-written
  frames, so nothing exercised what a real claude sends. Six tests now drive a 933-byte live capture
  and pin two facts only a real one shows: the wire is JSON (backslash-u-0-0-1-b as six characters,
  no raw ESC byte anywhere in 110KB -- a missing `JSON.parse` puts literal escape text on screen),
  and claude MOVES THE CURSOR instead of printing spaces. Three mutations.
- **A3. The TUI shows what the doctor shows** (08-24 16:15, and item 3 of the TARGET_ARCHITECTURE
  work order). `aify-env doctor` exists and works; the TUI does not render it.
- **A4. Bare `aify-env` opens the TUI** (08-24 20:19). Today a bare `aify-env` STARTS the environment
  and supersedes the incumbent. That collides with the standing safety rule and with the operator's
  own 08-24 23:23 ruling that starting means taking over. **Decide, do not assume:** either bare
  `aify-env` attaches when one is already running, or it keeps starting and the ask is retired.

### What herdr does that we should copy

Read from source (`src/detect/`), not from articles.

- **Per-agent manifests as versioned TOML data** (21 of them), carrying `version`,
  `min_engine_version`, `updated_at`, `aliases`. Patterns update without shipping code.
- **`region`** names WHERE on the screen to look: `osc_title`,
  `bottom_non_empty_lines(12)`, `last_non_empty_above_prompt_box`.
- **`priority` per rule**, so arbitration is declared rather than emergent from match order.
- **`not = [{ contains = [...] }]`** negative guards, so a "working" rule cannot fire on a
  confirmation prompt.
- **PTY activity is the working authority**; screen patterns corroborate. `skip_state_update`
  suppresses state entirely when the screen is a transcript viewer rather than live chrome.

**Why this matters to us specifically.** `_terminal_prompt_hint_from_raw` matches prompt markers
anywhere in a 64 KB tail, which is why it is restricted to `claude-code`: an agent's own prose about
"which option" reads as a prompt. herdr solved that generally with regions and negative guards; we
solved it by narrowing to one runtime. See D9.

---

## B. Dashboard

- **B1. The browser pseudo-terminal, fast and good** (08-30 17:33). *"we can use hermes dashboard as
  exmaple, they have worked on theirs for a long time."*
- **B2. A design / UI / UX pass** (08-30, and 08-25's *"easy improvement oportunities (also in
  dashboard web ui/ux)"*). Row 2 shipped three dead CSS rules and triage-tile keyboard access, which
  is not this.
- **B3. The console complaints, from May and never closed:** text gets scrambled, encoding issues,
  feels slow. Each needs a repro before a fix; do not guess.

  **ROOT-CAUSED 2026-09-03, and the first write-up of it was WRONG in its attribution -- corrected
  below.** A snapshot rendered at a width the source was not drawn at re-wraps every line, which is
  what garbled text looks like. `terminal_controls.py` already says so: recording the PTY's
  authoritative cols "kills the live-redraw garble caused by inferred != actual width".

  **THE MEASUREMENT THAT SETTLED IT.** Across 13 live terminals with a substantial log,
  `cols > 0` if and only if a completed resize control exists -- 13 of 13, no disagreement. A
  terminal has NO recorded width until a human opens its console and the fit round-trips. Until
  then the snapshot renders at an inferred width. That first paint is what the operator sees.

  **AND THE CAUSE IS UPSTREAM OF THE SERVICE ENTIRELY.** `aify-env`'s `Runner.#spawnChild` passed
  `{ cwd, env }` to the terminal opener and nothing else, so the `cols`/`rows` the aify-comms plugin
  computed for every start control were dropped one frame before the pty. Every pty was born at the
  opener's default of 120 regardless of what was asked for. **FIXED: aify-env `53b694f`** -- and only
  a POSITIVE size is forwarded, because callers send `0` for "unknown" and `?? 120` does not
  substitute for zero, so passing it through would have made a zero-width pty.

  **TWO CORRECTIONS TO THE FIRST WRITE-UP (`2e378282`), both of which would have misdirected the
  next reader.** It said the zero-width rows were the RESIDENT case; they are not -- every one of the
  13 is a managed spawn (`requested_by = spawn-request`, `claude-aify --aify-agent ... --auto`), and
  no resident mirror appeared in the sample at all. And it said "three live terminals inferring
  exactly 120 is the signature of plain scrolling text, not three 120-column terminals" -- backwards.
  They ARE 120-column terminals, because 120 is precisely the default those ptys were born at. The
  inference was right and I called it wrong. The controlled experiment behind that claim was sound;
  what was unsound was attributing it to live rows without first asking what width those ptys
  actually had.

  **STILL OPEN under B3, and the aify-env fix is LATENT for this path -- say so rather than claim a
  win.** Measured on the live control table: every START control carries `cols = 0, rows = 0` (35 of
  them); only RESIZE controls carry real dimensions (156/157 x 32, 43 of them). So the service never
  chooses a spawn size, `53b694f` finds nothing positive to forward for a managed spawn, and the pty
  is still born at 120. That fix is correct and it matters for callers that DO name a size --
  `aify-env run`, `--shared`, and the aify-dashboard callers coming -- but it moves nothing for the
  fleet today.

  **THE REMAINING HALF IS BUILT, 2026-09-03** -- aify-env `e7974fb`, aify-comms below. The service
  cannot record a width it never picked, so the tier that owns the pty reports it: `Runner.start`
  reads the size off the pty itself and hands it back, the aify-comms plugin sends it beside the pid
  on the completed start control, and `update_terminal_control` records a REPORTED size on any
  completed control. A generic dimension on a process report -- no service knowledge, so it sits
  inside the constraint.

  Three design points, each with a test that fails without it. The size is read off the PTY rather
  than echoed from the request, because a caller that asks for nothing gets the opener's default and
  a second copy of that default drifts. Only a POSITIVE size is sent or recorded, because a zero
  recorded as a width denies the renderer its own fallback and is worse than no width. And a
  REPORTED size beats a REQUESTED one, because a request is a wish -- a host may clamp or refuse it,
  and only the host knows what its pty took.

  **The gap a mutation found.** Deleting `cols` from the handle `Runner.start` returns left every
  existing test green: they all assert what reaches the OPENER, never what comes back. A field with
  no reader is the same defect as one with no writer, and only mutating both ends showed it.

  The alternative -- having the service pick a spawn size and send it in the start control -- is
  worse: it makes the service guess a terminal geometry it has no view of, which is the same class
  of mistake as inferring the width from drawn cells.

  Note anything landing in aify-env is inert until a restart, which is the operator's call.

  **Explicitly NOT closed by any of this:** "encoding issues" and "feels slow" still have no repro.
  Width explains re-wrapping; it does not explain a wrong glyph. One diagnosis must not close three
  complaints.

- **B4. The doctor, visible in the dashboard** (09-02). *"i never go to that path... i have container
  that should give me that info. some random path for aify-comms doctor... no. will never use it."*
  Four checks are answerable from the service's own data with no host agent -- `env-bridge`,
  `session-handles`, `context-window`, `bridge-current`. The rest need a host reporter, which is
  aify-env's job under TARGET_ARCHITECTURE.
- **B5. Browse an agent and its processes** (09-02). *"i cannot still check the processes themself?
  (like browse agent or something)"* **FIRST SLICE DONE 2026-09-03: the PROCESSES panel.**

  Measured before building: a drawer already existed and answered everything ABOUT an agent --
  runtime, mode, environment, workspace, session, machine, last seen -- and nothing about what is
  running for it. A pid was reachable only by reading the database by hand.

  `agent-processes.mjs` fills a panel in that drawer from `/terminals?agentId=<id>&status=all`:
  terminal id, status, PID, size, last update.

  Four decisions, each with a test that fails without it:
  - **`status=all`, not `live`** -- a row reading `stopped` while still holding a pid IS the orphan
    the operator went looking for (aify-env owned a live PTY for `ef-manager` pid 155844 while every
    recent session read `stopped`). Filtering to live hides the case the panel exists for.
  - **The PID gets a column**, because `/terminals`'s own docstring says it is the field aify-env's
    listing shares -- it is what lets a person match a row here against something alive on the host.
  - **A zero SIZE is flagged with why**: cols is 0 until a resize control completes, so that console
    is rendered at an inferred width and re-wraps every line. This is the one place a person can see
    which terminals are exposed to B3.
  - **Fetched when the drawer opens, never polled.** The refresh is already nine endpoints; a tenth
    paid every tick to fill a panel nobody opened is the trade this avoids.

  A failed read says so rather than rendering "no processes" -- claiming an operator has no terminals
  when the read failed is worse than admitting the panel is broken.

  **A MUTATION FOUND A GAP IN MY OWN GUARD.** The call-site test asserted the container exists;
  deleting `loadAgentProcesses(...)` from the drawer left it and all 36 tests green -- a slot nothing
  fills, which is the disconnected-call-site defect one layer along. Closed by driving the REAL `api`
  with `fetch` stubbed, so the assertion covers container, URL, response and render without a live
  GET against the operator's own service. Five mutations now each redden their own test.

  **SECOND SLICE DONE: RECENT RUNS.** `agent-runs.mjs` filters `state.runs` -- already polled, so no
  fetch at all, unlike terminals which nothing had loaded. Status, subject, requested-age, newest
  first, sorted HERE because `state.runs` is ordered for the runs PAGE and five rows out of a
  differently-ordered page are an arbitrary five.

  Three decisions with tests behind them. **The window is partial and the panel says so**: a limit=80
  page reached back only to 26 August, so an agent whose last run fell off it renders as an agent
  with no runs -- and "no work ever reached this agent" versus "none in the loaded page" are opposite
  answers, the same distinction `run-inspector.mjs` already makes. **It says what it shows out of
  what** ("Showing 3 of 7 loaded"), because a bare count is a count of the panel, not of the agent.
  And **a reply owed on a SETTLED run is flagged** while one on an open run is not: pending on a
  running run means "not yet", pending on a completed one means somebody is waiting, and it is
  invisible in a status column reading `completed`.

  **A mutation found a weak fixture, not a weak guard.** "A blank agent id matches nothing" passed
  with the guard deleted, because my fixture used a run with a real target -- which the filter
  excludes anyway. Measured: without the guard, a blank id paired with a run whose OWN target is
  blank returns a row, since `"" === ""` matches. The guard was load-bearing and the test was not
  reaching it. Five mutations now each redden their own test.

  **Still open under B5:** the console behind the same click.

  **AND A NAMED FLAKE, previously unnamed.** aify-env's `doctor-live.test.js` --
  "against a live environment, owned processes are reported as PASSED rather than unanswered" --
  fails roughly one run in three under the full 1,075-test suite and passes every time alone. It is
  properly isolated (`--port 0`, a sealed `AIFY_ENV_PROCESS_RECORD` in a temp dir, its own registry),
  so it does NOT touch the operator's daemon; that was checked by reading it, because a test that
  starts an aify-env is the shape that reaped five agents once. The candidate cause is its 20s
  daemon-start budget on a loaded host -- NOT diagnosed, just the first thing to look at.
- **B6. A login prompt instead of a URL parameter.** DONE 2026-09-02, deployed and verified.

---

## C. Other operator asks

- **C1. Triage every tool: are they reasonable, do they have good descriptions** (08-30). Partly done
  -- Row 3 fixed several descriptions -- but no pass over the whole surface.

  **THE INSTRUMENT NOW EXISTS**, built for C6: `mcp/stdio/tests/tool-surface-size.mjs` reports every
  registered tool with its description and schema cost. 30 tools, 14,491 characters. That turns "a
  pass over the whole surface" from a reading exercise into a ranked list, and the ratchet beside it
  means anything the pass saves is kept rather than left as room to regrow into.

  **One finding worth keeping, and one number withheld.** `comms_agents`, `comms_envs` and
  `comms_usage` carry no `.describe()` text because they take NO PARAMETERS -- correct, not a gap,
  and worth writing down so the next reader does not re-open it. A quick scan for tools whose fields
  lack `.describe()` reported seven; a CONTROL on one of them (`comms_send`'s `type`) showed it is
  described, across three lines with commas inside a `z.enum([...])` that the scan's regex stopped
  at. The number is a parser artifact and is not recorded here. A real count needs the span-aware
  parser `tool-surface-size.mjs` already has, pointed at fields rather than at whole registrations.
- **C2. Security work developed AND tested** (08-30). Round 7 closed and today's auth defects fixed.
  Residual: C4 and D7.
- **C3. SoC review of aify-env and aify-wrapper** (08-30), so a project unrelated to aify-comms can
  reuse them. **MEASURED AND ONE DEFECT FIXED, 2026-09-03** (aify-wrapper `c25a4b3`, pin
  `mcp/stdio/package.json`).

  **aify-env is clean.** 8 code mentions of `aify-comms` inside `lib/plugins/`, ZERO outside it. The
  21 product files elsewhere that name it are ALL comments -- a distinction worth making, because the
  raw file count reads like a wholesale violation and is not one. Positive-controlled: the same scan
  finds the 8 inside the seam, so the zero is an answer rather than a broken instrument.

  **aify-wrapper's identity axis is clean too.** The templates carry ZERO non-comment mentions, and
  the channel is DERIVED -- `server:@@SERVICE_NAME@@-channel` -- so it cannot disagree with the
  service it belongs to. The operator's "the wrapper's channel model must be N-service rather than
  aify-comms-only" was already satisfied.

  **The LOCATION axis was not.** `NATIVE_BASE="$HOME/.aify-comms"` was set before the arguments were
  parsed, so `--service my-service` with no `--native-base` rendered a launcher whose every NAME
  followed my-service and whose bridge directory, host repo and codex log root pointed at
  AIFY-COMMS' dotfolder. Silently, and only in the paths. `a-launcher-can-serve-another-service.test.js`
  EXCUSED exactly that, filtering `.aify-comms` paths out of its "no executable line names
  aify-comms" assertion -- the defect written down as a rule. Fixed by deriving
  `$HOME/.$SERVICE_NAME` after the name is validated; `--native-base` still wins outright, and the
  default render is byte-identical because SERVICE_NAME defaults to aify-comms.

  **Still open under C3:** nothing has been measured about aify-env's or aify-wrapper's HTTP contract
  shape for a second consumer -- this pass covered naming and paths only. aify-dashboard and
  aify-project-graph will be the real test of it.
- **C4. The install-time key** (09-01, restated 09-02). *"when installing first time agent should ask
  for it. if installing aify-comms + aify-env + aify-wrapper (full local install) then ofc all these
  can have one ask and use same key."* ~~The installer now CARRIES a key it finds; it does not yet ASK
  for one.~~ **ALREADY DONE -- this entry was stale, corrected 2026-09-03 by reading the code rather
  than the note.**

  `scripts/api-key.sh --ask` exists and `install.sh:2707` calls it on every install that is not
  `--with-api-key`. It resolves shell then `.env`, and only when both are empty does it prompt --
  reading from `/dev/tty` rather than stdin, with echo off, restored on every exit path including an
  interrupt, and persisting to `.env` so the next command does not ask again.

  **BOTH BEHAVIOURS VERIFIED, not just read.** On this host `--ask` with stdin closed returns the
  existing key and never prompts. On a keyless host with no terminal it is silent, writes nothing and
  exits 0 -- "no terminal means no ask", which is what keeps an unattended install honest.

  **And one ask already serves all three components**, which was the other half of the request:
  `install.sh:2711` pipes the resolved key to `scripts/credential-carrier.sh`, which puts it in
  aify-env's credential store and returns the `CREDENTIAL_REF` written into the registry; the wrapper
  reads it through `keyEnv`. Nothing further to build.

  **FOUND WHILE VERIFYING, and it is the operator's call: THE LIVE API KEY IS 6 CHARACTERS.**
  `MIN_KEY_LENGTH` in the same script is 32, and `key_is_weak` warns below it -- "it reads as
  protection while being guessable". Cross-checked two ways: the resolver returns 6 characters, and
  the credential-store file is 7 bytes. Not touched, because rotating it 401s every installed bridge
  until each is reinstalled, and `--with-api-key` deliberately REUSES an existing key rather than
  rotating for exactly that reason. Worth a deliberate rotation the next time the fleet is being
  reinstalled anyway.
- **C5. Terminal write path, the full fix** (operator chose the full option 09-02): move the two
  status-path readers off the stored tail, then write it lazily. Scoped: both readers want the
  rendered screen, and the live screen is already rendered, so they skip a pyte render as well as a
  database read. `claim_block_reason.py` stays on the raw log -- it compares marker POSITIONS in the
  stream and a rendered screen loses that ordering.
- **C6. `comms_send` unslop. DONE 2026-09-03**, and the surface it lives on is now gated.

  `COMMS_SEND_TOOL_DESCRIPTION` went 2,638 -> 1,794 characters, a 32% cut, with the reply contract
  intact. The test applied to each sentence was whether it changes what the CALLER does. What went:
  the resident-vs-environment-managed trigger mechanics (true, and the caller does not choose the
  delivery path); the `managed_reply_capture_fallback` safety net, at 294 characters the longest
  sentence in the description, which named a config flag the agent cannot read and then said not to
  rely on the behaviour; deliverability stated THREE times; and the two busy branches and two
  queueIfBusy clauses, each one fact split in half. "Do not send a courtesy acknowledgement" became
  "leave it unanswered" -- a prohibition makes the banned behaviour more available, not less.

  **AND THE WHOLE SURFACE IS MEASURED NOW, which it never was.** `tests/tool-surface-size.mjs` parses
  every `server.tool()` registration -- descriptions AND every `.describe()` on the schema, because on
  several tools the schema half is the larger one -- and reports **30 tools, 14,491 characters, ~3,623
  tokens**, re-read by every agent on every turn. `tool-surface-ratchet.test.js` holds each tool at
  its measured size, may only go DOWN, fails on a tool with NO ceiling, and fails on a ceiling left
  slack above its tool so the ratchet cannot quietly become a cap. Four mutations, each reddening
  exactly its own test; a fifth broke the parser and correctly reddened the positive control.

  **A gate caught a cut that went too far**, which is the system working:
  `test_the_reply_flag_text_matches_the_contract` requires every agent-facing mention to NAME the
  types it does not exempt, and shortening "enrols `request`, `review` and `error` by type" to
  "enrols by type" broke it. Restored, at +32 characters.

  **THAT FOLLOW-UP WAS WRONG, corrected 2026-09-03 by reading the test instead of assuming.** I
  recorded that the SSE transport's own `comms_send` docstring was "a second copy of one contract
  with nothing making the two agree". It is a second copy, and it IS gated:
  `test_the_reply_flag_text_matches_the_contract.py` lists four agent-facing places -- both skill
  mirrors, `mcp/stdio/send-tools.mjs` AND `service/sse/send_tools.py` -- and derives the bound type
  set from the SQL in `_contract_list_query` rather than hardcoding it. That is exactly the test that
  caught the cut above. No new test: a second test of the same property is cost without coverage.
- **C7. Hook source: repo rather than the installed copy** (decided 09-01, reverses `e8856126`).
  **NOT EXECUTED -- the decision is recorded, the REASON is not, and the two point opposite ways.**

  What the launcher does today: `claude-aify` writes a temporary `--settings` file wiring
  `SessionStart` and `UserPromptSubmit` to `node "${AIFY_BRIDGE_DIR_FWD}/claude-session-hook.js"`,
  i.e. the installed native copy. `e8856126` put it there deliberately, and its argument is on the
  record: one launcher had TWO deploy models, with the hook silently opting out of the load-speed and
  "security fixes flow on reinstall" properties the native copy exists to provide.

  **Measured 2026-09-03, and it does not support the reversal.** The installed hook is byte-identical
  to the repo's (3,320 bytes both sides) and `mcp/stdio/claude-session-hook.js` has not changed since
  2026-07-18. So there is no live drift, no stale hook running, and nothing in the current state that
  the reversal would fix.

  That leaves one plausible reason and it is a guess: a hook edited in the checkout takes effect
  without re-running `install.sh`, which is developer iteration speed traded against the reinstall
  property. **Say which it is and this is a ten-minute change.** Reversing a deliberate fix on an
  inferred motive is how a correct thing gets undone quietly, and the counter-argument here is
  already written down in the commit being reversed.

---

## D. Found while working, still open

- **D0. DONE 2026-09-03. `comms_envs` reported `status`, which is not whether a bridge is live.**
  The same defect the doctor had, in the tool AGENTS read: measured 2026-09-02, `comms_envs` said
  `windows:StevenZ-L:default [online]` while `/spawn` returned 409 in the same minute, and
  sc-manager -- correctly trusting the tool -- reported the fleet ready and was refused six times.
  **What was built.** The claim predicates left `doctor-predicates.js` for `spawn-claimer.mjs`, a
  pure leaf, because an MCP tool group importing the doctor to answer a question that is not the
  doctor's would drag its filesystem and home reads into every agent's bridge. Both tools now ask
  it, in the service's own order: advertised first, then claimable. The listing's BRACKET is the
  claim answer (`can spawn` / `CANNOT SPAWN: no bridge since 26h ago` / `spawn UNPROVEN`), and
  `advertised:` moved to the second line, named as what it is. `comms_spawn` auto-selection prefers a
  proven claimer and falls back to an unproven row rather than refusing -- the host cannot read
  `bridge_instances`, so refusing there would refuse environments that work.
  **PROVEN BY MUTATION:** answering from `status` again reddens five tests. And the work found a
  live defect of its own -- `envs.map(summarizeEnvironment)` passes the ARRAY INDEX as the clock, so
  every row aged against `now = 0` and read as a corrupt timestamp. Caught by the first test that
  asserted the rendered bracket, which is the argument for asserting rendered output.
  **The DASHBOARD had the same defect and is fixed in the same change** -- it is the tool the
  OPERATOR spawns from, so leaving it would have fixed the agents' instrument and not theirs. It
  preselected a host by `status`, offered "Spawn here…" on hosts where nothing could claim, and
  counted "Online bridges" as if that answered the question. The service now DERIVES the answer once
  (`spawnClaim` on every environment row, judged by `SPAWN_CLAIMER_FRESH_SECONDS` rather than the
  operator's advertisement window) and the page READS it -- so there is no fourth copy of the rule.
  `spawnClaim` lives in `status.js` beside `resolveStatus`, the canonical resolver, and both the
  environments page and the agent-edit form import it. It FAILS OPEN on an absent stamp, because the
  service settles that against `bridge_instances` and a page cannot. Mutation-proved on both halves:
  answering from `status` again reddens five bridge tests and five dashboard tests.
  **The skill got SMALLER**: it no longer has to warn a reader to distrust the tool, so its ceiling
  came down 17 characters and the debug reference's by 8.
  The service's 409 was rewritten too. It ended "moving the claim into aify-env is open work, not a
  setting" -- true the day it was written and false the next, once aify-env's plugin shipped a claim
  loop. It now names the missing capability and the remedy and says nothing about the roadmap, with
  a test asserting the phrase "open work" is absent.
- **D10. A skipped doctor check reports `ok: true`. FIXED 2026-09-03.**

  The HUMAN report was always honest -- `markFor` answers "–" before it looks at `ok`. The defect
  lived entirely in `--json`, which is the surface an INSTALLING AGENT parses: it keys on `.ok`, saw
  `true`, and could not tell "this passed" from "this never ran".

  **Derived from `code`, because there are TWO producers.** The `skip()` helper is one; predicate
  modules are the other -- `bridgeCurrentVerdict` returns `code: "skipped"` through `add()` and never
  touches `skip()`. A fix applied to the helper alone would have left the predicate path still
  claiming a pass, which is the half nobody would have noticed.

  **`--strict` is deliberately unchanged.** On Windows `bridge-running` and `agent-identity` ALWAYS
  skip, so making a skip fail would turn every ordinary Windows run red -- a worse lie than the one
  being fixed, dressed up as strictness. `ok` still means "nothing FAILED"; `passed`, `failed` and
  `skipped` are counted separately so "nothing failed" cannot be read as "everything was checked".

  Shipped as `doctor-report.mjs` rather than inline, because importing `doctor.js` RUNS the doctor --
  the same split `service-check.mjs` already makes. Live: 15 checks now report 7 passed / 6 failed /
  2 skipped, and the summary says "2 skipped, so NOT verified here" instead of a bare pass count.

  **A mutation found a hole in my own test.** "Skips do not fail the run" stayed green when the
  exclusion was deleted, because the fixture carried `ok: true` and never reached the deleted clause.
  Closed with a fixture in the shape `buildReport` itself emits. Four mutations now each redden their
  own test.

  **This makes D11 more visible rather than less.** Checks that skip because the API key could not be
  resolved now read as NOT VERIFIED instead of passing, which is the honest state and the one that
  should prompt the D11 fix.
- **D11. The doctor finds the API key only from the repo checkout. FIXED 2026-09-03.**

  **Reproduced first, with the INSTALLED doctor from the home directory.** It lives under
  `~/.aify-comms`, whose parent is not a git checkout, so `findRepo()` returns null, there is no
  `.env` to read, and EIGHT checks lost their answer against a service that was up and rejecting
  them: `env-bridge`, `bridge-current`, `context-window`, `session-handles`, `env-processes`,
  `managed-orphans`, `gateway-orphans`, `api-exposure`.

  **The fix is a third source, LAST in precedence so it is purely additive:** shell, then the
  checkout's `.env`, then aify-env's credential store -- `~/.aify/services.json` names this service's
  `credentialRef` and `~/.aify/credentials/<ref>` holds the key. Where a checkout exists the
  resolution is byte-for-byte what it was.

  **Verified on the real host, not only against fakes.** With no repo and no shell key the resolver
  returns the store's key, and it is the SAME VALUE as the one in `.env` -- so the fallback
  authenticates identically rather than merely producing something. That equality is the control:
  without it the test proves a key was found, not that it works.

  **A path-shaped `credentialRef` is refused rather than opened**, reusing `credentialRefProblem` --
  already this repo's cached copy of aify-env's read-time grammar. The registry is a shared file
  other installers write, so a ref carrying `../` is not hypothetical, and this code opens whatever
  it is handed.

  **Second cached decision on that seam, so it gets the same agreement test.** aify-comms cannot
  import `credential-store.mjs`, so the directory name is written once here and proven against
  aify-env's own constant. It matters because a wrong directory fails EXACTLY like an absent key: the
  resolver reads quiet on ENOENT by design, so a rename on their side would silently return the
  doctor to being blind everywhere but the checkout. Mutation-proved by renaming it in aify-env.

  Five mutations in all, each reddening exactly its own test. Two exports with no consumer
  (`REGISTRY_ENV_NAME`, `credentialRefIn`) were caught by `every-export-is-named-by-a-test` and made
  module-private rather than given tests to satisfy the gate.

- **D1. DONE 2026-09-02.** aify-env restarted, read the credential, and advertises: `advertising:
  true`, `acceptedAt` set, `fresh: true`. The 401 is gone. Root cause was `aify-env credential set`
  doing nothing through the dispatcher (fixed, aify-env `a598e7b`) plus `install.sh` gating the carry
  on `--with-api-key` (fixed, `a3ec9ed0`).
- **D2. Duplicate session handles.** 3 sessions claimed by 8 agents, measured 09-02. Report-only; the
  mechanism is still unknown and two traced explanations were disproved against hermes' own source.
- **D3. Orphaned hermes gateways. CLEARED 2026-09-02, and the open question is answered.** 15
  processes across 5 gateways (5 `hermes.exe` + 10 `python`, three per port). **They live at least 45
  HOURS** -- measured at 2736, 2699, 1803, 1801 and 1563 minutes old -- so nothing reaps them, which
  the doctor's own note recorded as never established.
  Killing the five trees produced a brief cascade: the orphaned delivery loops respawned gateways,
  the doctor caught 12 mid-flight, and both they and the loops were gone seconds later. Sustained
  measurement afterwards: ZERO `hermes.exe` over 18 seconds, and `hermes update` is unblocked.
  **What is still unfixed is the LEAK, not this instance.** aify-comms' survivor sweep runs at bridge
  BOOT and on GRACEFUL shutdown, so an abrupt kill is not covered -- which is how five gateways
  reached two days old. A state-based reaper is the shape this repo already prefers for exactly this
  (see `state-based-cleanup-over-event-based`).
- **D4. SPLIT-M1: no gate compares VERSION against the released tag. FIXED 2026-09-03.**

  **The defect was live when the gate was written**, which is the best proof it works: VERSION read
  `0.6.1`, `v0.6.1` sat at `b7d77fdf`, and HEAD was 24 commits past it -- every existing version gate
  green, because they only check the five declarations agree with EACH OTHER. Five files can agree
  perfectly on a number that stopped being true the moment the tag was cut.

  `test_version_is_not_an_already_released_tag.py` refuses exactly one claim: "I am the release `vX`"
  from a tree that is not the commit `vX` names. It stays silent when no such tag exists, because
  developing against an unreleased number is the ordinary state and demanding a tag would demand a
  release per commit.

  **VERSION is now `0.6.2` across all five declarations**, which is the truth the operator's own state
  line already carried: v0.6.1 shipped at `b7d77fdf`, v0.6.2 is in flight. `scripts/stamp.sh` re-run.
  The number reaches `/health`, the root endpoint, `/openapi.json`, Dashboard Next, every MCP
  handshake and `bridgeVersion` on the control plane -- so until now a live bridge reporting `0.6.1`
  could not be told apart from a genuine v0.6.1.

  Mutations: putting VERSION back to `0.6.1` reddens it; blinding the git helper reddens the positive
  control; and removing the commit comparison makes it MISS the real defect, which is what proves the
  comparison is the part doing the work.

  **Needs a container rebuild and an `install.sh` re-run** before anything live reports `0.6.2`.
- **D5. WITHDRAWN 2026-09-02.** The premise was mine and wrong twice over: `dd8b2d2a` is a CONTENT
  HASH, not a git sha -- I read it as one and told the operator the build identity was broken. It is
  not: running-hash against disk-hash is exactly the right instrument and it correctly read CURRENT.
  The missing `/health` fields were a stale process, resolved by the restart (`220b5280`).
- **D6. WRAP-M1: keyEnv secrets baked into world-readable launchers. CLOSED 2026-09-03** (aify-wrapper
  `00aaf55`, aify-comms pin below).

  **Reproduced before guarding.** `mcpEntriesFor` resolves `keyEnv` to the VALUE the installing shell
  carries; `strictMcpFragment` writes it into an `env` block; the installer bakes the fragment into
  every launcher. Rendering with `strictMcp: true` and a key exported produced
  `"env": { "OTHER_URL": ..., "AIFY_API_KEY": "<the key>" }`, decoded from the base64 in one line, in
  a mode-755 file. Base64 is a transport encoding, not a secret.

  **Still latent when it was closed**, which is when it was cheap: the live registry sets `strictMcp`
  nowhere and the fragment is the empty string.

  **The COMBINATION is refused**, never `strictMcp` alone and never a key alone -- verified by real
  exit status rather than by what it was piped through: key+strict exits 78 writing zero launchers,
  while strict-without-a-key and key-without-strict both exit 0 and render three. An ordinary install
  with a key exported is the case a blunter guard would have broken.

  **Refused at the INSTALL boundary**, because by the time a launcher exists the credential has
  already been published to every local user.

  **Why refuse rather than solve.** Keeping the pin AND keeping the secret out of the file means
  resolving per-server env in the launcher at RUN time -- and the fragment is base64 precisely so its
  content is never re-parsed by the shell, so adding a substitution hop back is how the escaping bugs
  that encoding ended would return. That design is worth building when a service actually needs strict
  mode WITH a credential. Inventing it speculatively inside a package aify-dashboard and
  aify-project-graph are about to consume is not. **Open, and now impossible to ship silently.**
- **D7. Per-agent authority.** A shared secret proves fleet membership, never "may act AS agent X".
  Blocks SSE-M1 (console input's actor is forgeable by construction), CRED-L1, Row 4 F4, and Row 1
  (dashboard terminal input). The operator's 09-02 answer described the INSTALL-TIME key (C4), which
  does not close this. Recorded as open by decision, not by oversight.
- **D8. RESOLVED with D5.** Absent because the process predated the restart, not because the code
  lacked the field.
- **D9. Prompt detection is claude-code-only.** ~~and unbounded in the tail~~ -- **MEASURED
  2026-09-03: one half is real and demonstrated, the other half is WRONG.**

  **"Unbounded in the tail" does not hold.** `_terminal_awaiting_input_hint` slices `clean[-2000:]`,
  and `_live_prompt` additionally requires at most 120 non-whitespace characters AFTER the match. Fed
  the same prompt with 1,500 trailing characters it returns `""`; with 50 it returns the hint. The
  region is already bounded to the bottom of the screen, by the trailing budget rather than by the
  slice. Nothing to fix here.

  **"Claude-code-only" is real, and here is the demonstration.** The only busy-suppression is
  `_CLAUDE_WORKING_FOOTER_RE`, claude's spinner. Same input shape, two runtimes:

  | input | result |
  |---|---|
  | `which option do you want` + claude spinner footer | `""` -- correctly suppressed |
  | `[codex] working...` + `which option do you want` | `Awaiting console input.` -- **false blocked** |

  So decision-flavoured prose from a hermes, codex or pi agent that is *generating* reads as blocked.
  That is the same defect the claude guard was added for after the subagent-to-blocked incident
  (2026-06-07), still open for three runtimes of four. The guard does not over-suppress: a genuine
  `(y/n)` after the spinner is still reported.

  **THE OBVIOUS FIX DOES NOT WORK, and this is the part worth writing down.** The bridge already
  emits per-runtime busy markers -- `codex-session.js` and `hermes-managed-gateway-session.js` push
  `[<runtime>] working...`, `hermes-session.js` pushes `[hermes] thinking...` -- so the patterns are
  known rather than guessed. But adding them to the footer regex would change nothing, because the
  suppression scans the region AFTER the last footer match, and the two markers have opposite
  positions: claude's spinner is REPAINTED at the bottom of the screen, so it lands after the prose;
  the bridge's marker is written once at TURN START, so the prose lands after IT. The semantics do
  not transfer.

  **What would work is not a text pattern at all.** Whether a runtime is generating is something the
  CALLER already knows -- there is an open dispatch run, or there is not -- while
  `_terminal_awaiting_input_hint` is a pure text function being asked to infer it. The suppression
  belongs at the call site, with the run state, and the text detector should answer only "is there a
  prompt on this screen". Not built: it moves a guard that currently protects the claude path, so it
  wants a deliberate change rather than a footnote to a measurement.

  (pi was not measured -- `pi-session.js` emits no equivalent marker that a grep for the same shape
  finds, and no pi terminal was live on this host to read.)

---

## E. Reviews -- LAST

- **E1. A new external review round.** The operator will request one *"ONLY IF ALL OF THESE ARE DONE,
  OR DROPPED OR CHANGED WITH REASONS THAT ARE PROOVEN AND THOUGH THROUGH"*.
- **E2. Independent dev review of my own commits.** 38 of the Round 7 commits are mine and I cannot
  close them myself.

---

## A note on history, so the record is navigable

`7071aac2` is titled `docs(plan)` and also carries the `/spawn` 409 rewrite and its test -- a
`git add -A` swept them in. The message does not describe the code change, so anyone tracing that
message will not find it there. Not rewritten, because it is pushed and shared; recorded here
instead, which is the cheaper of the two honest options.

## v0.6.1 progress, 2026-09-02

**T2 -- separation of concerns.** Steps 1 and 2 DONE and proven end to end: the plugin seam, the
claim pass, the API client, both loops, the daemon wiring, and an integration test against a real
socket, a real `Runner`, a real launcher and a real process. aify-env now heartbeats with a
`bridgeId` and claims spawn requests. Step 3 was rescoped twice by measurement and is now a small
cleanup -- see the design note; moving 2,098 lines would have put a service's domain model inside the
general host.

**T4 -- the installer. DONE.** All three components ask for what is missing rather than proceeding
silently, and update by the same command. aify-env had no installer at all and now has one; the
README describes three components and the single ordering constraint that exists.

**T3 -- docs and skills. SUBSTANTIALLY DONE.** Every correction traced to one root cause:
"spawning" was doing two jobs. Execution moved to aify-env on 2026-08-25; claiming did not; every
document measured the first and reported the work finished.

- **7 install instructions** across `README.md` and each `install.<runtime>.md` told a reader to get
  aify-env with `npm install -g`, which installs the command and cannot notice a missing credential
  -- the 2026-09-02 failure, replicated seven times.
- **`TARGET_ARCHITECTURE.md`** twice: the table's removal condition, and the Phase 8 entry marked
  DONE (kept struck through -- what a reader believed for eight days is the point).
- **`PHASE8_STATUS.md`, `CLAUDE.md`, `PRODUCT_BRIEF.md`.**
- **The 409 got its first troubleshooting entry.** The most expensive failure of the day was absent
  from the debug reference entirely.
- **The always-loaded skill now warns about the trap**: `comms_envs` `online` does not mean a spawn
  can run. Paid for by compressing four passages, so the file got SMALLER and its ceiling came down.

**A gate so the seven-place duplication cannot rot again**
(`test_the_install_table_agrees_across_every_guide`). It asserts a PROPERTY -- no guide routes a
reader around aify-env's installer -- rather than pinning a string, which would be edited to match
rather than obeyed.

**Superseded note, kept for the record.** Four corrected: `TARGET_ARCHITECTURE.md` (both the removal condition and
the Phase 8 entry that read as done), `PHASE8_STATUS.md`, `CLAUDE.md`, `PRODUCT_BRIEF.md`. Each
carried the same error -- "spawning" doing two jobs, execution and claiming, with only the first
measured.

**THE FIRST REAL SPAWN, 2026-09-03: the gate works, the claim works, the START does not.**
Six spawns were accepted (so `/spawn`'s claimer gate is fixed and proven on real hardware), all six
were claimed by aify-env's plugin within seconds (so the claim loop is proven), and all six failed
with `a start request must name a launcher to run`.

**The cause is a model mismatch, measured rather than guessed.** `mcp/stdio/spawn-loop.mjs` — the
bridge code the plugin replaced — contains ZERO process starts. It claims, reports `running` with
the BRIDGE's own pid, registers the agent warm, and arms the dispatch loop; the worker starts LATER,
when a message arrives. That is what `managed-warm` means, and every spawn uses it. The plugin
instead builds a start spec at claim time from `request.launcher`, a field the wire does not carry.

**This needs an operator decision before it is built** — see the design note's last section. Short
version: (a) aify-env grows the dispatch loop and the per-runtime adapters, which puts aify-comms'
messaging model inside the general host and is the opposite of the goal; or (b) aify-env stays a
process host, the claim reports the agent warm, and the dispatch path asks aify-env to run the
worker when work arrives — which needs NO new capability in aify-env, because `/processes` already
starts an allowlisted launcher and the bridge's delegation path already used it successfully.
**(b) is what the operator's own architecture statement asks for**, and the work it implies is in
aify-comms.

**WHAT v0.6.1 STILL NEEDS**, and the order:
1. Install the new aify-env on the operator's machine (operator's call -- it changes their setup).
2. Operator restarts it. Starting shared infrastructure is never the agent's.
3. **One real spawn with no `aify-comms` bridge running.** That is the proof; everything else is a
   measured claim.
4. T1's parallelism, T3's remaining pass.

## Done on 2026-09-02, for context

Each verified on the artifact that executes, not on the repo.

- The dashboard was unusable with `API_KEY` set, four independent ways: the WebSocket ignored the only
  credential a browser can send; the page never sent the key at all (cross-origin, cookie does not
  ride); CORS preflights were answered 401, which they can never pass; and an https page was told to
  call a plain-http port.
- Two Caddy defects: `HTTPS_SITES` never reached the container, so that knob had never done anything;
  and a browser reaching an address by IP sends no SNI, so Caddy had a certificate and still failed
  the handshake. HTTPS now on 8801.
- The port-collision gate read compose defaults rather than `.env`, and had stopped matching the https
  proxy's line entirely -- it let a real collision through, mine, onto aify-env's own port.
- Temp-directory leak closed in all three repos: 80 test files leaked, zero shipped files did.
- `install.sh` never carried an existing key to aify-env, so every advertisement 401'd with both sides
  reporting healthy.
- `aify-env credential set` through the dispatcher stored nothing, printed nothing, and exited 0.
- v0.6.1 tagged: service, installed bridge and repo HEAD all agree.
