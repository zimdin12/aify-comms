# D — docs and skills read against the code

Lens D, 2026-09-25, on `b7fde7c8` plus the untracked finalization plan. This pass was read-only: it
edited no docs and ran no aify-comms, aify-env or install commands.

**How it was read.** Five readers each took one slice:
- me: CLAUDE.md, README.md, AGENTS.md, the `aify-comms` skill and its references, and `.claude/commands`;
- one subagent each for the debug skill, the install docs (plus the install skill and the aify-wrapper README), DECISIONS/KNOWN_ISSUES, and the architecture docs.

**Evidence labels.** Every "absent" claim comes with a positive-control grep of the same shape. A
finding marked **[re-verified]** is one I re-ran myself. The others are as the subagent reported them,
with the evidence it gave. A consequence nobody ran is labelled **ASSUMED**.

**Mirrors.** `diff -r .claude/skills .agents/skills` exits 0 with no output, so the two skill trees
are byte-identical and every skill fix below applies to both.

Severity: P1 misleads into a wrong action · P2 real but bounded · P3 polish. Effort: S under an hour, M, L.

---

### D1. `bridge-current` no longer checks any running agent bridge, yet README and CLAUDE.md name it as the proof an update took effect
- where:
  - README.md:104 ("`bridge-current` (Windows) / `bridge-running` (Linux) names no agent still on old code").
  - CLAUDE.md:183 ("Platform-independent (the bridge reports its build on registration)").
  - CLAUDE.md:208 ("closes the first of those gaps on every platform … relaunching wrappers is no longer an unverified step").
  - CLAUDE.md:210 ("Expect `bridge-current` to read `unknown-all` … after upgrading from a pre-B1 bridge").
  - Code: `mcp/stdio/doctor-predicates.js:463-516`, `service/api_core/code_currency.py:7-14`.
- problem:
  - Nothing reports `bridgeBuild` any more. The environment bridge that sent it was deleted, and aify-env rows are routed to a `host-tier` verdict (`ok: true`) whose detail reads "Nothing here can be running stale aify-comms bridge code, because no bridge is running".
  - Every resident and managed agent still runs `~/.aify-comms/mcp/stdio/server.js`, and that is aify-comms bridge code.
  - So on Windows, nothing verifies that running agents picked up a bridge change, while the README playbook tells the operator this row is the "done when" signal.
- evidence **[re-verified]**:
  - `grep -rn bridgeBuild mcp/stdio/*.js mcp/stdio/*.mjs service` finds only readers: the doctor, `code_currency.py` (whose docstring says "v0.6.1 retired the tier that reported one"), and the dashboard badge. It finds no sender.
  - Positive control: the same grep finds `BRIDGE_BUILD_TAG` in `server.js:18`. That value is only logged to stderr (`server.js:133`).
- severity: P1
- effort: S (docs); M if a per-agent build report is wanted (hand to lens B: the `host-tier` detail text also overclaims)
- fix:
  - README:104: change "done when" to "`bridge-installed` green, then relaunch every agent that ran before the install; on Windows nothing verifies the running agents (`bridge-running` is Linux-only)".
  - CLAUDE.md:183: rewrite the row as "fails `unknown-all` only for a legacy bridge that reports no build; aify-env rows read `host-tier`; running agent MCP servers are NOT covered".
  - Delete CLAUDE.md:208's second sentence and all of CLAUDE.md:210.

### D2. Debug skill: "to host spawns locally, reinstall without `--delegate-spawns`" — no local spawner exists, and leaving the flag out keeps delegation on
- where: `.claude/skills/aify-comms-debug/references/dispatch-launch.md:128-129`. Code: `install.sh:155-167`, `install.sh:112`.
- problem:
  - When the flag is left out, `install.sh` reads the installed setting and prints "keeping DELEGATED to aify-env". Only `--no-delegate-spawns` changes the setting.
  - No setting makes spawns local: `env-client.mjs` was deleted.
  - The same fix line also tells the agent to "Start aify-env if it is down", which is the operator's action.
- evidence **[re-verified]**: `sed -n 155,167p install.sh` shows the keep-branch. `ls mcp/stdio/env-client*` finds nothing; `ls mcp/stdio/loop-gate.mjs` resolves (control).
- severity: P1 · effort: S
- fix: Replace lines 128-129 with: "aify-env is the only spawner. `spawn-delegation: unreachable` → tell the operator (starting aify-env is theirs). A launcher refused for a missing marker → re-run `install.sh --client <c>`."

### D3. Debug skill: the stale-build recipe `pkill -f 'mcp/stdio/server.js'` / `pkill -f claude-aify` takes down every agent on the host
- where: `references/dispatch-launch.md:150-175`. Code: `mcp/stdio/bridge-build.mjs:8-12`.
- problem:
  - The patterns match every agent's MCP bridge, including the caller's own and managed workers that aify-env owns.
  - The recipe also compares the build tag with `git rev-parse` in a checkout. The native copy has no `.git` and stamps `.aify-version` instead.
- evidence: `bridge-build.mjs:8-10` ("the native copy has NO `.git` … Stamp first, git second").
- severity: P1 · effort: S
- fix: Replace with: "Run `aify-comms doctor`. `bridge-installed` red → re-run `install.sh --client <c>`, then relaunch only the agents you own (`--resume <handle>`). Stop managed agents from the dashboard. Never pkill by pattern."

### D4. Debug skill: nine places still tell an agent to restart or run `aify-comms`, or rely on the deleted environment bridge
- where:
  - `references/dispatch-delivery.md:207` ("restart the target wrapper (or `aify-comms`)") and :102, :252.
  - `references/hermes-session.md:42-44`.
  - `references/pi.md:286`.
  - `references/dashboard-console.md:209,214`.
  - `references/status-symptoms.md:120-126`.
  - `references/status-model.md:89-91,104`.
- problem: `aify-comms` exits 2 for anything but the verifier verbs (`install.sh:1504-1508`). The "bridge" these lines restart is the tier v0.6.2 deleted. What an agent actually substitutes is aify-env, and restarting that reaps the fleet.
- evidence **[re-verified for dispatch-delivery.md:205-207]**: the text reads "restart the target wrapper (or `aify-comms`) so a live bridge re-claims". Also `launch-identity.mjs:52` ("`IS_ENVIRONMENT_BRIDGE` … RETIRED IN v0.6.2").
- severity: P1 · effort: S
- fix:
  - Delete "(or `aify-comms`)".
  - Everywhere else, write: "re-run `install.sh` and relaunch the affected `*-aify` agent; restarting aify-env is the operator's call".
  - Delete the env-bridge sentence at dispatch-delivery.md:252.

### D5. install.claude/codex/hermes.md: the "run agents on this machine" row names a command that is not on PATH and skips the step that registers the service
- where: install.claude.md:12, install.codex.md:12, install.hermes.md:14 (the row ends in `aify-wrapper-install --all --endpoint <url>`). It contradicts install.claude.md:46-50 ("You do not need any other package").
- problem:
  - `aify-wrapper-install` is an npm bin of aify-wrapper. Neither this `install.sh` nor aify-env's installs it.
  - The aify-comms client install is what writes `~/.aify/services.json` (`install.sh:2733` → `register-service-cli.mjs`), and the aify-env README says that registry is the only way aify-env learns the service.
- evidence: `grep "aify-wrapper-install" install.sh` → 0 hits; control `grep -c claude-aify install.sh` → 14. aify-env `install.sh` has 0 `aify-wrapper` hits.
- severity: P1 · effort: S
- fix: Change the row to "`bash install.sh --client <runtime> <url> --with-hook` (registers the service), then aify-env's `./install.sh`, then start `aify-env`".

### D6. README says "the service first, then aify-env, then the clients". aify-env started before any client install has no registry entry to start its claimer from
- where: README.md:85. Contradicted by docs/INSTALL_ONBOARDING.md:84-85 ("Register the selected service through the comms client installer first").
- problem:
  - aify-env reads `~/.aify/services.json` once, at boot, to start service plugins (`aify-env/bin/aify-env.mjs:734`, `readServices(readFileSync(REGISTRY_FILE))`). Advertising re-reads it on each beat.
  - Following the README order therefore gives a host that reads online, while nothing claims spawns until aify-env is restarted, and restarting it reaps workers.
- evidence **[re-verified: single `startServicePlugins` call site at :734 plus the import at :81]**. The end-to-end failure is **ASSUMED**; nobody ran it.
- severity: P1 · effort: S
- fix: "Order: service → `install.sh --client …` on each agent host (registers the service) → aify-env's `install.sh` → start `aify-env`."

### D7. README tells the operator to scope CORS in `config/service.json`; the default `.env` overrides it with `*`
- where: README.md:160-161 (the same advice is in the error text at `service/main.py:159`, which is lens A/B territory).
- problem:
  - `setup.sh:11-12` copies `.env.example`, whose line 61 is `CORS_ORIGINS=*`, uncommented.
  - Compose loads it via `env_file` (`docker-compose.yml:40`).
  - `service/config.py:187` maps `CORS_ORIGINS`, and environment beats service.json.
  - An operator following the README believes CORS is restricted while it is still `*`.
- evidence **[re-verified]**: the three lines above.
- severity: P1 (security) · effort: S
- fix: "Set `CORS_ORIGINS=` in `.env` and `docker compose up -d`", or comment the line out in `.env.example`.

### D8. install.pi.md / install.opencode.md tell the operator to "start `aify-comms`" and to restart aify-env to load controller code aify-env never loads
- where: install.pi.md:20, :78; install.opencode.md:22, :50, :64.
- problem: `aify-comms` exits 2. aify-env contains none of this repo's pi/opencode controller code, so the restart loads nothing new and reaps every managed worker on the host.
- evidence:
  - aify-env `lib/`+`bin/` grep for `virtual-rpc|omp --mode|opencode` → 0 hits. Control: the same grep for `launcher` → `lib/advertise.mjs`.
  - `install.sh:1504-1508` is the exit-2 path.
- severity: P1 · effort: S
- fix: Delete both restart instructions. Change :78 to "set `AIFY_PI_COMMAND` in the environment aify-env is started from". See also D33.

### D9. Windows notes and BRIDGE_SETUP.md still teach running `aify-comms.cmd` bare, "to run the bridge"
- where: install.claude.md:168; install.codex.md:178; docs/BRIDGE_SETUP.md:148-166 and :75 (README:205 and the install guides link here for "remote hosts and workspace roots").
- problem:
  - Harmless today (exit 2), but it is the exact habit the v0.6.1 refusal exists to end.
  - "Run the bridge" describes a deleted tier, inside a doc whose own header (:7) says the bridge "was".
- severity: P1 (per the lens rule: bare `aify-comms`) · effort: S
- fix:
  - Use `& "$env:USERPROFILE\.local\bin\aify-comms.cmd" doctor` as the PATH check.
  - Replace BRIDGE_SETUP.md:148-166 with `aify-env` examples.
  - Retitle the docs/README.md row "Setting up the environment bridge" to "the host tier and workspace roots".

### D10. DECISIONS/KNOWN_ISSUES say resume and compaction dialogs are auto-answered and can be turned off with two env vars. Only the dev-channels prompt is answered, and neither var exists
- where: DECISIONS.md:1268, :1302; KNOWN_ISSUES.md:469, :713.
- problem:
  - `service/api_core/console_prompts.py` has exactly one rule (`dev-channels-accept`, :140) and refuses resume menus (:121-124).
  - A managed claude that reaches the compaction dialog sits there, and the docs say it will not.
- evidence **[re-verified]**:
  - `grep -c "rule=" console_prompts.py` → 1.
  - `grep -rn "AIFY_AUTO_CONFIRM_COMPACTION|AIFY_NO_AUTO_ANSWER"` over service and mcp/stdio → only a stale test message (`service/tests/test_new_dashboard_app.py:163`).
- severity: P1 · effort: S
- fix: Rewrite both DECISIONS paragraphs to "only the dev-channels acknowledgement is answered; resume menus are refused". Delete the two knobs. Add a KNOWN_ISSUES entry: "compaction/resume dialogs are not auto-answered; a managed worker stops there".

### D11. DECISIONS says `managed_pty_eager_spawn` / `managed_terminal_backing_enabled` are "default off, flip live". Both default on, and turning one off stops every managed worker
- where: DECISIONS.md:911, :913-921 (contradicted by its own :1137). Code: `service/api_core/settings_spec.py:213-215` (both `True`, `_X`/internal) and :15-18 ("turning the first off stops every managed worker").
- problem: `validate_update` still accepts them on PUT, so following the doc's `PUT /settings` advice does exactly that.
- evidence **[re-verified]**: `sed -n 213,215p settings_spec.py` and `sed -n 911p DECISIONS.md` ("Why a setting, default off").
- severity: P1 · effort: S
- fix: Replace with "internal, default on, do not disable; kept only as a retired toggle".

### D12. CLAUDE.md is 73 KB, loaded into every Claude session on this repo, and about 45 KB of it is dated narrative that changes no action
- where: CLAUDE.md (72,968 bytes, measured `wc -c`). The repo's own rule at CLAUDE.md:113-115 ("a byte there is paid by every agent on every turn") applies to CLAUDE.md for exactly the reason it applies to a SKILL.md.
- measured ranges (bytes via `sed -n X,Yp | wc -c`) and what to keep:

  | lines | bytes | cut to |
  |---|---|---|
  | 13-38 roadmap entry | 2,527 | 3 lines: "v0.6: three repos. aify-env claims and runs spawns and owns PTYs; `aify-comms` is verifier-only since v0.6.1. Roadmap: <link>." (lines 28-34 are also wrong, see D15) |
  | 69-79 version history | 1,367 | "Do not set `SERVICE_VERSION` in `.env`; `config/service.json` may not set the five stamp-owned fields." |
  | 85-101 layout table | 11,986 | one sentence per row; move every "was X until DATE" clause to DECISIONS.md. Rows 87 and 90 are also wrong (D14, D13) |
  | 158-173 bare-`aify-comms` history | 1,399 | "Before v0.6.1 a bare run started an environment bridge and reaped the fleet twice; the refusal is enforced now." |
  | 179-210 doctor table | 16,765 | one line of "catches" per row (the 21 ids are correct, **[re-verified]** against `add("<id>"` in the doctor sources); rows 189/190/192/195/196 are 1.2-2 KB each of incident prose, which moves to DECISIONS.md or the check's own source comment |
  | 255-289 socket exhaustion | 2,805 | fixed 2026-09-17; keep "if `-n 8` gives a moving failure set with WinError 10055, the `conftest.py` SO_LINGER fix regressed; read every failure, run named files alone" |
  | 309-395 cross-repo tests | 7,253 | keep the list of which tests need a sibling checkout and `_sibling-checkout.mjs`; cut 336-342, 358-361, 384-395 (deleted-test history) |
  | 397-400 count history | 9,943 | one sentence: "The counts above are a snapshot; the run is the authority." (line 399 alone is 9,647 characters) |
  | 415-422 Phase-8 anecdote | 724 | keep "run all suites; no suite stays inside its own tree" |

- severity: P2 · effort: M
- fix: As in the table. Moved narrative goes to DECISIONS.md under one "Engineering history" heading.

### D13. The suite counts in CLAUDE.md contradict each other and the tree
- where:
  - CLAUDE.md:90 ("78 modules, 136 test files, 1800 tests").
  - :244 ("1800 tests").
  - :235 ("4847 tests").
  - :238 ("359 suites").
  - :367/:373 ("all 362", "362 is what the runner prints").
  - :397 ("measured snapshot (2026-08-27)").
  - :399 (the history ends "5789/373/1750").
- problem:
  - Measured: `ls service/new_dashboard/*.mjs | grep -v test | wc -l` → 83 and `ls *.test.mjs | wc -l` → 142, not 78/136.
  - The bridge runner population is 352 + 16 = 368 files minus 9 disabled pi = 359 **[re-verified]**, so 359 is right and 362 is stale.
  - Line 397 dates figures that `b7fde7c8` updated on 2026-09-25.
  - The history's last reading, 5789, sits beside a headline of 4847 with nothing explaining the ~940-test drop.
  - This is the "two copies of one fact" failure the paragraph itself predicts.
- severity: P2 · effort: S
- fix:
  - Delete the counts from the layout row at :90 and keep only the ones on the command lines.
  - Delete :367-376 down to one sentence ("run-all counts FILES in tests/, tests/adapters, tests/controllers, `.test.js` only").
  - Replace :397-400 per D12.

### D14. CLAUDE.md and ARCHITECTURE.md describe `service/control_plane.py` as the helper layer. It contains no code
- where:
  - CLAUDE.md:87 ("~140 helpers, constants and the two queue classes … 893 on 2026-08-24, so the regrowth … has started").
  - docs/ARCHITECTURE.md:72-97 (diagram "Shared helpers + the two queue classes … The 'carrier'").
- evidence **[re-verified]**:
  - `grep -cE "^(async )?def |^class " service/control_plane.py` → 0 (control: `service/api_core/status_broadcast.py` → 3).
  - Its own docstring: "THE COUNTS ABOVE ARE MEASURED, and were wrong for a while … the queues had moved to `service/terminal_write_queue.py`".
- severity: P2 · effort: S
- fix:
  - CLAUDE.md:87: "`service/control_plane.py` — a trail of where helpers moved; holds no code. New behaviour goes in `api_core/`, `reconcilers/`, or a top-level leaf."
  - Redraw the ARCHITECTURE diagram as routers → `api_core/**`, `reconcilers/**`, `dispatch_claim.py`, `terminal_write_queue.py`, `status_engine.py`.

### D15. CLAUDE.md:28-34 describes the bridge standing down when aify-env advertises. That bridge and its stand-down logic are gone
- where: CLAUDE.md:28-34 ("the bridge omits exactly those whenever aify-env's `/health` reports `advertising: true` … The bridge keeps `label` and `cwdRoots`").
- evidence **[re-verified]**: `grep -rn advertising mcp/stdio/*.js mcp/stdio/*.mjs` finds only comments (doctor-predicates.js:438, hermes-managed-host.js:640, service-registry.mjs:39). No code checks `advertising`. Control: the same grep in `service/` finds `routers/environments.py:164` and `api_core/environment_registration.py:98`.
- severity: P2 · effort: S
- fix: Delete 28-34 (covered by D12's 3-line replacement).

### D16. The `aify-comms` skill's canonical `comms_send` calls use a literal `from="me"`, and the reply pattern omits the required `to` and `subject`
- where: `.claude/skills/aify-comms/SKILL.md:41, 134, 146, 147, 149, 162` (`from="me"`), :182 and `references/leading-a-team.md:186` (`from="you"`). The register example at :88 uses `"my-agent"`, so the placeholders differ between examples.
- problem:
  - The schema says `from` is "Your agent ID". The service only checks its shape (`validate_sender`, `service/api_core/validation.py:48-57`) and does not check that the agent exists, so a literally copied `"me"` is stored as the sender.
  - The reply pattern `comms_send(from="me", type="response", inReplyTo=…)` would be refused by the bridge: `send-tools.mjs:72-74` returns "Error: need 'to' or 'toRole'", and `subject` is a required `z.string()` (:69).
  - Contrast: the service's own reminder builds `comms_send(from="{target}", to="{sender}", type="response", inReplyTo=…)` (`service/api_core/reply_contract.py:233`).
- evidence **[re-verified]**: the schema at send-tools.mjs:61-73. Whether any agent actually sent as "me" was not measured: the service needs a key and I did not query it (**ASSUMED** impact).
- severity: P2 · effort: S
- fix:
  - Use one placeholder everywhere, `from="<your-id>"`.
  - Reply pattern: `comms_send(from="<your-id>", to="<sender>", type="response", inReplyTo="<message-id>", subject="Re: …", body="…")`.

### D17. The skill says to "scan unread headers first" with `mode="headers"`, which marks every message read
- where: SKILL.md:128-131. Code: `mcp/stdio/inbox-tools.mjs:52-53` ("Viewing MARKS MESSAGES READ, mode=headers included — pass peek=true to leave them unread") and :64.
- problem: The recommended scan consumes the unread set, so the agent's next unread check shows nothing, even for messages it never opened.
- severity: P2 · effort: S
- fix: `comms_inbox(agentId="<your-id>", mode="headers", peek=true)`, then fetch by `messageId`.

### D18. The status vocabulary has 9 states in code; docs say 6 or 8
- where:
  - AGENTS.md ("exactly **6 states** (`VALID_STATUSES` …)").
  - DECISIONS.md:357-367, :963; KNOWN_ISSUES.md:546-559.
  - `aify-comms-debug/references/status-model.md:3, :42` ("8-state").
- evidence **[re-verified]**: `service/status_engine.py:18-20` lists working, shell, online, available, blocked, offline, stopped, misconfigured, starting. `skills/aify-comms/references/operations.md:149-159` already carries all nine.
- severity: P2 · effort: S
- fix: Make operations.md's table the single copy and have AGENTS.md, DECISIONS, KNOWN_ISSUES and status-model.md point at it.

### D19. AGENTS.md (the Codex entry point) still describes environment bridges and lists historical docs as "Primary Documents"
- where:
  - AGENTS.md line 3 ("connect Windows/WSL/Linux environment bridges").
  - Engineering Constraints ("A service container cannot directly spawn … unless a Windows bridge is connected").
  - "Primary Documents" (PRODUCT_BRIEF, ARCHITECTURE_PLAN "target architecture and data model", DASHBOARD_SPEC, WEB_APP_DESIGN, PLAN_REVIEW, IMPLEMENTATION_ROADMAP, FIRST_CODING_AGENT_TASK).
- problem: docs/README.md classifies every one of those docs as "Earliest planning" / "finished work — kept as evidence, not as instruction", and ARCHITECTURE.md:4 calls ARCHITECTURE_PLAN "a proposal from before the service existed".
- severity: P2 · effort: S
- fix:
  - Replace "environment bridges" with "aify-env on each host".
  - Replace the Primary Documents list with ARCHITECTURE.md, TARGET_ARCHITECTURE.md, docs/README.md, SESSION_MODEL.md and OPERATING_MODES.md.
  - Fix the 6-state table (D18).

### D20. operations.md sends agents to ARCHITECTURE_PLAN.md for runtime flags; that document is a pre-service proposal
- where: `.claude/skills/aify-comms/references/operations.md:81`.
- evidence: docs/ARCHITECTURE.md:4-6 and docs/README.md ("Earliest planning … `ARCHITECTURE_PLAN`").
- severity: P2 · effort: S
- fix: Point at docs/OPERATING_MODES.md (runtime settings) and docs/ARCHITECTURE.md.

### D21. PHASE8_STATUS.md documents deleted code; CLAUDE.md and docs/README.md still say "read before touching spawn or terminals"
- where:
  - CLAUDE.md:35-37.
  - docs/README.md:42.
  - DECISIONS.md:118; docs/CONNECTION_TRACE.md:91.
  - PHASE8_STATUS.md: all 415 lines. Its own banner (:3-13) says the code it describes was deleted. :361-415 cites `the-environment-bridge-marker-surface-is-held.test.js` and `child-env-hygiene.mjs`, both missing (control: `loop-gate.mjs` resolves). "Its last section" is a reviewer ruling, not the three defects.
- problem: The one live fact in it is the argv contract: "The launch travels as structured `argv` beside the command string (`terminal_sessions.argv`), because aify-env runs a launcher *file* by path and never a shell string." That contract is live at `service/schema.py:407` and in aify-env's `terminal-controls.mjs`.
- severity: P2 · effort: S
- fix:
  - Move that sentence into DECISIONS.md.
  - Reduce PHASE8_STATUS.md to its banner plus a sha, or delete it.
  - Drop the four pointers.
  - Update the docstring citation in `service/tests/test_console_argv_is_the_launch_shape.py:5`.

### D22. ARCHITECTURE.md is stale on four structural points
- where and evidence:
  1. :26 and :162-163 say the bridge owns "terminal/PTY ownership". No bridge module imports node-pty: the grep for `from 'node-pty'|require('node-pty')` over mcp/stdio (excluding node_modules) returns 0, while the control, the same shape for `ws`, hits `hermes-managed-gateway-session.js:18`. node-pty is still a dependency in `mcp/stdio/package.json` (that is lens E).
  2. :34 names a `wrapper-current` doctor check. It left on 2026-08-24; it has 0 hits in the doctor sources, while `bridge-current` has 3.
  3. :18 says "The three processes", but the table has five rows. CLAUDE.md:7 repeats "three processes".
  4. :84 says "single connection per call". `get_db` has used `CONNECTION_POOL.acquire` since `f538867f` (`service/db.py:546`).
  - Also :28-29 and :44 present pi as a live wrapper. `install.sh:78` disables it.
- severity: P2 · effort: S
- fix:
  - Bridge row "Holds": "MCP stdio server, runtime adapters, dispatch claim and delivery loops; aify-env owns PTYs".
  - :34: "`aify-wrapper-check` reports it".
  - Heading: "The tiers, and what reloads each".
  - :84: "pooled checkout per call; writes serialise through `BEGIN IMMEDIATE`".
  - Mark pi "(deprecated)".

### D23. ARCHITECTURE.md's "claim is a CAS" rule cites a test for a route with no remaining client, and its test block contradicts CLAUDE.md
- where: ARCHITECTURE.md:122, :134-136 (`test_two_bridges_cannot_claim_one_control.py` posts `/api/v1/environments/controls/claim`) and :199-205 (`python -m pytest service/tests -q`).
- problem:
  - The only client of `/environments/controls/claim` was the deleted environment bridge. Grep over mcp/stdio, service/new_dashboard, service/sse and aify-env lib/bin finds nothing outside `routers/environments.py:755`. Control: aify-env `api.mjs:175,192` calls `/spawn-requests/claim` and `/terminals/controls/claim`.
  - The dispatch-run claim is serialised by `BEGIN IMMEDIATE` (`service/dispatch_claim.py:84`, :403 has no status guard); it is not a compare-and-swap.
  - The test block omits `scripts/tests` (32 tests) and `-n 8 --dist loadfile`.
- severity: P2 · effort: S (doc); M (a real `/dispatch/claim` race test; lens A should decide whether to retire `/environments/controls/claim`)
- fix:
  - Reword the rule to "claims are serialised by `BEGIN IMMEDIATE`" and cite a test that races the live route.
  - Replace the test block with "see CLAUDE.md, Testing a change".

### D24. `--delegate-spawns` / `spawn-delegation` are documented as choosing where spawns run. They choose only which endpoint the doctor probes
- where:
  - install.claude.md:217, install.codex.md:263, install.hermes.md:475 ("managed spawns go to aify-env instead of being hosted by the aify-comms bridge … OFF by default").
  - `install.sh --help` :19, :69-71.
  - TARGET_ARCHITECTURE.md:163-165; PHASE8_STATUS.md:313-319.
  - The CLAUDE.md:185 row ("Reports `local` (the default)").
- evidence: `install.sh:1475-1490` ("Nothing in this file consumes them any more"). `git grep -ln delegation-setting -- mcp/stdio ':!**/tests/**'` → doctor-predicates.js only. The `local` verdict at doctor-predicates.js ~745-764 says "hosted by the aify-comms bridge itself" with `ok: true`.
- severity: P2 · effort: S (docs); M to retire the flag and the row, which is lens B's call
- fix: Docs: "vestigial: aify-env is the only spawner; the setting names which aify-env the doctor asks". Retiring the flag goes on the backlog.

### D25. The skill tells agents to route work by `comms_usage` pool %; nothing collects those pools since v0.6.2
- where: SKILL.md:199 ("shows each source pool's remaining subscription quota % … hand work to a pool with headroom"); DECISIONS.md:1296 describes collection as live; KNOWN_ISSUES.md:456-459 does not record it.
- evidence **[re-verified]**:
  - `mcp/stdio/usage-collector.js:3-4` says "NOTHING CALLS `collectOnce` OR `collectConsumptionOnce` IN PRODUCTION … since v0.6.2" (parked by operator decision 2026-09-04).
  - A grep for those names over mcp/stdio and aify-env lib/bin, outside the collector itself, → 0.
  - `usage-tool.mjs:11-13` prints `?` / stale rather than 0%, so the harm is bounded.
- severity: P2 · effort: S
- fix: SKILL.md:199: add "(pool collection is parked since v0.6.2; expect `?`/stale)", or cut the line until a collector exists. Add a KNOWN_ISSUES entry. Mark DECISIONS:1296 as parked.

### D26. KNOWN_ISSUES lists fixed defects as open
- where and fixing commit:
  - :486 role reset to coder → `a12b6914`, `launch_env.py:53,61`.
  - :487 and :337-349 model validation → `service/models.py:32-56`, `test_spawn_model_shape.py`.
  - :340 rename liveness → `routers/agents/rename.py:89-94`.
  - :475 "staged, NOT yet deployed" → shipped in `a12b6914`.
  - :246-266 `bridgeDir` path form → `64cec065`, `install.sh:2734`.
  - :715-745 codex console label → `5b9dafee`.
  - :446 console cannot scroll → `ad1afd9a`.
  - :453 (and DECISIONS:169) codex has no identity recovery → `codex-aify.sh.in:426-431`.
  - :438 "doctor has no unit coverage" → 15 doctor test files.
  - :480 records an "agreed" model allowlist; the commit says "Deliberately NOT an allowlist", and `allowed_models` has 0 hits.
  - :461-463 says the suite is "substantially red"; CLAUDE.md requires it green.
  - :541, :497, :529, :703-708 are about the deleted bridge or deprecated pi.
  - Line 3 says "Last reviewed 2026-08-13".
- severity: P2 · effort: S
- fix: Delete each item, citing its commit. Rewrite :480 as "rejected". Update the review date.

### D27. DECISIONS says the `PostToolUse` hook was removed; install.sh re-adds it
- where: DECISIONS.md:441, :523; KNOWN_ISSUES.md:667, :706; install.claude.md:253 (contradicting its own :234-248). DECISIONS:986 also says hermes has no turn-end event, but `install.sh:2233` wires `on_session_end`.
- evidence: `grep -c "wireTurnStart('PostToolUse')" install.sh` → 1. The comment in `install_claude_turn_start_hook` reads "WHY PostToolUse is back (reverses pure-event #4)".
- severity: P2 (whoever "cleans up" per DECISIONS removes a working hook) · effort: S
- fix: Add one DECISIONS entry recording the reversal, and correct the five lines.

### D28. Other DECISIONS entries that describe superseded behaviour as current
- where:
  - :1501-1515 says setting `API_KEY` would 401 the dashboard. The cookie exchange exists at `service/main.py:175-178`, and README:127-136 documents it.
  - :1170 says a gate writes back to `agent_live_state`. `registration_gates.py:198-205` says "READ-ONLY (2026-06-18)".
  - :1225-1226 treats `manual_session_mode` as live. `settings_spec.py:12,229` has it RETIRED, contradicting DECISIONS' own :23.
  - :993-999 and :1253 give a "120 s" `turn_busy` gate. The gates use the 30-min ceiling (`claim_gating.py:343`), per DECISIONS' own :437/:467.
  - :1264 describes the console "working" signal through deleted files (`claude-console-spinner.js`, `terminal-runtime.js`), cites a 12 s lease where the code has 20 s (`liveness.py:55`), and cites a deleted test.
- severity: P2 · effort: S
- fix: Mark each entry SUPERSEDED with a one-line current state, in the way neighbouring entries already do.

### D29. The debug skill describes deleted sweeps, `TERMINAL_MANAGER`, and a session-in-use auto-cleanup that nothing calls
- where:
  - `references/hermes-session.md:146, :150-154, :182-212` (env-bridge teardown and boot and tombstone sweeps).
  - `status-symptoms.md:111, :114-117, :147`.
  - `dashboard-console.md:234-240`.
  - `dispatch-delivery.md:14-15`.
  - `dispatch-launch.md:64-70` ("detects this error … kills the headless owner … retries once").
- evidence:
  - `server.js:504` ("DELETED IN v0.6.2 … TERMINAL_MANAGER") and :610-617 (every managed-teardown sweep deleted).
  - `isClaudeSessionInUseError` appears only at its definition (`runtimes-claude.js:159`), its re-export and a test.
  - Whether aify-env replaced the tombstone sweep: **not verified**.
- severity: P2 (an agent waits for a cleanup that never runs) · effort: M
- fix:
  - Cut hermes-session.md:187-212 to "stop/relaunch reaps the hermes triad; after a hard kill, `aify-comms doctor` `gateway-orphans`/`managed-orphans` name leftovers; killing them is the operator's".
  - Cut dispatch-launch.md:64-70 to "nothing clears this automatically; close the holder or Reset".
  - Drop the `TERMINAL_MANAGER` clauses.

### D30. Hermes health checks grep for log lines and paths that no longer exist
- where:
  - `references/hermes-session.md:27, :53-54, :73-74, :239` (`visible session bound: …` and three siblings).
  - :220-231 (the `hermes-aify-dashboard-*.log` / `wait_for_http` fallback).
  - `hermes-turns.md:79` (`head -80 … | grep AIFY_HERMES_PLUGIN`; the export is at template line 464).
  - `hermes-turns.md:49, :73` (active-session path; the real one is `${TMPDIR:-/tmp}/aify-hermes-active-<agent>.json`, `hermes-aify.sh.in:755`).
- evidence:
  - The four strings → 0 hits; controls `bind_transport` → 5 and `channel-sidecar` → 37.
  - In the template, `wait_for_http` → 0 and `ensure-host` → 6.
- severity: P2 (a check that can never pass) · effort: S
- fix: Delete :22-54 and :56-74. Point the checks at `comms_run_status` and `~/.local/state/aify-comms/hermes-gateway-host-<port>.log`. Fix the two paths and drop `head -80`.

### D31. The debug skill contradicts itself on Claude's permission default and gives unusable update steps
- where:
  - `references/dispatch-delivery.md:216` ("preserves normal permissions by default. Use `-auto`").
  - `codex.md:19` (every wrapper skips permissions by default).
  - `codex.md:53` ("`cd` into the install dir and `git pull`"; the native copy has no `.git`).
  - `codex.md:27` ("check with `npm test`", which is 359 files).
- evidence: `claude-aify.sh.in:227-232` ("Bypass permissions by DEFAULT") and `bridge-build.mjs:9`.
- severity: P2 (wrong security picture) · effort: S
- fix:
  - :216: "`claude-aify` passes `--dangerously-skip-permissions` by default; `--safe`/`--no-auto` opts out".
  - Update steps: "`aify-comms doctor` → re-run `install.sh` → relaunch".
  - Single test: `node --test tests/<file>`.

### D32. Runtime advertisement is attributed to the bridge and to override variables that nothing reads
- where:
  - Debug `references/dashboard-console.md:207, :209-214`; `dispatch-delivery.md:228`.
  - install.claude.md:153 (`AIFY_CLAUDE_COMMAND`, "reinstall to repair node-pty if the bridge reports terminal=false").
  - install.codex.md:71 and install.hermes.md:112 ("`aify-comms doctor --json`" for PTY proof).
- evidence:
  - aify-env builds `terminalRuntimes` by probing the runtime binary on its own PATH (`aify-env/lib/advertise.mjs:185-233`; `aify-wrapper/lib/detect-harnesses.mjs:32`).
  - `AIFY_CLAUDE_COMMAND|AIFY_HERMES_COMMAND|HERMES_COMMAND` → 0 hits in aify-env lib/bin (control: `terminalRuntimes` → `advertise.mjs`).
  - `doctor.js:711`: "`bridge-terminal` moved to `aify-env doctor`".
- severity: P2 · effort: S
- fix:
  - "A runtime is offered when `claude`/`codex`/`hermes` resolves on the PATH aify-env was started with (the operator's step); PTY capability: `aify-env doctor`."
  - Drop the override-variable advice.

### D33. The debug skill tells the agent to start aify-env, and gives a kill recipe that hits every Codex process
- where:
  - `references/dispatch-bridges.md:44` ("Start a claimer there — an aify-env"), which contradicts its own :13-15.
  - `references/codex.md:70-83` (`Get-Process node, codex | … | Stop-Process`, `pkill -f codex-aify`, "repeat for every Codex agent").
- severity: P2 · effort: S
- fix:
  - :44: "ask the operator to start aify-env".
  - codex.md: scope the kill to the resident agent's own tree (found by `--aify-agent <id>`), and add "managed Codex is stopped from the dashboard".

### D34. The install guides describe managed paths and hosts that no longer exist
- where:
  1. install.pi.md:16-17, :62-66, :82-86 (managed Pi "uses persistent `omp --mode rpc`"). The service launches `pi-aify --aify-agent …` (`service/runtimes/pi.py:46-52`), which install.sh refuses to install.
  2. install.opencode.md:24-25, :48 ("supported through the host tier"). The launch argv is bare `["opencode"]`, and aify-env's allowlist refuses a file with no `HARNESS_WRAPPER_VERSION` marker (`aify-env/lib/allowlist.mjs:88-106`). Spawn failure is **ASSUMED**; not run.
  3. install.codex.md:166-168, :239, :306-316, :326; install.hermes.md:146, :235-239, :322-327, :497-543; and "bridge-spawned managed PTYs" in install.claude.md:122, install.codex.md:121, install.hermes.md:199. All describe the bridge as the managed host.
  4. The "local-only mode" blocks (install.claude.md:52-57, install.codex.md:46-51, install.hermes.md:102-108, install.opencode.md:14-20) contradict install.claude.md:79-82 ("There is no 'local-only mode'"). `install.sh:177-198` asks for a URL or defaults to `http://127.0.0.1:8800`.
- severity: P2 · effort: M
- fix:
  - Pi: "managed Pi needs `pi-aify` installed via aify-wrapper; deprecated".
  - OpenCode: "managed OpenCode is unsupported".
  - Replace "bridge" with "aify-env" where the host tier is meant.
  - Collapse the four local-only blocks to one line.

### D35. Every quick start builds without `stamp.sh`, so the `service` doctor row is red on a fresh install
- where: README.md:73-74, :212 (stamp step placed after the rebuild); install.claude.md:30-31, install.codex.md:30-31, install.hermes.md:26-27.
- evidence:
  - The Dockerfile has no stamp step. Control: `scripts/compose-up.sh:16-17` does stamp.
  - `service/_build_stamp.json` is gitignored (`.gitignore:11`).
  - An unstamped build reports `0.0.0-dev` / `unknown` (`service/config.py:35`), and doctor-predicates.js returns `unknown-build` with the fix "Run scripts/stamp.sh". README:98 defines done as doctor `ok: true`.
- severity: P2 · effort: S
- fix: Use `bash scripts/stamp.sh && docker compose up -d --build` everywhere (or `scripts/compose-up.sh`, if that is the intended entry).

### D36. Newcomer gaps: no prerequisites, aify-wrapper commands never reach PATH, and `--resume` recovery quietly breaks once a key is set
- where:
  1. README.md:59-83 has no prerequisites. install.sh requires node and npm (`install.sh:2701-2708`), the client CLI, git and network for the aify-wrapper npm dependency, a native build toolchain if node-pty has no prebuilt binary, Git Bash on Windows, and Docker Compose v2. Only install.hermes.md:31 mentions Node.
  2. The aify-wrapper README (:320-362) relies on `aify-herdr-pane`, `aify-wrapper-check` and `herdr-aify` being on PATH, but its install.sh has no `npm link` or `npm install -g` (grep → 0). The aify-comms docs link there for Herdr (install.claude.md:71, :223).
  3. install.claude.md:134 and install.codex.md:138 say `--resume` recovers identity by asking the service. The wrappers curl `/api/v1/agents` with no `X-API-Key` (`claude-aify.sh.in:464`, `codex-aify.sh.in:423`, `hermes-aify.sh.in:400`; `grep -c X-API-Key` → 0 in each), and `/api/v1/agents` is not in `skip_paths` (`service/main.py:185`). So recovery gets a 401 once README's "turn on a key" is followed. That is an aify-wrapper code defect; flag it to lens B/F.
- severity: P2 · effort: S
- fix:
  - Add a 5-line Prerequisites block to the README.
  - Add `npm install -g .` (or `npm link`) as an explicit aify-wrapper step.
  - Add "(without an API key)" to the `--resume` claim until the wrapper sends the key.

### D37. The `/aify-comms:channel` slash command tells the agent to pass `silent=true`, a parameter `comms_channel_send` does not have; the host's plugin snapshot also serves stale April commands
- where:
  - `.claude/commands/channel.md` ("call it with `silent=true`"). Schema: `mcp/stdio/send-tools.mjs:196-207` (channel, from, body, type, priority, steer, queueIfBusy).
  - `install.sh:446-467` `refresh_plugin_snapshot` copies `.claude-plugin mcp service config scripts` plus six top-level files. It never copies `.claude/commands` or `.claude/skills`.
- problem:
  - `silent` was removed in `f46e4818` ("wake channel members by default").
  - On this host, `~/.claude/plugins/aify-comms/.claude/commands/` still holds `github/`, `analysis/`, `automation/`, `hooks/`, `monitoring/` and `optimization/`, which this session's own skill list surfaces as `aify-comms:github:code-review-swarm` and similar.
  - The repo tracks 11 commands (`git ls-files .claude/commands | wc -l` → 11).
  - The plugin copy's `SKILL.md` is dated Apr 28 (15,010 bytes); the installed `~/.claude/skills` copy is current (`cmp` equal).
- evidence **[re-verified]**: grep `silent` in the schema → 0; control `queueIfBusy` → :206. `ls` of the plugin commands dir as above.
- severity: P2 · effort: S (doc); S for lens B (refresh `.claude/commands` in the snapshot, or delete the stale directories)
- fix:
  - channel.md: "a background-only FYI: `type=\"info\"`, `queueIfBusy=true`".
  - Hand the snapshot gap to lens B.

### D38. The target-architecture, roadmap and boundary docs contradict themselves
- where:
  1. TARGET_ARCHITECTURE.md:48-55 and :191 ("PATH holds `aify-env` and the three launchers, and nothing else") against its own table at :65-68, which says the aify-wrapper bins "should" be included.
  2. The roadmap (`2026-08-20-three-repo-separation-roadmap.md`):11 says "Roadmap only", and :171 says "the seam … is still flag-off". Phase 8 execution and claiming are done, and its gate clause "The `aify-comms` command does not exist" (:152-156) is unmet: the verifier is installed (`install.sh:1357`).
  3. AIFY_ENV_BOUNDARY.md:3-7 says "Built and published since…" then "Nothing here is built". :21 ("Stops being a command") contradicts :79-82. :142-145 and :191-196 are stale "today" claims (doctor skip counting has since changed, per `doctor-report.mjs:37-48`). Open questions 1, 2 and 4 are answered.
  4. The TARGET numbers are stale: "answers 0.6.0" (:44, :204, :210); "Twelve checks" (:114; now 21); "22 `comms_*` tools" (:129; the SSE grep gives 23); `hermes-aify:562` (:158).
- severity: P2 · effort: S
- fix:
  - Put the aify-wrapper bins into TARGET's PATH list and rewrite :191.
  - Roadmap Phase 8: "EXECUTION + CLAIMING DONE (v0.6.1); open clause: where the verifier lives".
  - Delete "Nothing here is built", update the stale passages, and leave only open question 3.
  - Replace the counts and the line pointer with names.

### D39. P3 drift, one line each
- CLAUDE.md:46 says SKILL.md holds the "multi-instance matrix, status table"; both are in `references/operations.md`. CLAUDE.md:47 lists `buffer_full` for the debug skill, which has 0 hits for it.
- CLAUDE.md:88 names 10 reconciler modules; `service/reconcilers/` has 22.
- CLAUDE.md:128 uses "Never run a bare aify-comms" as its example of a guardrail that must stay a prohibition. The code now enforces it, so pick a live example.
- README.md:56 lists `comms_dispatch` among the "everyday tools"; SKILL.md:191 and the tool's own description say to prefer `comms_send`.
- README.md:116-119's list of fleet-watching doctor rows omits `env-code-currency`, `external-keys` and `client-api-key`, and sends newcomers to CLAUDE.md (a developer file) for what each row does.
- README.md:86 says the installer "refuses to finish without" an endpoint or key; non-interactively it defaults the URL and warns on a missing key.
- README.md:162: the open paths also include `/ready`, `/redoc` and `/ws` (`service/main.py:185`). README:170 never names the HTTPS port.
- SKILL.md:103: "it stops that agent's live instance on this host, managed worker, too" is garbled. Suggested: "it replaces any live instance of that agent on this host, including a managed worker."
- SKILL.md:158: "the service cold-starts a bridge-claimed worker" should say aify-env.
- operations.md:105-116, the "Environment Bridges" heading and "A newer bridge instance supersedes…", should become "Host tier (aify-env)". operations.md:73-74, :88 present `pi-aify`/`omp-aify` without "(deprecated)".
- Debug skill cross-references that point nowhere: dispatch-delivery.md:204, status-model.md:231, :251 (`status.md` does not exist), :263, dashboard-console.md:48.
- Renamed symbols:
  - `_apply_channel_only_to_claude_runs` → `_apply_channel_routing_to_claude_runs` (debug dispatch-delivery.md:268, DECISIONS:943).
  - `api_v2._environment_effective_status` → `service/env_status.py` (DECISIONS:1352, :1366).
  - `service/db.py` → `reconcilers/terminal_controls.py` (DECISIONS:1337, :1350).
  - `/api/v2` → `/api/v1` (DECISIONS:1086).
  - `createCodexControllerLegacy` → `CodexLegacyController` (DECISIONS:1025).
  - `cygpath -m` → `path_for_node` / `cygpath -w` (DECISIONS:862-866).
  - KNOWN_ISSUES:340, :410, :477 line pointers.
  - "the current `api_v2.py`" (hermes-session.md:15, :71).
- Knobs with 0 hits: `AIFY_HERMES_SKIP_GATEWAY` (DECISIONS:1047), `AIFY_NO_CONSOLE_KEEPALIVE`, and the "Action expired" toast (DECISIONS:738-744).
- `curl` examples without `X-API-Key` in the debug skill: dispatch-bridges.md:90, :97, :102, :257-258; dispatch-delivery.md:113, :172; pi.md:353. dispatch-bridges.md:39 already has the header.
- status-symptoms.md:183-187 uses the `sqlite3` CLI in the container. The Dockerfile does not install it; whether the base image ships it is unverified. Use `python -c "import sqlite3…"` as the neighbouring entries do.
- "v0.6.2" is cited as a release (PHASE8:4, :44; roadmap:137-139; CLAUDE.md), but the tag was retracted (`ae31c027`) and the deletions shipped in v0.6.3.
- docs/README.md labels BRIDGE_SETUP "Setting up the environment bridge"; see D9.
- install.codex.md:266 and install.hermes.md:479 say a Herdr-restored agent "comes back as `claude-aify`". install.codex.md:174 gives the dashboard as `:8800`; it is `:8801`.
- effort: S each

### D40. Structural proposal: DECISIONS, KNOWN_ISSUES, the debug skill and the install guides are mostly history
- measured:
  - DECISIONS.md 283,654 bytes / 1,654 lines. The reader estimates ~45% live.
  - KNOWN_ISSUES.md 126,061 bytes / 745 lines, ~40% live.
  - Debug skill references ~198 KB.
- proposal:
  - **DECISIONS.md.** Keep 5-367, 578-850 (selectively) and 1310-1654 after the D10/D11/D27/D28 fixes. Move 369-575 (superseded status and managed-hermes batches), 850-1070 (bridge-era entries) and 1072-1308 (dated "Plan 1-6"/pass entries) to `docs/history/DECISIONS-archive.md`. Refile the paragraphs sitting under the wrong heading (:1527 inside the API-key entry, :1296 under 2026-06-07, :409-417 July items inside 2026-06-03).
  - **KNOWN_ISSUES.md.** Keep 7-235 and 268-313, plus one condensed "Open backlog" drawn from 441-585 (`/ready` refreshing `turn_updated_at`, 429 not retried at `aify-service-endpoint.mjs:291`, dispatch-run ownership on PATCH, codex hook fragility), and 699-701. Cut 246-266, 351-439, 450-463 and 587-697 ("Resolved …" sections), and 315-349 down to 5 lines. Add entries for D10 and D25.
  - **Debug skill.**
    - `status-model.md:57-126`: the `status_engine=new` history. Keep only "`turn_busy` (delivery gate) and `in_turn` (status) are decoupled on purpose".
    - `status-symptoms.md:265-275`: cut.
    - `dispatch-bridges.md:157-166, :196-238, :252-259`: cut or reduce to one line each.
    - `hermes-session.md:262-336`: keep only the recovery sentence and 330-336.
    - `dispatch-launch.md:95-105`: cut.
    - Also collapse the duplicated facts: the status-cache note appears twice; "online needs a live sidecar" three times; the gateway turn detector in hermes-turns.md and status-model.md; the 30-minute `turn_busy` ceiling in two files.
    - Lower the ratchet ceilings as the bytes go.
  - **Install guides.** Per the install reader, cut install.claude.md:17-19, :98-101, :124-137 (keep the `--aify-agent` sentence), :141-155 (keep `AIFY_CLAUDE_STRICT_MCP`), :209-212 framing, :232-253 (a one-line hook list); install.codex.md:318-328; install.hermes.md:254-262, :312-320, :378-383, :393, :409-422, :488-491; install.pi.md:88-102; install.opencode.md:69-100 (becomes "not supported").
- severity: P2 · effort: M (moving text; the D10/D11/D26/D27/D28 corrections are S and should land first)

---

## Checked and clean

- **MCP tool surface.**
  - All 37 `comms_*` tools declared by `server.tool(` in `mcp/stdio/*-tools.mjs` appear in SKILL.md's Tool Map, and every tool name mentioned in the skills, README, install guides, CLAUDE.md, AGENTS.md, DECISIONS and KNOWN_ISSUES resolves to one of them. The exceptions are hermes' prefixed `mcp_aify_comms_comms_*` (correct for hermes) and `comms_run_steer`, which DECISIONS:1255 correctly records as removed.
  - Parameters used in the skill examples exist: register `cwd`; spawn `workspace`/`initialMessage`; inbox `mode`/`messageId`/`peek`; compact `mode`/`newAgentId`/`targetAgentId`; `runId`; console `text`/`lines`; read `name`; clear `target`/`olderThanHours`; search `scope`; dashboard `open`.
  - `comms_envs` output tokens `spawn UNPROVEN` and `advertised:` exist (`environment-tools.mjs:63,72`). `comms_console_tail` NOT LIVE behaviour matches its description. `usageSource`/`poolWeeklyPctLeft`/`quotaCritical` exist in `service/api_core/records.py`.
- **Doctor ids.** All 21 ids in CLAUDE.md's table exist as `add("<id>"` calls in the doctor sources, and none is missing from the table.
- **Named files and symbols.**
  - These CLAUDE.md files exist: `test_version_single_source.py`, `version-consistency.test.js`, `skill-size-ratchet.test.js`, `test_skill_mirror_parity.py`, both oversized gates, `no-unwatched-oversized-file.test.js`, the cross-repo tests, `_sibling-checkout.mjs`, `test_leaves_do_not_import_the_carrier.py`, `messenger-browser.mjs`, the three `scripts/*.sh` readers, `setup.sh`, and an empty-policy `oversized-allowlist.json`.
  - `service/routers/api_v2.py` is 53 lines with 15 `include_router` calls. `AIFY_HOME` is in install.sh:35.
  - The line counts CLAUDE.md quotes for the top of the 1000-line ranking are current: app.js 996, pi-session.js 993.
- **Service symbols named in AGENTS.md** exist: `_compute_session_display_status`, `LIVE_SESSION_STATUSES`, `_agent_liveness`, `switch_agent_session_mode`, `resident-lost`, the session/agent control routes, `cli_takeover`, `adapter-contract-symmetry.test.js`, and the adapter/controller/runtime files.
- **ARCHITECTURE.md's layer-rule tests** (architecture reader): every cited test exists and asserts its rule. `_LIVE_STATE_CACHE` is owned by `reconcilers/status_cache.py`, and `agent_live_state` is vestigial.
- **install.sh flags** named in the docs all exist in its parser (:83-150). The verifier verbs and the exit-2 refusal exist (:1395-1508). The wrapper flags `--shared`, `--safe`/`--no-auto`, `--aify-agent` exist in the templates. Ports 8800/8801/8802, `EXTERNAL_KEYS`, `OPERATOR_KEY` and the `?api_key=` cookie flow match the code.
- **The install skill** (`aify-comms-install/SKILL.md`): every command and flag it names exists (install reader).
- **Debug skill service symbols** (debug reader): `_CHANNEL_CLAIM_RUNTIMES`, `_turn_busy_holds_delivery`, `TURN_BUSY_BACKSTOP_SECONDS`, `MANAGED_ORPHAN_GRACE_SECONDS` and the registration gates exist. The `aify-agent-lease` exit code 75 and its messages match aify-wrapper.
- **Mirrors.** `.claude/skills` and `.agents/skills` are byte-identical.
- **README** is v0.6.1-correct throughout on the verifier-only `aify-comms` and aify-env as spawner, apart from D1, D6, D7, D35 and D39.
