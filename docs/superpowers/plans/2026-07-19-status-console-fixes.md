# Status + Console fixes (from the 2026-07-18 pulled-commits review)

> **For the executor:** each task is self-contained with traced PROOF, the exact minimal
> change, blast-radius reasoning, and the test. Follow in order. Commit per task.
> Everything here is SERVER-SIDE or DASHBOARD (container rebuild) — **no bridge/wrapper
> redeploy**, so live agent sessions are undisturbed.

**Goal:** fix 3 confirmed MED regressions from the pulled commits, safely, without changing
core dispatch/messaging behavior.

**Deploy grouping:** Task 1 + Task 3 → backend (`docker compose up -d --build`, container
`aify-comms-service`). Task 2 → dashboard (same compose build rebuilds `aify-comms-new-dashboard`).
Verify each with `curl http://localhost:8800/health` + `aify-doctor`.

---

## Task 1 — Status F1: `/turn-end` write amplification (the important one)

**PROOF (traced both ends, 2026-07-19):**
- Detector re-posts every 45s: `mcp/stdio/claude-turn-end-detector.js` (and the hermes mirror
  `hermes-gateway-turn-detector.js`) KEEP-CLEARED block re-calls `postTurnEnd()` every
  `idleRefreshMs=45000` for the agent's **entire idle life** whenever the transcript `classify()==="ended"`
  (never while in-flight).
- Server writes unconditionally: `service/routers/api_v2.py` `agent_turn_end` (~17150-17175) always does
  UPSERT `agent_turn_state` + `UPDATE agents SET last_seen` + `_apply_status_event(turn_end)` (writes
  `agent_status_state.in_turn`) + `db.commit()` + `_broadcast_engine_status` to all dashboards. The only
  early-return is the superseded-bridge guard; a live detector re-post falls straight through. The
  docstring's "calling when turn_busy is already 0 is a no-op" is **aspirational, not real**.
- Net: every idle agent → 1 write+commit+WS-broadcast every 45s, forever (~28-33 agents ≈ 0.6 writes/s +
  broadcasts, pure waste). This is the periodic-write anti-pattern the `_LIVE_STATE_CACHE` redesign + the
  whole db-lock remediation eliminated. Load begins as agents restart onto the KEEP-CLEARED bridge.

**THE TRAP (why the obvious fix breaks it):** served `working` derives from `agent_status_state.in_turn`
(`status_engine.py`: `if i.in_turn and live → working`), NOT `turn_busy`. KEEP-CLEARED exists to heal a
**stray `in_turn=1`**. So "skip when `turn_busy` already 0" would leave a stray `in_turn=1` latched forever
→ breaks the healing. **The short-circuit MUST require BOTH `turn_busy=0` AND `in_turn=0`** (a true no-op).

**Change** (`service/routers/api_v2.py`, in `agent_turn_end`, AFTER the superseded-bridge guard, BEFORE the
UPSERT at ~17149): add a fast-path SELECT of both bits; if both already cleared, return early WITHOUT the
write/commit/status-event/broadcast. Skipping `last_seen` in this no-op case is safe — the 60s liveness beat
owns liveness (verify: `mcp/stdio/liveness-heartbeat.js` beats unconditionally; `/heartbeat` refreshes
`last_seen`).

```python
        # No-op fast path (2026-07-19): a KEEP-CLEARED detector re-assert fires every ~45s for the
        # WHOLE idle life of every agent. When there is genuinely nothing to clear — turn_busy already 0
        # AND the engine's in_turn already 0 — the full write+commit+broadcast is pure waste (the
        # periodic-write anti-pattern the _LIVE_STATE_CACHE redesign removed). Skip it. A real stray
        # (either bit set) still takes the full clear below, preserving KEEP-CLEARED's healing purpose.
        _tb = await (await db.execute(
            "SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", (agent_id,)
        )).fetchone()
        _st = await (await db.execute(
            "SELECT in_turn FROM agent_status_state WHERE agent_id = ?", (agent_id,)
        )).fetchone()
        _turn_busy = int((_tb["turn_busy"] if _tb and "turn_busy" in _tb.keys() else 0) or 0)
        _in_turn = int((_st["in_turn"] if _st and "in_turn" in _st.keys() else 0) or 0)
        if _turn_busy == 0 and _in_turn == 0:
            return {"ok": True, "agentId": agent_id, "noop": "already-cleared"}
```

**VERIFY BEFORE WRITING:** confirm the column name is `in_turn` in `agent_status_state` (grep the table
schema in `service/db.py`); confirm `_apply_status_event(turn_end)` is what sets `in_turn=0` (already read:
`status_engine.py` sets `s["in_turn"]=0` on turn_end). Confirm no OTHER caller relies on `/turn-end`
refreshing `last_seen` as its sole liveness signal (it doesn't — heartbeat does).

**Blast radius:** the `agent_turn_end` handler only. Zero agent-visible status change (already idle). Removes
redundant writes/broadcasts. **Risk: LOW** with the both-bits-0 guard.

**Test** (`service/tests/` — new file `test_turn_end_noop_fastpath.py`, mirror `test_stranded_reply_fail.py`
harness): (a) turn_busy=1 → full clear runs (turn_busy→0, response has no `noop`); (b) both already 0 →
returns `{"noop":"already-cleared"}` and does NOT write (assert `turn_updated_at` unchanged across the call);
(c) turn_busy=0 but in_turn=1 (stray) → full clear runs and in_turn→0 (proves the healing is preserved —
this is the regression guard). Run: `python -m pytest service/tests/test_turn_end_noop_fastpath.py -q`.

---

## Task 2 — Console F2: `ownsPty` fails open → resizes the operator's resident PTY

**PROOF (traced):** `service/new_dashboard/app.js` (~1896):
`const ownsPty = String(agentForTerminal(terminalId)?.sessionMode || '').toLowerCase() !== 'resident';`
`agentForTerminal` → `agentForSession(sess)` returns `{}` when the agent isn't in `state.agents`, and reads
ONLY `agent.sessionMode`. So a missing agent object or empty mode → `'' !== 'resident'` → `ownsPty=true` →
`applyRenderedWidth(..., true)` → the dashboard POSTs `/terminals/<id>/resize` → the bridge SIGWINCHes the
operator's own resident terminal. This inverts the file's own safe-default convention (elsewhere unknown→
`'resident'`→don't-touch, e.g. ~2389, 3078, 3152).

**Change** — make `ownsPty` positively-managed, with a fallback to the session row's own mode:

```js
    // Own the PTY ONLY when POSITIVELY managed (2026-07-19). Unknown / missing-agent / resident all
    // fall through to false → we do NOT resize (a resident console mirrors the operator's real terminal;
    // SIGWINCHing it is the exact harm this guard prevents). Falls back to the session row's own mode so
    // a not-yet-populated state.agents can't flip a resident console to "owned".
    const _sess = (state.sessions || []).find((x) =>
      String(x?.terminalId || x?.terminal?.id || x?.terminal_id || '') === String(terminalId || ''));
    const _mode = String(
      agentForTerminal(terminalId)?.sessionMode || _sess?.sessionMode || _sess?.session_mode || ''
    ).toLowerCase();
    const ownsPty = _mode === 'managed';
```

**VERIFY BEFORE WRITING:** confirm the session object carries `sessionMode`/`session_mode` (grep how
`state.sessions` rows are shaped / where session_mode is read elsewhere in app.js, e.g. ~2389). Keep the
existing `applyRenderedWidth(...)` + `state.activeXterm.ownsPty = ownsPty` lines unchanged after it.

**Blast radius:** dashboard resize decision only. Failure mode flips from HARMFUL (resize the operator's
terminal) to COSMETIC (a managed console whose agent+session are BOTH momentarily unknown briefly isn't
resized-to-fit; self-heals when `state.agents`/`state.sessions` populate on the next poll). **Risk: LOW.**

**Test:** add a structural guard in `service/new_dashboard/app.test.mjs` asserting `ownsPty` is derived from
`=== 'managed'` (not `!== 'resident'`) and that a session-mode fallback is present. (Runtime DOM test is
overkill for this tier — the existing suite is structural.)

---

## Task 3 — Console F1: Start-agent 409 is misleading during boot

**PROOF (traced):** `service/routers/api_v2.py` control_agent `start` (~14294-14311): after the `live`
(already-running) check, it calls `_coldstart_spawn_request_for_dispatch(...) → started`, and `if not
started:` raises 409 "no environment bridge is available." But `_coldstart` returns `False` for SEVERAL
reasons incl. an already-pending spawn (`_has_pending_or_booting_spawn_request` truthy — idempotent
success). So clicking Start twice during a slow hermes boot (no session row yet → button re-enabled) yields
a false "no environment bridge — start one with `aify-comms`" while the agent is in fact coming up.

**Change** — distinguish the pending-spawn case before the 409:

```python
            if not started:
                if await _has_pending_or_booting_spawn_request(db, agent_id):
                    return {"ok": True, "agentId": agent_id, "action": "start", "spawnPending": True}
                raise HTTPException(
                    409,
                    f'Could not start "{agent_id}" — no environment bridge is available to run it. '
                    "Start one on its host with `aify-comms`.",
                )
```

**VERIFY BEFORE WRITING:** confirm `_has_pending_or_booting_spawn_request` exists, is `async`, and takes
`(db, agent_id)` (grep def, ~7501). If its signature differs, adapt. Confirm the dashboard tolerates a
`spawnPending`/200 response on the Start button (it should — it already handles `alreadyRunning`).

**Blast radius:** the endpoint's response for the pending case only; no change to spawn behavior. **Risk:
LOW.**

**Test:** `service/tests/test_api_v2_regressions.py` — seed a pending spawn_request for a cold agent, POST
the start control, assert 200 `spawnPending` (not 409). Reuse existing spawn-request seed helpers.

---

## NOT doing (with reasoning)

### Status F2 — widen `_PROMPT_MARKER_RE` to catch `your call:` → **RECOMMEND SKIP** (operator to confirm)
The blocked HARD-prompt branches (`(y/n)`, `enter to confirm`, `use arrows`) are well-gated (spinner
suppression + near-bottom staleness + reconstructed screen). But the `your call:` branch matches
decision-flavored **prose**, not a hard prompt — it CAN read `blocked` on an agent that merely ENDED with
"…your call: continue or revert?" as text while actually idle. And a false `blocked` is NOT cosmetic: blocked
targets DEFER reply-reminders/delivery, so a false blocked can strand work. Current behavior on
hermes/codex/pi is that `your call:` shows `working` — a SAFE default (working never defers delivery). The
risk/reward does not favor widening a fuzzy signal that can strand delivery. **If the operator wants it
anyway:** do NOT add the verbs (`continue`/`switch`/etc.) to the pre-gate (common words defeat the
cheap-skip); add ONLY `yourcall`; and first TIGHTEN the branch (require `your call:` to be the absolute last
non-whitespace line with the verb in the same clause) to cut prose false-positives.

### Usage cache stampede — OPTIONAL LOW
`service/routers/api_v2.py` (~10213): `_OPENAI_POOL_CACHE["at"]` is stamped AFTER the 8s `await
collect_openai_pool()`, so concurrent `/usage` calls in that window each fire the API. Fix if convenient:
stamp `["at"]=now` BEFORE the await (accept one wasted double-fetch on a genuine race), or wrap in an
`asyncio.Lock`. Not urgent; the token is currently connected (aify-doctor green).

---

## Execution order
1. Task 1 (backend) → test → commit.
2. Task 3 (backend) → test → commit. (Can share the backend rebuild with Task 1.)
3. Task 2 (dashboard) → test → commit.
4. Full suites: `node mcp/stdio/tests/run-all.mjs` + `python -m pytest service/tests/ -q`.
5. `docker compose up -d --build` → `curl :8800/health` → `aify-doctor`. No `install.sh` / wrapper restart.
</content>
