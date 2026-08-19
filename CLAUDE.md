# aify-comms — Claude Code project notes

Inter-agent communication hub: messaging, channels, file sharing, active dispatch, and a dashboard for Claude Code, Codex, OpenCode, and other MCP-connected coding agents. This file is loaded by Claude Code when working **on this repo itself**; usage docs for someone installing aify-comms live in [README.md](README.md).

## Primary entry points

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — **how it is built.** The three processes and what
  reloads each, the service layering and why it is flat, the message-to-work path, and every layer
  rule paired with the test that fails when it is broken. Read before your first change; the rules
  below assume it.
- [README.md](README.md) — what the service is, setup, day-to-day usage.
- [install.claude.md](install.claude.md) / [install.codex.md](install.codex.md) / [install.hermes.md](install.hermes.md) / [install.opencode.md](install.opencode.md) / [install.pi.md](install.pi.md) — per-runtime install guides (wrappers, hooks, verification).
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
| `service/control_plane.py` | **v0.5.3.** The live control plane: ~140 helpers, constants and the two queue classes shared across status, dispatch, terminals, spawn and console. This was `service/routers/api_v2.py` — 20,545 lines at its peak — until the route domains moved out and it was left declaring ZERO routes. It is NOT a router: `service/routers/api_v2.py` is now nothing but the 15 `include_router` calls, and there is deliberately no compatibility re-export, so a stale `from service.routers.api_v2 import <helper>` fails loudly instead of resolving. **v0.5.4 took it to 879 lines and it is OFF `oversized-allowlist.json`** — the "still far too big, a v0.6 question" note that stood here is retired as done. It is now an ordinary file under the 1000-line gate, so the risk it carries is REGROWTH: it is still the shared home for status, dispatch, terminal, spawn and console helpers, and the gate will not warn until it has already crossed back over. |
| `service/reconcilers/` | **v0.5.** The reconcilers extracted out of what is now `service/control_plane.py` — one module per responsibility (`status_cache`, `spawn_lifecycle`, `sessions`, `terminals`, `terminal_runs`, `terminal_consistency`, `dispatch_queue`, `dispatch_lifecycle`, `managed_workers`, `console_binding`). Leaf modules: they may import `service/clock.py`, `service/env_status.py` and each other, but must NOT import the control plane at all. **The borrow debt is PAID: reconciler imports of the control plane are ZERO**, measured by `test_leaves_do_not_import_the_carrier.py`, whose ceiling is now 0 and which fails if that ceiling is ever left slack above the real count. The function-scope "borrow shim" this table used to describe is retired — do not reintroduce one; a reconciler needing a control-plane helper means the helper is in the wrong layer. Two function-scope imports remain in `reconcilers/`, but they read `api_core` leaves, not the carrier, and each documents the cycle that forces it. |
| `service/clock.py`, `service/env_status.py` | Leaf helpers with no service dependencies, created so the reconcilers could stop importing the router for a timestamp or an environment status. |
| `service/new_dashboard/*.mjs` | Dashboard modules with their own `*.test.mjs` — 61 modules, 83 test files, 1097 tests (`api-client`, `shared-files`, `message-transport`, `state`, `session-rail`, `terminal-input`, …). **`app.js` is 987 lines (was 5,081) and is UNDER the 1000-line gate** — the "3,612 lines / only reachable by source-regex tests" note that stood here is retired twice over: the extracted modules import and run in Node, so they are tested by CALLING them, and the boot wiring, the delegated click dispatcher and the per-page actions have all left. Still put new behaviour in a module here rather than in `app.js`: what remains is the render orchestrator, and it is 13 lines from going red. |
| `service/new_dashboard/extraction-proof.mjs` + `.test.mjs` | **The gate that makes app.js safe to slice.** It RECONSTRUCTS the pre-extraction app.js from the current file plus every extracted module and requires byte-identity with a tracked pristine fixture — so a slice cannot quietly change anything outside the spans it declared. Moving a declaration out of `app.js` means appending an entry to its `EXTRACTIONS` plan **in the same change**: `importLine` (and `importWas` only if the slice EDITED an existing import — a module created by an earlier extraction has none in the fixture, so its line is deleted instead), plus one item per declaration with `at` = the 0-indexed line in the FIXTURE, not the live file. `marker` may be several lines and each is verified verbatim, which is what lets a slice leave a seeding call behind. |
| `mcp/stdio/` | Host-side MCP bridges (`server.js`, `claude-channel.js`, `runtimes.js`, `runtime-markers.js`, `notify-check.js`). Restart client wrapper after changes. |
| `mcp/stdio/*-predicates.js`, `register-identity.js` | PURE, unit-tested helpers extracted out of the bridges so their logic can fail a test instead of only failing in production. `doctor-predicates.js` (env liveness) and `register-identity.js` (resident launch-identity warning) are the pattern to follow — `doctor.js` was untestable until its predicates moved out, and the first thing the new test caught was a real bug. |
| `mcp/sse_server.py` | SSE MCP transport (runs inside the container). Rebuild container after changes. |
| `.claude/skills/aify-comms/` | Usage skill — tool reference, workflow, status table, multi-instance matrix. |
| `.claude/skills/aify-comms-debug/` | Troubleshooting skill — known issues and fixes. |
| `.agents/skills/aify-comms*/` | Mirrors of the two skills for Codex agents. Keep in sync. |
| `wrappers/*.sh.in` | **v0.6 Phase 2.** The launcher bodies for claude, codex and pi, as real files. `install.sh` renders them with `render_wrapper_template`, substituting `@@TOKEN@@` placeholders and stripping `#|` template-only comments. They lived in unquoted heredocs where every runtime `$` had to be written `\$`; as files they are diffable, reviewable and `bash -n`-able. Each move was proven BYTE-IDENTICAL before any behaviour changed. Hermes is sequenced last and is still a heredoc. |
| `install.sh` | Client installer. Targets Claude, Codex, or Hermes via `--client` (OpenCode/Pi installs are intentionally disabled). `--emit-wrappers <dir>` renders a wrapper and EXITS before npm, MCP registration or any env mutation — which is what lets the suite render and run the real launchers on a machine with a live fleet. |
| `examples/team-setup/` | Example team definition (manager, coder, tester, etc.) showing how to register a multi-role team. |

## Development notes

- **`install.sh` copies the bridge runtime into a native dotfolder.** The installer copies `mcp/stdio` + its `node_modules` into `~/.aify-comms/` (override with `AIFY_HOME`) and points every wrapper + MCP config at that native copy, not at the repo checkout. Reason: the repo often sits on a slow 9p/WSL2 bind-mount where the bridge takes ~5s to load — that blows hermes' hardcoded 0.75s MCP-discovery window; the native copy loads in ~0.3s. Re-running `install.sh` refreshes the copy, so **security fixes flow on reinstall** (no longer automatic). Consequence: editing files under `mcp/stdio/` now requires re-running `install.sh` (to re-copy) **and** restarting the client wrapper — not just a wrapper restart.
- **Forward-slash `cwd` on Windows** for Codex agents. The bridge auto-normalizes, but any new Codex thread must be created with a forward-slash cwd or it'll fail `thread/resume` later.
- **Re-register is a full state refresh** for everything except `description`. Tests and dev workflows should assume session state is wiped on re-register — see DECISIONS.md.
- **Skill files live in two places:** `.claude/skills/aify-comms*/` and `.agents/skills/aify-comms*/`. Keep them in sync when editing.
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

**Never run a bare `aify-comms` to check that something works.** It is not a client and not a smoke
test — it starts the **environment bridge**, which supersedes the one already serving this
environment; the older bridge exits and its managed workers are reaped. That is how the whole
managed fleet went down on 2026-08-11, from a four-second run meant only to confirm the launcher
still started. Use `aify-comms --check` (validates node, the script path and that it parses;
registers nothing) or `aify-comms doctor`.

`aify-doctor` is the same script under an older name and still works. It shipped first and the
operator's objection was fair: one product should not need two command names remembered, so the
verifier now lives under the name that already exists. The standalone binary stays because agent
habits and older docs point at it.

It proves each claim against the running system rather than checking that a file exists:

| check | catches |
|---|---|
| `service` | container serving a build ≠ repo HEAD (a healthy `/health` says nothing about *which* code) |
| `bridge-installed` | commits since the installed marker that actually **touched `mcp/stdio/`** — i.e. you edited the bridge and never re-ran `install.sh`. Being behind by docs- or service-only commits is reported as CLEAN (with the count), so this check does not cry wolf on every commit |
| `bridge-terminal` | the installed `node-pty` native module does not load → terminal-backed runtimes silently cannot start |
| `bridge-running` | **running** bridges started BEFORE the last install → still executing the old code. Names the agents that must restart. **Linux-only** — it reads `/proc`, so on Windows it SKIPS and nothing verifies this. |
| `agent-identity` | a REGISTERED agent whose process has no `AIFY_AGENT_ID` (status structurally dead). An unregistered plain session is legitimately id-less and is not flagged. **Linux-only**, same caveat. |
| `bridge-current` | a **live** bridge whose self-reported `bridgeBuild` ≠ repo HEAD → it is *running* old code even though the files on disk are current. Platform-independent (the bridge reports its build on registration), which is what makes it the answer to the Windows gap below. Says RESTART, never reinstall. Fails as `unknown-all` when **no** live bridge reports a build: that is no evidence, and a check that verified nothing must not read as a pass — it was green-by-default until `a2f9e42`, the same false green as `env-bridge` below. |
| `env-bridge` | no environment bridge is actually **ONLINE** → dashboard-managed spawns cannot run. Keys on each row's server-derived `status`, and names the registered-but-dead ones with their `lastSeen`. (Until `756f3a5` it counted *registered* rows and reported "2 connected" with zero bridges alive — the exact false green this tool exists to prevent.) |
| `wrappers`, `runtimes` | the wrappers are on PATH and the runtime CLIs exist |
| `wrapper-current` | an installed launcher generated by an OLDER install than the checkout. It **reads** `HARNESS_WRAPPER_VERSION` out of the wrapper file rather than running `--check`: a pre-contract wrapper does not know that flag and would forward it to the runtime, so asking would LAUNCH CLAUDE. A missing marker reads as stale, not as green. Says REINSTALL, never restart — the opposite of `bridge-current`. This exists because publishing the wrappers separately ends install.sh's same-build guarantee by construction |
| `usage-openai` | the ChatGPT quota token works — by calling the API, since an expired token passes a file check |

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
python -m pytest service/tests -q                      # 4165 tests (+8268 subtests)
cd mcp/stdio && node tests/run-all.mjs                 # 332 suites
cd service/new_dashboard && node --test *.test.mjs     # 1109 tests
```

Those counts are a **measured snapshot** (2026-08-17), not a target: they are there so a wrong invocation is
obvious (a `node --test` that reports 200 did not discover the suite). They rot with every slice — the run is
the authority, never the number written here. They were 3991/318/1097 on 2026-08-17 and are the figures above on 2026-08-19; before that they read 955/219/541 and 1576 while the real
figures were already these, which is the whole reason for this paragraph.

Editing `service/new_dashboard/app.js` also means updating `extraction-proof.test.mjs` in the SAME change
(see the layout table) — it fails loudly rather than silently if a declaration moves without being
declared.

Full end-to-end test is a two-session live round-trip. Register two agents, use `comms_send` from one to the other, verify the target wakes or receives a steer/queued turn according to capability, and verify the response is threaded back in chat.

### The 1000-line gate fails your change — read this before "fixing" it

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
`install.sh` (~3,570 lines, down from 4,371 as the wrapper bodies moved to `wrappers/`) and `service/new_dashboard/styles.css` (1,844) are non-test source over the
limit and bringing them in is an open reviewer question, not a widening to do quietly.

The failure this gate was built from: a v0.5.4 relocation moved a 6-line helper into `service/db.py` — the
correct subject owner — taking it 995 → 1006. `control_plane.py` shrank and a NEW file went over. The
undefined-name sweep, the stale-owner census, `create_app()` and all three suites were green, because none
of them measures the DESTINATION of a move. **When relocating, measure the destination's line count, not
just its dependency direction and transaction ownership.**

The allowlist is keyed by PATH, not basename — an earlier version keyed by basename and would have exempted
any file named `app.js` anywhere. A pure predicate in each gate pins that, and both fail if a listed file is
deleted or drops below the limit, so the list shrinks honestly instead of rotting into unchecked names.
