# E — ponytail lens: what can be deleted, collapsed, or replaced

Reader E, 2026-09-25, read-only pass over aify-comms at `b7fde7c8` plus the sibling checkouts
`~/projects/aify-env` and `~/projects/aify-wrapper` (read as source; nothing was started).

## How the searches were controlled

Every "nothing uses X" below comes from a search that was also shown to find something that exists,
and to return nothing for something that does not.

- **Python symbols.** An AST walk over every top-level def/class in `service/` (non-test) against a
  token index of every tracked file plus aify-env. Positive control: `derive` found in 125 files.
  Result: **top-level Python is clean.** Only test hooks came back (`*_for_tests`) plus
  `terminal_tail_buffer.held_count` (3 lines). The repo's dead-import gates are doing their job, so
  the Python wins below are at subsystem level, not symbol level.
- **JS exports.** Every `export` in `mcp/stdio` and `service/new_dashboard` against a token index of
  all non-test product files, the aify-env and aify-wrapper sources and their `.sh.in` templates.
  Positive control: `ws` found as `import WebSocket from "ws"` in two modules. Negative control: a
  made-up name (`NONEXISTENT_ZZ`) was not found.
- **Routes.** Each of the 133 entries in `service/tests/data/route_metadata_inventory.txt` searched by
  its last static path segment across the dashboard, the bridge, `service/sse`, scripts, the aify-env
  plugin and the aify-wrapper templates. Positive control: `/last-read` found in
  `agent-reporting-tools.mjs:90`. The first run missed `pi-session-state` because the wrapper filter
  left out `.sh.in`; re-run with the filter fixed, it found `wrappers/pi-aify.sh.in:358`, which is
  why that route is **not** listed as dead. Hits inside comments were checked by hand.
- **Line counts.** Measured with the repo's own `declarationSpan` (from `extraction-proof.mjs`) for JS
  and `ast` for Python. `NONEXISTENT_ZZ` returned NOT FOUND, so a miss can be told apart from a zero.
- **What no search here can see:** other machines calling the HTTP API or the SSE MCP endpoint;
  operators running scripts or `curl` by hand; hand-set env vars; older aify-comms bridges still
  running elsewhere. Where a finding depends on one of those, its **risk** line says so.

## Totals

| class | lines removable (product + tests + fixtures) | findings |
|---|---|---|
| **SAFE**: no runtime path, no external consumer possible | **about 25,500** | E1, E3, E4, E5, E8, E9, E10, E16, E18, E20, E21, E22, E23 |
| **RISKY**: an external consumer is possible, or it needs an operator decision | **about 23,000** (docs not counted) | E2, E6, E7, E11, E12, E13, E14, E15, E17, E19 |
| informational only | 36k comment lines, 59.5k doc lines | E24, E25, E26 |

---

### E1. Retire the two JS reconstruction proofs, their frozen fixtures, and the markers they pin
- where: `service/new_dashboard/extraction-proof.test.mjs:225-5085` (the `EXTRACTIONS` plan) and its reconstruction tests; `service/new_dashboard/fixtures/app.before-settings-fields.js` (5,081); `extraction-proof.mjs:170-453,544-578` (`unwrapBody`, `undoEdits`, `reconstruct`, `duplicateEntryKeys`); `mcp/stdio/tests/hermes-gateway-extraction.test.js:52-670`; `mcp/stdio/fixtures/hermes-managed-host.before-gateway.js` (3,016); 197 `// X moved to ./y.mjs in v0.5.4.` markers in `service/new_dashboard/app.js` and 57 in `mcp/stdio/hermes-managed-host.js`.
- what goes: about **15,000 lines**: dashboard about 11,400 (fixture 5,081, plan 4,861, reconstruction tests about 800, prover about 320, app.js markers 197) and hermes about 3,670 (fixture 3,016, plan and proof about 600, markers 57). **Keep** `declarationSpan`, `functionSpan`, `BROWSER_GLOBALS` and `moduleScopeBrowserRefs`: four bridge tests and one dashboard test import `declarationSpan`, and the module-scope-browser-globals purity check is a real ongoing property. Side effects: `app.js` drops from 996 to about 800 lines, so it is no longer four lines from the 1000-line gate, and every future app.js edit stops paying the "append to `EXTRACTIONS` in the same change" tax that CLAUDE.md describes.
- evidence: the prover's header says it proves every past extraction "was pure". The v0.5.x series it guarded closed 2026-08-17. The **Python analogue was retired on the operator's word on 2026-09-18** (`docs/superpowers/plans/2026-09-17-test-suite-audit.md:80-90`: 34 files, about 6,600 lines, `extract_method.py`). Its reasons apply here word for word: it says nothing about correctness, and every behaviour change to a moved function has to be declared in it. `git grep "moved to"` over the test trees finds only prose comments, so nothing but these two proofs reads the markers. `git grep extraction-proof` shows the only importers take `declarationSpan` or the browser-globals helpers.
- risk: none at runtime (test code and comments only). It loses the byte-inertness guarantee for extractions that have already landed.
- severity: P2 (the plan is a per-edit tax, and it is what keeps app.js at the gate)
- effort: M

### E2. The Docker sub-container orchestration subsystem, template residue that is never constructed
- where: `service/containers/` (manager 480, proxy 127, gpu 85, models 82), `service/routers/containers.py` (168: `/api/v1/containers*`, `/api/v1/gpu`, the `/route/{name}/{path}` proxy on 5 methods), `service/sse/container_tools.py` (111), container glue in `mcp/sse_server.py:36-97`, `service/main.py:417-437`, `service/routers/health.py:226-270`, `integrations/open-webui/` (98, whose tools only call `/api/v1/containers` and `/api/v1/gpu`), `integrations/claude-code/SKILL.md` (74, self-described as "legacy-aify-service ... retained for reference"), `docker>=7.0.0` in `service/requirements.txt`, the `docker` group in `Dockerfile:20-23`, and `/var/run/docker.sock` in `docker-compose.dev.yml`.
- what goes: about **1,500 product lines** plus the ten dedicated test files (`test_container_*`, `test_gpu_allocator`, `test_sse_container_tools`: about **2,300 lines**), one pip dependency, and the docker group in the image.
- evidence: the manager is built only `if config_data.get("containers", {}).get("definitions")` (`main.py:428`), and `config/service.example.json` ships `"definitions": {}` with the comment "Legacy optional container-router definitions. Leave empty for normal aify-comms". The route scan found **no caller** of any container route outside `integrations/open-webui/tool.py`. `docs/V0.2_ROADMAP.md:89-96,978-993` measured the live service on 2026-07-31: 503 "Container manager not initialized", and zero log lines. That file raised "**Operator decision: is `service/containers/` wanted-but-dormant, or dead weight?**", and the question is still open.
- risk: an operator on another machine with a `containers.definitions` block in their `service.json`, and anyone using the Open WebUI tool. Needs the operator's call. That call has been pending since 2026-07-31.
- severity: P2
- effort: M

### E3. One-off measurement and refactor scripts in `scripts/`
- where: `measure-console-projection.py` 447, `measure-ws-hop.py` 437, `measure-ws-hop-browser.py` 413, `measure-live-frame-gaps.mjs` 326, `measure-coalescing-severity.py` 251, `measure-stats-queries.py` 238, `measure-live-console-fetch.py` 228, `measure-console-latency.mjs` 202 plus `console-latency-pairing.mjs` 110 (and its test, 175), `measure-store-hop.py` 157, `check-deployed-console-transport.py` 369, `browser-paint/` 813, `render-a-real-console-through-the-emulator.mjs` 125, `deleted-import-census.py` 467, `dry_run_rewrite.py` 100, `stale_owner_census.py` 160, `js_free_functions.py` 131, `constant_readership.py` 166, `undefined_name_sweep.py` 143, `verify_crlf_repair.py` 210, `repair_shared_crlf.py` 130, `acceptance-ledger.py` 274, `comms_baseline.py` 210, `normalize_machine_id_casing.py` 128.
- what goes: about **6,300 lines**.
- evidence: a search for each script's stem across every tracked file returned **0 references** for `add-service`, `bump-version`, `dry_run_rewrite`, `measure-console-projection`, `measure-store-hop` and `stale_owner_census`. The rest are named only in dated plan or ledger docs, in comments, or by each other. Positive control: `stamp.sh` and `installed-endpoint.sh` were found in `install.sh`, `redeploy.sh` and tests. Only one test imports a script (`the-latency-collector-refuses-what-it-cannot-pair.test.js` imports `console-latency-pairing.mjs`), and that test goes with it. `comment_spans.py` is **kept**, because `test_no_source_bakes_in_a_host_path.py:31` imports it. Each script's docstring names the release or incident it was written for: v0.5.4 accessor migration, v0.6.3 console latency, 2026-08 CRLF repair.
- risk: the prose-path gate (`test_prose_paths_resolve.py`) goes red on comments that cite a deleted script: `api_core/agent_sessions.py:14`, `api_core/reply_contract.py:29` and `test_leaves_do_not_import_the_carrier.py:18` cite `constant_readership.py`. `test_no_new_oversized_source_file.py:205` uses `undefined_name_sweep.py` as a positive control, so it needs a different file. `bump-version.sh` is a working release helper that CLAUDE.md's release recipe never mentions. Either point the recipe at it or delete it; keeping both is two sources for one procedure. `normalize_machine_id_casing.py` is a one-time DB fix: keep it only if an un-normalized database can still exist.
- severity: P3
- effort: S-M

### E4. `node-pty` is a dependency of aify-comms, and the installer refuses to install without it, yet no aify-comms code loads it
- where: `mcp/stdio/package.json:21`, `install.sh:2714-2723` (rebuilds node-pty, then `exit 1` if it still fails to load), `mcp/stdio/tests/install-node-pty-recovery.test.js:11-13`.
- what goes: 1 dependency (**63 MB** on disk in `node_modules/node-pty`, and a native build), about 10 installer lines, and one installer test. It also removes an install failure mode.
- evidence: `git grep -E "import\(.*pty|require\(.*pty|from ['\"].*pty" -- mcp/stdio ':!node_modules'` finds nothing. The three `node-pty` hits in bridge source are comments (`hermes-managed-host.js:13`, `reap-managed-claude.js:7`, `virtual-terminals.mjs:194`). aify-wrapper: 0 hits. Positive control: the same search finds `require.resolve("node-pty")` in aify-env `lib/runner.mjs:60`, and aify-env declares its own `node-pty` in its `package.json`. PTYs moved to aify-env in v0.6. Reader D found the doc side of this (D-docs-skills.md:263).
- risk: low. A script run by hand from `~/.aify-comms` that expects node-pty. None was found.
- severity: P2 (a failed native build aborts an install for a module nothing uses)
- effort: S

### E5. The service image installs Node 20 and the Claude Code CLI; the service spawns no process
- where: `Dockerfile:11-17` (nodesource plus `npm install -g @anthropic-ai/claude-code`); the compose comment "enables trigger/dispatch inside container" (`docker-compose.yml:29-30`).
- what goes: about 8 Dockerfile lines, plus image size and build time for both containers (they share the image).
- evidence: `git grep -E "create_subprocess_exec|subprocess\.(run|Popen|check_output)|os\.system|shutil\.which|Popen"` over `service` and `mcp/sse_server.py` (non-test) returns **no process spawn**. Positive control: the same run matched the spawn-named functions `_settle_running_spawn` and `_prepare_restart_spawn`, so the pattern reads the tree. No test pins nodesource or claude-code (`git grep nodesource|anthropic-ai/claude-code` over the test trees: 0). **Keep** the `~/.claude`, `~/.codex` and `~/.hermes` read-only mounts; quota collection reads them.
- risk: an operator who `docker exec`s into the container to run `claude`. That `git` and `build-essential` in the image are also unneeded is ASSUMED; a build would have to prove it.
- severity: P3
- effort: S

### E6. Service-side residue of the environment-bridge tier retired in v0.6.2
- where: `POST /environments/controls/claim` and `PATCH /environments/controls/{id}` (`service/routers/environments.py:755-791`), `service/environment_claim.py` (135), `service/api_core/superseded_bridge_stops.py` (102), `environment_control_is_empty` (`api_core/claim_emptiness.py:69`), `EnvironmentControlClaim` and `EnvironmentControlUpdate` (`models.py:444-455`), the drain SQL in `reconcilers/terminal_controls.py:205-265`, `POST /terminals/{id}/report-dead` (`routers/terminal_lifecycle.py:124`, 132 lines, plus `TerminalDeadReport`), and `POST /agents/{id}/console-working` (`routers/agents/liveness.py:336`, 41 lines).
- what goes: about **600 product lines**, plus the dedicated tests (`test_env_supersede_stop` 129, `test_superseded_bridge_stops` 215, `test_two_bridges_cannot_claim_one_control` 164, and the report-dead cases in `test_bugd_coldstart_selfheal`), about **700 test lines**.
- evidence: the producers were deleted in `779099d7` (2026-09-04), `ac6d6e82` (2026-09-05) and `d0985660` (2026-09-17), found with `git log -S`. Today no caller exists in the bridge, the dashboard, aify-wrapper, or the aify-env plugin (`api.mjs` sends 10 routes; none is among these). aify-env says it outright at `lib/plugins/aify-comms/index.mjs:161-163`: "The service queues a stop for a superseded bridge in `environment_controls`, and nothing drains it -- the consumer was the aify-comms environment-control loop, which v0.6.2 deleted." `service/api_core/console_working.py:3-10` records that nothing replaced the console-working POST. The service now stamps that lease itself.
- risk: an aify-comms older than v0.6.2 still running its environment bridge on another machine. Behaviour note: `POST /environments/{id}/control` with `stop` (called by `environments-panels.mjs:386`) would still disable the environment and mark its sessions lost. Only the pending-control row that nobody claims would go.
- severity: P2
- effort: M

### E7. The hermes `api_server` daemon "ensure" path, retired on the wrapper side on 2026-06-02
- where: `mcp/stdio/hermes-daemon.js:168-282` (`ensureDaemon`, 115), `hermes-apiserver-client.js` (299), `hermes-version.js` (56, whose `probeApiServer` is used only by `ensureDaemon`; `stopDaemon` takes it "for symmetry/future use; not required", `hermes-daemon.js:400`), the ensure branch of `hermes-daemon-cli.js:73-80`, and the `AIFY_HERMES_APISERVER_KEY` diagnostic (`adapters/hermes.js:62`).
- what goes: about **485 product lines**. Tests: `hermes-apiserver-client.test.js` 218 and `hermes-version.test.js` 76, plus the `ensureDaemon` cases in `hermes-daemon.test.js` (23 mentions) and `hermes-daemon-cli.test.js`, about **600 lines**. **Keep** `stopDaemon` and `defaultKillByPort`: the wrapper's kill-prior step (`hermes-aify.sh.in:688`) and `hermes-delivery-loop.mjs:11` use them.
- evidence: aify-wrapper `wrappers/hermes-aify.sh.in:614-619`: "The retired `aify_hermes_ensure_daemon` helper ... was removed ... there is no api_server daemon to ensure. `AIFY_HERMES_DAEMON_CLI` remains for kill-prior's `stop`." The only invocation of the CLI anywhere is `node "$AIFY_HERMES_DAEMON_CLI" stop ...`. No product importer of `ensureDaemon` exists outside the CLI.
- risk: an operator running `node hermes-daemon-cli.js <agent>` by hand.
- severity: P3
- effort: M

### E8. The Python `RuntimeAdapter` family: 40% of it is reached only by tests
- where: `service/runtimes/base.py:36-91,138-145`; `discover_session_id` and its helpers in `codex.py:57-111`, `hermes.py:59-170` and `pi.py:59-119`; `wrapper_name`, `display_name` and `supports_multi_client` on every adapter; three `raise NotImplementedError("Plan 3 — not yet implemented")` stubs (`inject_message`, `interrupt`, `steer`).
- what goes: **289 of 725 lines** in `service/runtimes/`, plus their tests (`runtimes/test_hermes_session_discovery.py` 343, `test_discover_session_id.py` 23, `hermes_carriers.py` 60, and the matching parts of `test_base.py` and `test_per_adapter.py`): about **700 test lines**.
- evidence: an AST walk of every class member in `service/runtimes/*.py`, counted against all non-test Python outside that package. `get_current_session_id`, `normalize_session_handle`, `resume_args`, `normalize_model_override`, `diagnostic_env`, `discover_session_id`, `inject_message`, `wrapper_name`, `display_name` and `supports_multi_client` each have **0 production readers**. The private helpers are called only from inside that dead cluster (checked by grep). Positive control from the same walk: `resume_command` has 19 production readers, `console_command` 5 and `is_resident_ready` 3. `opencode.py:33` already concedes "nothing in `service/` reads `wrapper_name` at all". The live adapters are the JS ones in `mcp/stdio/adapters/`.
- risk: none external. The Python adapters run only inside the service.
- severity: P3
- effort: S-M

### E9. Twins pinned by agreement tests: merge them now that the reason for pinning is gone
- where (same-language twins only; cross-language and cross-repo agreement tests are legitimate and stay):
  - Python: `_queue_console_dispatch_inputs` and `_queue_console_inputs_for_dispatch` (`api_core/console_input_queue.py:196,278`; "FIFTY-ONE OF THEIR FIFTY-THREE BODY LINES ARE CHARACTER-FOR-CHARACTER IDENTICAL"); the byte-identical turn-start and turn-end supersession guards; four copies of one status set (`agent_sessions.py:32`, `tuning.py:29`, `terminal_status.py:140`, `routers/sessions.py:97`); two `_ANSI_RE` (`terminal_diagnostics.py:42`, `api_core/terminal_text.py:49`); four `INSERT INTO terminal_sessions`; `/analytics` and `/analytics/pulse` counting the fleet separately.
  - JS: `createDeferred` in 4 modules (`codex-session.js:43`, `hermes-session.js:28`, `hermes-managed-gateway-session.js:90`, `pi-session-timeouts.mjs:20`); `idleTimeoutFor` and `startupTimeoutFor` in 3; `parseProcLines` (`proc-probes.js:142`, `reap-managed-claude.js:70`); `defaultKillTree`; `defaultGetCmdline`; `DelegatedManagedController` (29 lines, twice: `codex-controller.js:26`, `hermes-controller.js:37`); `sleep` declared in 4 modules and hand-rolled `new Promise(r => setTimeout(r, ms))` 11 times. **Stdlib**: `import { setTimeout as sleep } from "node:timers/promises"` (Node 16+; this host runs v22.20.0).
- what goes: about **250 product lines**, and these agreement tests: `test_console_input_queueing_twins_agree` 142, `test_the_two_supersession_guards_agree` 79, `test_ended_status_sets_agree` 91, `test_ansi_strippers_agree` 188, `test_terminal_session_inserts_agree` 197, `test_the_two_analytics_endpoints_agree` 146, `deferred-agreement` 136, `parse-proc-lines-agreement` 89, `process-kill-helpers-agree` 77, `delegated-managed-controller-agreement` 83. About **1,230 test lines**, and 20 fewer `KNOWN_FORKS` entries in `each-name-has-one-owner.test.js:110-139`.
- evidence: several of these tests say why they pinned instead of merging. `test_the_two_supersession_guards_agree.py:5-7`: the block "does not fit either shape the extract-method gate can prove". That gate was **retired 2026-09-18**. The fork list is the repo's own census (`each-name-has-one-owner.test.js:110`).
- risk: none external. `claude-channel.js` is a separate process on purpose, so leave its copies alone. `Promise.withResolvers` would also replace `createDeferred`, but only from Node 22 on. The minimum Node version on other hosts is not stated anywhere, so one shared helper is the safe choice. The pi/claude idle-run closers (`test_idle_terminal_run_closers_agree.py`) have **drifted apart**, so merging them is a behaviour decision, not a tidy-up.
- severity: P3
- effort: M

### E10. Tombstone comments that no proof reads
- where: 505 lines matching `^\s*(#|//) .*(moved to|lives in) .*v0\.` across product code. After E1 takes app.js (197) and hermes-managed-host.js (57), about **251 remain**: `service/control_plane.py` 139 (of 897 lines), `mcp/stdio/server.js` 27, `routers/dispatch_messages/shared.py` 22, `routers/agents/shared.py` 15, and the rest scattered.
- what goes: about **250 lines**. `control_plane.py` would fall from 897 to about 760, which is the "regrowth" headroom CLAUDE.md worries about.
- evidence: the Python extraction proofs that read such markers were retired 2026-09-18. `git grep "moved to"` in the test trees finds only prose. `test_prose_paths_resolve.py` checks only that a path named in a comment exists, so deleting a comment cannot turn it red. `git log -S` answers "where did X go".
- risk: none.
- severity: P3
- effort: S

### E11. The native (non-wrapper) managed controllers, including a gateway mode that nothing turns on
- where: `mcp/stdio/hermes-managed-gateway-session.js` (484), selected only when `AIFY_HERMES_MANAGED_USE_GATEWAY=1` (`hermes-managed-gateway-session.js:483`). More widely, `HermesManagedController` with the ACP `hermes-session.js` (648) and `hermes-acp-protocol.js` (172), plus `CodexManagedController` with `codex-session.js` (803), are reached only for a `managed` run where `managedViaWrapper` is false (`controllers/hermes-controller.js:112-128`).
- what goes: the gateway sub-mode is about **484 product lines plus about 1,000 test lines** (`hermes-gateway-pool`, `-session-plumbing`, `-shutdown-all`). The wider native path is about **1,600 more**. That part is not proposed for deletion, only named.
- evidence: `AIFY_HERMES_MANAGED_USE_GATEWAY` is set nowhere: 0 hits in `install.sh`, the aify-wrapper templates and aify-env `lib/` and `bin/`. The only mentions are code comments and three docs, and `docs/HERMES_INTEGRATION.md:60` calls it "a debug/fallback surface". The `managed_via_wrapper` default is `["codex", "hermes"]` (`api_core/settings_spec.py:210`), so the native controllers run only if an operator unticks a runtime in Settings.
- risk: the ACP path is a live fallback that one setting away can reach. Removing it is an operator decision. The gateway sub-mode needs a hand-set env var that nothing documents for operators.
- severity: P3
- effort: M

### E12. The SSE MCP transport: a second implementation of the whole tool surface
- where: `mcp/sse_server.py` (186) and `service/sse/*` (1,184); `install.sh --mcp-transport sse` (`install.sh:74,119-127`).
- what goes: about **1,370 product lines and about 4,100 lines** of tests that touch it (`transport-parity.test.js` exists only to keep the two surfaces in step).
- evidence: on this host both installed clients use stdio: `~/.codex/config.toml` has `[mcp_servers.aify-comms] command = "node"`, and `~/.claude.json` `mcpServers.aify-comms` is `type: stdio, command: node`. The tool surface is written twice, once in JS and once in Python.
- risk: **other machines cannot be seen from here.** Any host installed with `--mcp-transport sse` depends on it, and so does any client pointed at `/mcp`. Operator decision.
- severity: P3
- effort: L

### E13. pi residue: quantified, not recommended for deletion (operator decision)
- where: dedicated product files `pi-session.js` 993, `pi-terminal-frame.mjs` 234, `service/pi_resident_flip.py` 166, `controllers/pi-controller.js` 141, `adapters/pi.js` 126, `service/runtimes/pi.py` 119, `runtimes-pi.js` 110, `virtual-terminal-input.js` 79 ("dashboard-input buffering for synthesized pi RPC terminals"), `pi-session-pool.mjs` 70, `pi-session-timeouts.mjs` 60, `pi-session-registry.mjs` 17.
- what goes, if the operator ever calls it: **about 2,115 dedicated product lines**, plus **about 140 lines across about 25 shared files** (largest: `runtimes.js` 19, `install.sh` 18, `contracts/vocabulary.json` 8, `runtimes/__init__.py` 6, `virtual-terminals.mjs` 6, `adapters/index.js` 6), **`pi-aify.sh.in` 458** in aify-wrapper, the `GET /agents/{id}/pi-session-state` route (called by `pi-aify.sh.in:358`), **about 2,260 test lines** (off by default under `AIFY_TEST_DEPRECATED`), `install.pi.md` 130, two copies of the `pi.md` skill reference (78 each), and about 2,580 lines of pi plan docs.
- evidence: the file inventory by name, and `git grep -P` for pi identifiers with `API_` excluded (the first pass counted `API_KEY` as pi and was thrown away).
- clean-removal cost: **M-L**, across three repos. `LAUNCHABLE_RUNTIMES` (`runtimes.js:370`) and `contracts/vocabulary.json` are held equal across languages and repos by agreement tests; `dispatch_hint.py:129` still suggests `runtime="pi"`; `virtual_rpc.py` has a pi sentinel; aify-wrapper needs its template removed and the pin bumped (with the reinstall trap CLAUDE.md records). `pi-session.js` at 993 lines is also the second file closest to the 1000-line gate.
- risk: operators who still run pi agents.
- severity: P3
- effort: M-L

### E14. opencode residue: quantified, and its reachability after v0.6.2 is unproven
- where: `controllers/opencode-controller.js` 253, `adapters/opencode.js` 39, `runtimes-opencode.js` 49, `service/runtimes/opencode.py` 49, about 70 lines in shared files, and `@opencode-ai/sdk` in `package.json` (imported only by `opencode-controller.js`).
- what goes: **about 460 product lines**, 1 npm dependency, **267 test lines**, and `install.opencode.md` 100.
- evidence: `install.sh:213-215` says "Managed OpenCode remains available through an environment bridge installed by a supported client". **That tier was deleted in v0.6.2.** aify-wrapper ships no opencode launcher (its `wrappers/` holds claude, codex, hermes, pi), and aify-env `lib/` and `bin/` mention opencode **0 times**. Whether a managed opencode spawn can still start and receive work is ASSUMED to be no. One real spawn attempt would settle it. If it fails, removal loses no behaviour.
- risk: as above. Keep it until that spawn has been tried.
- severity: P3
- effort: M

### E15. The v1 JSON-store to v2 SQLite migration
- where: `service/export_v1.py` 97, `service/import_v2.py` 122, `scripts/migrate-v1-to-v2.sh` 76, `service/tests/test_v1_migration_round_trip.py` 300, and a comment at `service/db.py:246`.
- what goes: about **595 lines**.
- evidence: the SQLite backend landed in `45a9182a` on 2026-04-02, nearly four months before the first tag (`v0.1`, 2026-07-26). No product module imports either file. Only the migration script and its test do.
- risk: an un-migrated v1 data volume on some machine. Keeping the files at a tag such as v0.6.22 covers that case.
- severity: P3
- effort: S

### E16. Container-project template leftovers, dead whether or not E2 goes
- where: `setup.bat` 41 ("Agentify Container - Initial Setup", referenced nowhere), `scripts/add-service.sh` 53 (0 references), `services/docker-compose.sub-service.example.yml` 46 (referenced only by `add-service.sh`), the `SUB_SERVICES` loop in `scripts/compose-up.sh:33-46`, the Makefile help banner "Agentify Container Commands", and `integrations/openclaw/` 202 (a scaffold with placeholder calls such as `/api/v1/your-endpoint`, `index.ts:115,133,158`; referenced nowhere).
- what goes: about **450 lines**.
- evidence: stem search across every tracked file: `setup.bat`, `openclaw`, `open-webui` and `integrations/claude-code` each 0; `services/docker-compose` only in `add-service.sh`.
- risk: none known. `compose-up.sh` itself stays (`Makefile up`).
- severity: P3
- effort: S

### E17. Two byte-identical copies of every skill
- where: `.agents/skills/**` mirrors `.claude/skills/**`, 3,295 lines, kept identical by `test_skill_mirror_parity.py` (72). `install.sh:479,1654` installs the `.agents` copy for hermes and codex.
- what goes: 3,295 duplicated lines plus the parity test, if `install.sh` installs from one tree for every client.
- evidence: the parity gate exists only because there are two copies.
- risk: Codex discovers `.agents/skills` in the repo it is working in, so agents working on this repo would lose the skill unless the tree is replaced by a git symlink, which is unreliable on Windows checkouts. Operator decision.
- severity: P3
- effort: S-M

### E18. `agent_live_state`, the vestigial table, and four tests that still write to it
- where: `service/schema.py:46-58` (table and index); test seeds at `test_api_v2_regressions.py:5326,5351,10014,12039`; the `("agent_live_state", "agent_id")` entry at `test_agent_rename_covers_every_agent_reference.py:117`.
- what goes: 13 schema lines, about 40 test lines of seeding into a table nothing reads, and the rename-list entry. Add one `DROP TABLE IF EXISTS agent_live_state` so existing databases reclaim it. Its `ON DELETE CASCADE` from `agents` means every agent delete still touches it today.
- evidence: `git grep -w agent_live_state -- service mcp ':!service/tests'` finds only `schema.py` and comments (`recovery_writes.py:140`, `registration_gates.py:86,169,177`, `status_cache.py:41`). `docs/V0_7_WEAK_POINTS.md:2091-2097` measured the last write on 2026-06-18 and zero production DML. A column pass of the same kind found `channel_members.joined_at` is written but never read. That one is not worth a migration.
- risk: none. `CREATE ... IF NOT EXISTS` removal is harmless, and the `DROP` only reclaims space.
- severity: P3
- effort: S

### E19. HTTP routes with no caller in any repo (listed, not recommended)
- where: `POST /messages/conversation/clear` (`routers/dispatch_messages/message_removal.py:155`, 30 lines), `POST /messages/cleanup/orphan-unread` (`:211`), `PATCH /agents/{id}/usage-source` (`routers/agents/config.py:43`, 26), `POST /rotate` (`routers/maintenance.py:128`, 13; documented in DECISIONS.md:30), `GET /api/v1/favicon.*` (duplicates of the root favicon routes), and `GET /dashboard/dispatches` (a legacy redirect, `routers/meta.py:66`).
- what goes: about **150 lines**, if the operator confirms nobody curls them.
- evidence: the route scan found 0 callers in the dashboard, the bridge, `service/sse`, scripts, aify-env and aify-wrapper. Only tests call them. `docs/V0_7_WEAK_POINTS.md:1331-1337` already noted that the orphan cleanup has no UI.
- risk: **high relative to the saving.** They are operator maintenance endpoints, and hand `curl` use cannot be seen from code.
- severity: P3
- effort: S

### E20. Product exports that only their own test calls
- where (bridge): `hermes-gateway-protocol.js`: `buildSessionResumeFrame` :80, `buildSessionCreateFrame` :104, `buildSessionInterruptFrame` :117, `isSessionNotFoundError` :446, `pickFreshestSessionFromList` :459 (61 lines together); `runtimes.js:217` `extractRuntimeSessionHandleFromArgv` (30); `env-process-reconciliation.mjs:197` `processesThisBridgeDoesNotKnow` (17); `hermes-loop-ready.js:45` `loopReadyFresh` (10); `reap-managed-claude.js:148` `pidsForResumeHandle` (5); `hermes-acp-protocol.js:61` `encodeNotification`; `registry-credential.mjs:81` `credentialRefIn`. (Dashboard): `status.js:114` `renderStatusDot`, `ui.js:126` `asyncAction`, `inspector-refresh.mjs:74` `shouldRefreshInspector`, `realtime-dispositions.mjs:83` `ignoredReason`, `api-client.mjs:36` `currentApiBase`.
- what goes: about **150 product lines** plus their test cases, about the same again.
- evidence: the export scan, then a whole-repo `git grep -l -w` per name. Every one appears only in its own file, its test, and dated docs. The `*_ForTests` hooks and `setOperatorKey` are kept: tests use them as fixture setters.
- risk: none external.
- severity: P3
- effort: S

### E21. `free-names.mjs`: a second missing-import detector and a second gate for one property
- where: `service/new_dashboard/free-names.mjs` (151) and `free-names.test.mjs` (162). Its final test (`:135`) is "NO DASHBOARD MODULE USES A SIBLING'S EXPORT WITHOUT IMPORTING IT".
- what goes: **313 lines**, and a test helper that sits in the dashboard's served directory.
- evidence: `no-missing-sibling-imports.test.mjs:40-44` already enforces the same property over the dashboard, using the bridge's `mcp/stdio/tests/missing-imports.mjs`. `git grep "free-names"` finds only its own test and comments.
- risk: none. The repo's bar applies before deleting: mutate a dashboard import and confirm the surviving gate goes red, including the template-interpolation case `free-names.test.mjs:30` covers.
- severity: P3
- effort: S

### E22. The 1000-line gate is written twice over one policy and one walk
- where: `service/tests/test_no_new_oversized_source_file.py` (259) and `mcp/stdio/tests/no-new-oversized-source-file.test.js` (231). Both read `oversized-allowlist.json` and walk the whole repo.
- what goes: about **230 lines**. Keep one; the Python one also governs the Python product files.
- evidence: CLAUDE.md, "The 1000-line gate": "Two tests enforce it ... Both read ONE policy file ... and walk the repo from its root". Whichever runs first answers for both.
- risk: none. A contributor who runs only the bridge suite loses the size check, which CLAUDE.md already says must never happen (all suites, every change).
- severity: P3
- effort: S

### E23. Config knobs and env vars that nothing sets or reads
- where: `MCP_APP_NAME` is in `.env.example` and read by nothing (`git grep -w` outside docs finds only `.env.example`). `python-dotenv` is in `service/requirements.txt` and imported by nothing. Env vars read but never set or documented anywhere (repo, aify-env, aify-wrapper, docs): `AIFY_AGENT_NAME` (`auto-registration.mjs:140`), `AIFY_CHANNEL_RELEASE_RECHECK_MS` (`claude-channel.js:61`), `AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV` (`server.js:143`, `registration-inputs.mjs:112`), `AIFY_HERMES_APISERVER_KEY` (goes with E7), `AIFY_PI_AIFY_COMMAND`, `AIFY_PI_STARTUP_TIMEOUT_MS` (go with E13), `CLAUDE_COMMAND` (a silent alias of `AIFY_CLAUDE_COMMAND`, `runtimes.js:268`), `CLAUDE_MCP_MESSAGES_DIR` (`local-store.mjs:26`), and `AIFY_REPO` (`doctor.js:151`).
- what goes: about **20 lines**, 1 requirement, and 1 `.env.example` entry.
- evidence: 116 env names read; each checked against every non-reader file in three repos (a platform-provided name such as `HOMEPATH` or `CODEX_SANDBOX` is not counted as a knob). Positive control: `AIFY_HERMES_MANAGED_USE_GATEWAY` was correctly left off because `docs/HERMES_INTEGRATION.md` mentions it.
- risk: an operator who hand-set one of them. `uvicorn[standard]` pulls in python-dotenv anyway, so dropping the line changes nothing installed.
- severity: P3
- effort: S

### E24. The installer ships `tests/` and `fixtures/` into every host's `~/.aify-comms`
- where: `install.sh:353-369` (`rsync -a --delete "$src/"` or `cp -RL "$src/."` of all of `mcp/stdio`).
- what goes: 0 repo lines. The install copy gets about **4 MB** lighter (`~/.aify-comms/mcp/stdio/tests` measures 3.9M) and stops carrying the 3,016-line frozen fixture from E1.
- evidence: `grep -E "from ['\"]\./(tests|fixtures|scripts)/"` over product modules finds nothing. The install copy on this host contains `tests/` and `fixtures/`.
- risk: none for runtime code. Check that no installed doctor path reads `tests/doctor-sources.mjs` from the installed copy. The grep found no import of it.
- severity: P3
- effort: S

### E25. Comment volume (informational; a matter of taste, not a deletion list)
- where: **36,238 of 98,959 product lines (36%)** are comments or docstrings. The heaviest: `hermes-managed-host.js` 568/739 (76%), `control_plane.py` 551/898 (61%), `doctor-predicates.js` 45%, `terminal_write_queue.py` 46%.
- what goes: nothing is proposed wholesale. The cheap, low-risk part is E10 (tombstones). The next tier is dated incident narratives ("MEASURED 2026-08-..", "found by review round N"). They belong in DECISIONS.md or the commit message, and could move there whenever a file is touched anyway.
- evidence: a tokenizer count (Python `COMMENT` tokens plus statement-level strings) and a JS line-comment and block-comment count over 528 product files.
- risk: this is the operator's documented style; each such comment was written to stop a regression.
- severity: P3
- effort: n/a

### E26. Documentation volume (for reader D; quantified only)
- where: `docs/` holds **59,527 lines**. At top level, PACKET, LEDGER, ROADMAP and PLAN files alone come to 12,033 (e.g. `V0.2_ROADMAP.md` 1,235, `V063_ACCEPTANCE_LEDGER.md` 662, `JS_SERVER_JS_PROOF_PACKET.md` 538); `docs/superpowers/plans/` holds 69 files.
- what goes: whatever is superseded. Deleting it also shrinks what the link and prose-path gates have to keep resolving.
- evidence: `git ls-files docs | xargs wc -l`.
- risk: link gates fail on a document that another document or a code comment cites. Delete in step with the citations.
- severity: P3
- effort: M

## Not findings (checked and clean, so nobody re-walks them)

- Top-level Python defs and classes: no dead ones outside test hooks (above).
- Schema: no table without readers (agrees with `V0_7_WEAK_POINTS.md:2078`). The only vestigial table is `agent_live_state` (E18).
- npm dependencies: `ws` and `@modelcontextprotocol/sdk` are used. `@opencode-ai/sdk` is opencode-only (E14). `node-pty` is unused (E4).
- Test duplication: the 2026-09-17/18 audit (`docs/superpowers/plans/2026-09-17-test-suite-audit.md`) already removed about 300 duplicate Python tests and the JS census duplicates, so no second pass was made here. The agreement tests in E9 are the one class it did not cover. They exist because the product code is duplicated, so they go when the twins are merged.
