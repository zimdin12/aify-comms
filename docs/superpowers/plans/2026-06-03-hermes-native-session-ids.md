# Hermes Native Session IDs + Always-Gateway-Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use `- [ ]` checkboxes.

**Goal:** Make resident hermes use its **own native session id** as the `sessionHandle` (symmetric with claude's UUID / codex's thread id), eliminate the synthetic `aify-<agentId>` session, and let `comms_register` be a first-class on-ramp by always bringing up the WebSocket gateway-host.

**Why:** The synthetic `aify-<agentId>` session is the source of most hermes fragility this project hit (placeholder gateway URL, cwd-keyed marker collisions, `--aify-agent` vs `--resume` confusion, "wtf is aify-mp-senior-dev"), and it's asymmetric — only hermes invents a name; claude/codex store the real session. Native ids make `sessionHandle` mean the same thing everywhere and remove the pre-seed/rename dance.

**Architecture:** `hermes-aify` always starts a per-agent WebSocket gateway-host (`hermes dashboard --tui`) and attaches the visible TUI to it. Identity is bound at launch (`--aify-agent X` → resume X's stored real session id, or fresh first time) OR mid-session (`comms_register(X)` captures the current real session id). The delivery loop targets the agent's session by its **stored real id**, not a derived `aify-<id>` key. WS primitives (`prompt.submit` / `session.steer` / `session.interrupt`) give deliver/steer/interrupt; queue stays aify-side.

**Tech Stack:** Bash (the `install.sh`-generated `hermes-aify` wrapper, `\$`-escaped heredoc), Node bridges under `mcp/stdio/` (`server.js`, `hermes-managed-host.js`, `hermes-endpoint.js`, `register-helpers.js`, `adapters/hermes.js`), FastAPI service (`service/routers/api_v2.py`, `service/runtimes/hermes.py`), Hermes v0.15.1 tui_gateway.

---

## Key code facts (grounded)

- Gateway WS session targeting today: `mcp/stdio/hermes-managed-host.js` `waitForActiveSession` → `pickSessionForKey(listResp, sessionKeyFor(agentId))` matches the **`aify-<agentId>`** title; `prompt.submit`/`session.steer` go to that sid.
- Synthetic-name producers: `mcp/stdio/hermes-session-id.js` `pinnedSessionId(agentId)` → `aify-<sanitized>`; `adapters/hermes.js discoverSessionId` returns `pinnedSessionId(agentId)`; `register-helpers.js fillSessionHandleFromAdapter` (commit e89af02) overrides the hermes handle with the pinned name — **this is the change to revert**.
- Wrapper gateway-host branch (`install.sh`, "GATEWAY-HOST launch"): `ensure-host` pre-seeds + resumes `aify-<id>` via `AIFY_HERMES_PINNED_SESSION` / `HERMES_TUI_RESUME`; guard fires for any agent-id launch.
- Hermes tui_gateway WS methods (verified): `prompt.submit` (server.py:3848), `session.steer`, `session.interrupt`, `session.active_list` (server.py:3074), `session.resume` (allocates a fresh ephemeral sid — server.py:2929). `session.active_list` returns each live session's id/title/key + running status.
- Service hermes deliverability keys on the **gateway** not the handle: `defaultCapabilitiesForRuntime` (runtimes.js:15-19) grants `resident-run` only on `gatewayOk`; `api_v2.py:1656/1662` `hermes-live`/`hermes-missing-handle` key on `_has_hermes_gateway_url`. So storing a real id as the handle is safe.

---

## File structure

- Modify `install.sh` (hermes wrapper): always start the gateway-host; resume by **looked-up real id** (or fresh); stop pinning `aify-<id>`; export the resolved id for the bridge + loop.
- Modify `mcp/stdio/hermes-managed-host.js`: target the agent's session by its **real id** (env-provided or looked up via active_list "most-recent for this gateway") instead of `pickSessionForKey("aify-<id>")`; bind/point the loop when identity is set via register.
- Modify `mcp/stdio/adapters/hermes.js`: `discoverSessionId` returns the **active-session-file / HERMES_SESSION_ID** real id (not `pinnedSessionId`).
- Modify `mcp/stdio/register-helpers.js`: revert the e89af02 override; for hermes, fill `sessionHandle` from the **real session id** (adapter), like other runtimes.
- Modify `mcp/stdio/server.js`: on `comms_register`, capture the real session id + persist the per-agent id marker so a relaunch can resume it; keep the gateway resolution from the marker.
- Modify `service/runtimes/hermes.py`: `supports_steering=True` (gateway path steers); resume/console commands show `hermes-aify --aify-agent <id>` (id looked up internally), not a synthetic name.
- Add `mcp/stdio/hermes-endpoint.js`: per-agent **session-id marker** `aify-hermes-session-<agentId>` (agent-keyed, like the gateway/port markers) so launch/loop/bridge agree on the real id without round-trips.

---

### Task 1: Per-agent real-session-id marker (the new source of truth)

**Files:**
- Modify: `mcp/stdio/hermes-endpoint.js`
- Test: `mcp/stdio/__tests__/hermes-endpoint.test.*` (if present) or an inline node check

- [ ] **Step 1: Add `writeSessionIdMarker` / `readSessionIdMarker` (agent-keyed)**

```js
// aify-hermes-session-<agentId>: the REAL hermes session id this agent is bound
// to. Written when an agent first binds (launch or comms_register), read at
// relaunch to resume the SAME real session. Agent-keyed (never cwd-keyed).
function sessionIdMarkerPath(agentId, tempDir) {
  return path.join(tempDir, `aify-hermes-session-${sanitizeAgentId(agentId)}`);
}
export function writeSessionIdMarker(agentId, sessionId, { tempDir = os.tmpdir() } = {}) {
  const safe = sanitizeAgentId(agentId); const id = String(sessionId || "").trim();
  if (!safe || !id) return false;
  try { fs.writeFileSync(sessionIdMarkerPath(agentId, tempDir), id); return true; } catch { return false; }
}
export function readSessionIdMarker(agentId, { tempDir = os.tmpdir() } = {}) {
  const safe = sanitizeAgentId(agentId); if (!safe) return "";
  try { const v = fs.readFileSync(sessionIdMarkerPath(agentId, tempDir), "utf8").trim(); return v || ""; } catch { return ""; }
}
```

- [ ] **Step 2: include it in `clearGatewayMarkers`'s name list**

Add `` `aify-hermes-session-${safe}` `` to the array in `clearGatewayMarkers`.

- [ ] **Step 3: node --check + round-trip test**

Run: `node --check mcp/stdio/hermes-endpoint.js`
Run a 6-line inline node script: write `("agent-x","20260603_x")`, read → equals, clear → "".
Expected: PASS.

- [ ] **Step 4: Commit** `feat(hermes): per-agent real-session-id marker`

---

### Task 2: Wrapper resumes by the real id; always start the gateway-host

**Files:** Modify `install.sh` (hermes wrapper)

- [ ] **Step 1: resolve the real session id before launch**

In the hermes wrapper, after agent-id resolution: if `HERMES_AIFY_AGENT_ID` is set and no explicit `--resume`, read `aify-hermes-session-<id>` (via a tiny `node -e readSessionIdMarker`); if present set `HERMES_RESUME_REAL_ID`, else leave empty (fresh session). Drop `AIFY_HERMES_PINNED_SESSION=aify-<id>`.

- [ ] **Step 2: gateway-host branch resumes the real id (or fresh)**

Replace `exec hermes --tui --resume aify-<id>` with: `--resume "$HERMES_RESUME_REAL_ID"` when set, else launch with NO `--resume` (fresh). `ensure-host` no longer pre-seeds `aify-<id>`; it just brings up the gateway-host on the agent's port.

- [ ] **Step 3: broaden so EVERY launch starts the gateway-host**

When `HERMES_ARGS` is empty (interactive TUI) and there's any aify identity OR none, start the gateway-host so a later `comms_register` can bind. (Keep passthrough subcommands — `hermes-aify model list` — bare.)

- [ ] **Step 4: `bash -n install.sh`; generate wrapper; `bash -n ~/.local/bin/hermes-aify`**

Expected: PARSE OK; grep shows no `aify-<id>` resume target.

- [ ] **Step 5: Commit**

---

### Task 3: Delivery loop targets the real session id

**Files:** Modify `mcp/stdio/hermes-managed-host.js`

- [ ] **Step 1: resolve the target sid by the real id**

Replace `pickSessionForKey(listResp, sessionKeyFor(agentId))` with a resolver that matches the agent's **real session id** (from `readSessionIdMarker(agentId)` / an env the wrapper exports) against `session.active_list` entries; if the agent hasn't bound an id yet, fall back to the gateway's most-recent live session and record it.

- [ ] **Step 2: when an id binds via register, persist + use it**

On first successful `active_list` match (or on a register signal), `writeSessionIdMarker(agentId, sid)` so launch + bridge agree.

- [ ] **Step 3: node --check; unit-test the resolver** with a fake active_list.

- [ ] **Step 4: Commit**

---

### Task 4: Bridge stores the real id; register binds identity

**Files:** Modify `mcp/stdio/adapters/hermes.js`, `mcp/stdio/register-helpers.js`, `mcp/stdio/server.js`

- [ ] **Step 1: `adapters/hermes.js discoverSessionId` returns the REAL id**

Return the active-session-file id / `HERMES_SESSION_ID` (the real visible session), not `pinnedSessionId(agentId)`.

- [ ] **Step 2: revert e89af02 in `register-helpers.js`**

For hermes, fill `sessionHandle` from the adapter's real id (drop the `aify-<agentId>` override).

- [ ] **Step 3: `server.js` comms_register binds the id**

On register, `writeSessionIdMarker(agentId, <real id>)` and write the gateway marker (already done), so a relaunch resumes the same real session and the loop targets it.

- [ ] **Step 4: node --check all three; Commit**

---

### Task 5: Service flags + commands

**Files:** Modify `service/runtimes/hermes.py`

- [ ] **Step 1: `supports_steering = True`** (gateway path steers via `session.steer`).
- [ ] **Step 2: `resume_command` / `console_command`** show `hermes-aify --aify-agent <id>` (no synthetic name).
- [ ] **Step 3: `python3 -c "import ast; ast.parse(open('service/runtimes/hermes.py').read())"`; Commit.**

---

### Task 6: Deploy + live verification

- [ ] Rebuild container (`docker compose up -d --build`); reinstall hermes (re-sync native bridge).
- [ ] **New agent:** `hermes-aify --aify-agent t1` → bridge registers `sessionHandle = <real timestamp id>`, `wakeMode=hermes-live`. `comms_send` from another agent renders in t1's TUI.
- [ ] **Register on-ramp:** bare `hermes-aify`, then `comms_register(agentId="t2", role="coder")` → t2 deliverable; readback `sessionHandle` = the real session id.
- [ ] **Resume:** exit, `hermes-aify --aify-agent t1` → resumes the SAME real session (continuous transcript), still hermes-live.
- [ ] **Symmetry check:** `comms_agents` shows hermes/codex/claude handles all as real session ids.

---

## Self-Review

**Spec coverage:** native id (Tasks 1,3,4), no synthetic name (Tasks 2,4), always-gateway-host on-ramp (Tasks 2,3,4), steer/interrupt (Task 5 flag; loop already uses `session.steer`/`session.interrupt`), revert e89af02 (Task 4).

**Risk / rollback:** the loop's session-targeting is the highest-risk change — keep a fallback to "gateway's most-recent live session" so delivery never hard-fails if the id marker is missing. Each task commits independently; revert is per-commit.

**Open question for the operator:** confirmed — every interactive `hermes-aify` launch starts the gateway-host (small overhead, makes the register on-ramp always work).
