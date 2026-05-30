# Runtime Symmetry & Session Governance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Design spec: `docs/superpowers/specs/2026-05-30-runtime-symmetry-and-session-governance-design.md` (read it — implementers must honor it). api_server contract: `docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md`.

**Goal:** Ship working managed hermes delivery (agent self-replies; no proliferation) and a runtime-agnostic session-governance layer (sticky identity, managed/resident FSM, mutual-exclusion guard, new-id warnings, reminders) on a symmetric runtime contract, with asymmetries documented.

**Architecture:** Per-agent hermes `gateway run` daemon (auto-ensured) hosting api_server + the aify-comms MCP tools; a per-agent `hermes-channel.js` sidecar delivers the wake via api_server `chat`; the hermes agent self-replies via `comms_send` (symmetric with claude). Governance is service-level and runtime-agnostic, calling per-runtime adapter/runtime-class hooks (the symmetric triad). Reminders re-wake agents owing replies.

**Tech Stack:** Node ESM bridges (`mcp/stdio/*`), Python FastAPI (`service/routers/api_v2.py`, `service/models.py`, `service/runtimes/*.py`), dashboard (`service/dashboard.html`), bash/PowerShell (`install.sh`), hermes 0.15.x api_server (HTTP/SSE).

---

## Already built (on `feature/session-status-robustness`) + required revisions

Built & tested: `hermes-apiserver-client.js`, `hermes-version.js`, `hermes-channel.js`, `hermes-session-id.js`, `hermes-daemon.js`, `adapters/hermes.js` (pinned id), WS-bind retired. The spec's self-reply decision requires the revisions in Phase 1 (Tasks 1.1–1.3). Do NOT rebuild from scratch — revise.

---

## Phase 1 — Hermes delivery completion (deliver the working fix first)

### Task 1.1: `hermes-channel.js` → wake-only (stop posting replies)

**Files:** Modify `mcp/stdio/hermes-channel.js`; Modify `mcp/stdio/tests/hermes-channel.test.js`. Read `mcp/stdio/claude-channel.js` (its run stays `delivered`, agent's `comms_send` closes it).

- [ ] **Step 1:** Update the test: after claim → ensure daemon/session → `chatStream` the dispatch prompt, the sidecar marks the run **delivered** (NOT completed) and does **NOT** POST `/messages/send`. It still pulses `turn_busy`. On `chatStream` transport failure it PATCHes the run failed with cause. (The agent's own `comms_send` reply closing the run is exercised in Phase 3/5 tests, not here.)
- [ ] **Step 2:** Run `node --test mcp/stdio/tests/hermes-channel.test.js` → FAIL (still posts reply / marks completed).
- [ ] **Step 3:** Remove the `/messages/send` POST and the reply-capture-as-reply logic; keep `chatStream` (run the turn to completion so the in-session agent can call `comms_send`), set run status `delivered`. Keep `turn_busy` pulse + `turn-end` clear + failure PATCH. Now structurally mirrors `claude-channel.js`.
- [ ] **Step 4:** Run → PASS. Full suite `node --test mcp/stdio/tests/*.test.js` (pre-existing failures `managed-message-prompts.test.js`, `terminal-runtime.test.js` OK).
- [ ] **Step 5:** Commit: `refactor(hermes): channel sidecar is wake-only; agent self-replies (claude-parity)`.

### Task 1.2: `hermes-daemon.js` → per-agent daemon

**Files:** Modify `mcp/stdio/hermes-daemon.js`; Modify `mcp/stdio/tests/hermes-daemon.test.js`; Create `mcp/stdio/hermes-endpoint.js` (pure id→{port,key,baseUrl}).

- [ ] **Step 1:** Create `hermes-endpoint.js` exporting `agentEndpoint(agentId)` → deterministic `{ port, host:'127.0.0.1', baseUrl, key }` where `port` is a stable function of agentId in a safe range (e.g. `8642 + (hash(agentId) % 1000)`) and `key` is derived/stored per agent (read from a per-agent key file under TEMP, generate+persist if absent). Unit-test determinism + range.
- [ ] **Step 2:** Update `hermes-daemon.test.js`: `ensureDaemon({agentId})` resolves its endpoint via `agentEndpoint`, spawns with that `API_SERVER_PORT`/`API_SERVER_KEY`, and two different agentIds get different ports/keys (no collision). Already-up path still no-spawn.
- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4:** Thread `agentId` (or an explicit endpoint) through `ensureDaemon`; default port/key from `agentEndpoint(agentId)`. Keep idempotent probe-before-spawn.
- [ ] **Step 5:** Run → PASS; full suite. Commit: `feat(hermes): per-agent api_server daemon endpoints (one identity per agent)`.

### Task 1.3: `adapters/hermes.js` → `resumeCommand` + `sessionIdSource`

**Files:** Modify `mcp/stdio/adapters/hermes.js`; Modify its adapter test.

- [ ] **Step 1:** Test: adapter exposes `sessionIdSource === "pinned"` and `resumeCommand(sessionId)` returning the operator takeover command string `hermes --tui --resume <sessionId>` (against that agent's daemon — include the gateway URL env hint in a comment). `discoverSessionId` still returns `pinnedSessionId(agentId)`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `sessionIdSource` + `resumeCommand` on the hermes adapter (these become part of the symmetric contract in Phase 2).
- [ ] **Step 4:** Run → PASS. Commit: `feat(hermes): adapter advertises sessionIdSource + resumeCommand`.

### Task 1.4: `install.sh` hermes branch rewrite

**Files:** Modify `install.sh` (hermes branch: `install_hermes_wrapper` ~1235, PowerShell ~1667, `patch_hermes_gateway_visible_bind` ~969 + call ~3260, MCP-register ~3137).

- [ ] **Step 1:** In the bash + PowerShell hermes wrappers: replace the `dashboard --tui`/`--tui` dual-spawn. For a **managed** launch (`--aify-agent` present, non-interactive): `node <repo>/mcp/stdio/hermes-daemon.js ensure <agentId>` (or call ensureDaemon via a tiny CLI shim) to bring up that agent's daemon, then exec the `hermes-channel.js` sidecar with env `AIFY_AGENT_ID`, `AIFY_HERMES_APISERVER_URL`/`_KEY` (from `agentEndpoint`). For an **interactive resident** launch (no `--aify-agent` or explicit resident): ensure the agent's daemon, then `exec hermes --tui --resume <pinnedId>` with `HERMES_TUI_GATEWAY_URL` pointed at that daemon's WS port (commented as the resident-attach asymmetry).
- [ ] **Step 2:** Register the aify-comms MCP server into the hermes daemon config so the agent has `comms_*` tools (mirror how claude wires `claude-channel.js`/the MCP server; write to hermes `~/.hermes/config.yaml` `mcp_servers` or pass via daemon env — per api_server-contract/recon B). This is what enables self-reply.
- [ ] **Step 3:** Before launching, kill any prior instance of the same `--aify-agent` (wrapper-level guard against proliferation). DELETE the `patch_hermes_gateway_visible_bind` call + function (+ `patch_hermes_tui_active_session_file` if now unused). Stop exporting `AIFY_HERMES_GATEWAY_URL` except for the resident-TUI attach (comment why).
- [ ] **Step 4:** Post-install: ensure + `assertApiServer` for a probe agent; on failure print the LOUD error and exit non-zero.
- [ ] **Step 5:** `bash -n install.sh`; `node --check` any new shim; review the `git diff install.sh` for heredoc-escaping correctness. Commit: `feat(hermes): install per-agent daemon + channel sidecar; drop dead gateway patch + TUI dual-spawn`.

### Task 1.5: Service routing — hermes managed → channel; drop handle dependency

**Files:** Modify `service/routers/api_v2.py` (`_agent_execution_mode` ~1183-1285, channel-routing ~484-511, `managed_via_wrapper` set ~205); Modify `service/runtimes/hermes.py`; Create `service/tests/test_hermes_channel_routing.py`.

- [ ] **Step 1:** Test: a managed hermes agent's dispatch routes to `channel` execution mode (claimable by the sidecar with `executionModes` incl `channel`); `_agent_execution_mode` for hermes no longer rejects on empty `session_handle`; gating uses a hermes channel-enabled runtime flag set by the wrapper env (mirror claude's `channelEnabled`).
- [ ] **Step 2:** Run `python -m pytest service/tests/test_hermes_channel_routing.py` → FAIL.
- [ ] **Step 3:** Add hermes to the channel-routing path (extend `_CHANNEL_MANAGED_RUNTIMES`/the claude routing or a symmetric equivalent in `service/runtimes/hermes.py`); set the channel-enabled flag from wrapper env; remove the hermes `session_handle` delivery requirement.
- [ ] **Step 4:** Run → PASS; full `service/tests/` (ignore known-unrelated `test_new_dashboard_app.py`). Commit: `feat(hermes): route managed hermes via channel; delivery no longer needs session_handle`.

### Task 1.6: Truthful status for hermes

**Files:** Modify `service/routers/api_v2.py:3593` `_compute_agent_status`; Create `service/tests/test_status_deliverability.py`.

- [ ] **Step 1:** Test: managed hermes with channel-enabled + live sidecar heartbeat → `ready`/`online`; without a live sidecar → `available` (never falsely online). claude/resident unchanged.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Gate ready/online on (live sidecar heartbeat) AND channel-enabled, mirroring claude; surface cause when not deliverable.
- [ ] **Step 4:** Run → PASS; full `service/tests/`. Commit: `fix(status): hermes ready/online requires live deliverability`.

**→ Checkpoint: managed hermes now delivers + self-replies, no proliferation. Container rebuild + a live smoke is reasonable here before Phase 2.**

---

## Phase 2 — Symmetric contract + AGENTS.md

### Task 2.1: Adapter contract — `sessionIdSource` + `resumeCommand` on every adapter

**Files:** Modify `mcp/stdio/adapters/{claude,codex,pi,opencode}.js` and `base.js`.

- [ ] **Step 1:** In `base.js`, document the contract: every adapter MUST expose `sessionIdSource ∈ {pinned,captured,resume}` and `resumeCommand(sessionId)`. Add sane defaults that throw/clearly-mark "unimplemented" so omissions are loud.
- [ ] **Step 2:** Implement per adapter with `ASYMMETRY(<rt>)` comments where they differ: claude `captured` + `claude-aify --resume <id>`; codex/pi `resume` + their CLI resume command; opencode per its model.
- [ ] **Step 3:** `node --check`; run adapter tests. Commit: `feat(runtimes): symmetric adapter contract — sessionIdSource + resumeCommand`.

### Task 2.2: Symmetry-guard test

**Files:** Modify `mcp/stdio/tests/runtime-adapter-consistency.test.js` (or create `mcp/stdio/tests/adapter-contract.test.js`).

- [ ] **Step 1:** Test: iterate ALL registered adapters (`adapters/index.js`); assert each implements the full contract (capability flags, `discoverSessionId`, `sessionIdSource`, `resumeCommand`). FAILS loudly if a new runtime omits a method — this is the symmetry enforcement.
- [ ] **Step 2:** Run → it should pass after 2.1 (RED only if an adapter is incomplete — fix the adapter, not the test).
- [ ] **Step 3:** Commit: `test(runtimes): symmetry guard — every adapter must satisfy the contract`.

### Task 2.3: AGENTS.md "Runtime symmetry" section

**Files:** Modify `AGENTS.md`; mirror to `.agents/` per repo convention if a runtime doc exists there.

- [ ] **Step 1:** Add a "Runtime symmetry" section: state P1/P2, the adapter/controller/runtime-class triad + responsibilities, the realization matrix, the `ASYMMETRY(<rt>): <why>` comment rule, and "adding a new harness = implement the triad; the symmetry-guard test enforces completeness."
- [ ] **Step 2:** Commit: `docs(agents): codify runtime-symmetry principle + triad contract`.

---

## Phase 3 — Sticky session identity (governance core)

### Task 3.1: Persist session_id; first-id auto-accept; new-id → pending/`session-changed`

**Files:** Modify `service/models.py` (agent fields: `pending_session_id`), `service/routers/api_v2.py` (registration/heartbeat session-id handling); Create `service/tests/test_session_identity_sticky.py`.

- [ ] **Step 1:** Test: (a) brand-new agent (no persisted id) reporting an id → accepted/persisted (no warning). (b) Same agent re-reporting the SAME id → no-op. (c) Reporting a DIFFERENT id → persisted id unchanged, `pending_session_id` set, status `session-changed`, delivery still targets the OLD id. (d) Confirm-new → repins to pending; Keep-current → clears pending.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement the sticky logic at the registration/heartbeat ingress; add `pending_session_id` + the `session-changed` status; add endpoints `POST /agents/{id}/session/confirm` and `/session/keep` (or extend an existing agent-update route).
- [ ] **Step 4:** Run → PASS; full `service/tests/`. Commit: `feat(governance): sticky session identity + new-id guard (catches split/merge)`.

---

## Phase 4 — Mode FSM + mutual exclusion

### Task 4.1: managed/resident driver state + one-driver invariant

**Files:** Modify `service/models.py` (`driver_state`), `service/routers/api_v2.py` (mode switch + attach guard); Create `service/tests/test_session_mode_fsm.py`.

- [ ] **Step 1:** Test: (a) switching managed→resident marks resident + signals release (a flag the managed sidecar reads to stop claiming); response includes `resumeCommand`. (b) An attach/registration to a session currently driven in the OTHER mode is REJECTED with an actionable error containing the resume command. (c) Same-mode supersession (managed restart) is allowed (machine_id supersession). (d) resident→managed flips back.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement the FSM + the mutual-exclusion guard; the managed sidecar (`hermes-channel.js`/`claude-channel.js`) reads the release flag from its claim/heartbeat response and stops driving when set. Surface `resumeCommand` (from the runtime class, sourced from the adapter contract) in the switch response + rejection error.
- [ ] **Step 4:** Run → PASS; full `service/tests/`. Commit: `feat(governance): managed/resident FSM + mutual-exclusion collision guard`.

---

## Phase 5 — Reminder subsystem (runtime-agnostic)

### Task 5.1: Reminder for unanswered `require_reply` runs

**Files:** Modify `service/routers/api_v2.py` (a reminder pass over open `require_reply` runs); Read `mcp/stdio/notify-check.js` (generalize its claude inbox reminder); Create `service/tests/test_reply_reminders.py`.

- [ ] **Step 1:** Test: a `require_reply` run unanswered past threshold → exactly one reminder re-wake is enqueued for the owing agent, with a body that reinforces the pattern (`answer message <id> with comms_send(..., inReplyTo=<id>)`); once a reply lands (run answered) no further reminders; reminders are capped (no infinite nagging).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement a reminder pass (on the existing dispatch/heartbeat tick or a periodic sweep) that enqueues a reminder dispatch via each runtime's normal wake path (runtime-agnostic — claude/hermes/codex/pi). Threshold + max-reminders configurable.
- [ ] **Step 4:** Run → PASS; full `service/tests/`. Commit: `feat(governance): runtime-agnostic reply reminders (reinforce the comms_send pattern)`.

---

## Phase 6 — Dashboard

### Task 6.1: Mode toggle + resume command + session-changed actions

**Files:** Modify `service/dashboard.html` (+ any dashboard JS); Read existing agent-card rendering; Create/extend a dashboard test if the repo has one (`service/tests/test_new_dashboard_*` — note pre-existing failures there are unrelated; don't depend on them).

- [ ] **Step 1:** Add per-agent: a managed/resident toggle (calls the Phase 4 switch endpoint), display of the takeover **resume command** when resident or `session-changed`, and a `session-changed` badge with **Confirm new** / **Keep current** buttons (Phase 3 endpoints).
- [ ] **Step 2:** Manual/scripted check that the controls call the right endpoints and render the states. Commit: `feat(dashboard): managed/resident toggle, resume command, session-changed actions`.

---

## Phase 7 — Tests, rollout, live verification

### Task 7.1: Full regression + build
- [ ] **Step 1:** `node --test mcp/stdio/tests/*.test.js` + `python -m pytest service/tests/ -q` — green except known-unrelated `test_new_dashboard_app.py`.
- [ ] **Step 2:** `docker compose up -d --build && curl http://localhost:8800/health`. `node --check` changed JS; `bash -n install.sh`.
- [ ] **Step 3:** Commit fixups.

### Task 7.2: Full install redeploy (operator) + live verify
- [ ] **Step 1:** Run the FULL redeploy for all clients (operator's standing request): `bash install.sh --client claude "http://192.168.100.10:8800"`, then `--client codex`, `--client hermes`, `--client pi`. hermes asserts api_server up.
- [ ] **Step 2:** Recreate ONE managed hermes agent → exactly one daemon + one sidecar (no TUI pair, no accumulation), status `ready`. Dispatch from another agent → hermes **self-replies** via comms_send, threaded back (model authenticated via `hermes login`).
- [ ] **Step 3:** Governance live checks: (a) flip the agent to resident in the dashboard → it shows the correct resume command, the sidecar releases, and `hermes --tui --resume aify-<id>` takes over the SAME session; flip back. (b) launch a second resident with the same id while managed → rejected with the actionable error. (c) force a drifted id → `session-changed` badge + Confirm/Keep works. (d) leave a `require_reply` unanswered → reminder fires once, stops after reply.
- [ ] **Step 4:** Regression: claude managed still delivers/self-replies; a second hermes agent gets its OWN daemon/pinned session (no collision); restart an agent twice → process count flat.
- [ ] **Step 5:** Mark #135 + #139 complete; update memory ([[hermes-apiserver-rearchitecture]], [[session-id-capture-progress]]).

---

## Self-Review

**Spec coverage:** symmetry contract + AGENTS.md (Phase 2) ✓; sticky identity + new-id guard (3) ✓; mode FSM + mutual exclusion (4) ✓; reminders (5) ✓; dashboard switch/resume/session-changed (6) ✓; hermes self-reply + per-agent daemon + no-proliferation (1) ✓; truthful status (1.6) ✓.

**Sequencing:** Phase 1 delivers the working hermes fix first (incremental value, closes the immediate #139). Phases 3–5 are the cross-runtime governance; 2 + 6 make it symmetric and operable. Each phase is independently testable.

**Type consistency:** `sessionIdSource`/`resumeCommand` defined in 1.3/2.1 and consumed in 4.1/6.1; `pending_session_id`/`session-changed` defined in 3.1 and consumed in 6.1; channel-enabled flag in 1.5 consumed by status 1.6.

**Out of scope:** true live co-view (hermes 0.15.x limit); codex `no_rollout` (#136); pi PTY (#137) — both should adopt this governance once landed.
