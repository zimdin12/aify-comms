# aify-comms — Claude Code project notes

Inter-agent communication hub: messaging, channels, file sharing, active dispatch, and a dashboard for Claude Code, Codex, OpenCode, and other MCP-connected coding agents. This file is loaded by Claude Code when working **on this repo itself**; usage docs for someone installing aify-comms live in [README.md](README.md).

## Primary entry points

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — **how it is built.** The three processes and what
  reloads each, the service layering and why it is flat, the message-to-work path, and every layer
  rule paired with the test that fails when it is broken. Read before your first change; the rules
  below assume it.
- [README.md](README.md) — what the service is, setup, day-to-day usage.
- [install.claude.md](install.claude.md) / [install.codex.md](install.codex.md) / [install.hermes.md](install.hermes.md) / [install.opencode.md](install.opencode.md) / [install.pi.md](install.pi.md) — per-runtime install guides (wrappers, hooks, verification).
- [docs/superpowers/plans/2026-08-20-three-repo-separation-roadmap.md](docs/superpowers/plans/2026-08-20-three-repo-separation-roadmap.md)
  — **v0.6, the work in flight.** aify-comms, [aify-wrapper](https://github.com/zimdin12/aify-wrapper)
  and [aify-env](https://github.com/zimdin12/aify-env) as three repos, which phases are done, and the
  operator decisions each one turned on. **Phase 8 moved EXECUTION on 2026-08-25 and CLAIMING
  only on 2026-09-02**, which is a distinction that cost a day: aify-env RAN managed spawns while the
  `aify-comms` environment bridge still had to CLAIM them, because `/spawn` keys on
  `metadata.bridgeLastSeen` and the service writes that only for a heartbeat carrying a `bridgeId`.
  Every document said spawning had moved; the command stayed load-bearing, and six spawns were
  refused on a host whose operator was running everything correctly. aify-env now carries an
  `aify-comms` plugin that claims — **PROVEN ON REAL HARDWARE 2026-09-03**, six managed lanes up with
  no bridge running at all: it claimed the spawns, registered the warm agents, resolved the launchers
  past a Windows `.cmd` shim, ran the workers, streamed the consoles, and carried input, resize and
  stop. **So v0.6.1 removed the command**: `aify-comms` starts nothing and refuses with exit 2 naming
  aify-env, which is the condition `docs/TARGET_ARCHITECTURE.md` had already written down. aify-env is
  REQUIRED for spawning either way and a spawn fails loudly rather than falling back — two spawners
  on one host is the collision the tier exists to end. **And since 2026-08-30
  aify-env also DESCRIBES the host**: it advertises `runtimes`, `terminalRuntimes`, `terminal` and
  `pty`, and the bridge omits exactly those whenever aify-env's `/health` reports `advertising: true`.
  Exactly one tier per host, decided from that fact rather than a flag on each side; standing down
  needs a literal `true`, so an absent, false or unreachable aify-env leaves the bridge doing it. The
  bridge keeps `label` and `cwdRoots` (aify-env sends neither, by design) and its own `bridgeId` /
  `bridgeVersion` / `bridgeStartedAt`, which supersession is arbitrated on. Read
  [docs/PHASE8_STATUS.md](docs/PHASE8_STATUS.md) before touching spawn or terminals; its last section
  records the three defects the first real spawn exposed, all of which sat on the joins between
  components that each reported healthy. `aify-comms doctor`'s `spawn-delegation` says where spawns
  run and whether aify-env is answering.
- [docs/TARGET_ARCHITECTURE.md](docs/TARGET_ARCHITECTURE.md) — **the shape this is heading for, as the
  operator specified it.** Container / host / `~/.aify`, four commands on PATH, and where each doctor
  lives. Not a proposal: anything disagreeing with it is the thing that is wrong. Read it before
  arguing about component boundaries — it exists because the same target had to be restated several
  times before it was written down.
- [DECISIONS.md](DECISIONS.md) — rationale for non-obvious design choices and current runtime limits.
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md) — known limitations, deferred work, watch-items, and pre-existing backlog.
- `.claude/skills/aify-comms/SKILL.md` — agent-facing usage guide (tool reference, multi-instance matrix, status table).
- `.claude/skills/aify-comms-debug/SKILL.md` — known issues and fixes (AbsolutePathBuf, hard-reset sequence, buffer_full, orphaned runs, stale bridges).

## Developing on this repo

```bash
git pull
docker compose up -d --build            # rebuilds the Python service container
curl http://localhost:8800/health        # should return {"status":"healthy"}
```

Changes under `service/`, `mcp/` (except `mcp/stdio/`), and `config/` are COPY'd into the container image — rebuild after editing any of them. The Dockerfile does `COPY mcp/ ./mcp/`, so **anything you add beside `mcp/sse_server.py` is container runtime**; doctor's `SERVICE_RUNTIME_PATHS` and `test_service_runtime_boundary.py` both name the DIRECTORY for that reason, and both named the single file until 2026-08-15 — which would have made a decomposed sibling invisible to the staleness check and free to import host-side bridge code. Changes under `mcp/stdio/` affect host-side bridges and MCP client sessions, so reinstall/restart `aify-comms`, `codex-aify`, or `claude-aify` after editing them. Changes to docs, skills, `install.sh`, and `.claude/` do not need a container rebuild, but installer changes require rerunning `install.sh`.

The MCP stdio bridges under `mcp/stdio/` run on the **host**, not in the container. They are loaded by Claude Code / Codex at startup, so changes there require restarting the client wrapper (`claude-aify` / `codex-aify`) — not a container rebuild.

## Versioning — one file, and a test that enforces it

**The release version lives in the repo-root `VERSION` file. Nothing else may declare one.**

`scripts/stamp.sh` bakes it into `service/_build_stamp.json` (the container has no repo root — the same reason the git sha is stamped), and `service/config.py` reads it from there, so the service, the root endpoint, `/openapi.json` and Dashboard Next all report it. On the Node side `mcp/stdio/version.js` exports `AIFY_VERSION`, imported by every MCP handshake and by `BRIDGE_VERSION` (which also reaches the control plane as `bridgeVersion`).

**To cut a release:** edit `VERSION`, set `mcp/stdio/version.js` + `mcp/stdio/package.json` + `mcp/stdio/package-lock.json` + `.claude-plugin/plugin.json` to match, run the suites, `bash scripts/stamp.sh`, rebuild, **re-run `install.sh` for each client if anything under `mcp/stdio/` changed**, then tag. (`plugin.json` was missing from this list until v0.2.0 even though `test_version_single_source.py` has always asserted it — the recipe was one step shorter than the test. `install.sh` was missing for the same reason: `aify-comms doctor` fails `bridge-installed` after a bridge edit, and the release is not shippable until that is green.) `test_version_single_source.py` and `mcp/stdio/tests/version-consistency.test.js` fail the suite if any of those disagree or if a new file hardcodes a version literal — so a missed step is a red test, not a silent lie.

**Why this exists:** until 2026-08-03 four components each carried their own version and none tracked a release. The service reported `0.1.0` (a stale `SERVICE_VERSION` in `.env`, which overrides the stamp), `config.py`'s default said `4.0.0`, Dashboard Next hardcoded `0.1.0`, and the bridge said `4.0.0` in **eight** hand-copied places — while the project actually shipped v0.1, v0.1.1 and v0.1.2. No single edit could have corrected it. **Do not set `SERVICE_VERSION` in `.env`**; env wins over the stamp and re-creates exactly that bug.

`config/service.json` was a SECOND way in, closed 2026-08-18 (`8d7d7c24`) after another instance's
service announced `3.6.6` while running `0.5.4`. `ServiceConfig.load()` applied that file with a
generic loop that set any key naming a config attribute, and it ran after the stamp — so a stale
`version` key silently won. The version was the mild half: the same loop reached `build_sha`, which
is the value `aify-comms doctor`'s `service` check compares against repo HEAD, so a hand-edited
service.json could make the one stale-deploy instrument agree with a sha nothing was ever built from.
All five stamp-owned fields (`version`, `build_sha`, `build_short`, `build_branch`, `built_at`) are
now refused from that file — they are observations of a build, not configuration, and no hand-edit
could make one of them true.

## Repo layout (what matters)

| Path | What |
|------|------|
| `service/` | FastAPI backend, SQLite persistence, dashboard HTML, dispatch logic. Rebuild container after changes. |
| `service/terminal_snapshot.py`, `service/terminal_diagnostics.py`, `service/status_engine.py` | PURE, unit-tested service modules extracted out of what is now `service/control_plane.py` for the same reason as the bridge predicates below: logic that lives in a 20k-line module is only reachable through the app, so it can only fail in production. `terminal_diagnostics.py` (which line of a dead terminal's output explains the death) is the pattern to follow for anything new — put behaviour here and import it, don't grow `service/control_plane.py`. |
| `service/control_plane.py` | **v0.5.3.** The live control plane: ~140 helpers, constants and the two queue classes shared across status, dispatch, terminals, spawn and console. This was `service/routers/api_v2.py` — 20,545 lines at its peak — until the route domains moved out and it was left declaring ZERO routes. It is NOT a router: `service/routers/api_v2.py` is now nothing but the 15 `include_router` calls, and there is deliberately no compatibility re-export, so a stale `from service.routers.api_v2 import <helper>` fails loudly instead of resolving. **v0.5.4 took it to 879 lines and it is OFF `oversized-allowlist.json`** — it is **893 on 2026-08-24**, so the regrowth this paragraph warns about has started — the "still far too big, a v0.6 question" note that stood here is retired as done. It is now an ordinary file under the 1000-line gate, so the risk it carries is REGROWTH: it is still the shared home for status, dispatch, terminal, spawn and console helpers, and the gate will not warn until it has already crossed back over. |
| `service/reconcilers/` | **v0.5.** The reconcilers extracted out of what is now `service/control_plane.py` — one module per responsibility (`status_cache`, `spawn_lifecycle`, `sessions`, `terminals`, `terminal_runs`, `terminal_consistency`, `dispatch_queue`, `dispatch_lifecycle`, `managed_workers`, `console_binding`). Leaf modules: they may import `service/clock.py`, `service/env_status.py` and each other, but must NOT import the control plane at all. **The borrow debt is PAID: reconciler imports of the control plane are ZERO**, measured by `test_leaves_do_not_import_the_carrier.py`, whose ceiling is now 0 and which fails if that ceiling is ever left slack above the real count. The function-scope "borrow shim" this table used to describe is retired — do not reintroduce one; a reconciler needing a control-plane helper means the helper is in the wrong layer. Two function-scope imports remain in `reconcilers/`, but they read `api_core` leaves, not the carrier, and each documents the cycle that forces it. |
| `service/clock.py`, `service/env_status.py` | Leaf helpers with no service dependencies, created so the reconcilers could stop importing the router for a timestamp or an environment status. |
| `service/new_dashboard/*.mjs` | Dashboard modules with their own `*.test.mjs` — 68 modules, 113 test files, 1515 tests (measured 2026-09-03) (`api-client`, `shared-files`, `message-transport`, `state`, `session-rail`, `terminal-input`, …). **`app.js` is 987 lines (was 5,081) and is UNDER the 1000-line gate** — the "3,612 lines / only reachable by source-regex tests" note that stood here is retired twice over: the extracted modules import and run in Node, so they are tested by CALLING them, and the boot wiring, the delegated click dispatcher and the per-page actions have all left. Still put new behaviour in a module here rather than in `app.js`: what remains is the render orchestrator, and it is 13 lines from going red. |
| `service/new_dashboard/extraction-proof.mjs` + `.test.mjs` | **The gate that makes app.js safe to slice.** It RECONSTRUCTS the pre-extraction app.js from the current file plus every extracted module and requires byte-identity with a tracked pristine fixture — so a slice cannot quietly change anything outside the spans it declared. Moving a declaration out of `app.js` means appending an entry to its `EXTRACTIONS` plan **in the same change**: `importLine` (and `importWas` only if the slice EDITED an existing import — a module created by an earlier extraction has none in the fixture, so its line is deleted instead), plus one item per declaration with `at` = the 0-indexed line in the FIXTURE, not the live file. `marker` may be several lines and each is verified verbatim, which is what lets a slice leave a seeding call behind. |
| `mcp/stdio/` | Host-side MCP bridges (`server.js`, `claude-channel.js`, `runtimes.js`, `runtime-markers.js`, `notify-check.js`). Restart client wrapper after changes. |
| `mcp/stdio/service-registry.mjs`, `register-service-cli.mjs` | **v0.6 Phase 6.** Writing this service's entry into the SHARED registry at `~/.aify/services.json`, which is how a launcher learns aify-comms exists. Installing a SERVICE registers it; installing the wrapper package is never the goal. aify-comms owns its own key and leaves every other service's alone — an unreadable or wrong-version registry is REFUSED, never rewritten, because overwriting would uninstall another service at the moment somebody reinstalls something unrelated. The reader lives in the aify-wrapper package (`lib/registry.mjs`); `endpointEnv` is exported from `aify-service-endpoint.mjs` as `ENDPOINT_ENV_NAMES` rather than typed twice, because a name the bridge reads but the registry does not declare gets INHERITED from whatever launched the runtime. |
| `mcp/stdio/*-predicates.js`, `register-identity.js` | PURE, unit-tested helpers extracted out of the bridges so their logic can fail a test instead of only failing in production. `doctor-predicates.js` (env liveness) and `register-identity.js` (resident launch-identity warning) are the pattern to follow — `doctor.js` was untestable until its predicates moved out, and the first thing the new test caught was a real bug. **`service-check.mjs` is the next step past a predicate: the whole `service` CHECK, not just its verdict.** A predicate proven in isolation still leaves the call to it unproven, and that is exactly where this one failed -- an early return answered the no-checkout case itself and never consulted the verdict. Anything that needs its call site executed belongs in a module like this, because importing `doctor.js` RUNS the doctor. Which files are "the doctor" is DERIVED, never listed (`tests/doctor-sources.mjs` and `service/tests/doctor_sources.py`, kept honest by an agreement test): four scanners hardcoded the filename, and moving one check reddened three while the fourth stayed green by no longer looking. |
| `mcp/sse_server.py` | SSE MCP transport (runs inside the container). Rebuild container after changes. |
| `.claude/skills/aify-comms/` | Usage skill — tool reference, workflow, status table, multi-instance matrix. |
| `.claude/skills/aify-comms-debug/` | Troubleshooting skill — known issues and fixes. |
| `.agents/skills/aify-comms*/` | Mirrors of the two skills for Codex agents. Keep in sync. |
| `mcp/stdio/node_modules/aify-wrapper/wrappers/` | The four launcher templates, from the **[aify-wrapper](https://github.com/zimdin12/aify-wrapper) package**, pinned to a sha in `mcp/stdio/package.json`. They were a byte-identical copy under `wrappers/` here, kept honest by a hash gate in each repo — two sources of truth for one artifact. The operator settled it on 2026-08-20: aify-comms consumes the package, no duplicates. Both drift gates are retired and `wrappers/` is deleted; `install.sh` renders from `WRAPPER_TEMPLATE_DIR`, and the swap was proven byte-identical on all six rendered launchers before the copy was removed. **`--emit-wrappers` now needs `npm install` to have run in `mcp/stdio`**, because it exits before the installer's own npm step by design. **BUMPING THE PIN AND RUNNING `npm install` DOES NOT UPDATE THE PACKAGE** — measured 2026-08-30: the sha was raised in `package.json` AND `package-lock.json`, `npm install` reported success, and `node_modules/aify-wrapper` still held the previous code. npm trusts a tree that matches the lock it was just handed. The gate caught it (`the-wrapper-pin-is-not-behind-a-template-change.test.js` was still red for the same reason), but the failure shape is this repo's favourite: a step that reports success and changes nothing. Remove `node_modules/aify-wrapper` and reinstall, then GREP the installed file for whatever the bump was for. |
| `install.sh` | Client installer. Targets Claude, Codex, or Hermes via `--client` (OpenCode/Pi installs are intentionally disabled). `--emit-wrappers <dir>` renders a wrapper and EXITS before npm, MCP registration or any env mutation — which is what lets the suite render and run the real launchers on a machine with a live fleet. `--prebuild-dry-run` is its sibling and carries the same property for the hermes web_dist branch: it exercises the detection logic with no npm invocation and no wrapper writes, which is how `test_install_hermes_prebuild.py` tests that branch without touching the operator's environment. |
| `scripts/installed-endpoint.sh`, `scripts/hook-installed.sh`, `scripts/api-key.sh` | **What the host already chose, read back before an update overwrites it.** `install.sh`'s prompt and `redeploy.sh` each held their own copy of one regex for "which endpoint is installed", both still matching the PRE-CONTRACT wrapper shape — so both silently stopped finding anything, and redeploy would have re-rendered every wrapper pointing at its loopback default. `hook-installed.sh` answers the same kind of question for notification hooks: `--with-hook` is opt-in and redeploy does not pass it, so an update printed "skipped" and left the hook's registration wherever an older install put it. `api-key.sh` is the third and the one that had teeth: `install.sh` resolved the service key from the SHELL only, never from `.env`, so the moment an operator set `API_KEY` the service began refusing unauthenticated calls, every installed client held no key, and re-running the installer wrote the same keyless config again — the obvious remedy made no difference. It also generates one on `--with-api-key`, reusing any existing key rather than rotating, because a fresh key 401s every bridge already installed. All three READ files; asking a launcher by running it starts a coding-agent runtime. |
| `examples/team-setup/` | Example team definition (manager, coder, tester, etc.) showing how to register a multi-role team. |

## Development notes

- **`install.sh` copies the bridge runtime into a native dotfolder.** The installer copies `mcp/stdio` + its `node_modules` into `~/.aify-comms/` (override with `AIFY_HOME`) and points every wrapper + MCP config at that native copy, not at the repo checkout. Reason: the repo often sits on a slow 9p/WSL2 bind-mount where the bridge takes ~5s to load — that blows hermes' hardcoded 0.75s MCP-discovery window; the native copy loads in ~0.3s. Re-running `install.sh` refreshes the copy, so **security fixes flow on reinstall** (no longer automatic). Consequence: editing files under `mcp/stdio/` now requires re-running `install.sh` (to re-copy) **and** restarting the client wrapper — not just a wrapper restart.
- **Forward-slash `cwd` on Windows** for Codex agents. The bridge auto-normalizes, but any new Codex thread must be created with a forward-slash cwd or it'll fail `thread/resume` later.
- **Re-register is a full state refresh** for everything except `description`. Tests and dev workflows should assume session state is wiped on re-register — see DECISIONS.md.
- **Skill files live in two places:** `.claude/skills/aify-comms*/` and `.agents/skills/aify-comms*/`. Keep them in sync when editing.

## Writing a skill

A skill is not read on demand: the `SKILL.md` files load into every agent's context every session, so
a byte there is paid by every agent on every turn rather than once by a reader. That is the whole
reason these rules exist.

- **Size is gated by a ratchet, not a cap.** `mcp/stdio/tests/skill-size-ratchet.test.js` holds all 17
  skill files at MEASURED sizes that may only go DOWN, and fails on a file with no ceiling so a new
  skill cannot arrive ungoverned. An always-loaded `SKILL.md` also has a hard 16 KB limit on top.
  **Raising a ceiling is a decision, not a repair** — pay for it elsewhere, split the file, or say in
  the commit what the reader gains. Nudging the number to clear a red test is the move the gate exists
  to catch. (It replaced a flat cap that covered 4 files and left slack to grow into.)
- **Steps in the skill, reference behind a pointer.** Inline what every run needs; push what only some
  branches reach into `references/` and name it. The pointer's WORDING decides whether it is followed,
  so say what the reader will find, not "see the reference".
- **Instruct positively.** A prohibition drags the banned behaviour into context and makes it more
  available, not less — write the target behaviour instead. Keep a ban only where it is a hard
  guardrail you cannot phrase positively (`Never run a bare aify-comms`).
- **Every rule carries its measurement.** The rules that survive here are the ones with a number
  attached — 2,021 over-long subjects, 1,248 self-wakes at a 27s median, one review that reached R7.
  A rule with a measurement is one nobody deletes next year; a rule with only rhetoric is prose.
- **Prose earns its keep by changing a decision.** If a sentence would not change what the agent does,
  delete the sentence rather than trimming its words.
- **Both mirrors, byte-identical**, gated by `test_skill_mirror_parity.py`.

Prior art worth reading before a large edit: [mattpocock/skills](https://github.com/mattpocock/skills)
`writing-for-agents` (context load vs cognitive load, progressive disclosure, negation) and
[pstack-claude](https://github.com/michael-denyer/pstack-claude) `principle-minimize-reader-load`.
They disagree about whether shared reference belongs inline or behind a pointer; both are right in
their own regime, and the split above is this repo's answer.
- **The live-status cache is in-memory (`_LIVE_STATE_CACHE`, owned by `service/reconcilers/status_cache.py` since v0.5 — the router reaches it as `status_cache._LIVE_STATE_CACHE`, never by value), and the service MUST stay single-worker.** As of 2026-06-18 (`97a497a`) the derived agent-status cache is a process-global in-memory dict, not a SQLite table — this resolved the recurring `database is locked` 503s (the old `agent_live_state` table was refresh-written on every dashboard poll). It is only correct with ONE uvicorn process / one event loop, so never add `--workers > 1` without first moving the cache to a shared store (Redis) or sticky routing. The `agent_live_state` table is vestigial (retained for schema compat, read/written by nothing) — don't debug status from a table dump; use `comms_agent_info` / the dashboard. See DECISIONS.md, "Live-status cache is in-memory, not SQLite".
- **Container name is `aify-comms-service`** on the `aify-comms-network` network. Compose project name is driven by `COMPOSE_PROJECT_NAME` in `.env`.
- **No secrets in commits.** `.env` is gitignored; `config/service.json` is generated by `setup.sh`.

## Verify a change actually took effect — `aify-comms doctor`

**Every deploy path in this repo fails silently.** No error, everything looks installed, and what you changed is not what is running. This bit us repeatedly: a container serving the previous build; `~/.aify-comms` holding new bridge code while every RUNNING wrapper still executes the copy it loaded at boot; an agent registered but with no `AIFY_AGENT_ID` in its process, so its status is dead. **Do not report success from the absence of an error.**

```bash
aify-comms doctor            # human-readable report
aify-comms doctor --json     # {ok, checks:[{id, ok, code, detail, fix}]} — for scripted/agent checks
aify-comms doctor --strict   # exit 1 if any check failed
```

**`aify-comms` is a verifier and starts nothing.** `doctor`, `--check` (validates node, the script
path and that it parses), `--version`, `--help`; anything else exits 2 and names aify-env.

**That is a v0.6.1 change, and the history is why the guarantee is worth stating.** Until then a
bare run exec'd the stdio server with `--environment-bridge` — a real **environment bridge**, which
by design superseded the one already serving this environment, so the older bridge exited and its
managed workers were reaped. It took the whole managed fleet down on 2026-08-11 from a four-second
run meant only to confirm the launcher still started, and again on 2026-08-20 when a backtick inside
an unquoted heredoc executed the name. The mitigation was a standing rule everybody had to remember,
which is a defect with a delay on it. **aify-env is the host tier** — it owns processes and PTYs,
claims spawns, runs the launchers and streams the consoles — so there is no second spawner left for
this command to be, and the refusal is enforced rather than remembered. **Starting `aify-env` is
still the operator's action**: supersession there reaps the predecessor's workers exactly as the
bridge's did, so ask with `aify-env doctor` instead of starting one to find out.

`aify-doctor` is the same script under an older name and still works. It shipped first and the
operator's objection was fair: one product should not need two command names remembered, so the
verifier now lives under the name that already exists. The standalone binary stays because agent
habits and older docs point at it.

It proves each claim against the running system rather than checking that a file exists:

| check | catches |
|---|---|
| `service` | container serving a build ≠ repo HEAD (a healthy `/health` says nothing about *which* code) |
| `bridge-installed` | commits since the installed marker that actually **touched `mcp/stdio/`** — i.e. you edited the bridge and never re-ran `install.sh`. Being behind by docs- or service-only commits is reported as CLEAN (with the count), so this check does not cry wolf on every commit |
| `bridge-running` | **running** bridges started BEFORE the last install → still executing the old code. Names the agents that must restart. **Linux-only** — it reads `/proc`, so on Windows it SKIPS and nothing verifies this. |
| `agent-identity` | a REGISTERED agent whose process has no `AIFY_AGENT_ID` (status structurally dead). An unregistered plain session is legitimately id-less and is not flagged. **Linux-only**, same caveat. |
| `bridge-current` | a **live** bridge whose self-reported `bridgeBuild` ≠ repo HEAD → it is *running* old code even though the files on disk are current. Platform-independent (the bridge reports its build on registration), which is what makes it the answer to the Windows gap below. Says RESTART, never reinstall. Fails as `unknown-all` when **no** live bridge reports a build: that is no evidence, and a check that verified nothing must not read as a pass — it was green-by-default until `a2f9e42`, the same false green as `env-bridge` below. |
| `skills-installed` | a skill edited in the checkout and never installed. `install.sh` COPIES the skill trees out to `~/.claude/skills`, so editing `.claude/skills/` changes nothing for the fleet until it is re-run — the same silent-deploy shape as the bridge, on a path nobody thinks of as a deploy |
| `spawn-delegation` | where managed spawns run, read from the installed launcher rather than by running it. Delegation makes aify-env REQUIRED — the bridge refuses rather than silently hosting spawns itself, because two spawners on one host is the collision the environment tier exists to end — so a down aify-env presents as spawns failing with no cause attached. Reports `local` (the default), `delegated`, `unreachable` (FAIL), or `pre-contract` for a launcher rendered before the setting existed |
| `managed-orphans` | managed delivery loops (`hermes-managed-host.js run <agent>`) running for an agent that belongs to NO live bridge. Nothing collects one during normal operation -- the survivor sweep runs at bridge BOOT, so a loop orphaned mid-session accumulates until the next relaunch -- and the control plane cannot see it: the agent reads `available` because it has no live sidecar, while its `lastSeen` keeps refreshing because the orphan itself is heartbeating. **Reports, never kills.** Six were alive on 2026-08-26, oldest 96 minutes |
| `gateway-orphans` | hermes GATEWAY hosts running in aify-comms' own port range (8642-9641) with no worker behind them. Its sibling `managed-orphans` watches the DELIVERY LOOPS and would have said nothing on 2026-08-31, when the operator's `hermes update` refused to run and listed 45 live processes from this install after aify-env was killed -- the loops were the half that died correctly. Establishing whether those processes were ours took a port range, a marker directory and a process walk; this row answers it directly. Scoped to MANAGED agents, because a resident hermes legitimately runs its own gateway with no loop and flagging it would fire on the operator's own terminal every run. A port NO marker claims is reported as `(unclaimed)` rather than dropped -- a process nobody can account for is the more suspicious one. REPORTS STATE AND MAKES NO CAUSAL CLAIM: how long an orphaned gateway actually lives was investigated, three explanations were wrong, and it is still unknown |
| `env-processes` | a process aify-env is RUNNING that the control plane has no live terminal for, and the reverse -- a live terminal naming a pid nothing is running. The operator watched a PTY for `ef-manager` (pid 155844) in aify-env while the dashboard showed nothing and every recent session read `stopped`; the agent read `available` because the orphan was heartbeating on its own behalf. Two reads were missing before this could be answered at all: terminals could not be LISTED, and `process_id` reached no response. Scoped to THIS host's environment, so another machine's terminals are not reported as missing. `unknown` rather than ok when aify-env is silent or the listing was truncated -- rows past the limit would otherwise read as orphans |
| `session-handles` | one conversation claimed by MORE THAN ONE agent. The ids are already unique -- the failure is several agents pointing at one, so the binding is what needs measuring. TWO live instances on 2026-08-31, found by hand hours apart and invisible to every status badge: a re-registered resident left a GHOST row holding its session handle, so every message to that id was refused and relayed for hours while its `lastSeen` kept refreshing on each tool call; and four hermes agents shared one conversation, which is how a thread reaches 1.1M tokens against a 900k window. REPORTS, never refuses -- a bind-time guard must mean "another agent holds this" and not "this exists", or it refuses an agent its own handle on every re-register, and the mechanism producing the duplicates is still unknown (two traced explanations were disproved against hermes' own source). One read of `/api/v1/agents` answers it for the whole fleet |
| `api-exposure` | an unauthenticated fleet listing that ALSO returns live gateway tokens. Neither half is a defect alone -- running without `API_KEY` is a configuration, and the token is in `runtimeConfig.gatewayUrl` because the dashboard's one-click hermes console link reads it -- so this fires only on the COMBINATION. Measured 2026-08-29: 200 with no key, 200 with a wrong key, 16 of 47 rows carrying a token, port published on 0.0.0.0. It REPORTS: every way out (require a key, bind loopback, move the token off the listing) is a decision with a cost, and none is a tool's to make. Asks with its OWN fetch, not doctor's `get` -- asking with a key can only answer "yes, with a key" |
| `context-window` | an agent whose CONVERSATION has outgrown its model, which every other signal reports as healthy. Measured 2026-08-31: five managed hermes agents silent for over two hours while status read `online`, `lastSeen` refreshed every few seconds and their runs reported `delivered` -- they read their messages, started work, and died on a context window filled by conversations resuming since JUNE. The auto-mirrored dispatch failure names four candidate causes and this is not one of them. Reads the runtime's own footer (`922.4k/900k`) rather than the rendered percentage, because a dying agent's screen wraps and the bar and percent are shredded while the pair survives. `unknown-all` when no console could be read. The 0.9 warning threshold is calibrated against a CONTROL: the one agent at 0.91 was the only one still producing |
| `env-bridge` | no environment bridge is actually **ONLINE** → dashboard-managed spawns cannot run. Keys on each row's server-derived `status`, and names the registered-but-dead ones with their `lastSeen`. (Until `756f3a5` it counted *registered* rows and reported "2 connected" with zero bridges alive — the exact false green this tool exists to prevent.) |
| `usage-openai` | the ChatGPT quota token works — by calling the API, since an expired token passes a file check |

**Four checks left this tool on 2026-08-24, and are answered by the tier that owns them.**
`wrappers`, `wrapper-current` and `runtimes` are aify-wrapper's — `aify-wrapper-check` already
implemented them, and a second implementation of one question does not agree for free: it agrees until
one is fixed, and the copy here was the one carrying a Windows bug where `which` returns an MSYS path
native Node cannot open. `bridge-terminal` is aify-env's — `aify-env doctor` reports whether this host
can open a terminal. The table that assigns them is [docs/AIFY_ENV_BOUNDARY.md](docs/AIFY_ENV_BOUNDARY.md).

**On Windows, `bridge-running` and `agent-identity` are skips** — they read `/proc`. `bridge-current` (v0.2 item B1, shipped v0.3.1) closes the first of those gaps on every platform by having each bridge report the sha it is running, so relaunching wrappers is no longer an unverified step. `agent-identity` is still Windows-unanswered.

Expect `bridge-current` to read **`unknown-all` (FAIL)** immediately after upgrading from a pre-B1 bridge: no live bridge reports a build until it restarts. That red is accurate — nothing has verified anything yet — and clears itself once the wrappers are relaunched. Do not "fix" it by making the check green again; that was the bug.

Operator-facing versions of these flows (install / update integrations / install / update container) are the **Agent playbooks** table in [README.md](README.md).

## Testing a change

```bash
# Backend change (service/ or mcp/sse_server.py)
docker compose up -d --build && curl http://localhost:8800/health

# Bridge change (mcp/stdio/)
# Restart codex-aify or claude-aify in whatever session tests the change.
node --check mcp/stdio/server.js
node --check mcp/stdio/runtimes.js

# Python change — the router is no longer the only safety-sensitive surface (v0.5 moved the
# reconcilers out), so parse the leaf modules too, not just the control plane.
python -m py_compile service/control_plane.py service/reconcilers/*.py service/clock.py service/env_status.py
```

**The three suites, all of which must be green before a commit.** `node --check` only PARSES — it passed
on a module that referenced an undefined name and threw on its first real call, so it is a smoke test, not
a test.

```bash
python -m pytest service/tests -q -n 8 --dist loadfile # 5281 tests (+10389 subtests)
cd mcp/stdio && node tests/run-all.mjs                 # 412 suites, 1 skipped test (named in its output)
cd service/new_dashboard && node --test *.test.mjs     # 1515 tests
```

**`-n 8 --dist loadfile` IS THE PYTHON INVOCATION, not an optimisation to remember.** Serial, that
suite is 22 minutes on one of this machine's 32 cores, and a 22-minute suite is a suite that gets
skipped — which is how three of this file's own counts went stale and how a cross-repo proof ran
nothing while reporting green. Parallel it is **2m50**, with nothing deleted. `--dist loadfile` is
load-bearing: it keeps a file's tests on ONE worker, so class-level state and the process-global
live-status cache behave exactly as they do serially. 16 workers buys a further 14 seconds and is not
worth the contention.

Two things behave differently in parallel and both are worth knowing before you read a red. A
`subTest` LABEL crosses a process boundary through execnet, which cannot encode an arbitrary object —
an enum there passes alone and fails at `-n 8`, and the traceback names the serializer rather than
your file (`79b878c4`). And two files on DIFFERENT workers touching one external resource — the real
`~/.claude.json`, the service registry, a fixed port — is the genuine hazard `--dist loadfile` does
not cover; a test that reaches outside its own sandbox needs pinning, not a retry.

**AND THE TWO SIBLING REPOS, because a change here can redden them and a change there can redden this
one.** They are not optional extras: `env-client-against-real-aify-env.test.js` and
`delegated-terminal-against-real-aify-env.test.js` in the BRIDGE suite start a real aify-env from the
checkout, so an aify-env edit is verified by running aify-comms' tests, and an aify-comms edit to the
seam is only verified by having aify-env present.

```bash
cd ~/projects/aify-wrapper && node --test tests/*.test.js   # 177 tests
cd ~/projects/aify-env    && npm test                          # 925 tests, 1 skipped; `npm test`
                                                           # NOT a bare `node --test`: the script
                                                           # carries --test-timeout=60000, and a
                                                           # hang there once left a test process
                                                           # and two daemons alive for 2.5 hours
```

PROVEN ON 2026-08-26, and it cost the operator's fleet three times before it was understood: an
aify-env fix (`908981b`, `cf92c57`) and the aify-comms test that drives it (`9a909c4a`) had to land as
a matched pair, and the evidence that the pair works is an aify-comms suite run with an aify-env
checkout present. Running three suites instead of five would have reported all of it green.

**The bridge suite uses TWO idioms, and counting one of them gives a third the answer.** 233 files use
`node:test` with `test(...)` blocks; 109 use plain top-level assertions and print "all assertions
passed" at the end. `run-all.mjs` judges every file by EXIT STATUS, so both work -- and its "N suite(s)
passed" is a FILE count, not a test count. Counting `test(` calls to size the suite reports 109 files as
empty when they are not. Measured 2026-08-20: all 342 files carry a `test(` or an `assert`, so none is
vacuous.

**Exit status alone cannot tell a proof from a skip, so the runner reads what each file reported.** A
file whose tests all SKIPPED exits 0 and used to read as passed — and
`delegated-terminal-against-real-aify-env.test.js`, the standing evidence that Phase 8's seam reaches a
real environment tier, skips itself when the aify-env checkout is absent. On any other machine that
proof ran nothing while the runner said everything passed. Skipped files are now NAMED under "skipped,
so NOT verified here" and never folded into the pass total; a file with no TAP summary counts as zero
skips, not as a skip, because 109 of them print none. Its sibling
`env-client-against-real-aify-env.test.js` goes further and FAILS when the checkout is missing, and the
delegated one does too now — for a cross-repo proof, "unverified" must not read as green.

Those counts are a **measured snapshot** (2026-08-27), not a target: they are there so a wrong invocation is
obvious (a `node --test` that reports 200 did not discover the suite). They rot with every slice — the run is
the authority, never the number written here. They were 3991/318/1097 on 2026-08-17, 4165/332/1109 on 2026-08-19, 4183/342/1135 and then 4226/349/1135 on 2026-08-20, 4271/351/1135 on 2026-08-24, and 4413/364/1221 then 4541/372/1254 on 2026-08-26, 4571/373/1273 on 2026-08-27, 4699/377/1334 on 2026-08-28, 4926/396/1429, 4943/396/1437 4966/396/1437 on 2026-08-29 and 5055/399/1439, 5073/400/1439 then 5081/400/1439 on 2026-08-30, and 5205/407/1463 then 5221/408/1463, 5228/408/1463 and 5228/408/1480 then 5235/408/1480 on 2026-09-01, and 5281/412/1515 on 2026-09-03 -- twenty-two readings in seventeen days, THREE of them on 2026-08-29 alone, which is the argument. **THE HISTORY TRACKS THREE OF THE FIVE SUITES, AND THE TWO IT OMITS ROT FURTHEST.** Each reading above is python/bridge/dashboard; aify-wrapper and aify-env are in the block below and in nobody's ritual, so nothing brings anyone to them. Measured 2026-09-01: the wrapper had drifted 158 -> 162, and aify-env 496 -> 798 (162 -> 177 and 798 -> 925 by 2026-09-03, so BOTH kept moving while nobody was looking at them), which is SIXTY-ONE PERCENT and the most stale figure this file carried. That is exactly the magnitude the numbers exist to rule out -- an agent whose aify-env run reports 798 against a documented 496 cannot tell fifteen days of growth from a wrong invocation, and 496 is close enough to half that a wrong invocation is the likelier reading. Re-measure all five, not the three with a habit attached. **The 2026-09-01 reading found the LAYOUT TABLE stale for the THIRD time**, and by three numbers at once rather than one: it read 65 modules / 108 test files / 1439 tests against a measured 66 / 109 / 1463. The paragraph below predicted exactly this and named the reason, and it still happened, because whoever comes to write the date arrives here and not there. The last pair is the sharpest version of it: a figure written into this file in the morning was wrong by the evening, without anyone doing anything unusual. Each of those readings was taken because somebody was about to quote the previous one. **Until that last update this file carried TWO different dashboard counts** -- 1097 in the layout table and 1109 here -- which is the failure this paragraph warns about, sitting inside the warning. **It happened a SECOND time and went unnoticed for a day**: the layout table read 1166 while this paragraph read 1254, both written on 2026-08-26. Twice is not bad luck. The layout table is the copy that rots, because whoever updates a count comes here to write the date and never scrolls up. Before that they read 955/219/541 and 1576 while the real
figures were already these, which is the whole reason for this paragraph.

Editing `service/new_dashboard/app.js` also means updating `extraction-proof.test.mjs` in the SAME change
(see the layout table) — it fails loudly rather than silently if a declaration moves without being
declared.

**Run all three suites for every change, because no suite stays inside its own tree.** Measured
2026-08-20, counting test files that read a path literal outside their own directory:

| suite | also reads | in N test files |
|---|---|---|
| bridge | `install.sh` / `service/new_dashboard` / `service` | 17 / 10 / 9 |
| python | `mcp/stdio` / `install.sh` / `service/new_dashboard` | 13 / 13 / 4 |
| dashboard | `service` / `mcp/stdio` | 4 / 3 |

So a bridge edit can redden python AND dashboard; an `install.sh` edit reaches all three. **The targeted
run is the trap** — it is green because it did not look. v0.6 Phase 8 hit exactly this: the seam grew
`TerminalProcessManager`, the bridge suite was green, and `extraction-proof.test.mjs` — a DASHBOARD test
that cross-checks `declarationSpan` against five BRIDGE classes — sat red until the next full sweep.
When one of those cross-checks fails, RE-MEASURE the value independently and record it, rather than
copying the number out of the failure message: that number is whatever the change produced, not what is
true.

### The 1000-line gate fails your change — read this before "fixing" it

**Re-measured 2026-09-03, closest to the limit first**, by the walk described below — the gates' own
`SKIP_DIRS`, extensions and counting convention, so this is the population they actually judge:

| lines | file | headroom |
|---|---|---|
| 993 | `mcp/stdio/pi-session.js` | 7 |
| 990 | `mcp/stdio/server.js` | 10 |
| 987 | `service/new_dashboard/app.js` | 13 |
| 981 | `mcp/stdio/terminal-runtime.js` | 19 |
| 893 | `service/control_plane.py` | 107 |
| 853 | `mcp/stdio/doctor-predicates.js` | 147 |

542 files, none at or over the limit. FOUR are within 19 lines of it and the top four have not
changed identity since 2026-08-29 — they have crept: `server.js` 984 -> 990, `terminal-runtime.js`
969 -> 981. `doctor-predicates.js` came DOWN 914 -> 853 when the spawn-claim predicates left for
`spawn-claimer.mjs`, which is the shape that relieves one of these: a file at the limit is usually
several subjects, and the one to move is the subject somebody else also needs.

Note what that table would have said if it had been believed rather than re-run: it recorded
`doctor-predicates.js` at 844 on 2026-08-29, and the file was at 914 before this measurement — a row
70 lines wrong, in the direction that matters. The next person should re-run the walk rather than
amend a row.

`doctor-predicates.js` WAS the one to watch, and the watch paid twice. It went 868 (sixth,
2026-08-26) -> 991 -> **998, two lines of headroom and FIRST, the same day** -- a row already wrong
when it was read, stale by a commit made after it was written. Splitting `usage-openai` out into
`openai-usage-check.mjs` took 153 lines off it; the spawn-claim predicates leaving for
`spawn-claimer.mjs` on 2026-09-03 took another 61, and it now sits sixth at 853. Nothing re-exports
the moved names in either case, so a stale import fails loudly rather than resolving, and
`tests/doctor-sources.mjs` walks the doctor's imports transitively so a new module joins "the doctor"
with no edit anywhere. Both moves followed the same rule: take out a SUBJECT somebody else also
needs, not whichever block is longest.

`pi-session.js` inherits the watch at 7 lines. It has no equivalent fix waiting: it is one session
class, not a file of independent checks, so relieving it means finding a real seam rather than
lifting a block out.

Nothing is broken — the gate is a red test, not a silent failure — but the next small edit to
pi-session.js goes red for a reason unrelated to that edit, and its author should hear it from this
paragraph rather than from the suite.

**This list was wrong in exactly the way it warns about, twice over.** Until 2026-08-25 it named only
`app.js` and `control_plane.py`, and pi-session.js at 993 was on nobody's list. The correction that
added pi-session.js then claimed to be the whole population and still SKIPPED `server.js` (961) and
`terminal-runtime.js` (896) — ranks three and four, both tighter than the `control_plane.py` it did
name. A ranked list that omits its own middle is worse than no list, because it reads as complete. The
table above came from one walk using the GATES' OWN parameters -- their `SKIP_DIRS`
(`node_modules`, `tests`, `fixtures`, `__pycache__`, `.git`, `.pytest_cache`, `.venv`, `venv`), their
extensions, and their `wc -l` counting convention -- so it is the population the gates actually judge,
not a similar one. That walk saw 528 files on 2026-08-29 and 542 on 2026-09-03, none at or over the limit — and the six are not the same six each time, so re-run it rather than trusting the names. A walk that FORGETS to exclude
`.test.` by NAME reports `service/new_dashboard/extraction-proof.test.mjs` at 2,925 as the worst offender: the
gates prune a `tests` DIRECTORY, and that file does not live in one. The next person to edit
this should re-run that walk rather than amend a row.

No product source file may reach 1000 lines. Two tests enforce it:
`service/tests/test_no_new_oversized_source_file.py` (Python) and
`mcp/stdio/tests/no-new-oversized-source-file.test.js` (JS). Both read ONE policy file,
`oversized-allowlist.json` at the repo root.

**The allowlist is EMPTY, and that is the end state, not a gap.** It held five files with an open decision
packet or a standing ruling; each earned its way off during v0.5.4, the last being `app.js` on 2026-08-14.
Both gates treat an empty list as exempting nothing — the input a predicate written as "no rule means
allow" gets backwards — and both fail if a listed file is deleted or drops below the limit, so the list
shrinks honestly instead of rotting into unchecked names. **Adding your file to it is a REVIEWER DECISION,
not a fix**: appending an entry to make a red test green is the exact move the gate exists to stop.

**Scope: non-test `.py` and `.js`/`.mjs`, repo-wide.** Both scans walk from the repo root and prune
`node_modules`, `tests`, `fixtures`, `__pycache__` and `.git` at the directory level. The Python half read
`service/**` only until 2026-08-15, which left fifteen files ungoverned — including `mcp/sse_server.py`,
which ships in the container — and the JS half's two hand-listed roots covered everything only by
coincidence. Neither hole was visible from the result: an unguarded population reports green exactly like a
guarded one. **Shell and CSS are deliberately OUT of scope** and each gate says so in a test, because
`install.sh` (3,074 lines on 2026-08-30, down from 4,371 once all four wrapper bodies became template files, and up from the 2,978 recorded here when that happened; the key resolver that grew it moved out to `scripts/api-key.sh` and its ceiling was raised the remaining 25 with the reason written into the gate) and `service/new_dashboard/styles.css` (1,843) are non-test source over the
limit and bringing them in is an open reviewer question, not a widening to do quietly.

The failure this gate was built from: a v0.5.4 relocation moved a 6-line helper into `service/db.py` — the
correct subject owner — taking it 995 → 1006. `control_plane.py` shrank and a NEW file went over. The
undefined-name sweep, the stale-owner census, `create_app()` and all three suites were green, because none
of them measures the DESTINATION of a move. **When relocating, measure the destination's line count, not
just its dependency direction and transaction ownership.**

The allowlist is keyed by PATH, not basename — an earlier version keyed by basename and would have exempted
any file named `app.js` anywhere. A pure predicate in each gate pins that, and both fail if a listed file is
deleted or drops below the limit, so the list shrinks honestly instead of rotting into unchecked names.
