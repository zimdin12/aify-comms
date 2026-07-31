# Session-Handle & Status Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make managed session-handle capture + agent-status computation robust and version-resilient so every runtime (esp. hermes on 0.15.1) reliably establishes, captures, and binds its OWN session — fixing managed hermes delivery and removing the fragility that lets a hermes upgrade silently break it.

**Architecture:** One "session truth" model: each managed runtime captures its own session id via a reliable, runtime-appropriate mechanism (claude = SessionStart hook, DONE & verified; hermes = gateway forge+capture retargeted to 0.15.1's NATIVE session API; codex/pi = existing). Status is computed strictly from real deliverability (live transport + valid own handle) with cause-accurate diagnostics. The aify↔hermes gateway integration is decoupled from fragile text-anchor patches by targeting 0.15.1's native `session.create/activate/bind_transport` and asserting capability at install/connect (loud failure, never silent no-op).

**Tech Stack:** Python (FastAPI service, `service/routers/api_v2.py`), Node ESM bridges (`mcp/stdio/*`), hermes 0.15.1 tui_gateway (WebSocket JSON-RPC), bash/PowerShell wrappers (`install.sh`).

---

## Background — verified root cause (2026-05-30)

The session-mixing fixes already shipped and are correct/verified:
- #138 claude: SessionStart hook captures each session's own id, keyed by `AIFY_AGENT_ID` (commit bc66923). **Verified live** — sc-claude holds its own handle.
- machine_id casing normalized end-to-end (commit 334265d) — supersession no longer splits on `DevBox-1` vs `DEVBOX-1`.
- #135 hermes managed-bind guard (commit 6c66891) — managed agents refuse the gateway-global `most_recent` fallback.

Managed **hermes** still can't deliver. Root cause = **hermes 0.15.1 restructured its gateway**, and aify's integration was built for an older hermes:
1. `patch_hermes_gateway_visible_bind` (install.sh:969, called :3260) **runs but silently no-ops** on 0.15.1 — its injected `TeeTransport` import and `aify.session.bind_transport` RPC method are ABSENT from the current `tui_gateway/server.py` (text anchors no longer match). 0.15.1 instead ships a NATIVE `bind_transport` (server.py:24,490) and NEW methods `session.activate`/`session.active_list`.
2. The controller (`hermes-resident-controller.js`) calls `aify.session.bind_transport` (via `buildAifySessionBindTransportFrame`) — which doesn't exist on 0.15.1 → bind impossible.
3. Because the gateway is single-client, the controller's delivery WS displaces the TUI's connection → **sending a dispatch closes the TUI** (the original "#135 supersession mid-turn" symptom).
4. A managed (non-interactive) hermes TUI never lands a persisted session (`hermes sessions list` shows none created today; per-port active-session file stays 0-byte). The forge path exists (ui-tui `createGatewayEventHandler.ts:234-278` → `newSession()`), but it doesn't complete/persist under managed launch.
5. Empty handle → `_compute_agent_status` returns "available" (not "ready") → `comms_send` live-gate refuses delivery. **Status is the gate hit, but it is downstream of the missing handle** (Steven's "is it status-driven?" — yes, as a symptom).

**Why claude works and hermes doesn't:** claude ALWAYS has a session the instant it launches and the hook captures it (process == session). Hermes depends on a gateway-forged session + a patched bind method + single-client sharing — three fragile, version-coupled links. The fix makes hermes' path robust and version-aware, and hardens status so it never falsely advertises deliverability.

Non-issues ruled out (do not chase): provider auth (Codex OAuth logged in, `provider_configured`=true), the WSL `server.js` path (current MCP config is correct `C:\Docker\aify-comms\...`), host→server reachability (HTTP 200), the model 403 (stale).

---

## File Structure (what changes and why)

| Path | Responsibility | Change |
|------|----------------|--------|
| `docs/superpowers/specs/2026-05-30-hermes-0.15.1-gateway-api.md` | Recon note: exact 0.15.1 gateway session API (request/response shapes for create/activate/active_list/resume/bind, prompt.submit, render-notice equivalent) | Create (Phase A) |
| `mcp/stdio/hermes-gateway-protocol.js` | Frame builders + event translation for the gateway | Modify — add 0.15.1-native frames (session.activate), retarget/remove the `aify.session.bind_transport` frame; keep legacy behind a capability flag |
| `mcp/stdio/controllers/hermes-resident-controller.js` | Bind into the visible session + submit/steer; manage the delivery WS | Modify — use native bind path; never displace the TUI (share/tee or activate); cause-accurate errors |
| `install.sh` (hermes branch: `patch_hermes_gateway_visible_bind` ~969, `patch_hermes_tui_active_session_file` ~1089, wrapper ~1455-1924) | Apply gateway capability to hermes install; managed forge; assert patch applied | Modify — make patch idempotent + 0.15.1-aware OR drop in favor of native API; add post-install ASSERT that the bind capability is present (loud fail, never silent); managed-launch forge wiring |
| `mcp/stdio/adapters/hermes.js` | `discoverSessionId()` (active-session file / env) | Modify if needed — robust per-agent capture; version-aware |
| `service/routers/api_v2.py` (`_compute_agent_status` ~3593) | Status from real deliverability | Modify — managed agent "ready" only when bindable (live transport + valid own handle); accurate "available/online/ready"; surface cause |
| `mcp/stdio/hermes-version.js` | NEW — detect hermes version + gateway capability (does `session.activate`/native bind exist?) | Create — single source for version-aware branching + loud capability assertion |
| `service/tests/test_status_deliverability.py` | Status reflects deliverability | Create |
| `mcp/stdio/tests/hermes-0151-bind.test.js`, `hermes-managed-forge.test.js` | Native bind + managed forge against the fake gateway fixture | Create |
| `mcp/stdio/tests/fixtures/fake-hermes-gateway.mjs` | Mock gateway | Modify — model 0.15.1 native session.create/activate/active_list/bind so tests exercise the real path |

---

## Phase A — Recon & contracts (produce the facts later phases need)

### Task A1: Document the 0.15.1 gateway session API

**Files:**
- Create: `docs/superpowers/specs/2026-05-30-hermes-0.15.1-gateway-api.md`
- Read: `C:\Users\dev\AppData\Local\hermes\hermes-agent\tui_gateway\server.py` (methods at lines 2294 session.create, 2447 session.resume, 2568 session.active_list, 2589 session.activate, 2745 session.status, 3338 prompt.submit, 3301 session.steer; native `bind_transport` at 24/490)
- Read: `C:\Users\dev\AppData\Local\hermes\hermes-agent\ui-tui\src\app\useSessionLifecycle.ts` (170 session.create, 184 writeActiveSessionFile, 251 activateLiveSession→session.activate)

- [ ] **Step 1:** Read each method handler and record the exact params + response dict shape (keys like `session_id`, `info`, `session_key`, `cols`). Capture how `bind_transport(t)` (server.py:490) is invoked and whether a per-call transport can be supplied by a WS client (the mechanism aify needs to receive a specific session's events without displacing the TUI).
- [ ] **Step 2:** Determine the canonical "make THIS session the one the visible TUI renders + receive its events on my WS" sequence on 0.15.1. Candidate: `session.activate {session_id}` then events flow; confirm whether a second WS client gets events for the activated session or displaces the TUI (the single-client question). Record the answer with the server.py evidence.
- [ ] **Step 3:** Record whether any `aify.*` custom methods are needed at all on 0.15.1, or whether native `session.create`+`session.activate`+`prompt.submit`+`session.steer` fully cover forge→bind→deliver. Decide: **retarget controller to native API** (preferred — no patch) vs **re-port the patch**. Write the decision + rationale into the spec.
- [ ] **Step 4:** Commit the spec.

```bash
git add docs/superpowers/specs/2026-05-30-hermes-0.15.1-gateway-api.md
git commit -m "docs(hermes): record 0.15.1 gateway session API + native forge/bind decision"
```

### Task A2: Capture the current status-computation contract

**Files:**
- Read: `service/routers/api_v2.py:3593` `_compute_agent_status` (and call sites 3871, 5599, 9855, 9977, 10452, 14565)

- [ ] **Step 1:** Document in the same spec: the inputs `_compute_agent_status` uses, how it currently decides available/online/ready/working/stale/offline for a managed agent, and exactly where an empty `session_handle` + attached terminal yields "available". Identify the precise condition to change so "ready/online" requires real deliverability.
- [ ] **Step 2:** Commit (append to spec).

```bash
git add docs/superpowers/specs/2026-05-30-hermes-0.15.1-gateway-api.md
git commit -m "docs(status): record _compute_agent_status deliverability contract"
```

---

## Phase B — Hermes version + capability detection (anti-fragility foundation)

### Task B1: hermes-version capability module

**Files:**
- Create: `mcp/stdio/hermes-version.js`
- Test: `mcp/stdio/tests/hermes-version.test.js`

- [ ] **Step 1: Write failing test** — `detectGatewayCapabilities(methodList)` returns `{ nativeActivate: true, aifyBindMethod: false }` for a 0.15.1 method list containing `session.activate` but not `aify.session.bind_transport`; and the inverse for legacy.

```javascript
import assert from "node:assert/strict";
import test from "node:test";
import { detectGatewayCapabilities } from "../hermes-version.js";

test("0.15.1 native methods → nativeActivate, no aify bind", () => {
  const caps = detectGatewayCapabilities(["session.create","session.activate","prompt.submit"]);
  assert.equal(caps.nativeActivate, true);
  assert.equal(caps.aifyBindMethod, false);
});
test("legacy patched gateway → aify bind present", () => {
  const caps = detectGatewayCapabilities(["session.create","aify.session.bind_transport"]);
  assert.equal(caps.aifyBindMethod, true);
});
```

- [ ] **Step 2:** Run: `node --test mcp/stdio/tests/hermes-version.test.js` → FAIL (module missing).
- [ ] **Step 3: Implement** `detectGatewayCapabilities(methods)` returning `{ nativeActivate: methods.includes("session.activate"), nativeActiveList: methods.includes("session.active_list"), aifyBindMethod: methods.includes("aify.session.bind_transport") }`. (Method list comes from the gateway's `rpc.discover`/`system.methods` if available, or a probe; A1 records which.)
- [ ] **Step 4:** Run test → PASS.
- [ ] **Step 5:** Commit.

### Task B2: Install-time capability assertion (no more silent no-op)

**Files:**
- Modify: `install.sh` — after `patch_hermes_gateway_visible_bind` (line ~3260)

- [ ] **Step 1:** After the hermes install applies its gateway integration, ASSERT the chosen bind capability is actually present in `tui_gateway/server.py` (native `session.activate` for the native path, or the `aify.session.bind_transport` method for the legacy patch path). If absent, print a LOUD, explicit error (`[install.sh] FATAL: hermes gateway bind capability missing for this hermes version — managed hermes delivery will not work; see docs/superpowers/specs/2026-05-30-hermes-0.15.1-gateway-api.md`) and exit non-zero. This converts today's silent failure into an immediate, obvious one. (Exact grep target per A1/A3 decision.)
- [ ] **Step 2:** `bash -n install.sh`; run `bash install.sh --client hermes <url>` and confirm it either succeeds with capability present or fails loudly.
- [ ] **Step 3:** Commit.

---

## Phase C — Retarget the controller to 0.15.1 native bind (the core fix)

> Implements the A1 decision. If A1 chose "re-port the patch" instead of "native", swap the method names accordingly; the task shape is identical.

### Task C1: Native forge+activate frames in the protocol

**Files:**
- Modify: `mcp/stdio/hermes-gateway-protocol.js`
- Test: `mcp/stdio/tests/hermes-0151-bind.test.js`

- [ ] **Step 1: Write failing test** — `buildSessionActivateFrame({sessionId})` produces `{jsonrpc, id, method:"session.activate", params:{session_id}}`; protocol exposes the native bind sequence builder.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `buildSessionActivateFrame` (and any A1-identified native frame, e.g. transport-scoped subscribe). Keep `buildAifySessionBindTransportFrame` exported but only used on the legacy capability path.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

### Task C2: Controller binds without displacing the TUI

**Files:**
- Modify: `mcp/stdio/controllers/hermes-resident-controller.js` (bind block ~300-369)
- Modify: `mcp/stdio/tests/fixtures/fake-hermes-gateway.mjs` (model native session.create/activate/active_list)
- Test: `mcp/stdio/tests/hermes-0151-bind.test.js`

- [ ] **Step 1: Write failing test** — with the fixture advertising native caps and an agent that has its OWN session id, the controller binds via `session.activate` (NOT `aify.session.bind_transport`), submits the prompt, and the fixture records that the TUI's transport was NOT closed/displaced. Keep the #135 guard: managed + no own session → refuse (unchanged).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** — branch on `detectGatewayCapabilities`: native path uses `session.activate`+`prompt.submit`/`session.steer` and the native transport-scoped receive (per A1) so the controller shares rather than displaces; legacy path keeps the old method. Preserve resident behavior.
- [ ] **Step 4:** Run → PASS; re-run existing `hermes-resident-dispatch.test.js` (no regressions).
- [ ] **Step 5:** Commit.

---

## Phase D — Managed hermes forge (so the handle actually populates)

### Task D1: Managed launch establishes + persists its own visible session

**Files:**
- Modify: `install.sh` hermes wrapper (bash ~1455-1525, PowerShell ~1884-1917) — the `--tui` launch
- Read: ui-tui `createGatewayEventHandler.ts:234-278` (forge path), `useSessionLifecycle.ts:155-235` (startNewSession)
- Test: `mcp/stdio/tests/hermes-managed-forge.test.js`

- [ ] **Step 1: Recon (A1 dependent):** determine why the forge (`newSession`) doesn't persist under managed/non-interactive launch — candidates: forge runs but session isn't persisted until first turn; or the bridge/heartbeat WS displaces the TUI before forge completes (Phase C fixes displacement). Record finding.
- [ ] **Step 2: Write failing test** — simulate a managed launch (AIFY_SESSION_MODE=managed, empty handle): after gateway-up, a session is created AND its id is written to `AIFY_HERMES_ACTIVE_SESSION_FILE`, and `discoverSessionId()` returns it.
- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4: Implement** the minimal forge guarantee for managed mode: if Phase C's non-displacement is sufficient (TUI forges on its own once not displaced) the fix may be "ensure Phase C ships + retest"; otherwise the wrapper (managed branch only) explicitly creates+activates a session via the gateway after bind-up and writes the active-session file. Resident mode unchanged.
- [ ] **Step 5:** Run → PASS.
- [ ] **Step 6:** Commit.

---

## Phase E — Status handling (truthful deliverability, accurate diagnostics)

### Task E1: `_compute_agent_status` requires real deliverability for ready/online

**Files:**
- Modify: `service/routers/api_v2.py:3593` `_compute_agent_status`
- Test: `service/tests/test_status_deliverability.py`

- [ ] **Step 1: Write failing test** — a managed agent with an attached terminal but EMPTY `session_handle` (and no live controller) computes to "available", never "online"/"ready"; the same agent WITH a valid own handle + live transport computes "ready". (Per A2 contract.)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** the deliverability gate per A2: ready/online requires (live terminal/controller) AND a non-empty own `session_handle` for runtimes whose delivery needs it (hermes/codex). Keep claude/resident behavior.
- [ ] **Step 4:** Run → PASS; run full `service/tests/` (ignore the pre-existing `test_new_dashboard_app.py` failures — unrelated).
- [ ] **Step 5:** Commit.

### Task E2: Cause-accurate bind/delivery diagnostics

**Files:**
- Modify: `mcp/stdio/controllers/hermes-resident-controller.js` (refusal/throw messages)

- [ ] **Step 1: Write failing test** — when bind can't proceed, the surfaced error names the ACTUAL cause: (a) gateway capability missing (version drift) → "hermes gateway too old/new for aify bind — reinstall/upgrade"; (b) provider not configured → "hermes provider not authenticated — run hermes login"; (c) no own session → the existing message. (Controller queries `setup.status` + capability to pick.)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** the branch; replace the blanket "restart hermes-aify" with the cause-specific message.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

---

## Phase F — Tests, rollout, live verification

### Task F1: Full regression + build/deploy

- [ ] **Step 1:** `node --test mcp/stdio/tests/*.test.js` and `python -m pytest service/tests/ -q` — all green except the known unrelated `test_new_dashboard_app.py`.
- [ ] **Step 2:** Container changes (api_v2.py): `docker compose up -d --build && curl http://localhost:8800/health`. Bridge/install changes: `bash install.sh --client hermes <url>` (assert passes Phase B capability check), `node --check` changed JS.
- [ ] **Step 3:** Commit any fixups.

### Task F2: Live round-trip verification (the real acceptance test)

- [ ] **Step 1:** Operator recreates ONE managed hermes agent (Stop → Recreate). Confirm in DB: non-empty own `session_handle` + `gatewayUrl`, status "ready", and a fresh row in `hermes sessions list`.
- [ ] **Step 2:** Send it a dispatch from another agent. Confirm: TUI does NOT close, the prompt is delivered to the visible session, and a reply is captured back (model must be authenticated — separate operator precondition).
- [ ] **Step 3:** Regression: confirm claude managed (sc-claude) still holds its own handle and delivers. Confirm a second hermes agent in the same repo gets its OWN distinct session (no collision).
- [ ] **Step 4:** Mark #135 + #139 complete; update memory.

---

## Self-Review

**Spec coverage:** session capture (B,C,D) ✓; status handling (E1) ✓; stability/anti-fragility (B1 version detect, B2 loud assert, E2 diagnostics) ✓; the verified root cause (0.15.1 gateway drift) is the spine of B/C ✓; claude-vs-hermes difference addressed by making hermes' path robust + status truthful ✓.

**Known dependency:** Phases C/D/E2 depend on A1/A2 facts (0.15.1 API shapes). A1/A2 are explicit recon tasks that PRODUCE those facts before implementation — not placeholders. The native-vs-report-patch decision is made in A1 with recorded rationale; C/D are written to either branch via the capability flag.

**Out of scope (operator/hermes-side, not this plan):** hermes model-provider auth (`hermes login`) — required for replies but independent of bind; codex `no_rollout` (#136); pi PTY (#137).
