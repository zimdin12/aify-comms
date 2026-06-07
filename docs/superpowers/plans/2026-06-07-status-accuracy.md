# Status Accuracy & Stability — Implementation Plan

> **For agentic workers:** execute task-by-task; steps use `- [ ]`. This plan ships the SAFE,
> additive Phase-1 wins now and DOCUMENTS the larger unified-model refactor as Phase 2
> (deliberately NOT built this round — it touches every turn-signal writer + both engines and
> must be done supervised, behind the `status_engine` flag, validated against the disagreement log).

**Goal:** Make agent status accurate and stable through `*-aify` for BOTH resident and managed
agents, by closing the worst concrete gaps with isolated, testable changes — without the
multi-signal churn that has made past status changes flaky.

**Architecture:** Status is derived in `service/status_engine.py:derive()` (the LIVE `new` engine)
from a `StatusInputs` byproduct assembled in `service/routers/api_v2.py:_compute_live_status_cache`.
Turn state flows from the harness `*-aify` wrappers + `mcp/stdio/` bridges via `/turn-start`,
`/turn-end`, `/heartbeat`, `/console-working`. The single best turn signal in the system is the
hermes **gateway-status detector** (`mcp/stdio/hermes-gateway-turn-detector.js`), which today runs
ONLY in the managed delivery loop.

**Tech stack:** Node ESM bridges (`mcp/stdio/`), FastAPI/SQLite service, node:test + pytest.

---

## Background (from research) — why status feels flaky

1. **Resident hermes has NO turn-end signal at all** → up to 30 min of false `working` after every
   turn (the worst single inaccuracy). `pre_llm_call` sets turn-start; nothing clears it; it
   self-heals only at `TURN_BUSY_BACKSTOP_SECONDS=1800`.
2. **Two turn tables drift** — `agent_turn_state` (legacy) vs `agent_status_state` (v2). Any writer
   that touches only one diverges. (Phase 2 collapses these.)
3. **Edge-set/clear vs refreshed-lease mismatch** — a dropped turn-end edge latches `working` until
   the 1800s ceiling. (Phase 2's `working_until` lease fixes this generally.)
4. **Managed codex/pi/opencode autonomous turns are dispatch-bounded only** (no continuous
   detector) — they under-show `working` during a non-dispatch turn. Lower severity.

Phase 1 below fixes (1) directly and is fully additive. Phase 2 (documented, not built) fixes
(2)/(3)/(4) structurally.

---

### Task 1: Resident hermes turn-END via the gateway detector (closes the 30-min gap)

The managed delivery loop already runs `startHermesGatewayTurnDetector` against the gateway's
`session.active_list` status and posts `/turn-start`/`/turn-end` (with a 3-tick idle debounce and a
45s working re-stamp). A RESIDENT hermes registers a `gatewayUrl` but runs no such detector, so its
turn never ends. Run the SAME detector in the resident bridge path.

**Files:**
- Modify: `mcp/stdio/server.js` (resident hermes startup path — where the resident liveness beat /
  gateway-liveness probe is armed; search `startGatewayLivenessProbe` / the resident hermes branch)
- Reuse (no change): `mcp/stdio/hermes-gateway-turn-detector.js`, `hermes-gateway-protocol.js`
- Test: `mcp/stdio/tests/resident-hermes-turn-detector.test.js`

- [ ] **Step 1: Read the resident hermes startup in `server.js`** — find where a resident hermes
  bridge (wakeMode hermes-live) arms its liveness beat + `startGatewayLivenessProbe`, and how it
  obtains the gateway WS URL (the same `readGatewayUrlMarker` / registered `gatewayUrl` the managed
  loop uses). Confirm it has: `httpCall`, the agent id, and a way to open the gateway WS
  (`openGatewayWsClient`) + `buildSessionActiveListFrame` + the marker readers.

- [ ] **Step 2: Write the failing wiring test** (`resident-hermes-turn-detector.test.js`): assert a
  small helper `shouldArmResidentHermesTurnDetector({runtime, sessionMode, gatewayUrl})` returns true
  only for `runtime==="hermes"` + a non-empty `gatewayUrl` (resident OR managed-resident), and that
  the detector, fed a stubbed `readGatewayStatus` returning `"working"` then sustained `"idle"`,
  posts exactly one turn-start then one turn-end (reuse the patterns in
  `tests/hermes-gateway-turn-detector.test.js`). Run → FAIL.

- [ ] **Step 3: Implement** — in the resident hermes path, after the gateway-liveness probe is armed,
  construct a `readResidentGatewayStatus` (mirror `readManagedSessionStatus` in
  `hermes-managed-host.js:2081`: open the current gateway WS, `session.active_list`, match the
  agent's real session by `pickSessionStatusById(realId)` → `pickSessionStatusForKey` → most-recent
  row fallback) and pass it to `startHermesGatewayTurnDetector({ intervalMs: GATEWAY_TURN_POLL_MS,
  idleDebounce: GATEWAY_TURN_IDLE_DEBOUNCE, workingRefreshMs: 45000, readGatewayStatus, postTurnStart:
  () => reportTurnBusy(httpCall,id,{busy:true}), postTurnEnd: () => clearTurn(httpCall,id) })`. Store
  the stop fn and clear it on resident teardown / `resident-lost`. Gate with the helper from Step 2 so
  non-hermes / no-gateway residents are a no-op.

- [ ] **Step 4: Run the test → PASS; `node --check`; commit.**

- [ ] **Step 5: Doc** — DECISIONS.md + `references/status.md` (BOTH skill mirrors): resident hermes
  now ends turns via the gateway detector (no more 30-min false `working`). KNOWN_ISSUES: remove/
  amend the "resident hermes has no turn-end" item.

**Safety:** purely additive — a new clearing path. Gateway-truth-driven (anti-feedback invariant
holds, same as managed). Worst case if the gateway read fails: it no-ops and the 1800s backstop
still applies (today's behavior). It can never manufacture `working` (only `postTurnStart` on a
gateway `working` read; only `postTurnEnd` on sustained idle).

---

### Task 2: Tighten the managed-claude console-working keepalive lapse

Research flagged: the 20s console-working lease can lapse if the SIGWINCH keepalive misfires when the
Console is closed (the ppid self-exit guard interaction). Verify the keepalive arms for every managed
claude PTY and add a node:test asserting it survives a `consoleClass` flap (working→unknown→working
must NOT pause; only sustained `idle` pauses). This is hardening of the existing 2026-06-06 keepalive.

**Files:** Modify (only if a gap is found): `mcp/stdio/terminal-runtime.js`; Test: extend
`mcp/stdio/tests/terminal-runtime-console-keepalive.test.js`.

- [ ] **Step 1:** Add a test: a managed-claude state whose `consoleClass` goes
  `working → unknown → working` across ticks keeps getting poked (accumulator resets on non-idle).
- [ ] **Step 2:** Run; if it passes, no code change (document the guarantee). If it fails, fix the
  gate so only sustained `idle` pauses. Commit.

---

### Task 3 (DOC ONLY — DEFERRED): the unified `working_until` model (Phase 2)

**Do NOT implement this round.** Write it up as `docs/superpowers/plans/2026-06-07-status-phase2-working-until.md`
so it's ready for a supervised pass. Summary to capture:

- One authoritative `working_until` epoch per agent (collapses `agent_turn_state` +
  `agent_status_state`). `working` ⟺ `now < working_until AND alive`.
- EXTEND `working_until = now + LEASE` (~90s) on ANY positive signal (turn-start hook, transcript
  in-flight edge, gateway `working` tick, native `turn/started`/`agent_start`, dispatch turn_busy,
  console-spinner tick). Detectors re-extend at 30s/3s/45s.
- CLEAR `working_until = 0` instantly on any explicit end (turn-end hook, transcript end edge,
  gateway sustained-idle, native `turn/completed`/`agent_end`, dispatch clearTurnBusy); newer
  turn-start always wins the ownership race.
- Dropped-end self-heal is automatic at ≤LEASE (kills the 1800s ceiling AND the resident gap
  generally).
- Finish the `_agent_liveness` consolidation (`api_v2.py:2208` TODO) — one predicate for
  resident+managed so liveness can't disagree across call sites.
- Roll out behind a third `status_engine` value; validate against the existing disagreement log;
  add codex/pi autonomous re-pulse off native `turn/started`/`agent_start`.

---

## Self-Review
- **Spec coverage:** accurate+stable status via `*-aify` for resident+managed → Task 1 fixes the
  worst resident gap (hermes); Task 2 hardens managed claude; Phase 2 (doc) is the structural fix.
- **Risk:** Task 1/2 are additive + test-gated + can only ever CLEAR or hold `working`, never
  fabricate it. Phase 2 is explicitly deferred.
- **No placeholders:** Task 1/2 cite exact files/functions to read + reuse.
