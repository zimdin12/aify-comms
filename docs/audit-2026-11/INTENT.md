# INTENT: how the aify stack is supposed to work now (reference for the audit re-pass)

Built 2026-11-03 from the repos' own records at the HEADs cloned on 2026-11-02 (comms 1c9611b, env dde9d6d,
wrapper 7e9ce65, dashboard 31af77f, apg 8dccb03, agents-setup 42339d1). History was deepened read-only for dating
(comms back to 2026-10-14, env 2026-09-25, wrapper 2026-08-19, dashboard 2026-10-17, apg 2026-09-04, agents-setup
2026-08-19). "Last touched" dates below are commit dates (`git log -1`); a date equal to a repo's boundary means
"on or before". Dates inside headings are the docs' own.

**Precedence used throughout.** (1) Owner statements in [ROLES.md](ROLES.md) and the owner guidance in
[OWNER-GUIDANCE.md](OWNER-GUIDANCE.md) (both 2026-11, during this audit) override every repo doc. (2) Code. (3) The newest dated
owner ruling in a repo record (comms DECISIONS.md, dashboard `docs/decisions.tsv`, quoted with his words). (4) Other
docs. Owner addenda that matter most: #6 "agent status lives in aify-env ... comms and dashboard are consumers";
#7 "NO MONOREPO (including no env+wrapper merge)"; #11 "remove only where the counterpart exists AND is used";
#4 dashboard = second brain, "should CONSUME agent status ... not own it or control the fleet ... not 'make
dashboard the only UI'"; #5 comms = message store-and-deliver incl. its own UI on :8801; #2 on-demand, not time-based.

Abbreviations: comms = aify-comms, env = aify-env, wrapper = aify-wrapper, dash = aify-dashboard,
apg = aify-project-graph, AS = agents-setup. `D:` = comms DECISIONS.md entry, `TA` = comms docs/TARGET_ARCHITECTURE.md,
`RM` = comms docs/ROADMAP.md, `dtsv` = dashboard docs/decisions.tsv.

---

## 1. How it is supposed to work now (one page)

### 1.1 Components and responsibilities

| Component | Is (owner, ROLES 2026-11) | Runs where | Key current facts from the records |
|---|---|---|---|
| **aify-wrapper** | PLATFORM: launches any service's agents with identity + MCP config; must not know specific services | host, launchers on PATH (`claude-aify`, `codex-aify`, `hermes-aify`, `pi-aify` deprecated, `herdr-aify`) + bins (`aify-wrapper-check`, `-install`, `-connect`, `aify-turn-start`) | templates `wrappers/*.sh.in` rendered at install, bake the registry + a fingerprint (REGISTRY.md, 2026-10-29); export `AIFY_AGENT_ID/RUNTIME/AGENT_ROLE/SESSION_MODE` (contracts/dashboard-launch-env.md, 2026-10-28); read the agent's definition as defaults (0.8, RM); one-live-instance lease `~/.aify/agents/<id>.json` (D: 2026-09-14); instructions-at-launch into `~/.aify/sessions/<id>/` (env docs/0.9/instructions-at-launch.md, approved 2026-10-28); Claude channel shipped as wrapper plugin `aify-channel@aify-local` (D: 2026-10-22); turn-start runner asks every service declaring `turnStart` (contracts/dashboard-turn-start.md 2026-10-28) |
| **aify-env** | PLATFORM: host truth — processes, PTYs, spawns, the agent REGISTRY and AGENT STATUS served to all services; must not carry consumer-specific logic/plugins | host, one daemon per host on 127.0.0.1 (bin/aify-env.mjs:122) | only spawner since Phase 8 (2026-08-25, D: "Three repos"); one writer of `~/.aify/agent-definitions/<id>.json` (D: 2026-09-30, 0.8); derives agent state (`lib/agent-state.mjs`) and publishes it to every registry entry with `agentState` (lib/agent-state-sender.mjs; contract AGENT-STATE-DASHBOARD-v1.md 2026-10-28); alone decides "busy" (D: 2026-10-20); announces `reportEveryMs` (D: 2026-10-21); hermes gateway host since 0.9.10 (TA gap 4); launch roots `~/.aify/launch-roots.json` (README 2026-11-02); guided install chain `aify-env install` (README); TUI + `aify-env doctor` that relays service self-reports (TA). **Today still carries** `lib/plugins/aify-comms` (6,359 LOC) and `lib/plugins/aify-dashboard` (3,739 LOC) |
| **aify-comms** | Message store-and-deliver: owner's comms UI (:8801), history, delivery, statistics, work loop message->run->reply; must not re-derive process/agent state, carry launcher/harness knowledge, or grow knowledge features | container (`aify-comms-service` :8800 API, `aify-comms-dashboard-next` :8801 UI, optional Caddy :8443) + host-side stdio bridge copied to `~/.aify-comms` | messaging, dispatch/runs, channels, files; a copy of each host's definitions (D: 2026-09-30); status word = aify-env's via `operational_view` (D: 2026-10-20, 0.9.8 switch live 2026-10-23); comms tools over `/mcp/http` with caller in header (0.9.11, live 2026-10-27); stdio bridge stays for the Claude channel, the hermes delivery loop and Codex (TA gap 1); single uvicorn worker (D: 2026-06-18, CLAUDE.md 2026-11-02); `aify-comms` on PATH is only a verifier (TA) |
| **aify-dashboard** | Second brain: project knowledge (super-LSP, graph context, queryable KB) + owner<->agent questions/events; must not control the fleet or own agent status (consumes it) | container :9700 (`BIND_HOST 0.0.0.0`) + own Caddy :9743; bridge in session (stdio or `/mcp/http`, v1.12) | projects, State page, tasks (title+link for external trackers, dtsv 2026-10-25), documents hidden (docs live in repos, dtsv 2026-10-25), authored graph layers + feature map, questions, events ("Rules"), cron, secrets injection, file activity receiver; receives aify-env agent-state push. **Today also** has Agents tab with start/stop/restart/kill/spawn via comms (v1.3, GUIDE.md 2026-10-29) and a comms directory poll |
| **aify-project-graph** | Code-structure producer (graph, briefs, code_intel) feeding the knowledge layer; must not be a competing "orient me" entry point | host: per-runtime MCP stdio server + CLI; run as "provider" by env's dashboard plugin | split agreed 2026-09-20 (DESIGN-APG-OWNERSHIP, PLAN-2026-09-20-dashboard-seam): apg answers "what does this code do"; dashboard owns "what do we intend"; feature/task truth moves to dashboard, apg reads an opt-in export. **Export never built**: apg still reads/writes `.aify-graph/functionality.json` (overlay/loader.js:34, features-cmd.js:7) and its README/AGENTS.md (2026-09-20 / 2026-10-05) still teach it as the source |
| **agents-setup** | Owner's doctrine, skill selection, one machine bootstrap; no hand-restated skill lists, no competing installers | Steven's repo, `bootstrap/SKILL.md` run by an agent | soul docs rendered by `generate.py` into `~/.claude/CLAUDE.md` / Hermes `SOUL.md` managed blocks; skills via `~/.agent-skills/src` clones + `enabled/` symlinks into both harnesses (README 2026-11-02); `check.py` 3,500-token always-loaded budget; `drift.py` |

### 1.2 Shared state on the host (`~/.aify`)

- `services.json` — service registry, v1 schema (wrapper docs/REGISTRY.md 2026-10-29). Written by each service's
  installer **or** `aify-wrapper-connect` from a service spec (connect is the guided-install S2 path); read by the
  wrapper at install (never at launch) and by aify-env at start and on change. Fields: `endpoint`, `endpointEnv`,
  `mcp`, `mcpTransport`, `sessionInject`, `turnStart`, `advertise` (env), `agentState` (env), `*CredentialRef`.
- `agent-definitions/<id>.json` — desired state per agent, one writer (aify-env store). Services hold pushed copies.
- `agents/<id>.json` lease (one live instance), `sessions/<id>/` (instructions, mcp.json; removed by events),
  `env-processes.json` (owned processes, reaped by next daemon), `launch-roots.json`, `tui.json`, `config.json`
  (per-plugin settings, e.g. `plugins.aify-comms.autoConfirm`), credential store (`credentialRef` files).

### 1.3 Data flows as intended now

**Install / connect (TA 2026-11-02; env README; comms install.sh:1890).** Backend path = the service container.
Client path = aify-env + aify-wrapper, nothing from comms in the target. Today `aify-comms/install.sh --client X`
hands off by default to `aify-env install` (scripts/chain-handoff.sh), which acquires the approved artifact,
runs `aify-wrapper-connect`, renders launchers, applies service assets (skills, hooks) and calls comms' install.sh
back in `--acquire-only/--assets-only`. `--standalone` keeps the old path. Dashboard has its own
`npm run register|install-agent`; apg has six manual guides; AS has its own bootstrap.

**Agent definition (D: 2026-09-30, 0.8; RM).** Operator edits via `aify-env agents set` (or a service edit becomes a
change request). aify-env pushes definitions to each service (every minute and after a change) and claims change
requests (every 10 s) — env README 2026-11-02. A defined agent's Edit in comms is one definition request (D: 2026-10-28).
Moving an agent between machines = remove there, define here (D: 2026-10-19 Q6).

**Agent launch.** Managed: a service asks (comms spawn/lifecycle request) -> aify-env's comms plugin claims ->
runs the launcher **file** with structured argv (`terminal_sessions.argv`, D: "Three repos") inside a PTY it owns ->
launcher takes identity/definition, takes the lease, writes the session folder, builds MCP config from the baked
registry (+ `sessionInject` services, `/mcp/http` where `mcpTransport: http`), execs the runtime with
`--dangerously-skip-permissions`/`--yolo`/codex bypass by default (`--safe` opts out; claude-aify.sh.in:600).
Resident: operator runs `claude-aify --aify-agent X` in a terminal or Herdr pane. Start intent decides replace vs
refuse (`AIFY_START_INTENT`, D: 2026-09-14). Explicit START needed to replace a live one; automatic starts exit 75.

**Message -> run -> reply (comms ARCHITECTURE.md 2026-10-27; D: 0.9.8 2026-10-16; D: 2026-10-22).**
`comms_send` / `POST /messages/send` stores the message (nonce-deduped). With `trigger`, a dispatch run is queued
for the recipient. Delivery holds while aify-env says busy or unknown (`operational_view.claim_decision`); an
aify-env not reporting (3 missed `reportEveryMs`) -> recipient `unknown`, messages queue. Claims are long-poll:
Claude via its channel (`claude-channel.js`, plugin route), hermes via the bridge delivery loop
(`hermes-delivery-loop.mjs`), Codex via the tool bridge inside codex-aify through the app-server
(0.9.11 plan table). A recipient that is not running is cold-started by asking aify-env; if it cannot be woken the
message is kept in its inbox with a "wake failed" notice (D: 2026-10-16). The reply (`comms_send inReplyTo`) closes
the run; reply reminders and stranded-reply failure wait for idle (D: 2026-10-21). A stop fails queued work and
leaves it unread in the inbox (D: 2026-10-22). Steer/interrupt are message-backed. Dashboard (:8801) chat sends as
`dashboard` = the operator.

**Agent status (D: 2026-10-18/19/20/21/24/26; ADDENDUM 6).** Inputs on the host: turn hooks (Claude
`UserPromptSubmit/PostToolUse/Stop/SessionStart`, Codex, hermes `pre_llm_call/on_session_end`) post to aify-env
through `agent-state-event.mjs` (still a comms bridge file installed into `~/.claude/settings.json` by comms'
installer, TA gap 4); process truth (owned PTYs, leases); screen observation for managed PTYs (Herdr screen rules
vendored in `lib/plugins/aify-comms/screen-rules.mjs`, permitted by the 2026-09-14 amendment). aify-env derives one
record per agent and pushes snapshots/changes/unavailable to every registry entry with `agentState`
(comms `/api/v1/agent-state`, dashboard `/api/v1/agent-state`, one key per service: `API_KEY` in
`x-aify-agent-state-key`, dtsv 2026-10-14). comms projects the word in `operational_view` and adds only what it
owns: its stop records, `misconfigured` (outside launch roots), `offline` for a resident with no wake handle
(D: 2026-10-18 Q3), `blocked` when the model usage pool is exhausted (D: 2026-10-26). Pushed and polled status
come from one view (Q4). A silent publisher key yields to a fresh peer after 3 intervals (D: 2026-10-24).
Model actually running: read on demand via a host long-poll, 3 s wait (D: 2026-10-12, "time based stuff is bad").

**Lifecycle (D: 2026-10-11, 2026-10-28; dash DESIGN-LIFECYCLE).** Start/stop/restart/kill/spawn/delete are
requests stored in comms (`POST /agents/{id}/lifecycle-requests`, `/spawn-requests`), claimed and executed by
aify-env's comms plugin. Delete needs `OPERATOR_KEY` only when one is set. Every UI "Stop" is a kill; "Hold" is
aify-env's `stop`, released with `release` (0.9.15). Surfaces today: comms :8801 drawer, dashboard Agents tab
(through comms, `requestedBy aify-dashboard`), aify-env TUI menu, agents' `comms_*` lifecycle tools.

**Project knowledge (dash PLAN/DESIGN-PRODUCT, DESIGN-APG-OWNERSHIP; ADDENDUM 4).** Agents register a project
folder when asked; the turn-start runner fetches the dashboard brief at session start / after compaction
(v1.10). Dashboard tools (`dashboard_*`, v1.7-v1.8: bounded reads, one-call briefing, search, context, activity).
Code facts come from apg: aify-env's dashboard plugin watches registered folders' git HEAD, reports git facts, file
lists and signals, and runs the provider (apg) on the host (`providerCheckout` granted by Steven 2026-10-18);
the dashboard's code-index subgraph feed is live (PLAN status). Authored graph layers + feature map live in the
dashboard DB (G3: "feature truth lives in the dashboard, with an opt-in export"). File activity: wrapper observer ->
aify-env shipper -> dashboard receiver (v1.5/1.6, live 2026-10-25 for Claude). Agents ALSO still have apg's own
MCP server with `graph_*` verbs, briefs in `.aify-graph/`, and functionality.json/tasks.json overlays.

**Owner questions / events (dash DESIGN-PRODUCT §4; dtsv 2026-10-25/26; ROLES).** Agent asks with
`dashboard_question_ask`; owner answers on a Questions tab; the answer reaches the agent at its next turn start, or
wakes it through comms when installed. Events = trigger (turn start, schedule, file changed, node marked, task
changed) + scope + action; agents may create events in their own project (or a named one), environment-wide events
are the owner's. Comms separately teaches agents to send the operator `comms_send(to="dashboard", ...)`
(comms SKILL.md:113) — the second inbox the audit flags.

**Skills / install of agent guidance.** AS bootstrap installs third-party + Steven's skills via symlinks and renders
soul docs; comms `install.sh` copies `.claude/skills/aify-comms*` (and the byte-identical `.agents/` mirror) into
harness homes; dashboard ships `skill/` + AGENT-GUIDANCE.md generated from server instructions (pasted into Hermes
SOUL.md); apg deploys four skill trees; aify-env's chain "applies the service's assets". Doctrine (AS
skills-doctrine.md, 2026-08-19): description is a trigger, CLAUDE.md is a pointer to AGENTS.md, prune as hard as
you install, never keep a copy where a pointer will do.

### 1.4 Contracts between components (where each is written down)

| Contract | Producer -> consumer | Where | Status |
|---|---|---|---|
| Service registry `services.json` v1 | services/connect -> wrapper, env | wrapper docs/REGISTRY.md (2026-10-29) | current; no `skills` field |
| Launcher inputs `HARNESS_*` (six) and markers `HARNESS_WRAPPER_VERSION`, `HARNESS_REGISTRY_FINGERPRINT` | installer -> launcher; launcher -> env/check | comms docs/superpowers/specs/2026-08-19-harness-wrapper-contract.md; wrapper README "The contract" | current; spec lives in comms, not wrapper |
| Launch env exported to service bridges | wrapper -> dashboard bridge (and comms bridge) | wrapper docs/contracts/dashboard-launch-env.md (2026-10-28) | current; consumer-named |
| Turn-start request | wrapper runner -> any service with `turnStart` | wrapper docs/contracts/dashboard-turn-start.md (2026-10-28) | current; consumer-named |
| aify-env HTTP (processes, output SSE, input, resize, lifecycle reservations) | env -> plugins, attach, services | env docs/PROTOCOL.md (2026-10-24) | current |
| Agent-state publication (envelope C5, record C1) | env -> comms, dashboard | comms plans/2026-10-02-aify-env-owns-agent-state-P0.md; env docs/AGENT-STATE-DASHBOARD-v1.md (2026-10-28) | current; schema copies in comms, env, wrapper "change together" (D: 2026-10-21) |
| Definitions push / change requests | env plugin <-> comms (and dashboard plugin) | comms plans/2026-09-30-aify-env-owns-the-agents-P0.md | current, timer-driven |
| Lifecycle + spawn requests | dashboard/comms UI/agents -> comms -> env plugin | comms routes; dash DESIGN-LIFECYCLE (2026-10-12) | current |
| Environment advertisement | env -> services (every 30 s) | comms docs/ENVIRONMENT_ADVERTISEMENT.md; env README | current, timer |
| File activity v1 | wrapper spool -> env consumer -> dashboard | wrapper docs/FILE-ACTIVITY-SPOOL-v1.md, env docs/FILE-ACTIVITY-CONSUMER-v1.md, dash docs/FILE-ACTIVITY-CONTRACT-v1.md | current |
| apg <-> dashboard seam (stamp, signals, resolve, subgraph; feature export) | apg via env plugin -> dashboard | dash DESIGN-GRAPHS / DESIGN-APG-OWNERSHIP; apg PLAN-2026-09-20-dashboard-seam.md | partial (export not built) |
| comms MCP tools | comms -> agents (`/mcp/http` + stdio copy) | comms service/sse + mcp/stdio, parity test | migrating (Codex still stdio) |

---

## 2. Decision ledger

Status legend: CURRENT; SUPERSEDED by X (date); PARTIAL (migration in progress: what remains); CONFLICTS (with owner
statement in ROLES.md / ADDENDA — owner wins). Dates: decision date as recorded / file last-commit date.

### 2.1 Agent status, turn-end detection

| Decision | repo:file | Date (rec / commit) | Status |
|---|---|---|---|
| Status is proof-based; `derive()` over `agent_status_state` is sole authority; wrapper signals turns, comms reflects | comms:DECISIONS.md "Status is proof-based" | 2026-06-18 / 2026-10-28 | SUPERSEDED by 0.9.8 switch (2026-10-23); derive() deleted 0.9.9 S3b, tables S3c (banner in entry) |
| Delivery gates read raw `turn_busy` with one 30-min ceiling | comms:DECISIONS.md "Delivery gates read raw turn_busy" | 2026-07-26, rev 2026-08-30 | SUPERSEDED (0.9.8; code deleted 0.9.9 S3b) |
| Turn state re-asserted from process truth (KEEP-FRESH/KEEP-CLEARED) by bridge detectors | comms:DECISIONS.md 2026-07-13 | 2026-07-13 | SUPERSEDED in practice (bridge detectors deleted 0.9.9 S2b); entry not marked |
| Turn hooks per runtime (claude PostToolUse re-asserts start, stop gate) | comms:DECISIONS.md 2026-06-18 hooks | 2026-06-18 | PARTIAL: hooks still installed by comms install.sh, now post to aify-env only (0.9.9 S2b); hook script `agent-state-event.mjs` should move to launchers (TA gap 4) |
| Turn-state hooks run async, ordered by `firedAtUs` (service ordering) | comms:DECISIONS.md 2026-09-26 | 2026-09-26 | PARTIAL: async hooks current; comms ordering deleted 0.9.9 S2 (entry says so) |
| **comms never times turns; aify-env alone decides busy**; comms queues when env not reporting | comms:DECISIONS.md | 2026-10-20 (Steven "jeah") | CURRENT; aligned with ADDENDUM 6. Remnant: comms still closes Claude/Pi terminal runs from screen text (`reconcilers/terminal_runs.py:200` `_close_idle_claude_terminal_run_without_reply`, called from `managed_workers.py:427` and via `routers/agents/identity.py:41`) |
| aify-env's remaining 30-min fallback for unverifiable processes is env's to remove ("weird") | comms:DECISIONS.md 2026-10-20; env lib/agent-state.mjs:13 `STRICT_TURN_MS` | 2026-10-20 | PARTIAL: still in env |
| Not-reporting env -> `unknown`, not `offline` (Q1); missing-handle resident -> `offline` (Q3); push and poll from one view (Q4) | comms:DECISIONS.md | 2026-10-18 | CURRENT |
| env announces `reportEveryMs`; comms derives "not reporting" (3 intervals) | comms:DECISIONS.md | 2026-10-21 | CURRENT; but `MINIMUM_AIFY_ENV_VERSION = "0.9.0"` (tier-version-check.mjs:41) predates it (env sends it from 0.9.5) |
| Stale publisher keys yield after 3 own intervals to a live peer | comms:DECISIONS.md | 2026-10-24 | CURRENT in comms only; dashboard receiver has no yield |
| Reply reminders wait for idle | comms:DECISIONS.md | 2026-10-21 | CURRENT |
| Usage-limited agent reads `blocked`, from comms' own pool reading | comms:DECISIONS.md | 2026-10-26 | CURRENT, but a comms-side overlay on env's word; borderline vs ROLES "comms must not re-derive agent state" (usage is comms "statistics"; owner has not ruled) |
| aify-env derives state and publishes "as a SHADOW: the service's status still governs" | env:README.md lines 7-9 | 2026-10-13 (blame) / file 2026-11-02 | SUPERSEDED by the switch (2026-10-23) — stale text in a current file |
| "Status is still not here ... turn status belongs to the service" | env:README.md:209 | 2026-10-02 (blame) | SUPERSEDED by 2026-10-20 / ADDENDUM 6 |
| aify-env owns agent state; pushes to every service that opts in with its own registry field; dashboard needs no comms connection for status | comms:docs/ROADMAP.md "After 0.8" item 1; plans/2026-10-02-aify-env-owns-agent-state.md | 2026-10-02 / 2026-11-02 | CURRENT design; matches ROLES ("served to all services") |
| Dashboard agent-state receiver with its own `AGENT_STATE_KEY` | dash:DESIGN-AGENT-STATE-RECEIVER.md | 2026-10-05 / 2026-10-24 | SUPERSEDED on the key by dtsv 2026-10-14 (one key per service); rest of design stands; body unchanged under banner |
| Status vocabulary has ONE owner per language (`VALID_STATUSES` + `status.js` bound by a test) | comms:DECISIONS.md | 2026-07-31 | CURRENT in comms code, but ROLES/ADDENDUM 6 make aify-env the owner of status words; comms-side "owner" is now a consumer copy |
| A resident claude with background shells reads `shell` | comms:KNOWN_ISSUES.md | 2026-10-01 ask | PARTIAL: managed only; residents need env's count (D10) |

### 2.2 Spawn / lifecycle / definitions ownership

| Decision | repo:file | Date | Status |
|---|---|---|---|
| Three repos: comms = messaging, dispatch, sessions, **agent status**; wrapper = launchers; env = processes/PTYs | comms:DECISIONS.md "Three repos"; plans/2026-08-20-three-repo-separation-roadmap.md table | 2026-08-20 / 2026-10-14 | PARTIAL: "agent status" moved to env (2026-10-20); roadmap table row stale |
| aify-env is the only spawner; no fallback | comms:DECISIONS.md; TA | 2026-08-25 | CURRENT |
| One live instance per agent per host; replaced only on explicit start | comms:DECISIONS.md; wrapper README | 2026-09-14 | CURRENT |
| aify-env owns the agents (definitions); services hold a copy; edits become change requests | comms:DECISIONS.md; RM "Shipped in 0.8" | 2026-09-30 | CURRENT (timer-driven push 60 s / claims 10 s) |
| aify-env starts, stops, restarts agents from definitions; comms decides a message should wake one, and asks | comms:RM "After 0.8" item 1 | 2026-10-02 | PARTIAL: starting a defined agent still requires env's aify-comms plugin and comms-stored lifecycle requests (`lib/plugins/aify-comms/agent-starter.mjs`, `lifecycle-sync.mjs`) |
| Operator key locks only irreversible actions (delete); kill/stop/start/spawn under API key | comms:DECISIONS.md | 2026-10-11 | CURRENT |
| Every dashboard Stop is the kill; Hold asked for by name | comms:DECISIONS.md | 2026-10-28 | CURRENT |
| A stop fails queued work, keeps it in inbox; dead env keys handled automatically | comms:DECISIONS.md | 2026-10-22 | CURRENT |
| Compaction = runtime's own command or fresh-session handoff | comms:DECISIONS.md | 2026-09-27 | CURRENT |
| Hermes gateway host moves to aify-env | comms:TA gap 4; plans/2026-10-26-0.9.10-hermes-host-to-aify-env.md | 2026-10-26 | CURRENT (done 0.9.10); resume marker + hermes delivery client stay in comms bridge (5,486 hermes-named LOC) |
| Instructions at launch (definition `instructions` -> `--append-system-prompt-file`) | env:docs/0.9/instructions-at-launch.md | approved 2026-10-28 | CURRENT (built: wrapper lib/session-folder.mjs); hermes refused (exit 78); canary pending |
| Launch roots enforced by aify-env | env:README | 2026-11-02 | PARTIAL: enforced in the comms plugin path, not core spawn path (finding launch-roots-bypass-post-processes) |

### 2.3 Operator UI(s)

| Decision | repo:file | Date | Status |
|---|---|---|---|
| Dashboard Next is the only (comms) dashboard; legacy monolith retired | comms:DECISIONS.md | 2026-07-15 | CURRENT (scope: comms' own UI) |
| "aify-comms is for communication; aify-dashboard is for management — tasks, projects, understanding" | dash:DESIGN-PRODUCT.md header | 2026-09-20 / 2026-10-26 | CURRENT in spirit; consistent with ROLES once "management" excludes fleet control |
| Q14: replace comms' dashboard eventually, or live beside it -> "Beside it, then revisit" | dash:PLAN.md open questions | 2026-09-14 / 2026-10-28 | CURRENT as recorded; ROLES now settles it: comms UI :8801 stays the owner's comms UI |
| Lifecycle controls belong in the dashboard (later) — "reverses the earlier no-controls position; the operator uses the dashboard away from the PC" | dash:decisions.tsv | 2026-10-05 | **CONFLICTS** with ROLES/ADDENDUM 4 (dashboard must not control the fleet) -> superseded by owner 2026-11 |
| Dashboard lifecycle via comms 0.9 under `requestedBy aify-dashboard`; Kill/Delete key rules | dash:decisions.tsv; DESIGN-LIFECYCLE; PLAN v1.3 | 2026-10-11, 10-13 | **CONFLICTS** with ROLES (built, shipped v1.3; to be handed back) |
| GUIDE: dashboard = "the record and the management ... and the agent controls"; two pages offer start/stop | dash:docs/GUIDE.md:25-32 | 2026-10-29 | **CONFLICTS** with ROLES |
| aify-env TUI may start/stop/attach agents | env:README "What the view shows" | 2026-09-08+ | CURRENT (host tier; ROLES gives env processes/spawns) |

### 2.4 Dashboard vs project-graph ownership (feature map, tasks, graphs)

| Decision | repo:file | Date | Status |
|---|---|---|---|
| apg = multi-layer map (code, tasks, files, functionality, docs) | apg:docs/THE-GOAL.md | <=2026-09-04 | SUPERSEDED in part by 2026-09-20 split (intent layers move to dashboard) and ROLES (apg = code-structure producer) |
| apg as "knowledge system for AI agents" | apg:docs/PLAN-agent-knowledge-system.md | 2026-09-08 | SUPERSEDED by 2026-09-20 split + ROLES (dashboard is the knowledge layer / front door) |
| Split: apg answers "what does this code do"; dashboard owns intent records; features + tasks truth -> dashboard DB, apg reads opt-in export; `apg features confirm` becomes export-aware | dash:DESIGN-APG-OWNERSHIP.md; apg:PLAN-2026-09-20-dashboard-seam.md; dtsv 2026-09-19/20 (G3) | 2026-09-20 | **PARTIAL (stalled)**: dashboard feature map exists (171 nodes, 118 anchors, PLAN status); no exporter in dashboard (grep `functionality` in server/src: none); apg still reads/writes `functionality.json`; apg README/AGENTS still teach the overlays as truth |
| A1: feature export opt-in per project | dash:PLAN.md A1 | 2026-10-01 | OPEN (owner call) |
| A2: apg's visual dashboard retires only after the owner has used the replacement and said so | dash:PLAN.md A2; DESIGN-APG-OWNERSHIP §7 | 2026-09-20 | OPEN; 2D/3D rebuilt in dashboard (2026-10-16/19); `graph_dashboard` still in apg default set |
| Graph = one automatic base layer (provider) + N authored layers in dashboard, symbolic anchors | dash:DESIGN-GRAPH-ARCHITECTURE.md; dtsv 2026-09-20 | 2026-09-20 | CURRENT |
| apg runs only as library/CLI called by env's dashboard plugin, no standing service (G4) | dash:PLAN.md G4 | 2026-09-19 | CURRENT-ish: provider runner in env plugin; apg also still runs as per-session MCP server for agents |
| git-hook reindex and code-intel scheduling move to aify-env (conditions) | dtsv 2026-09-20 | 2026-09-20 | PARTIAL (env head-watcher/sweep-runner exist; apg hooks remain supported) |
| No tracker integrations; external task = title + link | dtsv 2026-10-25 | 2026-10-25 | CURRENT (supersedes v1.4 plan of one source per tracker; PLAN "After 1.0" item 7 still lists ClickUp/Plane sources -> stale row) |
| Documents live in the repo; dashboard document tools hidden | dtsv 2026-10-25 | 2026-10-25 | CURRENT |
| Conversation memory / search by meaning later; transcript storage NOT decided | dtsv 2026-10-25; ROLES | 2026-10-25 / 2026-11 | CURRENT ("keep comms thin") |

### 2.5 Registry, connect, plugins in aify-env, launchers' service knowledge

| Decision | repo:file | Date | Status |
|---|---|---|---|
| One config, many readers: `~/.aify/services.json`; each service's installer writes its own entry | comms:three-repo roadmap; wrapper REGISTRY.md | 2026-08-20 | PARTIAL: `aify-wrapper-connect` added as writer (guided install); dashboard's own register still writes without the lock (REGISTRY.md 2026-10-29) |
| Registry read at install, never at launch; launcher bakes + fingerprints | wrapper:REGISTRY.md | <=2026-08-19 / 2026-10-29 | CURRENT |
| `--service` defaults to `aify-comms`; paths `~/.aify-comms` via `--bridge-dir` | wrapper:README.md:176-192 | 2026-08-25 | **CONFLICTS** with ROLES (wrapper must not know specific services) — leftover |
| "aify-env ... has no such coupling at all ... every mention of aify-comms in it is a comment" | wrapper:README.md:190-192 | 2026-08-25 | SUPERSEDED by fact: env now ships `lib/plugins/aify-comms` (6,359 LOC) |
| Host core knows no service; a SERVICE PLUGIN may know its service; daemon reaches it by capability | comms:docs/AIFY_ENV_BOUNDARY.md "plugin carve-out" | 2026-09-08 / 2026-10-26 | **CONFLICTS** with ROLES (env must not carry consumer-specific plugins) |
| Screen knowledge may live in `lib/plugins/<service>/`; host core stays PTY-only, never what a screen means | comms:AIFY_ENV_BOUNDARY.md "Screen knowledge — amended" | 2026-09-14 (owner) | **CONFLICTS** with ROLES on the plugin location; ROLES gives agent STATUS to env, wrapper owns harness knowledge -> screen rules are harness knowledge (wrapper data) run by env core. Owner should confirm which half of the 09-14 rule survives |
| "aify-env is the environment provider, with the controls and the plugins"; wrapper's "comms and dashboard modules switch on when the service exists"; wrapper may later split per harness | comms:DECISIONS.md 2026-10-20 (owner's role statement) | 2026-10-20 | PARTIAL/CONFLICTS: plugin-in-env and service modules in wrapper conflict with ROLES 2026-11; "wrapper stays one repo" still CURRENT |
| aify-env has a plugin per service that switches on when detected; wrapper can talk to a service directly | comms:RM "Shipped in 0.8" | 2026-09-30 | CONFLICTS with ROLES on plugins |
| aify-env dashboard plugin (git facts, provider runner, secrets fetch, file activity) | dash:DESIGN-INTEGRATION.md "The aify-env dashboard plugin" | 2026-09-19 / 2026-10-18 | CONFLICTS with ROLES on location (function itself is wanted) |
| Harness-driver semantics belong to aify-wrapper (library, not daemon) | comms:docs/HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md | 2026-08-29, "Status: PROPOSED ... Nothing here is built" | CURRENT direction (owner quote: "hermes is harness. aify-wrapper knows about harnesses"); not built; listed as "Live reference" in docs/README |
| `sessionInject`, `mcpTransport: http`, `turnStart`, `agentState` registry fields | wrapper:REGISTRY.md; comms mcp/stdio/service-spec.mjs:41-44 | 2026-09-19..10-29 | CURRENT |

### 2.6 Single-worker comms, auth/one-key, bind/LAN, bypass defaults

| Decision | repo:file | Date | Status |
|---|---|---|---|
| Live-status cache in memory; service MUST stay single-worker | comms:DECISIONS.md; CLAUDE.md | 2026-06-18 / 2026-11-02 | CURRENT (more singletons than the doc names: mirror, MODEL_READS, CHANGE_FEED, TERMINAL_OUTPUT_WRITES, longpoll...) |
| `API_KEY` opt-in; "the deployment's real perimeter is that the host is the operator's own machine"; port on every interface and CORS `*` are operator decisions | comms:DECISIONS.md "service key is opt-in"; KNOWN_ISSUES.md:647 | 2026-08-18, rev 2026-08-30 | CURRENT (owner-chosen); note owner uses other PCs: EXTERNAL_KEYS (2026-09-24), dashboard reached at 192.168.100.10 |
| OPERATOR_KEY off by default: "api key is that trust surface, external ones are basically blocked from doing serious actions" | comms:DECISIONS.md (SUPERSEDED note 2026-09-27) | 2026-09-27 | CURRENT |
| One key per service ("how many keys can one service have !!!! i do not use any of these") | dash:decisions.tsv | 2026-10-14 | CURRENT; secrets-fetch key kept separate |
| Remove the operator check when no key set ("Lets remove the check if it is not set") | dash:decisions.tsv | 2026-10-13 | CURRENT |
| Browser signs in by pasting API_KEY once (30-day session); dashboard used from LAN | dash:decisions.tsv | 2026-10-14 | CURRENT |
| aify-env binds 127.0.0.1; no inbound auth beyond loopback + browser guard | env bin/aify-env.mjs:122; comms RM security | — | CURRENT (open item in RM) |
| Every `*-aify` wrapper launches in no-prompt mode by default (`--dangerously-skip-permissions`, `--yolo`, codex bypass) "so a resident agent runs autonomously"; `--safe` opt-out | comms:docs/history/DECISIONS-archive.md (2026-06-03 entry); wrapper templates | 2026-06-03 | CURRENT in code; RM security "whether a resident codex auto-approves is decided (V0.2 A2)" still OPEN |

### 2.7 Testing, docs/evidence, skill size, repo structure, versioning

| Decision | repo:file | Date | Status |
|---|---|---|---|
| Wrapper: "The full run is on Linux"; Windows runs only Windows-only files | wrapper:README.md:444 | 2026-11-02 | CURRENT (newest testing statement; no owner "Windows-first" rule found anywhere) |
| comms suites all green before every commit (5 suites, run on owner's Windows host) | comms:plans/NOW.md | 2026-09-03 | SUPERSEDED/stale (NOW.md is a 2026-09-03 working log still in plans/) |
| Sandbox gate in container/WSL instead of live comparison ("time based logic again (i hate these)") | comms:DECISIONS.md | 2026-10-20 | CURRENT |
| "Evidence lives where the work lives, in the repo or the planning directory, never in temp" | AS:Steven_and_agents.md:120 (Hard constraints) | 2026-08-19 | CURRENT (owner doctrine) |
| "Evidence is the smallest thing that settles the claim ... A log nobody will reopen does not get committed" | AS:Steven_and_agents.md:87-88 | 2026-10-11 | CURRENT (newer; limits what evidence is kept) |
| Checks write output to an ignored folder; dated evidence only with `--record` | dash:decisions.tsv 2026-10-05; dash AGENTS.md "Evidence" | 2026-10-05 | CURRENT for new checks; older ones differ |
| "Nothing is deleted for being history: a finished-work record is evidence" | comms:docs/README.md | — | CURRENT repo policy (labels, not deletion) |
| Superseded DECISIONS entries live in docs/history | comms:DECISIONS.md:5, CLAUDE.md | — | PARTIAL: five inline SUPERSEDED bodies remain |
| Files: 400 lines = signal, 1000 = refactor (also functions) | AS:Steven_and_agents_personal.md "Size"; dash AGENTS.md (test-enforced 1000) | 2026-10-29 | CURRENT |
| Skills: description is a trigger; short enough to read cold; index cost counted; prune as hard as you install | AS:skills-doctrine.md §5, §7, §8 | 2026-08-19 | CURRENT; `check.py` BUDGET_TOKENS=3500 covers soul docs only (not skill index) |
| CLAUDE.md is a pointer to AGENTS.md (Hermes reads only AGENTS.md) | AS:skills-doctrine.md §2 | 2026-08-19 | CURRENT; comms inverts it (rules in CLAUDE.md) |
| Three repos (+dashboard, apg) | comms:DECISIONS.md 2026-08-20; CLAUDE.md "Three repos, one product" | 2026-08-20 / 2026-11-02 | CURRENT polyrepo; CLAUDE.md wording stale (five products) |
| NO MONOREPO, including no env+wrapper merge | owner ADDENDUM 7 (2026-11); ROLES principle | 2026-11 | CURRENT (owner) |
| wrapper consumed as npm dep pinned to a commit; version markers tested on producing side | comms:DECISIONS.md "Three repos" | 2026-08-20 | CURRENT; pins: comms+env `dd04bd4a`, dashboard `f1776048` (44 commits behind) |
| tier versions are "separate products on separate cadences"; doctor checks a MINIMUM | comms:mcp/stdio/tier-version-check.mjs:18 | — | CURRENT; minimum stale (0.9.0) |
| One `VERSION` file per repo, bump script writes all copies | comms:CLAUDE.md "Versioning" | — | CURRENT |
| Now: one tagged release of all three repos (env v0.9.10, wrapper v0.9.4, comms v0.9.16 next) | comms:RM "Now" | 2026-11-02 | SUPERSEDED same day by tags: comms v0.9.17 tagged (HEAD 0.9.18), env v0.9.11 tagged (HEAD 0.9.12-dev), wrapper HEAD 0.9.6-dev; comms still pins untagged `dd04bd4a` |
| Pi deprecated: support kept, tests off unless `AIFY_TEST_DEPRECATED=pi` | comms:DECISIONS.md | 2026-09-18 | CURRENT (loop still runs every deploy) |
| Dormant containers subsystem: kept or deleted is the operator's decision | comms:RM "Older goals" (V0.2 WS-10) | 2026-09-30 | OPEN (owner) |
| No source decides a location from a typed path ("we never know where user installs anything") | comms:DECISIONS.md | 2026-09-16 | CURRENT |
| Each component checks itself; no cross-service doctor; env doctor relays, never inspects | comms:three-repo roadmap; TA "Where each doctor lives" | 2026-08-20 / 2026-11-02 | CURRENT |

### 2.8 Transport and per-agent cost

| Decision | repo:file | Date | Status |
|---|---|---|---|
| Comms tools served once over Streamable HTTP, caller identity in a header; only local process = Claude channel; Hermes and Codex get a `url` entry | comms:plans/2026-10-26-0.9.11-one-local-comms-process.md; TA gap 1 | 2026-10-26 | PARTIAL: `/mcp/http` live 2026-10-27, `/mcp/sse` deleted; Codex keeps local tool process (docs/CODEX_CATCHUP.md); stdio bridge still makes agents pass ids in places (T1) |
| Dashboard tools over HTTP, one generic HTTP entry in wrapper (v1.12) | dash:PLAN.md v1.12; DESIGN-HTTP-TOOLS.md | 2026-10-28 | PARTIAL/CURRENT (dash at 1.12.1); stdio bridge kept for Codex/fallback |
| Four tool surfaces removed: fewer, clearer tools | comms:DECISIONS.md | 2026-09-27 | CURRENT |

---

## 3. In-flight migrations (what moved where, how far, what is left)

These explain most of the apparent duplication. For each: source -> target, evidence it moved, what remains.

**M1. Spawning and PTYs: comms -> aify-env.** DONE (Phase 8 flip 2026-08-25; env-bridge code deleted v0.6.3).
Remaining leftovers in comms: terminal row bookkeeping, console relay and screen snapshot rendering
(`routers/terminals.py` 751, `terminal_write_queue.py` 661, `terminal_snapshot.py` 555 — by design: "the service
only relays", D: "Dashboard console is a PTY the host tier owns"), dialog answering (`console_prompts.py`),
and run-closing from screen text (`terminal_runs.py:200`). ~1,800 lines of dead env-bridge modules noted in NOW.md
(2026-09-03) were deleted in v0.6.3.

**M2. Agent definitions: comms rows -> aify-env files.** DONE for storage (0.8, 2026-09-30). Remaining: the push is
timer-driven (60 s) and change requests are polled (10 s) by env's aify-comms plugin; comms still has
`definition_*` modules (~1.5k LOC) holding the projection and the request queue; pre-0.8 undefined agents still run
the old path (D: 2026-10-04).

**M3. Agent status / turns: comms -> aify-env.** DONE for the word (0.8 plan 2026-10-02, hard switch 2026-10-23,
deletions 0.9.9 S1-S3c 2026-10-25). Remaining: (a) the turn hook script `agent-state-event.mjs` lives in comms'
bridge and is installed by comms' installer (TA gap 4: "belongs with the launchers"); (b) screen-based observation
lives in env's aify-comms plugin (`screen-rules.mjs`, `screen-observer.mjs`), not env core or wrapper; (c) comms
still closes Claude/Pi terminal runs from screen text (`_close_idle_claude_terminal_run_without_reply`); (d) comms
receiver keeps its own mirror, ordering, staleness and yield (`agent_state_ingest.py`, `agent_state_mirror.py`,
`report_age.py`, `operational_view.py`, ~3.5k LOC) and the dashboard re-implements the same in TS
(`server/src/agent-state/*`, 516 LOC + order/feed rules) — one projection per consumer, not one served by env;
(e) env's 30-min fallback; (f) comms' 15 s `status_push.py` loop and dashboard's 30 s comms roster poll; (g)
`MINIMUM_AIFY_ENV_VERSION` not raised; (h) env README still says shadow.

**M4. Hermes gateway host: comms bridge -> aify-env.** DONE 0.9.10 (env chooses port, records gateway, reaps
orphans; launcher starts it). Remaining in comms bridge: hermes delivery loop, ACP/gateway protocol client,
session/resume marker (5,486 hermes-named LOC) — kept as "putting a message into a runtime session stays comms'
delivery" (TA gap 4), but by ROLES + HARNESS_KNOWLEDGE (PROPOSED 2026-08-29) the harness-driver half belongs in
wrapper. Not started.

**M5. Comms tools transport: stdio bridge per agent -> service `/mcp/http`.** PARTIAL (0.9.11): Claude and
Hermes on HTTP where `mcpTransport: http`; Claude keeps the local channel; Codex keeps its local tool process
(CODEX_CATCHUP.md); parity test holds two copies (JS bridge + `service/sse/`) equal meanwhile. Dashboard did the
same in v1.12. Left: Codex, delete the second tool copy, binary file tools.

**M6. Client install: comms `install.sh` -> aify-env install chain + aify-wrapper connect.** PARTIAL: plain
`install.sh --client` now hands off to `aify-env install` (scripts/chain-handoff.sh, install.sh:1890); comms'
installer still renders launchers through the pinned wrapper, writes hooks/skills/MCP config (2,101 lines, 49
inline `node -e` editors) and copies the bridge; `--standalone` keeps the old path. TA gap 1 and gap 2 (verifier
`aify-comms` / `aify-doctor` still on PATH). Dashboard and apg have their own install routes; AS bootstrap is a
fourth. Skill placement has no registry field.

**M7. Host doctor checks: comms doctor -> aify-env doctor.** DONE 0.9.7 (env-processes, context-window,
managed-orphans, claude-login moved; implemented inside env's aify-comms plugin `doctor-checks.mjs`,
`context-window-check.mjs`, `managed-orphans-check.mjs` — so the move landed in the plugin, not core).

**M8. Usage/quota readers.** OPEN: TA gap 4 lists `mcp/stdio/usage-collector.js` (bridge, host side) as host work
still in comms; but D: 2026-10-26 builds `blocked` on comms' own service-side pool reading
(`usage_openai.py`, `usage_anthropic.py`, `usage_pools.py`). Service-side usage = "statistics" (ROLES ok);
bridge collector = leftover. Not separated in any doc.

**M9. Feature/task truth: apg overlays -> dashboard DB with opt-in export.** STALLED: decided 2026-09-20, export
never built, apg docs unchanged since 2026-09-20/10-05, apg last commit 2026-10-19. Two feature stores and two
"orient me" entry points exist (dashboard brief + `dashboard_*`; apg `graph_health`/`graph_packet`/briefs) — this is
the stalled migration, not two independent designs.

**M10. apg scheduling/visuals -> aify-env plugin / dashboard views.** PARTIAL: env's dashboard plugin runs the
provider (head-watcher, sweep-runner, provider-runner) since 2026-10-18; dashboard 2D/3D built; apg's
`graph_dashboard` and its HTTP API (with `ACAO: null`) retire only after the owner says so (A2, open).

**M11. Service plugins: inside aify-env -> each service (owner ROLES 2026-11).** NOT STARTED. Repo docs still argue
the opposite (AIFY_ENV_BOUNDARY 2026-09-08 carve-out; D: 2026-10-20 role quote; RM 0.8). 10,098 LOC in
`lib/plugins/{aify-comms,aify-dashboard}`. Also launchers' `--service aify-comms` default and consumer-named
contract files in wrapper. This is the migration the platform findings describe; it is new direction, not a
regression.

**M12. Fleet control: dashboard -> comms UI / aify-env (owner ROLES 2026-11).** NOT STARTED, and reverses the
dashboard's own 2026-10-05/10-11 owner rulings and its v1.3 release (lifecycle ~704 LOC server + UI control-desk,
spawn-desk, controls; finding counts ~3,800 src / 4,850 test lines incl. roster/mirror).

**M13. Agent guidance placement: hand-pasted SOUL.md / per-service skill installers -> one composer.** OPEN (AS
findings). Dashboard asks for a hand-paste of AGENT-GUIDANCE into Hermes SOUL.md; comms copies its skill trees;
apg deploys four copies.

---

## 4. Contradictions between repos' docs (and which is right)

| # | Topic | Says A | Says B | Which is right |
|---|---|---|---|---|
| C1 | Who owns status | env README:7-9 "SHADOW: the service's status still governs" (blame 2026-10-13); env README:209 "turn status belongs to the service" (2026-10-02); three-repo roadmap table "aify-comms owns ... agent status" (2026-08-20) | comms D: 2026-10-20 "aify-env alone decides busy"; switch live 2026-10-23; ADDENDUM 6 | **aify-env** (owner). env README is stale in a file edited 2026-11-02 |
| C2 | Can aify-env carry service plugins | comms AIFY_ENV_BOUNDARY 2026-09-08 "a SERVICE PLUGIN may know its service"; D: 2026-10-20 "aify-env ... with the controls and the plugins"; RM 0.8; dash DESIGN-INTEGRATION "aify-env dashboard plugin" | ROLES 2026-11 "must not carry consumer-specific logic/plugins"; ADDENDUM 3 | **ROLES**. Docs record an earlier owner position |
| C3 | Does aify-env mention comms | wrapper README:190-192 (2026-08-25) "every mention of aify-comms in it is a comment" | env code: `lib/plugins/aify-comms/` 6,359 LOC | **code**; wrapper README stale |
| C4 | Who controls the fleet / which UI | dash dtsv 2026-10-05, 10-11; PLAN v1.3; GUIDE.md 2026-10-29 (dashboard = management incl. agent controls); cross-stack finding two-dashboards recommends dashboard as single UI | ROLES: comms owns owner's comms UI :8801; dashboard must not control fleet; ADDENDUM 4 "not make dashboard the only UI" | **ROLES**; dashboard docs superseded |
| C5 | Where feature/task truth lives | apg README (2026-09-20), AGENTS.md (2026-10-05): drop `.aify-graph/functionality.json`, edit `tasks.json`, `/graph-build-functionality` | dash DESIGN-APG-OWNERSHIP + apg's own PLAN-2026-09-20-dashboard-seam: truth in dashboard DB, apg reads an export | **dashboard** (agreed by both owners 2026-09-20; matches ROLES); apg docs not updated |
| C6 | apg's identity | apg THE-GOAL (multi-layer map incl. tasks/functionality), PLAN-agent-knowledge-system (apg = the knowledge system) | 2026-09-20 split; ROLES (producer, not front door) | **ROLES / split** |
| C7 | Number of repos | comms CLAUDE.md "Three repos, one product" (2026-11-02); TA never names dashboard/apg | five components exist and are integrated | five; CLAUDE.md/TA incomplete, not wrong about the three it names |
| C8 | Turn-state rules | DECISIONS 2026-07-13 KEEP-FRESH/KEEP-CLEARED, 2026-06-18 hooks (bridge detector still backstops) | 0.9.9 S2 deleted the detectors (plan 2026-10-25) | **0.9.9**; 07-13 entry unmarked |
| C9 | Dashboard agent-state key | DESIGN-AGENT-STATE-RECEIVER body decision 1 (own key) | its own banner + dtsv 2026-10-14 (API_KEY) | **dtsv 2026-10-14** |
| C10 | Release state | comms RM "Now" 2026-11-02 (env v0.9.10, wrapper v0.9.4, comms v0.9.16 next) | HEAD commits: comms v0.9.17 tagged, env v0.9.11 tagged, wrapper 0.9.6-dev | **git**; RM "Now" stale within the day |
| C11 | Dashboard status | dash PLAN.md status "2026-10-20 ... schema 50 ... 1.3.5", "design is split over nine files" | VERSION 1.12.1, schema 60, 19 DESIGN files | **code**; PLAN status block stale (body has v1.4-v1.12 rows) |
| C12 | Testing platform | comms NOW.md (2026-09-03) suites run on the owner's host; env/dashboard suites red on Linux | wrapper README 2026-11-02 "The full run is on Linux" | no owner rule either way; wrapper's is the newest written practice; dashboard runs in a Linux container |
| C13 | Install path | TA (2026-11-02): client path = env + wrapper only | comms install.sh still the client installer behind a handoff; dashboard `npm run install-agent`; apg 6 guides; AS bootstrap | TA is the target; others are migration state |
| C14 | Tracker integration | dash PLAN "After 1.0" item 7: ClickUp, Plane, GitHub, GitLab as task sources | dtsv 2026-10-25: no integrations, title+link | **dtsv 2026-10-25** |
| C15 | Who asks the owner | comms SKILL.md:113 `comms_send(to="dashboard")` = operator | dashboard owns questions (DESIGN-PRODUCT; ROLES) | **ROLES**: questions in dashboard; comms `to="dashboard"` = operator chat in comms UI, should not be taught for decisions |
| C16 | Screen knowledge location | AIFY_ENV_BOUNDARY 2026-09-14 owner amendment: core PTY-only, screen meaning in `lib/plugins/<service>/` | ROLES: env serves status; no consumer plugins; wrapper owns harness knowledge | unresolved: both owner statements; ROLES is newer -> plugin location is out; whether core or wrapper holds the rules needs the owner |
| C17 | Harness doc status | comms docs/README lists HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER as "Live reference" | the doc says "PROPOSED ... Nothing here is built" | direction current, design unbuilt |
| C18 | Monorepo | cross-stack finding stack-monorepo; comms CLAUDE.md "one product" | ADDENDUM 7 no monorepo; tier-version-check "separate products on separate cadences" | **ADDENDUM 7** |

---

## 5. Notes for auditors: findings that rely on a stale/superseded record or describe a migration as a defect

Classification: **STALE** = cites or argues from a superseded decision/doc; **CONFLICT** = recommendation conflicts
with a current owner statement or decision (needs reframing or explicit owner re-decision); **MIGRATION** = the
"defect" is an unfinished move with a recorded decision — frame as "finish the move", name the counterpart (ADDENDUM
11); **OK** = no issue found against intent. Only non-OK are listed in detail.

### 5.1 Strong flags (recommendation would undo or contradict current owner intent)

- **cross-stack `two-dashboards`** — CONFLICT/STALE. Recommends "aify-dashboard becomes the single operator UI, comms
  headless" and reads TA gap 4 "pure comms service" as "no UI in comms". TA gap 4 is about host work leaving comms,
  not the UI. ROLES + ADDENDUM 4/5: comms keeps its :8801 owner UI; dashboard drops fleet control. Re-scope to "hand
  fleet control back to comms/env; dashboard consumes status" (= SB-4 / three-agent-sources).
- **cross-stack `agent-state-two-receivers`** — CONFLICT. Fix says "comms already owns the projected status per
  DECISIONS 2026-10-20 ... dashboard reads comms' view; remove `agentState` from the dashboard's registry entry".
  D: 2026-10-20 says comms *reads* env's busy flag; ADDENDUM 6 and RM item 1 make env the server of status to every
  service. The DRY problem is real (two receivers re-implement ordering/staleness/yield); the intended remedy is one
  projection served by aify-env (or a shared receiver library from env), not routing dashboard through comms.
  perf-06 already says this ("contradicts ADDENDUM 6").
- **cross-stack `stack-monorepo`** — CONFLICT with ADDENDUM 7 (no monorepo, no env+wrapper merge). Drop; superseded by
  `platform-monorepo-reframe` (MERGE_PLAN #13).
- **aify-comms `status-vocab-three-owners`** — CONFLICT (partial). Makes comms' `service/contracts/vocabulary.json`
  the owner of agent status words. By ADDENDUM 6 agent status words are env's; comms owns run/message/session/
  terminal vocabularies. Keep the DRY point, change the owner for agent statuses. Also challenges the CURRENT
  D: 2026-07-31 binding; say so.
- **aify-comms `runtime-if-ladders-not-data`** — CONFLICT (partial). Puts a per-runtime capability contract in comms
  (`mcp/stdio/contracts/runtimes.json`). ROLES: comms must not carry harness knowledge; HARNESS_KNOWLEDGE doc
  (owner quote 2026-08-29) puts harness-driver semantics in aify-wrapper. Land the data in wrapper; comms consumes.
- **platform `platform-agent-status-screen-in-core`** — CONFLICT with the 2026-09-14 owner amendment ("aify-env's host
  core stays PTY-only ... never what a runtime's screen means"). ROLES removes the plugin location but does not say
  core; wrapper (harness owner) supplying the rules as data and env core executing them satisfies both. The finding
  notes the 09-14 comment; it must ask the owner, not assume core.
- **cross-stack `one-stack-bringup`** — CONFLICT (partial). "One aggregated doctor" contradicts the CURRENT rule
  "each component checks itself; no cross-service doctor; env relays" (three-repo roadmap 2026-08-20, TA). The
  compose/hairpin part is fine. Item 4 (revisit opt-in key at stack level) is an owner re-decision.
- **aify-dashboard `api-key-is-operator`** — CONFLICT (partial). Option B (require operator-key proof on lifecycle,
  spawns, question answers) adds key machinery the owner rejected ("how many keys can one service have !!!! i do
  not use any of these", dtsv 2026-10-14; "Lets remove the check if it is not set", 10-13), and lifecycle routes
  are slated to leave the dashboard (ROLES). Option A (honest single tier) matches the owner.

### 5.2 Decision-change requests (valid, but must say they reverse a CURRENT owner decision)

- **aify-comms `default-open-port-lan-console-rce`** — cites the decision correctly. Owner deliberately chose open
  port + opt-in key (2026-08-18/30, 09-27) and uses other PCs (EXTERNAL_KEYS 2026-09-24; dashboard at
  192.168.100.10, dtsv 2026-10-14). A loopback default breaks his multi-PC use unless `SERVICE_BIND`/`API_KEY` are set
  by the installer; frame as owner decision with that migration step.
- **aify-wrapper `bypass-default-for-residents`** — reverses the 2026-06-03 default ("so a resident agent runs
  autonomously"); RM keeps "whether a resident codex auto-approves" open. Owner call.
- **aify-comms `pi-loop-runs-untested`** — D: 2026-09-18 keeps Pi support. Gating the loop is compatible; deleting Pi
  reverses the decision (owner call).
- **aify-comms `legacy-template-scaffolding`** — RM: containers subsystem keep/delete is "the operator's decision (V0.2
  WS-10)". Say it is the pending owner decision; `make clean` part is independent.
- **Evidence/docs retention group** — `aify-dashboard evidence-tree-bloat`, cross-stack `docs-and-evidence-sprawl`,
  `aify-wrapper evidence-shipped-and-doc-sprawl`, `aify-env comment-and-evidence-sprawl`, `aify-comms
  docs-history-in-agent-path`, `aify-project-graph docs-and-scripts-sprawl`. Owner hard constraint "Evidence lives
  where the work lives, in the repo ... never in temp" (2026-08-19) vs newer "A log nobody will reopen does not get
  committed" (2026-10-11) and dtsv 2026-10-05 (ignored output, `--record`). Moving raw logs out / excluding from npm
  packages / archiving with `.rgignore` fits; "separate evidence repo", "release assets", "LFS" or deletion conflicts
  with the hard constraint and comms docs/README ("nothing is deleted for being history"). Recommend: trim to the
  smallest settling artifact, keep it in-repo, keep it out of packages and agent search paths.

### 5.3 Migrations described as defects (reframe as "finish the move"; name the counterpart)

- **aify-comms `idle-closers-scrape-tui-on-roster-poll`** — M3 remnant. Counterpart exists and is used: env's busy/idle
  in `operational_view.claim_decision`. Valid removal candidate under ADDENDUM 11 once run-settlement uses it.
- **aify-comms `install-sh-embedded-node`** — M6: install.sh's job is moving to the env chain (TA gap 1); refactoring 49
  editors in place may be wasted. Prefer moving them with the chain (the finding's step 4).
- **cross-stack `one-host-installer`** — M6, partly done (chain-handoff since guided install S2). Say what is already
  handed off.
- **cross-stack `per-session-mcp-cost`**, **tools `T1-comms-two-contracts-one-skill`**, **performance `perf-08`/`perf-09`**
  — M5 in progress (0.9.11, dash v1.12, CODEX_CATCHUP pending).
- **cross-stack `wrapper-pin-drift`** — versioning plan exists (RM "Now": tag all three, comms pins a tagged wrapper);
  dashboard pin is the real drift.
- **cross-stack `min-env-version-stale`** — follow-through of D: 2026-10-21; valid.
- **second-brain `SB-1`**, **tools `T2-two-graph-stores-two-entry-points`**, **skills `skills-two-orient-paths`**,
  **tools `T7-three-first-calls`** — M9 stalled migration with an agreed design (2026-09-20, G3, A1 open). Should cite
  that decision and ask only A1 (opt-in export), not re-open ownership.
- **aify-project-graph `doc-code-drift`** (README/AGENTS claims) — partly C5: apg docs describe the pre-split overlay
  ownership.
- **second-brain `SB-4`**, **aify-dashboard `three-agent-sources`** — M12. Aligned with ROLES, but the dashboard's own
  record has owner rulings for controls (dtsv 2026-10-05/10-11/10-13, GUIDE.md 2026-10-29). Cite ROLES as the
  superseding owner statement; three-agent-sources leaves controls as "decide in decisions.tsv" — ROLES already
  decided.
- **platform `platform-env-plugins-out-of-tree`**, **`platform-launchers-registry-only`**,
  **`platform-agent-start-in-core`**, **`platform-shared-host-helpers`**, **`platform-one-integration-contract`**,
  **`platform-skill-assets-by-harness`**, **agents-setup `env-skill-placement-seam`**, **skills
  `skills-registry-skill-distribution`**, **tools `T8-platform-tool-injection-contract`** — M11. Aligned with ROLES but
  contradict the repos' recorded rules (AIFY_ENV_BOUNDARY carve-out 2026-09-08, D: 2026-10-20, RM 0.8, wrapper README
  2026-08-25). Each should cite ROLES/ADDENDUM 3 explicitly so the next reader does not "correct" them back.
- **aify-env `launch-roots-bypass-post-processes`** — same M11 shape (enforcement lives in the comms plugin).
- **performance `perf-04`** — explicitly "finish moving process and terminal steps to aify-env's events": correctly
  framed.
- **aify-comms `docs-history-in-agent-path`** — partly STALE-detector (correct): DECISIONS keeps inline SUPERSEDED bodies
  contrary to its own header.

### 5.4 Minor notes

- **cross-stack `project-graph-into-contract`**: a registry entry with `sessionInject` would put apg's MCP surface in
  every session = strengthens the competing front door ROLES forbids. Keep the provider contract (env -> dashboard),
  not a per-session apg injection.
- **cross-stack `docs-and-evidence-sprawl`** cites comms CLAUDE.md "Three repos" and MULTI_SERVICE_STACK_TRACE as drift
  (correct, C7).
- **aify-comms `single-worker-state-understated`**, **`no-ci-for-5k-tests`**, **aify-env `linux-suite-red`**,
  **aify-dashboard `suite-fails-on-linux`**: no "Windows-first" owner rule exists; wrapper's newest README says the
  full run is on Linux (2026-11-02). OK.
- **skills `skills-comms-carries-platform`**, **tools `T5-two-operator-inboxes-dashboard-word`**, **skills
  `skills-two-ways-to-ask-owner`**, **agents-setup `comms-claude-md-inversion`**: aligned with ROLES and doctrine. T5/
  skills-two-ways depend on "aify-comms#26" (UI decision) — ROLES already decides it (comms keeps its UI; owner
  questions in dashboard).
- **platform `platform-monorepo-reframe`**: matches ADDENDUM 7.
- **performance `perf-02`/`perf-03`/`perf-05`/`perf-10`/`perf-11`**, **platform `platform-push-on-change`**,
  **second-brain `SB-5`**, **tools `T6-code-ask-poll-chain`**: aligned with ADDENDUM 2; the timers they name are
  CURRENT implementation choices, not recorded owner decisions (the owner has repeatedly ruled against time-based
  logic: 2026-10-12, 2026-10-20).
- All remaining ids (aify-comms compose-up-env-export-breaks, layer-rules-not-honoured, image-carries-unused-toolchain,
  comments-as-changelog, text-scan-tests, auth-skip-prefix-and-public-docs; aify-dashboard healthcheck-host-refused,
  malformed-cookie-500, docs-sprawl-drift, hardcoded-defined-causes, body-shape-defined-twice, no-retention,
  image-hygiene, stale-ssh-wrapper-devdep, owner-specific-default-roots, positional-migration-versions,
  missing-security-headers, complexity-vs-value; aify-env posix-*, two-kill-tree-implementations,
  dns-rebinding-get-routes, owned-record-not-atomic-or-locked, supersede-reap-is-one-shot,
  daemon-entry-tested-by-source-regex, process-identity-probes-triplicated, harness-vocabulary-hardcoded,
  readme-protocol-drift, install-not-reproducible; aify-wrapper all others; apg all others; cross-stack
  schema-defined-wire-contracts, cross-repo-ci, env-var-contract; agents-setup as-*; skills/tools others;
  second-brain SB-2, SB-3, SB-6, SB-7): no conflict with recorded intent found.

---

## 6. What aify-comms actually still does today (code volume)

Non-test source at 1c9611b, bucketed by file name (heuristic; `api_core/`, `reconcilers/`, `routers/` merged):
service Python 54,108 LOC; host bridge JS (`mcp/stdio`, excl. tests) 26,343; comms UI (`service/new_dashboard`,
excl. tests/vendor) 17,871; `install.sh` 2,101 + `scripts/` 2,657. Tests: bridge 59,869 lines; service tests ~7.4 MB.

| Area | Service (py) | Bridge (js) | Live job per ROLES? |
|---|---|---|---|
| Messages, dispatch runs, claims, delivery, replies, channels, inbox, files, records | 14,815 | 4,438 (claude channel 554, hermes delivery loop/run 1,191, dispatch loop 392, tools) | **LIVE core** (store-and-deliver, work loop) |
| Owner UI (:8801 Dashboard Next: chat, drawer, consoles, runs, analytics, environments) | — | — (17,871 UI) | **LIVE** (owner's comms UI) — includes spawn/lifecycle/console controls |
| Terminal rows + console relay + screen snapshot + dialog answering | 7,567 | 687 | Mixed: relay for the UI is live by decision; screen-text run closers (`terminal_runs.py`) and console prompt answering are leftovers of host/harness knowledge |
| Registration, sessions, bridges, environments, residents, bindings | 8,085 | 6,100 (incl. pi-session 994, codex-session 779, hermes-session 632) | Partly live (who is reachable for delivery); harness session handling = harness knowledge (wrapper by intent) |
| Lifecycle/spawn/definition request queues and projection, compaction, model reads | 7,192 | 595 | Delegation layer: requests are stored here and claimed by env's comms plugin. Live today, but the job (start/stop/define) is env's; shrinks when M2/M11 finish |
| Agent-state ingest/mirror/operational view, liveness, report age, status cache/push | 3,469 | 458 (turn hook script, heartbeats) | Consumer projection of env's word: live but duplicative (dashboard has a second); hook script belongs to wrapper |
| Usage/quota pools, analytics, stats | 1,878 | 940 (usage-collector 574) | Service side = "statistics" (live); bridge collector = host leftover (TA gap 4) |
| Harness runtime adapters (hermes/codex/claude/pi/opencode controllers, runtimes-*) | 732 | 8,703 | Leftover by intent (harness knowledge -> wrapper); still load-bearing for delivery into Codex/hermes sessions |
| Auth, settings, DB, schema, models, main, ntfy, doctor/verifier | 6,914 | 4,188 (doctor 1,426) | Infra: live; host-side doctor/verifier is TA gap 2 |
| Sweep loop and misc (sweep.py 397, recovery, workspace) | 2,502 | 203 | Timer-driven reconcile (34 steps, perf-04) |
| Container manager, GPU, `/route` proxy (template) | 954 | — | Leftover; keep/delete is an open owner decision |
| Pi-specific | 141 (`pi_resident_flip.py`) + branches | 2,196 | Deprecated (support kept, D: 2026-09-18) |

By harness name in the bridge: hermes 5,486, codex 2,431, pi 2,196, claude 1,593, opencode 341 LOC.

**Plain answer.** About 40% of comms' non-UI code (80.5k) is the live product the owner describes (message store,
delivery, runs and replies ~19k; owner UI ~18k; statistics ~2k; infra ~11k). Most of the rest is either a
delegation/projection layer whose decision-making already moved to aify-env (lifecycle/spawn/definition queues
~7.8k, agent-state projection ~3.9k, terminal/console relay ~8.3k of which only the UI relay is intended) or
harness knowledge that by owner intent belongs to aify-wrapper (adapters and per-harness session/delivery code
~9-15k, incl. deprecated Pi ~2.3k). Clear leftovers with no current job: container/GPU/proxy scaffolding (~1k),
screen-text run closers, the bridge usage collector, the host-side verifier/doctor, `install.sh`'s client path
once the env chain owns it.
