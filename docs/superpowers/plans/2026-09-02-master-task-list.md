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

**v0.6.1 IS ALREADY TAGGED** at `a9c963f0` (2026-09-02, today's fixes). Under this scope it is not
v0.6.1 yet, so the tag ACCUMULATES and is re-cut when T1 and T2 land and are manually proven --
consistent with the operator's earlier *"make tag now, but all fixes... should all be added to this
tag later on."* Manual proof is a release gate here, not a nicety: today produced four separate cases
where every automated check was green and the thing did not work.

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

  **DELIBERATELY NOT DONE, and it is a v0.6.2 item.** ~1,800 lines of now-unreachable bridge modules
  remain (`spawn-loop`, `terminal-control-loop`, `environment-control-loop`,
  `managed-environment-sync`, `managed-teardown-sweeps`, `boot-marker-sweep`,
  `reap-managed-survivors`, `terminal-manager`) plus `IS_ENVIRONMENT_BRIDGE` and the 37 test files
  naming them. Deleting them touches `server.js`, which every running wrapper loads as its MCP
  server. Measured before deciding: nothing live is lost by the removal -- `managed-orphans` reports
  no delivery loops, `bridge-current` reads `unknown-all`, `usage/consumption` is empty, and the
  OpenAI pool is collected by the SERVICE.

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

  **THAT BLOCKER IS GONE as of 2026-09-03, and this is now the cheapest item in the file.** aify-env
  is the process host for every managed agent: `GET /processes` on the operator's own machine returns
  `sc-lead`, `sc-tester`, `sc-critic`, `sc-coder` with their pids, each carrying the agent id as its
  `label` and the runtime's own title. So "show the managed agents that are running" is a render over
  a listing aify-env ALREADY HAS -- no join, no aify-comms call, no new plumbing. It is the oldest
  unmet ask in this file and the one the operator has raised most often, and it stopped being hard
  the night the host tier took over spawning. Do it first in the A series.
- **A2. Control a managed agent's TUI directly from aify-env** (09-02). The operator names
  [herdr](https://github.com/ogulcancelik/herdr) as the model: a persistent headless server plus a
  TUI client that attaches to REAL terminals, not redraws.
  **Open design question, settle before building:** whose PTY is it? aify-env owns the PTYs it
  spawns; the bridge owns the managed hermes ones. Attaching means either moving those PTYs into
  aify-env or attaching through aify-comms. Establish which before designing on top of it.
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
- **B4. The doctor, visible in the dashboard** (09-02). *"i never go to that path... i have container
  that should give me that info. some random path for aify-comms doctor... no. will never use it."*
  Four checks are answerable from the service's own data with no host agent -- `env-bridge`,
  `session-handles`, `context-window`, `bridge-current`. The rest need a host reporter, which is
  aify-env's job under TARGET_ARCHITECTURE.
- **B5. Browse an agent and its processes** (09-02). *"i cannot still check the processes themself?
  (like browse agent or something)"* No agent-detail view exists: no terminals, console, runs or pids
  behind a click.
- **B6. A login prompt instead of a URL parameter.** DONE 2026-09-02, deployed and verified.

---

## C. Other operator asks

- **C1. Triage every tool: are they reasonable, do they have good descriptions** (08-30). Partly done
  -- Row 3 fixed several descriptions -- but no pass over the whole surface.
- **C2. Security work developed AND tested** (08-30). Round 7 closed and today's auth defects fixed.
  Residual: C4 and D7.
- **C3. SoC review of aify-env and aify-wrapper** (08-30), so a project unrelated to aify-comms can
  reuse them. Not started.
- **C4. The install-time key** (09-01, restated 09-02). *"when installing first time agent should ask
  for it. if installing aify-comms + aify-env + aify-wrapper (full local install) then ofc all these
  can have one ask and use same key."* The installer now CARRIES a key it finds; it does not yet ASK
  for one.
- **C5. Terminal write path, the full fix** (operator chose the full option 09-02): move the two
  status-path readers off the stored tail, then write it lazily. Scoped: both readers want the
  rendered screen, and the live screen is already rendered, so they skip a pyte render as well as a
  database read. `claim_block_reason.py` stays on the raw log -- it compares marker POSITIONS in the
  stream and a rendered screen loses that ordering.
- **C6. `comms_send` unslop** -- tighten all 2,636 B rather than cut the reply contract. `tools/list`
  costs ~7.9k tokens per agent per turn, so this is paid every turn by every agent.
- **C7. Hook source: repo rather than the installed copy** (decided 09-01, reverses `e8856126`).

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
- **D10. A skipped doctor check reports `ok: true`.** `env-bridge` skipped for a missing API key reads
  as PASSING in `--json`. With D11, that is how "I could not ask" became "no bridge is online" in an
  agent's summary.
- **D11. The doctor finds the API key only from the repo checkout.** `doctor-api-key.mjs` reads the
  `.env` beside the repo, so every agent running `aify-comms doctor` from its own working directory
  loses every service-reading check. Reproduced from the home directory. Now fixable properly: the
  key lives in aify-env's credential store and the registry names it with `credentialRef`, so a
  host-side tool can resolve it wherever it runs.

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
- **D4. SPLIT-M1: no gate compares VERSION against the released tag.** VERSION read `0.6.0` while HEAD
  was 407 commits past the v0.6.0 tag and every version gate was green -- they check that the five
  files agree with EACH OTHER. The v0.6.1 bump was a hand-correction, not something a test demanded.
- **D5. WITHDRAWN 2026-09-02.** The premise was mine and wrong twice over: `dd8b2d2a` is a CONTENT
  HASH, not a git sha -- I read it as one and told the operator the build identity was broken. It is
  not: running-hash against disk-hash is exactly the right instrument and it correctly read CURRENT.
  The missing `/health` fields were a stale process, resolved by the restart (`220b5280`).
- **D6. WRAP-M1: keyEnv secrets baked into world-readable launchers.** Latent only because no service
  sets `strictMcp: true`. Fix before one does.
- **D7. Per-agent authority.** A shared secret proves fleet membership, never "may act AS agent X".
  Blocks SSE-M1 (console input's actor is forgeable by construction), CRED-L1, Row 4 F4, and Row 1
  (dashboard terminal input). The operator's 09-02 answer described the INSTALL-TIME key (C4), which
  does not close this. Recorded as open by decision, not by oversight.
- **D8. RESOLVED with D5.** Absent because the process predated the restart, not because the code
  lacked the field.
- **D9. Prompt detection is claude-code-only and unbounded in the tail.** See the herdr notes in A.
  A general detector with regions and negative guards would cover hermes, codex and pi, which today
  fall back to native events only.

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
