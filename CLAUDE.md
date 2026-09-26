# aify-comms — Claude Code project notes

Inter-agent communication hub: messaging, channels, file sharing, active dispatch, and a dashboard for
Claude Code, Codex, Hermes and other MCP-connected coding agents. This file loads into every Claude
Code session **working on this repo**, so every byte here is paid on every turn: it keeps what changes
a decision and points at the rest. Usage docs for someone installing aify-comms live in
[README.md](README.md). The dated history this file used to carry is in
`docs/history/CLAUDE-history.md`.

## Primary entry points

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — **how it is built**: the tiers and what reloads
  each, the service layering, the message-to-work path, and every layer rule paired with the test that
  fails when it is broken. Read it before your first change.
- [README.md](README.md) — what the service is, setup, day-to-day usage, and the operator playbooks.
- [install.claude.md](install.claude.md) / [install.codex.md](install.codex.md) /
  [install.hermes.md](install.hermes.md) / [install.opencode.md](install.opencode.md) /
  [install.pi.md](install.pi.md) — per-runtime install guides. Pi is deprecated (its tests are off
  unless `AIFY_TEST_DEPRECATED=pi`); managed OpenCode is unsupported and unverified.
- [docs/TARGET_ARCHITECTURE.md](docs/TARGET_ARCHITECTURE.md) — the shape the operator specified:
  container / host / `~/.aify`, the commands on PATH, where each doctor lives. Anything disagreeing
  with it is the thing that is wrong.
- [DECISIONS.md](DECISIONS.md) — why the non-obvious choices were made. Its superseded entries are in
  `docs/history/`.
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md) — what is open now.
- `.claude/skills/aify-comms/SKILL.md` — the agent-facing usage skill; its `references/operations.md`
  holds the status table and the multi-instance matrix.
- `.claude/skills/aify-comms-debug/SKILL.md` — troubleshooting, by symptom.

## Three repos, one product

- **aify-comms** (this repo): the service container (FastAPI, SQLite, dashboard, SSE MCP transport),
  the host-side MCP stdio bridge every agent runs, the installer, and the `aify-comms` verifier.
- **[aify-env](https://github.com/zimdin12/aify-env)**: the host tier. It owns processes and PTYs,
  claims and runs managed spawns, streams consoles, and advertises what the host can run. It is the
  only spawner: a spawn fails loudly when it is down rather than falling back. Starting or restarting
  it is the **operator's** action, because a new aify-env supersedes the running one and reaps its
  managed workers. Ask with `aify-env doctor`.
- **[aify-wrapper](https://github.com/zimdin12/aify-wrapper)**: the launchers (`claude-aify`,
  `codex-aify`, `hermes-aify`) and their templates, consumed here as an npm dependency pinned to a sha
  in `mcp/stdio/package.json`.

The v0.6 separation is recorded in
`docs/superpowers/plans/2026-08-20-three-repo-separation-roadmap.md`.

## Developing on this repo

What you edited decides how it reaches the running system:

| edited | to make it live |
|---|---|
| `service/`, `mcp/` outside `mcp/stdio/`, `config/` | `bash scripts/stamp.sh && docker compose up -d --build`, then `curl http://localhost:8800/health` |
| `mcp/stdio/` (the bridge) | re-run `install.sh --client <runtime>` (it copies the bridge into `~/.aify-comms`), then relaunch each `*-aify` agent |
| `.claude/skills/` | re-run `install.sh`: it copies the skill trees out to `~/.claude/skills` |
| `install.sh` | re-run it |
| docs | nothing |

- The Dockerfile copies all of `mcp/` into the image, so a new file beside `mcp/sse_server.py` is
  container runtime. `service/tests/test_service_runtime_boundary.py` keeps host-side bridge code out.
- The bridge runs from the native copy under `~/.aify-comms` (override with `AIFY_HOME`), not from the
  checkout: a 9p/WSL bind mount loaded it in about 5 s, past hermes' 0.75 s MCP-discovery window. So a
  bridge edit is live only after `install.sh` **and** a relaunch.
- Build with `scripts/stamp.sh` first. An unstamped image reports `0.0.0-dev` and the doctor's
  `service` row reads `unknown-build`.
- Codex threads on Windows need a forward-slash `cwd`, or a later `thread/resume` fails.
- Re-register refreshes all agent state except `description` (see DECISIONS.md).
- Keep `.claude/skills/aify-comms*/` and `.agents/skills/aify-comms*/` byte-identical
  (`test_skill_mirror_parity.py`).
- The container is `aify-comms-service` on the `aify-comms-network` network. `.env` is gitignored and
  holds secrets; never commit it.
- **The service must stay single-worker.** The live-status cache is a process-global in-memory dict
  (`_LIVE_STATE_CACHE`, owned by `service/reconcilers/status_cache.py`). A second uvicorn worker would
  split it. Debug status with `comms_agent_info` or the dashboard.

## Versioning

The release version lives in the repo-root `VERSION` file. `bash scripts/bump-version.sh X.Y.Z`
writes it into every file that must agree (`mcp/stdio/version.js`, `mcp/stdio/package.json`,
`mcp/stdio/package-lock.json`, `.claude-plugin/plugin.json`), and `test_version_single_source.py`,
`mcp/stdio/tests/version-consistency.test.js` and (for the lockfile) `test_repository_build_contract.py`
fail on any disagreement or a new hardcoded version.
`scripts/stamp.sh` bakes the version and git sha into `service/_build_stamp.json`, which
`service/config.py` reads.

To cut a release: bump, run all suites, stamp, rebuild, re-run `install.sh` for each client if
`mcp/stdio/` changed, confirm `aify-comms doctor` is green, then tag.

Leave `SERVICE_VERSION` out of `.env`: an environment value overrides the stamp. `config/service.json`
cannot set the six stamp-owned fields (`version`, `build_sha`, `build_short`, `build_branch`,
`built_at`, `build_dirty`); they are observations of a build, and `test_service_json_cannot_override_the_build_stamp.py`
holds that.

## Repo layout (what matters)

| Path | What |
|------|------|
| `service/` | The FastAPI service. Routers under `service/routers/` hold HTTP only and call into `service/api_core/` (behaviour), `service/reconcilers/` (one module per sweep or reconciliation), and top-level leaves such as `dispatch_claim.py`, `status_engine.py`, `terminal_write_queue.py`, `clock.py` and `env_status.py`. Put new behaviour in the module that owns its subject, as a pure function where it can be one. The layer rules and their tests are in docs/ARCHITECTURE.md. |
| `service/routers/api_v2.py` | Composition only: the `include_router` calls. There is no compatibility re-export, so a stale import of a moved helper fails loudly. |
| `service/new_dashboard/` | Dashboard Next. Behaviour lives in `*.mjs` modules, each with a `*.test.mjs` that calls it in Node; `app.js` is the render orchestrator. |
| `service/new_dashboard/fixtures/messenger-browser.mjs` | The only test of the real message sanitizer: DOMPurify needs a DOM, so under `node --test` every `richMessageHtml` assertion runs the escape fallback. It runs in a browser by hand; `fixtures/README.md` has the procedure. |
| `mcp/stdio/` | The host-side MCP stdio bridge (`server.js`, `claude-channel.js`, the runtime adapters, `notify-check.js`) and the doctor. |
| `mcp/stdio/*-predicates.js`, `mcp/stdio/*-check.mjs` | Pure, unit-tested logic pulled out of the bridge and the doctor, so it fails a test instead of production. A `*-check.mjs` holds a whole doctor check, call site included, because importing `doctor.js` runs the doctor. Which files make up the doctor is derived (`mcp/stdio/tests/doctor-sources.mjs`, `service/tests/doctor_sources.py`), never listed. |
| `mcp/stdio/service-registry.mjs`, `register-service-cli.mjs` | Writes this service's entry into the shared registry `~/.aify/services.json`, which is how aify-env and the launchers learn the service exists. It owns only its own key and refuses an unreadable or wrong-version registry rather than rewriting it. |
| `mcp/stdio/node_modules/aify-wrapper/wrappers/` | The launcher templates `install.sh` renders. To move the pin, raise the sha in `package.json` and `package-lock.json`, **delete `node_modules/aify-wrapper`**, run `npm install`, then grep the installed templates for the change: npm trusts a tree that matches the lock and will otherwise keep the old code while reporting success. |
| `mcp/sse_server.py` | The SSE MCP transport, inside the container. |
| `install.sh` | Client installer: `--client <runtime>` (claude, codex or hermes), `--with-hook`, `--with-api-key`. `--emit-wrappers <dir>` renders a launcher and exits before npm or any config change, and `--prebuild-dry-run` exercises the hermes web_dist branch without npm; both exist so tests can run the real installer on a machine with a live fleet. |
| `scripts/installed-endpoint.sh`, `hook-installed.sh`, `api-key.sh` | Read back what the host already chose (endpoint, hook, key) so an update keeps it. `api-key.sh` reuses an existing key rather than rotating, because a new key 401s every installed bridge. |
| `examples/team-setup/` | An example team definition. |

## Verify a change took effect: `aify-comms doctor`

Every deploy path here can fail silently: a container serving the previous build, a native bridge
copy that is current while every running agent still runs what it loaded at boot, an agent registered
without `AIFY_AGENT_ID`. Report success from the doctor, not from the absence of an error.

```bash
aify-comms doctor            # human-readable report
aify-comms doctor --json     # {ok, passed, failed, skipped, repo, service_url, checks:[{id, ok, code, detail, fix?, skipped?}]}
aify-comms doctor --strict   # exit 1 if any check failed
```

`aify-comms` is a verifier and starts nothing: `doctor`, `--check`, `--version` and `--help`, and
anything else exits 2 and names aify-env. `aify-doctor` is the same script under its older name. A
check that could not run reports `skipped` (`ok: false, skipped: true` in `--json`, counted apart, and
not a `--strict` failure); one that ran and found no evidence reports an `unknown-*` code, which fails.
Neither reads ok.

| check | catches |
|---|---|
| `service` | the container serves a build other than repo HEAD; a healthy `/health` does not say which code |
| `bridge-installed` | a commit since the installed marker touched `mcp/stdio/` and `install.sh` was not re-run (docs- or service-only commits read clean) |
| `bridge-running` | a running bridge started before the last install, so it runs old code; names the agents to relaunch. Linux only (reads `/proc`) |
| `agent-identity` | a registered agent whose process has no `AIFY_AGENT_ID`, so its status cannot be proven. Linux only |
| `bridge-current` | a live bridge (`GET /bridges`) whose self-reported `bridgeBuild` is behind; `unknown-all` when no live bridge reports one |
| `skills-installed` | a skill edited in the checkout that `install.sh` has not copied out yet |
| `spawn-delegation` | the aify-env serving this host is not answering, so spawns fail with no cause attached |
| `tier-version` | the aify-env serving this host is older than `MINIMUM_AIFY_ENV_VERSION` (`tier-version-check.mjs`) |
| `spawn-queue` | a spawn request a host claimed and never started, aged against the service's `SPAWN_ORPHAN_GRACE_SECONDS`; reports, never acts |
| `managed-orphans` | managed delivery loops (`hermes-managed-host.js run <agent>`) running for an agent no live host owns; reports, never kills |
| `gateway-orphans` | hermes gateway hosts in aify-comms' port range with no worker behind them, including unclaimed ports and elevated processes whose command line cannot be read |
| `env-code-currency` | aify-env running code that differs from the code on its disk (`build` against `codeOnDisk` on its `/health`) |
| `env-processes` | a process aify-env runs with no live terminal in the control plane, or the reverse, on this host |
| `session-handles` | one native session handle claimed by more than one agent; reports, never refuses |
| `api-exposure` | an unauthenticated fleet listing that also returns live gateway tokens |
| `external-keys` | `EXTERNAL_KEYS` that restrict nothing: no `API_KEY` is set, or the entry is malformed |
| `client-api-key` | this host cannot authenticate against the service it points at, probed with the key it actually resolves |
| `context-window` | a running agent whose conversation has outgrown its model, read from the runtime's own `used/limit` footer (warns at 0.9) |
| `env-bridge` | no environment can claim a spawn now (it asks what `/spawn` asks: a fresh `metadata.bridgeLastSeen`), so dashboard-managed spawns cannot run |
| `claude-login` | the refresh window of the one Claude OAuth grant every claude-code agent on this host shares; reads timestamps, never a token |
| `usage-openai` | the ChatGPT quota token works, proven by calling the API |

`bridge-running` and `agent-identity` skip on Windows. Wrapper and runtime checks belong to
`aify-wrapper-check`, and terminal capability to `aify-env doctor` (docs/AIFY_ENV_BOUNDARY.md).

## Testing a change

Run every suite for every change: no suite stays inside its own tree (bridge tests read `install.sh`
and the service, python tests read `mcp/stdio`, dashboard tests read both), so a targeted run is green
because it did not look. `node --check` only parses. The counts below are a snapshot; the run is the
authority, and a very different number means a wrong invocation before it means anything else.

```bash
python -m pytest service/tests scripts/tests -q -n 8 --dist loadfile # 4831 tests, 25 skipped
cd mcp/stdio && node tests/run-all.mjs                 # 365 suites
cd service/new_dashboard && node --test *.test.mjs     # 1835 tests
cd ~/projects/aify-wrapper && npm test                 # 563 tests, 66 skipped
cd ~/projects/aify-env && npm test                     # 2094 tests, 4 skipped
```

- **Python:** `-n 8 --dist loadfile` is the invocation (about 3 minutes; serial is over 20).
  `loadfile` keeps a file's tests on one worker, so class state and the process-global status cache
  behave as they do serially. It needs fastapi 0.138 or newer in that interpreter; match the
  container. Across workers, a `subTest` label must be serialisable, and two files touching one
  external resource (`~/.claude.json`, the service registry, a fixed port) need pinning, not a retry.
- **Socket exhaustion:** on Windows every event loop opens a loopback TCP socketpair.
  `service/tests/conftest.py` closes them abortively (SO_LINGER 0). If a run fails a moving set of
  tests with `WinError 10055`, that fix has regressed: read every failure, run the named files alone,
  and compare against a stashed tree before calling anything flaky.
- **Bridge:** `run-all.mjs` runs every `.test.js` in `tests/`, `tests/adapters` and
  `tests/controllers` and judges each FILE by exit status, so its figure is a file count. It names
  skipped files under "skipped, so NOT verified here" and never counts them as passed.
- **aify-wrapper and aify-env:** use `npm test`, not a bare `node --test`. It runs under one temp root
  and carries the test timeout.
- **Cross-repo tests** read the sibling checkouts (`AIFY_ENV_REPO` / `AIFY_WRAPPER_REPO`, else beside
  this checkout, else `~/projects`; `mcp/stdio/tests/_sibling-checkout.mjs`). They read source and
  never start a daemon. An aify-env edit can redden this repo's suites (for example
  `the-credential-ref-we-write-is-one-aify-env-resolves.test.js` and
  `test_the_env_plugin_addresses_routes_this_service_serves.py`), so run both sides after a change to
  the seam.
- When a cross-check fails, measure the value independently rather than copying the number out of
  the failure message.

### The 1000-line gate

No product source file (`.py`, `.js`, `.mjs`, repo-wide, tests and fixtures excluded) may reach 1000
lines: `service/tests/test_no_new_oversized_source_file.py`, with its policy in
`oversized-allowlist.json`. The allowlist is empty. Adding a file to it is a reviewer's decision, never
a fix. `install.sh` and `service/new_dashboard/styles.css` are held at measured ceilings by
`mcp/stdio/tests/no-unwatched-oversized-file.test.js`, so any addition there is paid for elsewhere.

Ask the tree what is closest to the limit:

```bash
git ls-files '*.py' '*.js' '*.mjs' | grep -vE '(^|/)(tests|fixtures|node_modules)/|\.test\.m?js$' \
  | xargs wc -l | sort -rn | sed -n '2,11p'
```

When a file is near the limit, take out a subject somebody else also needs, not the longest block.
When you move code, measure the destination's line count too: a move once pushed `service/db.py` over
the limit while every other check stayed green.

## Writing a skill

The `SKILL.md` files load into every agent's context every session, so a byte there is paid by every
agent on every turn.

- **Size is a ratchet.** `mcp/stdio/tests/skill-size-ratchet.test.js` holds every skill file at its
  measured size: a file that grows fails, and a file that shrinks must lower its ceiling in the same
  commit. An always-loaded `SKILL.md` also has a hard limit of 16,000 characters (`ALWAYS_LOADED_LIMIT`). Raising a ceiling is a decision
  argued in the commit; pay for new bytes elsewhere in the file.
- **Steps in the skill, reference behind a pointer.** Inline what every run needs; put what only
  some branches reach in `references/`, and word the pointer so it says what the reader will find
  there.
- **Instruct positively.** A prohibition makes the banned behaviour more available. Keep a ban only
  for a hard guardrail you cannot phrase positively, such as "never start aify-env to find out whether
  one is running", and pair it with what to do instead ("ask `aify-env doctor`").
- **A rule carries its measurement**, like "1,248 self-wakes at a 27 s median". A rule with a number
  survives the next pruning; one with only rhetoric does not.
- **A sentence earns its place by changing a decision.** If it would not change what the agent does,
  delete it.
- **Both mirrors, byte-identical**, gated by `test_skill_mirror_parity.py`.

Prior art: [mattpocock/skills](https://github.com/mattpocock/skills) `writing-for-agents` and
[pstack-claude](https://github.com/michael-denyer/pstack-claude) `principle-minimize-reader-load`.
