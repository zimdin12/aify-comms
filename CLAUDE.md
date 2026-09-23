# aify-comms — Claude Code project notes

Inter-agent communication hub: messaging, channels, file sharing, active dispatch, and a dashboard for Claude Code, Codex, OpenCode, and other MCP-connected coding agents. This file is loaded by Claude Code when working **on this repo itself**; usage docs for someone installing aify-comms live in [README.md](README.md).

## Primary entry points

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — **how it is built.** The three processes and what
  reloads each, the service layering and why it is flat, the message-to-work path, and every layer
  rule paired with the test that fails when it is broken. Read before your first change; the rules
  below assume it.
- [README.md](README.md) — what the service is, setup, day-to-day usage.
- [install.claude.md](install.claude.md) / [install.codex.md](install.codex.md) / [install.hermes.md](install.hermes.md) / [install.opencode.md](install.opencode.md) / [install.pi.md](install.pi.md) (pi is deprecated: support kept, tests off unless `AIFY_TEST_DEPRECATED=pi`) — per-runtime install guides (wrappers, hooks, verification).
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
| `service/new_dashboard/*.mjs` | Dashboard modules with their own `*.test.mjs` — 78 modules, 136 test files, 1790 tests (the test figure RE-MEASURED 2026-09-22, all three on 2026-09-12; modules and test files re-counted 2026-09-11 rather than carried forward, and the test figure is the one the run printed; re-measured 2026-09-08 three ways; it read 83 modules and had been stale by EIGHT, the FOURTH time this copy has rotted -- 74 of the 75 have a sibling test, and there are more test files than modules because some are cross-cutting) (`api-client`, `shared-files`, `message-transport`, `state`, `session-rail`, `terminal-input`, …). **`app.js` is 996 lines (was 5,081) and is UNDER the 1000-line gate** — the "3,612 lines / only reachable by source-regex tests" note that stood here is retired twice over: the extracted modules import and run in Node, so they are tested by CALLING them, and the boot wiring, the delegated click dispatcher and the per-page actions have all left. Still put new behaviour in a module here rather than in `app.js`: what remains is the render orchestrator, and it is FOUR lines from going red (996 -> 999 on 2026-09-16, for the hidden-tab refresh gate, and back to 996 on 2026-09-17 when the poll timer moved into change-refresh.mjs) -- it actually WENT red at 1001 on 2026-09-05 while the message-history slice was being wired, and the room was found by moving that feature's URL into the module that owns paging, not by touching the gate. |
| `service/new_dashboard/extraction-proof.mjs` + `.test.mjs` | **The gate that makes app.js safe to slice.** It RECONSTRUCTS the pre-extraction app.js from the current file plus every extracted module and requires byte-identity with a tracked pristine fixture — so a slice cannot quietly change anything outside the spans it declared. Moving a declaration out of `app.js` means appending an entry to its `EXTRACTIONS` plan **in the same change**: `importLine` (and `importWas` only if the slice EDITED an existing import — a module created by an earlier extraction has none in the fixture, so its line is deleted instead), plus one item per declaration with `at` = the 0-indexed line in the FIXTURE, not the live file. `marker` may be several lines and each is verified verbatim, which is what lets a slice leave a seeding call behind. |
| `mcp/stdio/` | Host-side MCP bridges (`server.js`, `claude-channel.js`, `runtimes.js`, `runtime-markers.js`, `notify-check.js`). Restart client wrapper after changes. |
| `mcp/stdio/service-registry.mjs`, `register-service-cli.mjs` | **v0.6 Phase 6.** Writing this service's entry into the SHARED registry at `~/.aify/services.json`, which is how a launcher learns aify-comms exists. Installing a SERVICE registers it; installing the wrapper package is never the goal. aify-comms owns its own key and leaves every other service's alone — an unreadable or wrong-version registry is REFUSED, never rewritten, because overwriting would uninstall another service at the moment somebody reinstalls something unrelated. The reader lives in the aify-wrapper package (`lib/registry.mjs`); `endpointEnv` is exported from `aify-service-endpoint.mjs` as `ENDPOINT_ENV_NAMES` rather than typed twice, because a name the bridge reads but the registry does not declare gets INHERITED from whatever launched the runtime. |
| `mcp/stdio/*-predicates.js`, `register-identity.js` | PURE, unit-tested helpers extracted out of the bridges so their logic can fail a test instead of only failing in production. `doctor-predicates.js` (env liveness) and `register-identity.js` (resident launch-identity warning) are the pattern to follow — `doctor.js` was untestable until its predicates moved out, and the first thing the new test caught was a real bug. **`service-check.mjs` is the next step past a predicate: the whole `service` CHECK, not just its verdict.** A predicate proven in isolation still leaves the call to it unproven, and that is exactly where this one failed -- an early return answered the no-checkout case itself and never consulted the verdict. Anything that needs its call site executed belongs in a module like this, because importing `doctor.js` RUNS the doctor. Which files are "the doctor" is DERIVED, never listed (`tests/doctor-sources.mjs` and `service/tests/doctor_sources.py`, kept honest by an agreement test): four scanners hardcoded the filename, and moving one check reddened three while the fourth stayed green by no longer looking. |
| `mcp/sse_server.py` | SSE MCP transport (runs inside the container). Rebuild container after changes. |
| `.claude/skills/aify-comms/` | Usage skill — tool reference, workflow, status table, multi-instance matrix. |
| `.claude/skills/aify-comms-debug/` | Troubleshooting skill — known issues and fixes. |
| `.agents/skills/aify-comms*/` | Mirrors of the two skills for Codex agents. Keep in sync. |
| `mcp/stdio/node_modules/aify-wrapper/wrappers/` | The four launcher templates, from the **[aify-wrapper](https://github.com/zimdin12/aify-wrapper) package**, pinned to a sha in `mcp/stdio/package.json`. They were a byte-identical copy under `wrappers/` here, kept honest by a hash gate in each repo — two sources of truth for one artifact. The operator settled it on 2026-08-20: aify-comms consumes the package, no duplicates. Both drift gates are retired and `wrappers/` is deleted; `install.sh` renders from `WRAPPER_TEMPLATE_DIR`, and the swap was proven byte-identical on all six rendered launchers before the copy was removed. **`--emit-wrappers` now needs `npm install` to have run in `mcp/stdio`**, because it exits before the installer's own npm step by design. **BUMPING THE PIN AND RUNNING `npm install` DOES NOT UPDATE THE PACKAGE** — measured 2026-08-30: the sha was raised in `package.json` AND `package-lock.json`, `npm install` reported success, and `node_modules/aify-wrapper` still held the previous code. npm trusts a tree that matches the lock it was just handed. The gate caught it (`the-wrapper-pin-is-not-behind-a-template-change.test.js` was still red for the same reason), but the failure shape is this repo's favourite: a step that reports success and changes nothing. Remove `node_modules/aify-wrapper` and reinstall, then GREP the installed file for whatever the bump was for. **DONE THAT WAY ON 2026-09-12** and it worked: the pin moved to the Herdr integration, `node_modules/aify-wrapper` was removed before `npm install`, and the installed templates were grepped for `AIFY_HERDR_ARGV` before anything was believed -- all four carry it. **Those templates now claim their Herdr pane**, which is what makes `claude-aify` come back as `claude-aify` rather than a bare `claude` after a reboot; it is gated on `HERDR_ENV`, so an ordinary terminal launch never starts node for it, and it can never fail a launch. The design, the measurements behind it and the one install step an operator runs are in aify-wrapper's `HERDR.md`. It also means every template now carries `@@BRIDGE_DIR@@`, so a renderer or test that substitutes a partial set is refused by `render.sh` -- which is how it should fail, and did. |
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

- **Size is gated by a ratchet, not a cap.** `mcp/stdio/tests/skill-size-ratchet.test.js` holds all 18
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
| `spawn-delegation` | where managed spawns run, read from the installed launcher rather than by running it. Delegation makes aify-env REQUIRED — the bridge refuses rather than silently hosting spawns itself, because two spawners on one host is the collision the environment tier exists to end — so a down aify-env presents as spawns failing with no cause attached. Reports `local` (the default), `delegated`, `unreachable` (FAIL), or `pre-contract` for a launcher rendered before the setting existed. **The env rows ask the aify-env actually serving**: the launcher bakes 8802, and a `herdr-aify env` daemon listens on its own port, so on 2026-09-13 this row, `env-processes` and `env-code-currency` all reported a healthy daemon as unreachable. `serving-env-endpoint.mjs` now falls back to the daemon's `ready.json` receipt, believed only when `/health` answers with the receipt's pid AND instance |
| `tier-version` | an aify-env SERVING this host that is older than the aify-comms installed on it. Three repos ship one product and nothing compared them until 2026-09-04 -- the external review's own preamble, and `bridgeVersion` was already on the wire with NO reader. A MINIMUM rather than equality, because the tiers are separate products on separate cadences and aify-dashboard will consume the same ones; `MINIMUM_AIFY_ENV_VERSION` is declared with the capability that forced it, so a bump has to argue for itself. What it catches: an aify-comms carrying H4's fix against an aify-env too old to send `bridgeKind` takes the legacy path SILENTLY -- both sides healthy, the feature absent. A row declaring no kind is `unknown-all`, NOT skipped: that is exactly what an aify-env too old to declare one looks like, and scoping to rows that announce themselves scoped out every row that is behind. The first version of this check did that and reported green on the operator's own host while its tier was two versions back |
| `spawn-queue` | a spawn request a host CLAIMED and never started -- work that was taken and not done, which every other row here reads as healthy. `spawn-delegation` says where spawns RUN, `env-bridge` says a claimer is registered and `bridge-current` says it runs current code; a host whose CLAIM loop died after taking a request passes all three, because the heartbeat is a SEPARATE loop from the claim loop. That is exactly how the external review's H2 stayed invisible: accepted spawns that never appeared, with every instrument green and the queue never draining. Ages against `SPAWN_ORPHAN_GRACE_SECONDS` (180), the service's own window for the same question, read rather than re-invented. REPORTS, never acts -- a claimed request is somebody's in-flight work. `unknown-all` when the listing could not be read, because a check that gathered no evidence is not a passed one |
| `managed-orphans` | managed delivery loops (`hermes-managed-host.js run <agent>`) running for an agent that belongs to NO live bridge. Nothing collects one during normal operation -- the survivor sweep runs at bridge BOOT, so a loop orphaned mid-session accumulates until the next relaunch -- and the control plane cannot see it: the agent reads `available` because it has no live sidecar, while its `lastSeen` keeps refreshing because the orphan itself is heartbeating. **Reports, never kills.** Six were alive on 2026-08-26, oldest 96 minutes |
| `gateway-orphans` | hermes GATEWAY hosts running in aify-comms' own port range (8642-9641) with no worker behind them. Its sibling `managed-orphans` watches the DELIVERY LOOPS and would have said nothing on 2026-08-31, when the operator's `hermes update` refused to run and listed 45 live processes from this install after aify-env was killed -- the loops were the half that died correctly. Establishing whether those processes were ours took a port range, a marker directory and a process walk; this row answers it directly. Scoped to MANAGED agents, because a resident hermes legitimately runs its own gateway with no loop and flagging it would fire on the operator's own terminal every run. A port NO marker claims is reported as `(unclaimed)` rather than dropped -- a process nobody can account for is the more suspicious one. MEASURED 2026-09-05, after three earlier explanations were wrong: an orphaned gateway lived 2 days 18 hours and was bounded by nothing. The cause is that gateway hosts are spawned `detached: true` on purpose, while every reaper for one lives inside the delivery loop that dies at the same instant -- so a HARD kill of the host tier orphans them all. Since v0.6.8 a gateway ends with its agent: it carries the launcher's lease pid as `HERMES_PARENT_PID` (hermes' own parent-death watchdog), the lease's watch stops what a killed launcher left, and the next start collects the rest. **An ELEVATED gateway reads as having no command line**, so this row missed one on 2026-09-15 while netstat showed its socket -- `hermes update` run from an Administrator terminal had relaunched it. The row now also reads the socket table (`listening-ports.mjs`) and reports a marker-claimed port held by an unreadable process as `unidentified`. See KNOWN_ISSUES.md. It counts HOST TREES, not processes: one gateway is hermes.exe plus two pythons all carrying `--port`, and until 2026-09-13 it read "3 gateway host(s)" for one listener |
| `env-code-currency` | an aify-env RUNNING code that is not the code on its disk. `tier-version` one row up compares VERSIONS, and a daemon that loaded its modules days ago reports exactly the version a current one does -- so the row that exists for tier staleness cannot see this at all. **v0.6.3 was built on a host in that state**: the renderer the operator asked for sat in files the running daemon had never loaded, with `env-bridge`, `spawn-delegation`, `env-processes` and `tier-version` all green. aify-env ALREADY ANSWERED IT on its `/health`: `build`, a content hash of the package SOURCE that process was started from, beside `codeOnDisk` over the files there now. **The SERVICE already compares the same pair** when it is advertised (`api_core/code_currency.py`, shown as a dashboard badge); what was missing is a row here, and on this host the advertised halves arrive EMPTY so that badge reads `unknown`. Measured 2026-09-09 on this host: boot `3b2bf8f9`, disk `1c36464c`. A missing half is `unknown` rather than agreement, because an older aify-env sends one and not the other and reading that as a match would report green on exactly the hosts most likely to be behind. REPORTS, never acts: restarting reaps the predecessor's managed workers, so it is the operator's call. A hash mismatch says the loaded bytes differ from the disk; it does NOT decompose into WHICH files, and the row does not pretend it does |
| `env-processes` | a process aify-env is RUNNING that the control plane has no live terminal for, and the reverse -- a live terminal naming a pid nothing is running. The operator watched a PTY for `ef-manager` (pid 155844) in aify-env while the dashboard showed nothing and every recent session read `stopped`; the agent read `available` because the orphan was heartbeating on its own behalf. Two reads were missing before this could be answered at all: terminals could not be LISTED, and `process_id` reached no response. Scoped to THIS host's environment, so another machine's terminals are not reported as missing. `unknown` rather than ok when aify-env is silent or the listing was truncated -- rows past the limit would otherwise read as orphans |
| `session-handles` | one conversation claimed by MORE THAN ONE agent. The ids are already unique -- the failure is several agents pointing at one, so the binding is what needs measuring. TWO live instances on 2026-08-31, found by hand hours apart and invisible to every status badge: a re-registered resident left a GHOST row holding its session handle, so every message to that id was refused and relayed for hours while its `lastSeen` kept refreshing on each tool call; and four hermes agents shared one conversation, which is how a thread reaches 1.1M tokens against a 900k window. REPORTS, never refuses -- a bind-time guard must mean "another agent holds this" and not "this exists", or it refuses an agent its own handle on every re-register, and the mechanism producing the duplicates is still unknown (two traced explanations were disproved against hermes' own source). **Two mechanisms are known since 2026-09-16, neither of them hermes'**: `POST /agents` bound a live agent's id without the guard (closed, `registration_handle_collision.py`), and a live agent taking over a DEAD agent's id leaves it on the dead row by design, because clearing it could hand a conversation to the wrong party. One read of `/api/v1/agents` answers it for the whole fleet |
| `api-exposure` | an unauthenticated fleet listing that ALSO returns live gateway tokens. Neither half is a defect alone -- running without `API_KEY` is a configuration, and the token is in `runtimeConfig.gatewayUrl` because the dashboard's one-click hermes console link reads it -- so this fires only on the COMBINATION. Measured 2026-08-29: 200 with no key, 200 with a wrong key, 16 of 47 rows carrying a token, port published on 0.0.0.0. It REPORTS: every way out (require a key, bind loopback, move the token off the listing) is a decision with a cost, and none is a tool's to make. Asks with its OWN fetch, not doctor's `get` -- asking with a key can only answer "yes, with a key" |
| `client-api-key` | this host cannot authenticate against the service it is pointed at. Asks the REAL question rather than a proxy: probe UNAUTHENTICATED (a request carrying a key can only answer "yes, with a key"), then send the SAME request with the key this host actually resolves, through the module every bridge component uses. Fires on the COMBINATION, like `api-exposure`: running with no `API_KEY` is a configuration. **Its first version parsed MCP configs for a key NAME and was wrong twice over** -- review reproduced the detector returning true for `# AIFY_API_KEY: old-key`, for `AIFY_API_KEY: "" # no key`, for `NOT_AIFY_API_KEY` and for a block under the wrong root, while its gatherer turned 500, 404, malformed JSON and EACCES all into green. And correct, it would STILL have missed the outage it was built during: the process that could not authenticate was a STANDALONE worker whose environment no MCP config describes. A 500 or a 404 now reads `unknown`, never `no-key-required` |
| `context-window` | an agent whose CONVERSATION has outgrown its model, which every other signal reports as healthy. Measured 2026-08-31: five managed hermes agents silent for over two hours while status read `online`, `lastSeen` refreshed every few seconds and their runs reported `delivered` -- they read their messages, started work, and died on a context window filled by conversations resuming since JUNE. The auto-mirrored dispatch failure names four candidate causes and this is not one of them. Reads the runtime's own footer (`922.4k/900k`) rather than the rendered percentage, because a dying agent's screen wraps and the bar and percent are shredded while the pair survives. A running console with no pair on screen (Claude Code always, hermes before its first turn) is left out, and a fleet of only those SKIPS (operator's ruling: no conversation files); `unknown-all` is for consoles that did not answer. The 0.9 warning threshold is calibrated against a CONTROL: the one agent at 0.91 was the only one still producing. **It measures RUNNING consoles only** (the console route's `live`): `consoleAvailable` is set for every managed agent, so until 2026-09-16 a fleet of stopped agents read `unknown-all` on one host and, on this one, `ok` off an exited terminal's recorded footer. Stopped agents are counted and named, not opened against the cap |
| `env-bridge` | no environment bridge is actually **ONLINE** → dashboard-managed spawns cannot run. Keys on each row's server-derived `status`, and names the registered-but-dead ones with their `lastSeen`. (Until `756f3a5` it counted *registered* rows and reported "2 connected" with zero bridges alive — the exact false green this tool exists to prevent.) |
| `claude-login` | the ONE OAuth grant every claude-code agent on this host shares, running out. Nothing watched it: `usage-collector.js` had read `~/.claude/.credentials.json` for the quota pool all along, so the gap was never the data but that nobody asked it the question with a deadline attached. Measured 2026-09-03 16:36 UTC, which is why it exists: the task list said "expires ~2026-09-05" while the file said the access token expired at 23:09 THAT NIGHT and the refresh token at 11:57 the next morning -- two days optimistic, in the direction where being wrong means 21 agents stopping overnight with nobody awake. Keys on the REFRESH window only: the access token is short-lived and renews itself, so failing on it would go red most hours, get switched off, and take the real deadline with it. Reads two timestamps and never a token, because this row gets pasted into reports. SKIPS on a host with no claude login -- a codex/hermes-only machine is not broken |
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
python -m pytest service/tests scripts/tests -q -n 8 --dist loadfile # 4785 tests, 24 skipped (+10592 subtests; 21 are deprecated pi tests)
cd mcp/stdio && node tests/run-all.mjs                 # 357 suites, 2 skipped tests, 9 pi files disabled (all named in its output)
#   pi is DEPRECATED and its coverage is off by default -- 9 bridge files and 3 Python files. It is
#   still shipped, still in LAUNCHABLE_RUNTIMES, and `dispatch_hint.py` still suggests it, so the
#   code is live while the tests are not. `AIFY_TEST_DEPRECATED=pi` turns them back on (8 skipped
#   by default, 8 passing under the flag). That opt-in was written down in DECISIONS.md and
#   install.pi.md and NOT here, which is where people look (external review, finding 8).
cd service/new_dashboard && node --test *.test.mjs     # 1790 tests
```

**`-n 8 --dist loadfile` IS THE PYTHON INVOCATION, not an optimisation to remember.** Serial, that
suite is 22 minutes on one of this machine's 32 cores, and a 22-minute suite is a suite that gets
skipped — which is how three of this file's own counts went stale and how a cross-repo proof ran
nothing while reporting green. Parallel it is **2m50**, with nothing deleted. `--dist loadfile` is
load-bearing: it keeps a file's tests on ONE worker, so class-level state and the process-global
live-status cache behave exactly as they do serially. 16 workers buys a further 14 seconds and is not
worth the contention.

**AND A THIRD, MEASURED 2026-09-09: AT `-n 8` THE SUITE CAN EXHAUST ITS OWN SOCKETS.** Four
consecutive runs failed 3, 3, 4 and 3 tests, a DIFFERENT set each time, every named file passing
alone and all of them passing together. Stashing the working tree and re-running still failed --
which establishes that SOME failure happens without the candidate change, and nothing more: the
failing population differed, so it does not exclude an additional defect the candidate
introduced. That still needs per-failure attribution, and this session found two real defects
exactly there. Past the assertion messages one cause names itself:
`OSError: [WinError 10055] ... the system lacked sufficient buffer space`, raised by the
`connect()` inside CPython's `socket.socketpair()`, with
`'ProactorEventLoop' object has no attribute '_ssock'` beside it.

`socketpair()` on Windows is emulated with a REAL LOOPBACK TCP CONNECTION and every asyncio event
loop builds one for its self-pipe, so a suite of 5,600 tests that each run a loop opens thousands
of them. Sampled during one invocation, from a resting **296**: 3,290 at twelve seconds, 5,771 at
a minute forty, 8,455 at two minutes, **12,170 just after** -- against an ephemeral range of
16,384 (`netsh int ipv4 show dynamicport tcp`). It drains on its own in about twenty-five minutes.

**FIXED 2026-09-17, after it HUNG a run for 56 minutes** (four workers blocked in the emulation's
`accept()`): `service/tests/conftest.py` closes those test-process socketpairs abortively (SO_LINGER
0), which skips TIME_WAIT. Same suite, same sampler: peak 12,803 before, 374 after. The paragraphs
below stay as the record of what a red looked like; re-read them if the count climbs again.

**SO A RED HERE IS NOT AUTOMATICALLY A DEFECT, AND A GREEN IS NOT AUTOMATICALLY ITS ABSENCE.**
Before believing either: READ EVERY FAILURE, then run the named files alone, then stash and
re-run the suite. A moving failure set is consistent with this and does not identify it -- two of
the failures found while chasing it were ordinary defects, one of them real and newly introduced,
and a story about the set would have buried both. `-n 4` gave 1 failure in one run and 3 in
another, so it does not reliably halve anything. The real cost sits in creating an event loop per
test, which nothing here has ever counted.

**AND THE FIRST DIAGNOSIS OF IT WAS WRONG IN THE WAY THIS FILE KEEPS RECORDING.** The count was
sampled BETWEEN runs, read at 9,516 as ambient load, and reported to the operator as a host
condition -- "a lead about the machine rather than the code", with the remedy assigned to them.
It was residue from the previous run. The sample that settles it is taken DURING one, and the
correction is that the measuring instrument was manufacturing the condition it reported.

Two things behave differently in parallel and both are worth knowing before you read a red. A
`subTest` LABEL crosses a process boundary through execnet, which cannot encode an arbitrary object —
an enum there passes alone and fails at `-n 8`, and the traceback names the serializer rather than
your file (`79b878c4`). And two files on DIFFERENT workers touching one external resource — the real
`~/.claude.json`, the service registry, a fixed port — is the genuine hazard `--dist loadfile` does
not cover; a test that reaches outside its own sandbox needs pinning, not a retry.

**AND ONE GATE THAT IS NOT IN ANY OF THE FIVE, because it needs a browser.**
`service/new_dashboard/fixtures/messenger-browser.mjs` is the only thing that exercises the REAL
message sanitizer: DOMPurify needs a DOM and reports `isSupported === false` under `node --test`, so
every Node assertion about `richMessageHtml` exercises its no-DOM ESCAPE FALLBACK -- the branch that
does not sanitize -- while reading as coverage. Nothing ran that file until 2026-09-11, when it was
served and driven in Chrome 152: **15 of 15 passed**, including nine XSS vectors, and the green was
negative-controlled by flipping `ALLOW_DATA_ATTR`, which took it to 14 of 15 naming the exact attack
(`unsafe attr data-chat-view`). `message-format.test.mjs` gates the POLICY every run; that fixture
proves the policy is enforced, and it runs when somebody remembers. `fixtures/README.md` has the
procedure and the trap that wastes the first ten minutes (`.mjs` served as `text/plain`).

**AND THE TWO SIBLING REPOS, because a change here can redden them and a change there can redden this
one.** They are not optional extras:
`the-credential-ref-we-write-is-one-aify-env-resolves.test.js` in the BRIDGE suite imports aify-env's
`lib/credential-store.mjs` from the checkout (it starts nothing), so an aify-env edit is verified by
running aify-comms' tests, and an aify-comms edit to the seam is only verified by having aify-env
present. Every cross-repo test finds the checkout one way: `AIFY_ENV_REPO` / `AIFY_WRAPPER_REPO` if
set, else beside this checkout, else `~/projects` (`mcp/stdio/tests/_sibling-checkout.mjs`).

**AND SINCE 2026-09-09 A SECOND ONE, IN THE PYTHON SUITE**, which is the direction nobody expects:
`service/tests/test_the_env_plugin_addresses_routes_this_service_serves.py` reads aify-env's
`lib/plugins/aify-comms/api.mjs` and asserts every `#send(METHOD, path)` in it names a route this
app serves, at that method -- ten requests against 130 routes -- and that every top-level FIELD
it sends is one the receiving route's model declares, since Pydantic drops an undeclared key in
silence. A SECOND python test goes the other way -- `test_the_env_plugin_reads_what_this_service
_answers.py` runs aify-env's OWN readers on real `/agents` and `/sessions` responses, so a
reader looking for a field this service does not send fails here rather than by quietly deciding
something. A THIRD runs aify-env's own CLAIM PASS on the real `/spawn-requests/claim` answer
and asserts it reaches a registered agent -- the return leg, which has a defect on the record:
six spawns were claimed and all six failed because the plugin read `request.launcher`, a field
the wire has never carried. **All of them redden on an aify-env edit**, which is the direction
nobody expects. **So an aify-env PLUGIN edit can
redden aify-comms' PYTHON suite, and a route rename here can redden it for the opposite reason.**
It reads SOURCE and never starts a daemon, because starting aify-env supersedes the one serving this
host. Point `AIFY_ENV_REPO` at a checkout to judge that one; a named tree with no plugin is REFUSED
rather than replaced by the default, which is a defect its own mutation run found. With no checkout
it SKIPS by name. It covers the ADDRESS only -- not bodies, responses, auth, or a live round trip.

**IT USED TO BE SIX SUCH TESTS AND IS NOW ONE**, which is a real reduction and worth knowing before
you rely on this. Five of them drove aify-comms' own `env-client` and `terminal-runtime` against a
real aify-env, and both modules belonged to the environment-bridge tier v0.6.2 retired -- so those
five proved a seam that no longer exists, and they were deleted with it on 2026-09-05. The survivor
proves something still true: the credential reference this service writes is the one aify-env
resolves. If a future change needs stronger cross-repo evidence, it has to be built against the
seam that is actually load-bearing now -- aify-env's own plugin talking to this service's HTTP API.

```bash
cd ~/projects/aify-wrapper && npm test                      # 551 tests, 66 skipped on Windows (16 on Linux). `npm test`
                                                           # NOT a bare `node --test`: the script
                                                           # runs the suite under ONE temp root and
                                                           # deletes it, which is why this machine
                                                           # stopped accumulating ~150 aify-* temp
                                                           # directories per morning.
cd ~/projects/aify-env    && npm test                          # 1946 tests, 2 skipped; `npm test`
                                                           # NOT a bare `node --test`: the script
                                                           # carries --test-timeout=60000, and a
                                                           # hang there once left a test process
                                                           # and two daemons alive for 2.5 hours
```

PROVEN ON 2026-08-26, and it cost the operator's fleet three times before it was understood: an
aify-env fix (`908981b`, `cf92c57`) and the aify-comms test that drives it (`9a909c4a`) had to land as
a matched pair, and the evidence that the pair works is an aify-comms suite run with an aify-env
checkout present. Running three suites instead of five would have reported all of it green.

**The bridge suite uses TWO idioms, and counting one of them gives a third the answer.** 285 files use
`node:test` with `test(...)` blocks; 77 use plain top-level assertions and print "all assertions
passed" at the end. `run-all.mjs` judges every file by EXIT STATUS, so both work -- and its "N suite(s)
passed" is a FILE count, not a test count. Counting `test(` calls to size the suite reports those 77
files as empty when they are not. Re-measured 2026-09-08: all 362 carry a `test(` or an `assert`, so
none is vacuous.

**THE POPULATION IS THE RUNNER'S, NOT A GLOB.** `run-all.mjs` walks THREE directories -- `tests`,
`tests/adapters` and `tests/controllers` -- and takes only `.test.js`. Listing `tests/*.test.js`
plus `*.test.mjs` gives 346, which is wrong in both directions at once: it misses two directories and
counts `.test.mjs` files the runner never executes. 362 is what the runner itself prints, which is
how this reading was checked. The figures here read 233 / 109 over 342 until this measurement, so the
split had moved by more than fifty in each direction while the conclusion drawn from it stayed
correct -- growth with an innocent cause, which is exactly the drift this file keeps recording.

**Exit status alone cannot tell a proof from a skip, so the runner reads what each file reported.** A
file whose tests all SKIPPED exits 0 and used to read as passed — and
`the-credential-ref-we-write-is-one-aify-env-resolves.test.js`, the standing evidence that this
service and the host tier agree, would read that way if it skipped without the aify-env checkout, so
it FAILS instead.

**THAT SENTENCE NAMED TWO DIFFERENT TESTS UNTIL 2026-09-05, and both were deleted that day.** It
cited `env-client-against-real-aify-env.test.js` and `delegated-terminal-against-real-aify-env.test.js`
-- which drove aify-comms' OWN `env-client` and `terminal-runtime` against a real aify-env. Those
two modules were part of the tier v0.6.2 retired and no production path reached either, so the
"standing evidence" was a proof about a seam that no longer existed. Five of the six tests that
drove a real aify-env went with them; the one named above survives because what it proves -- that
the credential reference this service writes is the one aify-env resolves -- is still a live seam. On any other machine that
proof ran nothing while the runner said everything passed. Skipped files are now NAMED under "skipped,
so NOT verified here" and never folded into the pass total; a file with no TAP summary counts as zero
skips, not as a skip, because 109 of them print none. For a cross-repo proof, "unverified" must not
read as green. (The two tests this sentence named until
2026-09-05 were deleted with the tier they exercised.)

Those counts are a **measured snapshot** (2026-08-27), not a target: they are there so a wrong invocation is
obvious (a `node --test` that reports 200 did not discover the suite). They rot with every slice — the run is
the authority, never the number written here. They were 3991/318/1097 on 2026-08-17, 4165/332/1109 on 2026-08-19, 4183/342/1135 and then 4226/349/1135 on 2026-08-20, 4271/351/1135 on 2026-08-24, and 4413/364/1221 then 4541/372/1254 on 2026-08-26, 4571/373/1273 on 2026-08-27, 4699/377/1334 on 2026-08-28, 4926/396/1429, 4943/396/1437 4966/396/1437 on 2026-08-29 and 5055/399/1439, 5073/400/1439 then 5081/400/1439 on 2026-08-30, and 5205/407/1463 then 5221/408/1463, 5228/408/1463 and 5228/408/1480 then 5235/408/1480 on 2026-09-01, and 5281/412/1515 then 5405/418/1586 and 5408/418/1586 on 2026-09-03, and 5409/389/1586 then 5431/393/1589 on 2026-09-04, the second reading that day and the first taken to close an external review, and 5439/394/1632, 5448/395/1636, then 5454/353/1643, 5454/353/1649 and 5461/353/1661 on 2026-09-05 -- SEVEN readings in one day -- then 5472/353/1663 and 5480/353/1666 then 5486/353/1666, 5488/353/1666, 5488/354/1666, 5494/354/1666, 5499/354/1666, 5501/354/1666, 5508/354/1666, 5508/355/1666, 5508/356/1666, 5511/356/1666, 5512/356/1666, 5514/356/1666, 5514/357/1666 then 5514/358/1666, 5514/359/1666, 5514/359/1667, 5517/359/1667, 5517/360/1667, 5517/361/1667, 5517/361/1670, 5517/362/1670, 5517/363/1670, 5520/362/1670, 5520/362/1676, 5525/362/1676, 5528/362/1676, 5529/362/1676, 5539/362/1676, 5544/362/1676, 5552/362/1676, 5558/362/1676, 5563/362/1676, 5566/362/1676 and 5568/362/1676 then 5572/362/1676 and 5573/362/1676 then 5578/362/1678, 5578/362/1679, 5583/362/1679 then 5583/362/1681, 5583/362/1684, 5588/362/1684, 5596/362/1685, 5601/362/1685, 5603/362/1686, 5608/362/1686, 5608/362/1688, 5610/362/1688, 5610/362/1697, 5610/362/1702, 5611/362/1704, 5611/362/1705, 5611/362/1708, 5611/362/1709, 5614/362/1709, 5618/362/1709, 5618/363/1709, 5622/363/1709, 5623/363/1709, 5627/364/1712 (cut as v0.6.3), 5633/364/1712, 5635/364/1712, 5638/364/1712 5644/364/1712 5646/364/1712 5653/364/1712 5659/364/1712 5664/364/1712 5666/364/1712 5670/364/1712, 5671/364/1712 and **5671/365/1712 on 2026-09-09, the reading v0.6.4 was cut on**, then 5671/366/1733, 5671/366/1737 and **5703/366/1737 on 2026-09-11**, and **5703/366/1742 then 5707/366/1742 on 2026-09-12**, then **5709/366/1747 on 2026-09-13**, the repair round for comms-senior-dev's review of the Claude handoff, then 5709/367/1747 the same day when the doctor learned to find a `herdr-aify env` daemon, and 5716/367/1747 when a spawn's envVars finally reached its worker (v0.6.7), then 5724/367/1747 on the merged review round and 5728/367/1750 once PRs #9 and #10 landed on 2026-09-14, and 5730/367/1750 when the launch answer began naming what a worker must never inherit, then 5775/369/1750 on 2026-09-15 when the held-terminal, screen-state and resident-hook PRs merged, and 5783/371/1750 the same day when one live instance per agent landed, then 5784/371/1750 after two independent reviews of it, and 5787/371/1750 after a fourth, then 5787/373/1750 when an ended agent stopped leaving processes behind and the doctor learned to see an elevated one, then 5789/373/1750 when a reinstall stopped rewriting launchers under running agents -- the dashboard step is dev's, arriving with the work this session reviewed, and the reading was taken because the Herdr integration bumped the aify-wrapper pin and every suite had to be re-run against the new templates -- the python step is not growth but COLLECTION: `scripts/tests` held 29 tests that the documented invocation never collected, so they had been green by never executing since the day they were written, and the invocation now names them -- the bridge step is `env-code-currency`, the doctor row that would have seen the stale daemon this whole version was built on -- all taken while closing an external review's Round 9, and the BRIDGE FELL 395 -> 353 because the environment-bridge tier's residue was deleted, which is the second time a figure here has gone down and the second time it was a deletion -- and there is now a THIRD, 363 -> 362 on 2026-09-08, when a skill-pointer gate written that morning turned out to duplicate `test_skills_name_real_things.py` and was deleted the same day. All three downward movements are deletions, and none was a loss of coverage: the third moved a genuinely new symbol check into the gate that already owned the question and removed the second implementation of the file check, all of them driven by an independent review of this session's own work -- twenty-nine readings in nineteen days, THREE of them on 2026-08-29 alone, which is the argument. **THE HISTORY TRACKS THREE OF THE FIVE SUITES, AND THE TWO IT OMITS ROT FURTHEST.** Each reading above is python/bridge/dashboard; aify-wrapper and aify-env are in the block below and in nobody's ritual, so nothing brings anyone to them. Measured 2026-09-01: the wrapper had drifted 158 -> 162, and aify-env 496 -> 798 (162 -> 177 -> 198 and 798 -> 925 -> 1078 -> 1081 by 2026-09-03, THREE times in one day, so BOTH kept moving while nobody was looking at them), which is SIXTY-ONE PERCENT and the most stale figure this file carried. That is exactly the magnitude the numbers exist to rule out -- an agent whose aify-env run reports 798 against a documented 496 cannot tell fifteen days of growth from a wrong invocation, and 496 is close enough to half that a wrong invocation is the likelier reading. Re-measure all five, not the three with a habit attached. **THE 2026-09-04 READING IS THE FIRST TIME A FIGURE HERE HAS GONE DOWN**: the bridge fell 418 -> 389 when v0.6.2 deleted the environment-bridge cluster and its twenty-nine test files. Every earlier reading recorded growth, so a shrinking number has no precedent to be read against -- and 389 is close enough to 418 that an agent meeting it cold will suspect a wrong invocation before a deletion. It was a deletion. The wrapper (198 -> 202) and aify-env (1081 -> 1095) grew in the same window, which is the ordinary case. Re-measured 2026-09-06 in the same run as the reading above, which is the point: the wrapper is 215 -- 219 on 2026-09-07, on an agreement test that catches a doc naming a function that does not exist, and 302 then 345, 350, 356, 365, 370 and 382 on 2026-09-12 and 391 on 2026-09-13 -- the last step is the resident half of `herdr-aify`, where the two defects that mattered were facts about WINDOWS rather than about the code, found by closing the TUI and watching the launcher fail to leave -- the last step is the TUI the command never attached, which no test could see because every verification run piped its output and a pipe is exactly the condition under which no TUI is attached when the Herdr integration landed with 55 tests of its own and then 43 more -- the second step is the FIVE defects that stood between a green suite and a `herdr-aify` that ran at all, every one of them found by the operator running the command rather than by any test -- and aify-env 1166 -- 1172 within the hour, on an aify-env fix made after this sentence was written -- so BOTH moved again (202 -> 215, 1095 -> 1166 -> 1172 -> 1225 -> 1235 -> 1250 -> 1261 -> 1276 -> 1317 -> 1331 -> 1362 -> 1384 -> 1428 -> 1452 -> 1477 -> 1489 -> 1494 -> 1502 -> 1512 -> 1514 -> 1531 -> 1568 -> 1597 -> 1611 -> 1621 -> 1634 -> 1644 -> 1647 -> 1649 -> 1650 -> 1653 -> 1655 -> 1656 -> 1658 -> 1661 -> 1666 -> 1668 -> 1669 -> 1672 -> 1674 -> 1677 -> 1678 -> 1682 -> 1683 -> 1687 -> 1697 -> 1699 -> 1742 -> 1753 -> 1756 -> 1764 -> 1768 -> 1773, the last forty-nine on the aify-env TUI work and the review rounds that followed it -- the 1687 -> 1697 step is the two INPUT-SAFETY P1s a product-scoped review found on 2026-09-09, a menu Attach landing on the wrong agent and a pane showing a refusal notice while still forwarding keystrokes -- the 1331 -> 1362 step is v0.6.3's console work plus the two blind-input defects an external review found in it) while the three-suite history recorded nothing. Nothing was wrong -- they are this session's own R9 fixes arriving with their tests -- and that is exactly why the drift is invisible: growth with an innocent cause still leaves the written figure wrong for whoever quotes it next. **The 2026-09-01 reading found the LAYOUT TABLE stale for the THIRD time**, and by three numbers at once rather than one: it read 65 modules / 108 test files / 1439 tests against a measured 66 / 109 / 1463. The paragraph below predicted exactly this and named the reason, and it still happened, because whoever comes to write the date arrives here and not there. The last pair is the sharpest version of it: a figure written into this file in the morning was wrong by the evening, without anyone doing anything unusual. Each of those readings was taken because somebody was about to quote the previous one. **Until that last update this file carried TWO different dashboard counts** -- 1097 in the layout table and 1109 here -- which is the failure this paragraph warns about, sitting inside the warning. **It happened a SECOND time and went unnoticed for a day**: the layout table read 1166 while this paragraph read 1254, both written on 2026-08-26. Twice is not bad luck. **AND A THIRD TIME, found 2026-09-05**: the layout table read 1589 while the suite block three paragraphs below read 1586, and BOTH were stale against a real 1632. Three times is a mechanism, not luck -- and the mechanism is that these are two copies of one fact. The next person to touch either should consider deleting one of them rather than updating both again. The layout table is the copy that rots, because whoever updates a count comes here to write the date and never scrolls up. Before that they read 955/219/541 and 1576 while the real
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
that cross-checks `declarationSpan` against four BRIDGE classes — sat red until the next full sweep.
(It was five until 2026-09-05; the fifth was `TerminalProcessManager`, deleted with the tier.)
When one of those cross-checks fails, RE-MEASURE the value independently and record it, rather than
copying the number out of the failure message: that number is whatever the change produced, not what is
true.

### The 1000-line gate fails your change — read this before "fixing" it

No product source file may reach 1000 lines. Two tests enforce it:
`service/tests/test_no_new_oversized_source_file.py` (Python) and
`mcp/stdio/tests/no-new-oversized-source-file.test.js` (JS). Both read ONE policy file,
`oversized-allowlist.json` at the repo root, and walk the repo from its root, pruning `node_modules`,
`tests`, `fixtures`, `__pycache__` and `.git`.

**What is closest to the limit right now** -- ask the tree rather than a table, because a hand-kept
ranking of this was wrong four times and needed its own 19-test gate before it was retired
(2026-09-18):

```bash
git ls-files '*.py' '*.js' '*.mjs' | grep -vE '(^|/)(tests|fixtures|node_modules)/|\.test\.m?js$' \
  | xargs wc -l | sort -rn | sed -n '2,11p'
```

On 2026-09-18 that read `app.js` 996 and `pi-session.js` 993 at the top, so the next edit to either
goes red for a reason unrelated to that edit: slice something out first, taking out a SUBJECT
somebody else also needs, not whichever block is longest. `app.js` actually went over once (1001 on
2026-09-05) and the room came from moving code to the module that owned it. The walk the gates use is
the FILESYSTEM, so untracked files (a `.monitor/` scratch copy, an agent worktree under
`.claude/worktrees`) are counted by them and not by the command above; a gap between the two is
litter, not growth.

**The allowlist is EMPTY, and that is the end state.** Every entry earned its way off during v0.5.4.
Both gates treat an empty list as exempting nothing, key it by PATH (not basename), and fail if a
listed file is deleted or drops below the limit. **Adding your file to it is a REVIEWER DECISION, not a
fix**: appending an entry to make a red test green is the exact move the gate exists to stop.

**Scope: non-test `.py` and `.js`/`.mjs`, repo-wide.** Shell and CSS are deliberately out, and each gate
says so in a test: `install.sh` and `service/new_dashboard/styles.css` are over the limit and are held
by the ratchet in `mcp/stdio/tests/no-unwatched-oversized-file.test.js` instead, each exactly at its
ceiling, so any addition to either has to be paid for elsewhere.

**When relocating, measure the DESTINATION's line count**, not just its dependency direction. The gate
exists because a v0.5.4 move put a 6-line helper into `service/db.py` (995 -> 1006): the source shrank,
a new file went over, and every other check was green because none of them measures where code lands.
