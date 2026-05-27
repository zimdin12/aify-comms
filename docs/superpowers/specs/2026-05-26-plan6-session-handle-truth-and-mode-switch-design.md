# Plan 6 — Session-handle truth + manual mode switch design

**Date:** 2026-05-26
**Branch:** feature/dashboard-console-mode (continuation from Plans 1-5)
**Goal:** Make stored session handles always reflect the runtime's actual current session, and give operators explicit control over the resident/managed mode (today auto-detected via TTY).

---

## Background

Plan 4 introduced `discoverSessionId()` per adapter (gateway RPC for hermes, app-server probe for codex, filesystem scan for pi/claude). The bridge's `session-handle-heartbeat` and the initial register path were supposed to keep the server's stored handle aligned with reality. They don't — discover is only a fallback when env-read returns null.

Operator's live evidence (2026-05-26): `HERMES_SESSION_ID=20260524_190451_29e0d7` persists in the operator's shell from a previous hermes session. Hermes-aify exports it forward; hermes loads with it but silently starts a fresh session with a different id. The bridge registers with the stale id; dispatch fires `prompt.submit` against the gateway with the stale id; gateway returns "session not found". Affects **both resident** (sc-hermes-test-1 → sc-hermes-test-2 ping-pong) **and managed** (hermes-test pseudo-terminal) delivery.

Additionally: today resident-vs-managed is auto-decided by `[ -t 0 ]` in each wrapper. Operator wants explicit mode control via the dashboard UI ("switching between resident and managed manual ... then this kind of issues would not happen").

---

## Section A — Bridge: prefer discover over env

Today (`mcp/stdio/session-handle-heartbeat.js` and `server.js:873-883`):

```javascript
let current = null;
try { current = adapter.getCurrentSessionId(); } catch {}      // env-read first
if (!current && typeof adapter.discoverSessionId === "function") {
  try { current = await adapter.discoverSessionId(); } catch {}  // fallback
}
```

Reverse the priority:

```javascript
let current = null;
if (typeof adapter.discoverSessionId === "function") {
  try { current = await adapter.discoverSessionId(); } catch {}  // runtime authoritative
}
if (!current) {
  try { current = adapter.getCurrentSessionId(); } catch {}      // env fallback
}
```

Same change at the initial-register call site in `server.js:873` so the agent registers with the discovered handle (or env as fallback). Self-correcting from the first heartbeat.

**Per-runtime discover cost:**
- hermes: gateway RPC `session.most_recent` over WS — ~10–50ms
- codex: app-server WS query if URL present, else filesystem scan up to 4 levels — ~50–200ms
- pi: filesystem scan one level deep — ~10–30ms
- claude: filesystem scan one project dir — ~10–30ms
- opencode: stays unimplemented (returns null)

All well within the 60s heartbeat interval. No perf concern.

**Rollback:** if discover throws or returns null, we fall through to env-read — exactly the legacy behavior. Strictly additive.

---

## Section B — Wrappers: rediscover and re-export at session start

Each wrapper currently trusts whatever the parent shell exported. After the runtime is launched and a session id is established, the wrapper should query that runtime's authoritative source and overwrite the env var the inner bridge will see.

**hermes-aify:** After `wait_for_http` succeeds and the dashboard token is captured, query `session.most_recent` on the gateway via a one-shot WS round-trip. If the response carries a session id, overwrite `HERMES_SESSION_ID` and `AIFY_SESSION_HANDLE` before exec'ing `hermes chat --tui`. If the gateway can't be reached or returns nothing, leave the env vars alone (the bridge's runtime discover will catch up via the heartbeat).

**codex-aify:** After `wait_for_port` succeeds, query the app-server's introspection (or run `codex sessions list` if the layout is consistent enough) to get the most-recent thread id. Overwrite `CODEX_THREAD_ID`. The existing `--resume` logic still wins when operator passed an explicit handle.

**pi-aify:** Query the running pi's session-state endpoint (already used by the Phase-4 watchdog at line 105 of pi-aify wrapper) to read the live session id. Overwrite `PI_SESSION_ID`. If pi isn't running yet (resident-start path), leave alone.

**claude-aify:** Validate `CLAUDE_SESSION_ID` against `~/.claude/projects/<encoded-cwd>/<id>.jsonl` existence. If missing, unset the env var — claude itself will create a fresh session and the bridge's discover will pick it up.

For all four: this is bash-side work that only runs in the wrapper before the runtime exec; no node code. Add a small helper in install.sh per wrapper.

**Failure mode:** if the rediscover step times out or errors, the wrapper logs a single WARNING line (similar to the Plan 5 hermes fallback banner) and continues with the env values it has. The bridge's discover-first heartbeat (Section A) will correct any drift within 60s.

---

## Section C — Manual resident/managed mode switching UI

Today the wrapper auto-detects via `[ -t 0 ]`. Operator wants explicit control.

**Backend (`service/routers/api_v2.py`):**

- New endpoint `PATCH /agents/{id}/session-mode` accepts `{ "mode": "resident" | "managed" }`.
- Validation: the requested mode must be supported by the agent's runtime adapter (`adapter.supports_resident` / `adapter.supports_managed`).
- State transition logic:
  - **resident → managed:** mark agent's resident bridge superseded (let any in-flight delivery complete); subsequent dispatches use the managed path. If no managed PTY exists, eager-spawn creates one (existing Plan 4 path).
  - **managed → resident:** stop the managed PTY (if any), unset eager-spawn marker. Operator is expected to launch a resident `*-aify` session themselves. Until they do, the agent shows `offline` with reason `awaiting_resident_attach`.
- Audit log: write a `dispatch_events` row `mode_switch_resident_to_managed` (or vice versa) keyed by agent id, so the change is traceable.

**Settings flag:**

- New setting `manual_session_mode` (bool, default `false`).
- When `false`: today's TTY-auto-detect behavior in the wrapper. Dashboard hides switch buttons.
- When `true`: wrapper still auto-detects on the operator's CLI, but the dashboard exposes switch buttons and the operator can flip mid-life.

**Dashboard (`service/new_dashboard/`):**

- **Details panel** (where agent metadata lives today): a small `[Switch to managed] / [Switch to resident]` chip near the existing session-mode label. Disabled and hidden when `manual_session_mode === false`.
- **Sessions panel** (under each session row's action menu): same affordance — useful when operator has multiple sessions for one agent and wants to flip per-session.
- Both surfaces call the new PATCH endpoint.
- Toast on success: `Agent "<id>" mode switched to <new mode>.`
- Toast on failure: surface the server's error message verbatim.

**Edge cases:**

- An agent currently in an active dispatch run gets blocked from switching until the run finishes (server returns 409 with the active run id; UI shows tooltip "Wait for active dispatch to finish before switching mode").
- Hermes special case: switching managed → resident on a hermes agent that doesn't have a `gatewayUrl` would leave it un-wakeable. Server warns (409 + actionable message) unless `force=true` is passed.

---

## Out of scope

- Cross-runtime session migration (e.g., import a hermes session into codex). Stays unsupported.
- Multi-machine session reconciliation. Same as today — one bridge per agent per machine.
- Opencode rediscover (Section B). Opencode has no live runtime probe; stays env-only.

---

## Success criteria

- Operator re-launches hermes-aify with a stale `HERMES_SESSION_ID` in their shell — within seconds the bridge corrects the server's handle to the actual current hermes session id. Next ping-pong succeeds.
- Operator can flip an agent from resident to managed (or back) via the Details panel chip without re-registering or restarting any wrapper.
- All existing tests pass; new TDD tests cover each of A1, A2, B per-runtime, C endpoint + UI mount.

---

## Implementation outline

3 sections, ~20 tasks total. Each section's tasks are TDD-driven (failing test → implement → pass → commit).

**Section A — Bridge discover priority:**
- A1. Reverse priority in `session-handle-heartbeat.js`.
- A2. Apply same reversal in `server.js:873` initial register path.
- A3. (Optional) Cache discoverSessionId result with short TTL to avoid duplicate calls within the same tick.

**Section B — Per-wrapper rediscover:**
- B1. hermes-aify dashboard `session.most_recent` query + overwrite env.
- B2. codex-aify app-server thread-id query + overwrite env.
- B3. pi-aify session-state endpoint query + overwrite env.
- B4. claude-aify projects-dir existence check + clear stale env.
- B5. Documentation update in `install.*.md`.

**Section C — Manual mode switch:**
- C1. `PATCH /agents/{id}/session-mode` endpoint + tests.
- C2. State transition logic + `dispatch_events` audit log.
- C3. `manual_session_mode` setting in `DEFAULT_SETTINGS`.
- C4. Dashboard Details panel chip wiring.
- C5. Dashboard Sessions action menu item.
- C6. Settings UI toggle + visibility gating of switch buttons.

**Section D — Holistic + finish:**
- D1. Full python + js test suites.
- D2. Code-reviewer subagent over Plan 6 diff.
- D3. DECISIONS.md entries.
- D4. `aify-comms-debug` skill recipes for stale-handle detection.
- D5. `finishing-a-development-branch`.

---

## Non-goals

- Do NOT change the existing 60s heartbeat interval. Different concern.
- Do NOT introduce new MCP tools for mode switching — the dashboard endpoint is the source of truth.
- Do NOT touch claude-aify or opencode delivery paths beyond Section B's defensive env-validation step.
