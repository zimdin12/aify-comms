# Hermes api_server Delivery (claude-parity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. SUPERSEDES Phases B–D of `2026-05-30-session-and-status-robustness.md` (gateway-WS bind). Status (its Phase E) and the loud-capability-assert idea (its Phase B) are carried forward here.

**Goal:** Make managed hermes delivery work like claude-aify: one persistent `hermes gateway run` daemon (api_server enabled) + a per-agent sidecar that claims dispatch runs over HTTP by agentId and drives the agent's pinned hermes session via the api_server HTTP API — eliminating the tui_gateway WS bind, the silently-no-op gateway patch, the per-launch TUI process spawn (hermes.exe proliferation), and the `session_handle` delivery dependency.

**Architecture:** Mirror the claude model's *shape* (sidecar pulls runs over HTTP keyed by agentId → feeds one persistent agent → reply via comms_send) using hermes's native transport. Where claude pushes an MCP notification into its own process, hermes' sidecar POSTs to the api_server platform (`POST /api/sessions/{id}/chat/stream`, SSE reply) of a shared long-lived gateway daemon, with one stable pinned session per aify agent (`X-Hermes-Session-Id`). No TUI, no per-request spawn, no gateway text-patch. Version-robust: depends on a documented HTTP API, probed at install with a loud assert.

**Tech Stack:** Node ESM bridges (`mcp/stdio/*`), Python FastAPI service (`service/routers/api_v2.py`), hermes 0.15.x `gateway/platforms/api_server.py` (HTTP+SSE), bash/PowerShell wrappers (`install.sh`).

---

## Background — verified (2026-05-30, two recons)

- **claude-aify mechanism (recon a3fefeb3333ba1424):** `mcp/stdio/claude-channel.js` is a sidecar MCP server loaded into claude; its `pollLoop()` long-polls `POST /api/v1/dispatch/claim` with `executionModes:["channel","resident"]` keyed by **agentId** (from `aify-agent-<ppid>` binding file, `claude-channel.js:104-121,346-352`), and on claim pushes the run into the session via a server-initiated MCP notification (`:241-248`). Reply = `comms_send` with `inReplyTo=<messageId>` (`:188-190`), linked server-side by `_link_reply_message_to_dispatch_run` (`api_v2.py:5925`). **`session_handle` is NOT required for claude delivery** — claude's resident gate is `runtime_config.channelEnabled===true` (`api_v2.py:1128-1131`), never the handle. Managed claude routes to `channel` via `_CHANNEL_MANAGED_RUNTIMES` + `_apply_channel_routing_to_claude_runs` (`api_v2.py:484-511,1219-1220`); claude is EXCLUDED from `managed_via_wrapper` (`api_v2.py:205`).
- **hermes capability (recon a4d7d73233edf35d8):** hermes MCP-client can't be woken by a server push (only `sampling/createMessage` mid-tool-call + `tools/list_changed`). But `gateway/platforms/api_server.py` (`Platform.API_SERVER`, env `API_SERVER_ENABLED`/`API_SERVER_KEY`, default port 8642) runs in-process inside the long-lived `hermes gateway run` daemon and exposes: `POST /api/sessions/{session_id}/chat[/stream]` (drive a persisted continuing session, SSE the agent output), session continuity via `X-Hermes-Session-Id` + memory scope via `X-Hermes-Session-Key`, and `POST /v1/runs` → run_id (202) / `GET /v1/runs/{run_id}/events` (SSE) / `POST /v1/runs/{run_id}/stop` / `/approval`. No TUI, no per-request spawn, no displacement.
- **Proliferation root cause (diagnosed live):** `hermes-aify.ps1 --aify-agent X` spawns TWO hermes.exe per launch (`dashboard --tui --port N` + `--tui [--resume]`) and never tears down the prior instance → 18 hermes.exe from 9 stale wrappers. The new design removes both: no TUI spawn, and the daemon is shared/singleton.

**Non-issues (ruled out, do not chase):** provider auth (Codex OAuth logged in), WSL/server.js path, host→service reachability, the model 403. Operator must still `hermes login` for model auth so replies have content (separate precondition).

---

## File Structure

| Path | Responsibility | Change |
|------|----------------|--------|
| `docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md` | Exact api_server request/response/SSE shapes + daemon lifecycle, verified against the INSTALLED (post-update) hermes | Create (Task A1) |
| `mcp/stdio/hermes-apiserver-client.js` | Thin HTTP/SSE client for the api_server platform: `chatStream`, `createRun`, `runEvents`, `stopRun`, header/session pinning, auth key | Create |
| `mcp/stdio/hermes-channel.js` | Per-agent sidecar mirroring `claude-channel.js`: claim runs by agentId → dispatch to pinned session via apiserver-client → stream reply → report (`comms_send`/run-status PATCH) + `turn_busy` pulse | Create |
| `mcp/stdio/hermes-daemon.js` | Ensure ONE `hermes gateway run` daemon up with api_server enabled (idempotent start, health probe, port/key discovery) | Create |
| `mcp/stdio/hermes-version.js` | Probe api_server availability + version/capability; loud-assert helper | Create |
| `mcp/stdio/adapters/hermes.js` | Capabilities reflect channel/api_server model; `discoverSessionId()` returns the pinned session id | Modify |
| `mcp/stdio/controllers/hermes-resident-controller.js` | tui_gateway WS bind path | Remove/retire (delete WS bind + `aify.session.bind_transport`); keep only if a thin shim is needed |
| `install.sh` (hermes branch) | Enable api_server (config + key), ensure daemon, wire the hermes-channel sidecar like claude's channel, wrapper kills prior same-agent instance, DROP `patch_hermes_gateway_visible_bind` + dashboard/TUI dual-spawn; post-install LOUD assert api_server reachable | Modify |
| `service/routers/api_v2.py` | hermes managed → `channel` routing (like claude); hermes `_agent_execution_mode` no longer requires `session_handle`; `_compute_agent_status` truthful deliverability | Modify |
| `mcp/stdio/tests/hermes-apiserver-client.test.js`, `hermes-channel.test.js`, `hermes-daemon.test.js`, `hermes-version.test.js` | Unit tests vs a fake api_server fixture | Create |
| `mcp/stdio/tests/fixtures/fake-hermes-apiserver.mjs` | Mock api_server (chat/stream SSE, /v1/runs, auth) | Create |
| `service/tests/test_status_deliverability.py`, `test_hermes_channel_routing.py` | Status + routing | Create |

---

## Phase A — Verify the api_server contract against the freshly-updated hermes

### Task A1: Document exact api_server shapes + daemon lifecycle

**Files:**
- Create: `docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md`
- Read: `C:\Users\dev\AppData\Local\hermes\hermes-agent\gateway\platforms\api_server.py` (endpoints, headers, SSE event format, auth), `gateway\config.py` (`Platform.API_SERVER`, `API_SERVER_ENABLED`/`API_SERVER_KEY`, default port), `gateway\run.py` (`start_gateway`, daemon lifecycle, how platforms are enabled)

- [ ] **Step 1:** Record EXACT shapes: for `POST /api/sessions/{id}/chat` and `/chat/stream` — request headers (`X-Hermes-Session-Id`, `X-Hermes-Session-Key`, auth header name + `API_SERVER_KEY`), request body JSON keys, response body / SSE `event:`+`data:` frame format (which events carry assistant text vs tool/status, terminal event). Same for `POST /v1/runs`, `GET /v1/runs/{id}/events`, `POST /v1/runs/{id}/stop`, `/approval`. Note how a session is created/pinned if `X-Hermes-Session-Id` names a not-yet-existing session.
- [ ] **Step 2:** Record daemon lifecycle: exact command to start (`hermes gateway run` + flags/env to enable api_server + set key + port), how to health-check it's up (endpoint), how the TUI attaches to it (`HERMES_TUI_GATEWAY_URL`), and whether the api_server port is fixed or discovered.
- [ ] **Step 3:** Confirm the post-update hermes still exposes these (the operator is updating hermes; re-verify versions/paths). Record the installed version.
- [ ] **Step 4:** Commit the spec.

```bash
git add docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md
git commit -m "docs(hermes): record api_server HTTP/SSE contract + daemon lifecycle (verified vs installed)"
```

### Task A2: Capture the status-computation contract (carried from prior plan)

**Files:**
- Read: `service/routers/api_v2.py` `_compute_agent_status` (~3593) + call sites; `_agent_execution_mode` (~1183-1285)

- [ ] **Step 1:** Append to the spec: how `_compute_agent_status` currently decides available/online/ready/working for managed hermes, where empty `session_handle`+attached terminal yields a wrong status, and the precise condition to change so ready/online require real deliverability. Document how claude's `channelEnabled` gate works so hermes can adopt the same shape.
- [ ] **Step 2:** Commit (append).

---

## Phase B — api_server client + version/capability probe (anti-fragility)

### Task B1: `hermes-apiserver-client.js`

**Files:** Create `mcp/stdio/hermes-apiserver-client.js`; Create `mcp/stdio/tests/fixtures/fake-hermes-apiserver.mjs`; Test `mcp/stdio/tests/hermes-apiserver-client.test.js`

- [ ] **Step 1: Write failing test** — against the fake fixture: `chatStream({baseUrl, key, sessionId, sessionKey, text})` sends the auth header + `X-Hermes-Session-Id`, and yields assistant-text chunks parsed from the SSE stream, resolving with the full reply on the terminal event. (Use the EXACT shapes from A1.)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** the client (Node 18+ global `fetch` + manual SSE line parsing; the bridge already bundles `ws`/express but this is plain HTTP). Methods: `chatStream`, `createRun`, `runEvents`, `stopRun`, `health`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

### Task B2: `hermes-version.js` capability probe + loud assert

**Files:** Create `mcp/stdio/hermes-version.js`; Test `mcp/stdio/tests/hermes-version.test.js`

- [ ] **Step 1: Write failing test** — `probeApiServer({baseUrl,key})` returns `{available:true, version}` when the fixture health endpoint answers, `{available:false, reason}` on connection refused / 401 / 404. `assertApiServer(probe)` throws a LOUD, explicit error string when unavailable.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** using the apiserver-client `health`. Error names the cause (daemon down / wrong key / endpoint missing → "reinstall/upgrade hermes integration; see docs/.../hermes-apiserver-contract.md").
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

---

## Phase C — The per-agent sidecar (mirror claude-channel.js)

### Task C1: `hermes-channel.js` claim→dispatch→reply loop

**Files:** Create `mcp/stdio/hermes-channel.js`; Test `mcp/stdio/tests/hermes-channel.test.js`. Read `mcp/stdio/claude-channel.js` for the claim/report/turn_busy patterns to mirror.

- [ ] **Step 1: Write failing test** — with a fake aify dispatch endpoint (claimable run for agentId) + the fake api_server: the sidecar claims the run by agentId (`executionModes` incl. `channel`), calls `apiserver-client.chatStream` against the agent's pinned session, captures the reply, PATCHes the run delivered/answered, and pulses `turn_busy`. Mirror `claude-channel.js` claim + `markDispatchDelivered` + `reportTurnBusy`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** the loop reusing claude-channel.js structure: read bound agentId, `pollLoop` claim, on claim → dispatch to pinned session via apiserver-client, stream reply, report. Resolve the pinned session id via adapter (Task C2). Keep it ≤ the 500-line budget; factor shared claim/report helpers if claude-channel.js exposes them, else duplicate minimally.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

### Task C2: `adapters/hermes.js` — pinned session + capabilities

**Files:** Modify `mcp/stdio/adapters/hermes.js`; Test (extend existing hermes adapter tests).

- [ ] **Step 1: Write failing test** — `discoverSessionId()` returns the pinned per-agent session id (derived stably from agentId, or read from the per-agent active-session file written by the daemon-pin step), never the gateway global most_recent. Capabilities advertise the channel/api_server model (no tui_gateway WS bind).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.** Pinned session id = stable function of agentId (e.g. `aify-<agentId>`), or the value the install/daemon step persisted. Remove WS-bind capability advertisement.
- [ ] **Step 4:** Run → PASS; retire/delete `hermes-resident-controller.js` WS bind (and its `aify.session.bind_transport` usage). Re-run existing hermes tests; delete/replace now-obsolete gateway-bind tests.
- [ ] **Step 5:** Commit.

---

## Phase D — Daemon lifecycle + install rewrite (kills proliferation)

### Task D1: `hermes-daemon.js` — ensure one api_server daemon

**Files:** Create `mcp/stdio/hermes-daemon.js`; Test `mcp/stdio/tests/hermes-daemon.test.js`

- [ ] **Step 1: Write failing test** — `ensureDaemon({port,key,hermesCmd,spawn})`: if `probeApiServer` says up → no spawn; if down → spawns `hermes gateway run` (with api_server env) exactly once and waits for health. Idempotent (second call no-op). Uses injected `spawn`/`probe` for testability.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** idempotent ensure-up with health-wait + single-instance guard (lockfile or probe-before-spawn).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

### Task D2: `install.sh` hermes branch rewrite

**Files:** Modify `install.sh` hermes branch.

- [ ] **Step 1:** Enable api_server in hermes config (set `API_SERVER_ENABLED=1`, generate/persist `API_SERVER_KEY`, fixed port); export the key+port+baseUrl into the hermes-aify env. Wire the `hermes-channel.js` sidecar to launch with the agent (the claude-channel analogue) instead of spawning TUIs.
- [ ] **Step 2:** Make the wrapper KILL any prior instance of the same `--aify-agent` before launching (fix proliferation), and DROP both the `dashboard --tui` and `--tui` spawns and the `patch_hermes_gateway_visible_bind` call (+ its function and `patch_hermes_tui_active_session_file` if now unused).
- [ ] **Step 3:** Post-install LOUD ASSERT: `ensureDaemon` + `assertApiServer`; on failure print explicit error + exit non-zero (no silent no-op).
- [ ] **Step 4:** `bash -n install.sh`; `node --check` all new JS; run `bash install.sh --client hermes <url>` — confirm daemon comes up, assert passes, and a relaunch of the same agent leaves exactly one set of processes (no accumulation).
- [ ] **Step 5:** Commit.

---

## Phase E — Service routing + truthful status

### Task E1: hermes managed → channel routing; drop handle dependency

**Files:** Modify `service/routers/api_v2.py`; Test `service/tests/test_hermes_channel_routing.py`

- [ ] **Step 1: Write failing test** — a managed hermes agent's dispatch run is routed to `channel` execution mode (claimable by the hermes sidecar) and `_agent_execution_mode` for hermes no longer rejects on empty `session_handle`; the sidecar-claim path is gated on a channel-enabled flag set by the wrapper (mirror claude's `channelEnabled`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** — add hermes to the channel-routing path (or its own equivalent), set a hermes channel-enabled runtime flag from the wrapper env, remove the `session_handle` requirement for hermes delivery. Keep codex/pi untouched.
- [ ] **Step 4:** Run → PASS; full `service/tests/` (ignore known-unrelated `test_new_dashboard_app.py`).
- [ ] **Step 5:** Commit.

### Task E2: `_compute_agent_status` truthful deliverability

**Files:** Modify `service/routers/api_v2.py:3593`; Test `service/tests/test_status_deliverability.py`

- [ ] **Step 1: Write failing test** — a managed hermes agent with channel-enabled + live sidecar heartbeat computes "ready/online"; without a live sidecar it computes "available" (never falsely online). Claude/resident behavior unchanged.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** the deliverability gate (live sidecar/controller heartbeat + channel-enabled), mirroring claude. Surface cause when not deliverable.
- [ ] **Step 4:** Run → PASS; full `service/tests/`.
- [ ] **Step 5:** Commit.

---

## Phase F — Tests, rollout, live verification

### Task F1: Regression + build/deploy
- [ ] **Step 1:** `node --test mcp/stdio/tests/*.test.js` and `python -m pytest service/tests/ -q` — green except known-unrelated `test_new_dashboard_app.py`.
- [ ] **Step 2:** Container (api_v2.py): `docker compose up -d --build && curl http://localhost:8800/health`. Bridge/install: `node --check` changed JS; `bash install.sh --client hermes <url>` (assert passes).
- [ ] **Step 3:** Commit fixups.

### Task F2: Full install/update integrations (operator-requested) + live round-trip
- [ ] **Step 1:** After operator's hermes update, run the FULL redeploy for all clients: `bash install.sh --client claude "http://192.0.2.10:8800"`, then `--client codex`, `--client hermes`, `--client pi` (same URL). Confirm each completes; hermes asserts api_server up.
- [ ] **Step 2:** Recreate ONE managed hermes agent. Confirm: exactly one daemon + one sidecar (NO hermes.exe pair, NO accumulation), status "ready".
- [ ] **Step 3:** Dispatch a message to it from another agent. Confirm: reply captured + threaded back via `comms_send`/inReplyTo (model must be authenticated — `hermes login`). No TUI displacement (there's no TUI).
- [ ] **Step 4:** Regression: claude managed (sc-claude) still delivers; a second hermes agent gets its OWN pinned session (no collision); restart an agent twice → process count stays flat (proliferation fixed).
- [ ] **Step 5:** Mark #135 + #139 complete; update memory ([[hermes-apiserver-rearchitecture]]).

---

## Self-Review

**Spec coverage:** delivery (B,C) ✓; proliferation fix (D — daemon singleton + wrapper kills prior + no TUI spawn) ✓; status (E2) ✓; anti-fragility (A1 verify-vs-installed, B2 probe + loud assert) ✓; claude-parity shape (C mirrors claude-channel.js) ✓; resident-TUI co-view explicitly out of live scope (snapshot via shared daemon only) ✓.

**Dependency:** C/D/E depend on A1's exact api_server shapes — A1 is an explicit recon task run AGAINST the freshly-updated hermes (not a placeholder). If the post-update hermes changed the api_server contract, A1 captures it and B/C adapt.

**Out of scope:** hermes model auth (`hermes login`); codex `no_rollout` (#136); pi PTY (#137); true live co-view of an aify-owned session inside the operator's TUI (architecturally limited on 0.15.x — snapshot only).
