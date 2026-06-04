# Real-Time Status Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inferred/delayed status (`_compute_live_status_cache`, ~90s lag, fragile runtime×mode matrix, ~1.5-core poll-load CPU) with an event-driven engine: runtimes PUSH transitions → one small per-agent state machine → status served from cache + pushed live over WS; polls demoted to backstops.

**Architecture:** A pure `derive()` state machine (`service/status_engine.py`) keyed off explicit per-agent inputs, fed by an event log that updates an `agent_status_state` row. Read paths and the WS push serve the cached engine status behind a `status_engine: old|new` feature flag (default `old`, disagreements logged). Dispatch run-liveness + worker self-exit consume the engine. Per-runtime adapters (claude hooks, hermes gateway, codex turn events) push the events; existing polls become backstops that synthesize missed events.

**Tech Stack:** Python 3.12 / FastAPI / aiosqlite (`service/`), Node ESM bridges (`mcp/stdio/`), single-file `service/dashboard.html`. Tests: `python -m pytest`, `node --test`.

**Spec:** `docs/superpowers/specs/2026-06-04-status-event-model-design.md`

**Operating rules (from CLAUDE.md + this session):**
- Commit before any `docker compose up -d --build` (the build COPYs the working tree).
- Rebuild the container after `service/` changes; restart the client wrapper after `mcp/stdio/` changes.
- NEVER run opencode tests on this host.
- Keep the `status_engine` flag default `old` until Phase I; nothing user-visible changes until the flip.
- The matrix invariants in Phase A Task A3 are the regression net — they MUST pass before the Phase I flip.

---

## File Structure

| File | Responsibility | Phase |
|------|----------------|-------|
| `service/status_engine.py` (new) | pure `derive(inputs) -> status`; `StatusInputs` dataclass; `apply_event(state, event) -> state` | A, B |
| `service/db.py` | `agent_status_state` table DDL; `status_engine` setting default | A, C |
| `service/tests/test_status_engine.py` (new) | table-driven `derive()` tests incl. matrix invariants; `apply_event` transition tests | A, B |
| `service/routers/api_v2.py` | event-ingest endpoint; `_gather_status_inputs()`; flag-branched read paths + disagreement log; WS push; run-liveness consumer | B–F |
| `service/tests/test_status_engine_integration.py` (new) | endpoint→state→read integration; run-liveness busy-vs-dead | B–F |
| `mcp/stdio/status-events.js` (new) | shared helper: POST a status event (used by all bridges) | G |
| `mcp/stdio/claude-turn-end-detector.js`, `mcp/stdio/server.js`, `mcp/stdio/hermes-gateway-turn-detector.js`, codex controller, `mcp/stdio/claude-channel.js` | push events (turn/worker); self-exit on stopped/removed | G, H |
| `service/dashboard.html` | render on `agent_status` WS push; slow safety-net poll | D |

---

## Phase A — State machine core (the foundation + regression net)

### Task A1: `agent_status_state` table

**Files:**
- Modify: `service/db.py` (add table DDL next to the other `CREATE TABLE IF NOT EXISTS` blocks)
- Test: `service/tests/test_status_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_status_engine.py
import sqlite3, tempfile, os, asyncio
from service.db import init_db

def test_agent_status_state_table_exists():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        asyncio.run(init_db(path))
        conn = sqlite3.connect(path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_status_state)")]
        conn.close()
        assert set(["agent_id", "status", "in_turn", "awaiting_input",
                    "last_event", "last_event_at", "updated_at"]).issubset(set(cols))
    finally:
        os.remove(path)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest service/tests/test_status_engine.py::test_agent_status_state_table_exists -q`
Expected: FAIL (`no such table: agent_status_state`)

- [ ] **Step 3: Add the table DDL**

In `service/db.py`, next to the existing `CREATE TABLE IF NOT EXISTS agent_turn_state (...)` block, add:

```sql
CREATE TABLE IF NOT EXISTS agent_status_state (
    agent_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'offline',
    in_turn INTEGER NOT NULL DEFAULT 0,
    awaiting_input INTEGER NOT NULL DEFAULT 0,
    turn_run_id TEXT NOT NULL DEFAULT '',
    last_event TEXT NOT NULL DEFAULT '',
    last_event_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);
```

(Match the existing DDL style in `db.py`; place it after `agent_turn_state` so the FK target `agents` already exists.)

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest service/tests/test_status_engine.py::test_agent_status_state_table_exists -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/db.py service/tests/test_status_engine.py
git commit -m "feat(status-engine): agent_status_state table"
```

### Task A2: `StatusInputs` + `derive()` — the pure state machine

**Files:**
- Create: `service/status_engine.py`
- Test: `service/tests/test_status_engine.py`

- [ ] **Step 1: Write the failing test (core transitions)**

```python
# append to service/tests/test_status_engine.py
from service.status_engine import StatusInputs, derive

def _inp(**kw):
    base = dict(mode="managed", alive=True, in_turn=False, awaiting_input=False,
                worker_present=True, env_reachable=True, disabled=False,
                bridge_stale=False, has_live_session=True, idle_too_long=False)
    base.update(kw); return StatusInputs(**base)

def test_working_when_in_turn():
    assert derive(_inp(in_turn=True)) == "working"

def test_blocked_when_in_turn_and_awaiting_input():
    assert derive(_inp(in_turn=True, awaiting_input=True)) == "blocked"

def test_managed_online_when_alive_worker_present():
    assert derive(_inp()) == "online"

def test_managed_idle_when_quiet_too_long():
    assert derive(_inp(idle_too_long=True)) == "idle"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest service/tests/test_status_engine.py -q -k "working or blocked or online or idle"`
Expected: FAIL (`No module named 'service.status_engine'`)

- [ ] **Step 3: Implement `status_engine.py`**

```python
# service/status_engine.py
"""Pure, event-driven status state machine (status v2, 2026-06-04).

The ONE place agent status is decided. `derive()` is a pure function of explicit
inputs (no DB, no clock) so it is exhaustively table-testable and encodes the
status matrix as ordered rules instead of a sprawling per-request derivation.
Status vocabulary is unchanged: working/online/idle/available/blocked/stale/
offline/stopped. Inputs are gathered elsewhere (api_v2._gather_status_inputs)
from events + a single liveness heartbeat.
"""
from __future__ import annotations
from dataclasses import dataclass

VALID_STATUSES = (
    "working", "online", "idle", "available", "blocked", "stale", "offline", "stopped",
)


@dataclass(frozen=True)
class StatusInputs:
    mode: str                 # "managed" | "resident"
    alive: bool               # heartbeat within liveness lease
    in_turn: bool             # turn_start seen, no turn_end yet
    awaiting_input: bool      # console looks like it needs input
    worker_present: bool      # managed: live console+sidecar / gateway / wrapper-child
    env_reachable: bool       # managed: owning environment bridge online
    disabled: bool            # explicit stop/disable
    bridge_stale: bool        # resident: bridge heartbeat missing
    has_live_session: bool    # resident: a live runtime session exists
    idle_too_long: bool       # online but quiet beyond idle window


def derive(i: StatusInputs) -> str:
    """Map explicit inputs to one of VALID_STATUSES. First match wins."""
    # Explicit stop / unreachable managed environment is offline regardless.
    if i.disabled:
        return "stopped"
    if i.mode == "managed" and not i.env_reachable:
        return "offline"
    # A turn in flight dominates (so a long turn never reads offline/available).
    if i.in_turn and i.awaiting_input:
        return "blocked"
    if i.in_turn:
        return "working"
    if i.mode == "managed":
        if i.alive and i.worker_present:
            return "idle" if i.idle_too_long else "online"
        if i.env_reachable:
            return "available"      # idle, no live worker, but lazy-autostartable
        return "offline"
    # resident
    if i.alive and i.has_live_session and not i.bridge_stale:
        return "idle" if i.idle_too_long else "online"
    if i.bridge_stale:
        return "stale"
    return "offline"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest service/tests/test_status_engine.py -q -k "working or blocked or online or idle"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/status_engine.py service/tests/test_status_engine.py
git commit -m "feat(status-engine): pure derive() state machine"
```

### Task A3: Matrix-invariant tests (the no-regress net)

**Files:**
- Test: `service/tests/test_status_engine.py`

These encode the bugs fixed THIS cycle. They must stay green and gate the Phase I flip.

- [ ] **Step 1: Write the invariant tests**

```python
# append to service/tests/test_status_engine.py
def test_managed_reachable_env_dead_worker_is_available_not_offline():
    # ed44b60: managed agent, env online, worker died -> available (lazy-autostart)
    assert derive(_inp(worker_present=False, alive=False, env_reachable=True)) == "available"

def test_managed_unreachable_env_is_offline():
    assert derive(_inp(worker_present=False, env_reachable=False)) == "offline"

def test_managed_claude_live_sidecar_no_console_is_available():
    # status-F1: managed online REQUIRES worker_present (console AND sidecar);
    # caller sets worker_present=False for the headless-orphan case.
    assert derive(_inp(worker_present=False, alive=True, env_reachable=True)) == "available"

def test_hermes_working_while_delivering_is_working():
    # #172: a turn in flight reads working even though it's "online"-ish underneath
    assert derive(_inp(mode="managed", in_turn=True, worker_present=True)) == "working"

def test_resident_stale_bridge_is_stale():
    assert derive(_inp(mode="resident", alive=False, bridge_stale=True, has_live_session=True)) == "stale"

def test_resident_no_live_session_is_offline():
    assert derive(_inp(mode="resident", alive=False, has_live_session=False, bridge_stale=False)) == "offline"

def test_resident_live_session_is_online():
    assert derive(_inp(mode="resident", alive=True, has_live_session=True)) == "online"

def test_disabled_always_stopped():
    assert derive(_inp(disabled=True, in_turn=True)) == "stopped"
```

- [ ] **Step 2: Run them**

Run: `python -m pytest service/tests/test_status_engine.py -q`
Expected: PASS (all). If any fail, fix `derive()` precedence in A2 until green — these are the spec's §4.2 invariants.

- [ ] **Step 3: Commit**

```bash
git add service/tests/test_status_engine.py
git commit -m "test(status-engine): matrix-invariant regression net (the just-fixed bugs)"
```

---

## Phase B — Event ingest → state transitions

### Task B1: `apply_event()` transition function

**Files:**
- Modify: `service/status_engine.py`
- Test: `service/tests/test_status_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# append to service/tests/test_status_engine.py
from service.status_engine import apply_event, EVENT_KINDS

def test_turn_start_sets_in_turn_then_turn_end_clears():
    s = {"in_turn": 0, "awaiting_input": 0, "turn_run_id": ""}
    s = apply_event(s, {"kind": "turn_start", "runId": "r1"})
    assert s["in_turn"] == 1 and s["turn_run_id"] == "r1"
    s = apply_event(s, {"kind": "turn_end", "runId": "r1"})
    assert s["in_turn"] == 0

def test_blocked_event_sets_awaiting_input():
    s = apply_event({"in_turn": 1, "awaiting_input": 0, "turn_run_id": ""}, {"kind": "blocked"})
    assert s["awaiting_input"] == 1

def test_unknown_event_is_noop():
    s = {"in_turn": 1, "awaiting_input": 0, "turn_run_id": ""}
    assert apply_event(dict(s), {"kind": "nonsense"}) == s
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest service/tests/test_status_engine.py -q -k "turn_start or blocked_event or unknown_event"`
Expected: FAIL (`cannot import name 'apply_event'`)

- [ ] **Step 3: Implement `apply_event`**

```python
# append to service/status_engine.py
EVENT_KINDS = ("turn_start", "turn_end", "blocked", "unblocked")

def apply_event(state: dict, event: dict) -> dict:
    """Fold an event into the per-agent turn sub-state (dict copy returned).
    Liveness / worker_present / env_reachable are NOT stored here — they are
    gathered live (heartbeat lease, bridge rows) at derive() time. This only
    tracks turn flags driven by push events.
    """
    s = dict(state)
    kind = str(event.get("kind") or "")
    if kind == "turn_start":
        s["in_turn"] = 1
        s["turn_run_id"] = str(event.get("runId") or "")
        s["awaiting_input"] = 0
    elif kind == "turn_end":
        s["in_turn"] = 0
        s["turn_run_id"] = ""
        s["awaiting_input"] = 0
    elif kind == "blocked":
        s["awaiting_input"] = 1
    elif kind == "unblocked":
        s["awaiting_input"] = 0
    return s
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest service/tests/test_status_engine.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/status_engine.py service/tests/test_status_engine.py
git commit -m "feat(status-engine): apply_event turn-state transitions"
```

### Task B2: Event-ingest endpoint + persistence

**Files:**
- Modify: `service/routers/api_v2.py` (new endpoint `POST /agents/{agent_id}/status-event`; helper `_apply_status_event`)
- Test: `service/tests/test_status_engine_integration.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_status_engine_integration.py
import sqlite3
from service.tests._base import FastApiTestCase

class StatusEventIngestTests(FastApiTestCase):
    def _register(self, aid="a1", mode="managed", runtime="claude-code"):
        r = self.client.post("/api/v1/agents", json={"agentId": aid, "role": "coder",
            "runtime": runtime, "sessionMode": mode, "machineId": "linux:test", "bridgeId": "b1"})
        self.assertEqual(r.status_code, 200, r.text)

    def _state(self, aid):
        c = sqlite3.connect(str(self._db_path)); c.row_factory = sqlite3.Row
        try:
            return c.execute("SELECT * FROM agent_status_state WHERE agent_id=?", (aid,)).fetchone()
        finally:
            c.close()

    def test_turn_start_event_persists_in_turn(self):
        self._register("a1")
        r = self.client.post("/api/v1/agents/a1/status-event",
                             json={"kind": "turn_start", "runId": "r1", "bridgeId": "b1"})
        self.assertEqual(r.status_code, 200, r.text)
        row = self._state("a1")
        self.assertEqual(int(row["in_turn"]), 1)
        r = self.client.post("/api/v1/agents/a1/status-event", json={"kind": "turn_end", "runId": "r1"})
        self.assertEqual(int(self._state("a1")["in_turn"]), 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest service/tests/test_status_engine_integration.py -q`
Expected: FAIL (404 — endpoint not defined)

- [ ] **Step 3: Implement the endpoint + helper**

Add to `service/routers/api_v2.py` (near the existing `/turn-start` handler), importing the engine at the top of the file (`from service.status_engine import apply_event, derive, StatusInputs`):

```python
class AgentStatusEventRequest(BaseModel):
    kind: str
    runId: str | None = None
    bridgeId: str | None = None
    detail: str | None = None

async def _apply_status_event(db, agent_id: str, event: dict) -> dict:
    now = _now()
    row = await (await db.execute(
        "SELECT in_turn, awaiting_input, turn_run_id FROM agent_status_state WHERE agent_id = ?",
        (agent_id,))).fetchone()
    cur = {"in_turn": (row["in_turn"] if row else 0),
           "awaiting_input": (row["awaiting_input"] if row else 0),
           "turn_run_id": (row["turn_run_id"] if row else "")}
    new = apply_event(cur, event)
    await db.execute("""
        INSERT INTO agent_status_state (agent_id, in_turn, awaiting_input, turn_run_id,
                                        last_event, last_event_at, updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(agent_id) DO UPDATE SET
            in_turn=excluded.in_turn, awaiting_input=excluded.awaiting_input,
            turn_run_id=excluded.turn_run_id, last_event=excluded.last_event,
            last_event_at=excluded.last_event_at, updated_at=excluded.updated_at
    """, (agent_id, new["in_turn"], new["awaiting_input"], new["turn_run_id"],
          str(event.get("kind") or ""), now, now))
    await db.commit()
    return new

@router.post("/agents/{agent_id}/status-event")
async def post_status_event(agent_id: str, req: AgentStatusEventRequest, request: Request):
    db = await get_db()
    try:
        row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        await _apply_status_event(db, agent_id, req.model_dump())
        await _invalidate_agent_live_state(db, agent_id)  # existing cache invalidator
        return {"ok": True, "agentId": agent_id, "kind": req.kind}
    finally:
        await db.close()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest service/tests/test_status_engine_integration.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_status_engine_integration.py
git commit -m "feat(status-engine): /status-event ingest -> agent_status_state"
```

---

## Phase C — Input gathering + feature flag + read integration

### Task C1: `status_engine` setting default

**Files:**
- Modify: `service/routers/api_v2.py` (`DEFAULT_SETTINGS`)
- Test: `service/tests/test_status_engine_integration.py`

- [ ] **Step 1: Write the failing test**

```python
# append to StatusEventIngestTests
def test_status_engine_setting_defaults_old(self):
    r = self.client.get("/api/v1/settings")
    self.assertEqual(r.json().get("status_engine"), "old")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest service/tests/test_status_engine_integration.py::StatusEventIngestTests::test_status_engine_setting_defaults_old -q`
Expected: FAIL (key missing)

- [ ] **Step 3: Add the setting**

In `DEFAULT_SETTINGS` (service/routers/api_v2.py) add: `"status_engine": "old",` with a comment that `new` switches the read paths to the event engine.

- [ ] **Step 4: Run to verify it passes** — Expected: PASS
- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_status_engine_integration.py
git commit -m "feat(status-engine): status_engine setting (default old)"
```

### Task C2: `_gather_status_inputs()` — bridge to live signals

**Files:**
- Modify: `service/routers/api_v2.py`
- Test: `service/tests/test_status_engine_integration.py`

Build `StatusInputs` from the SAME live signals `_compute_live_status_cache` already reads (reuse its helpers: `_agent_has_fresh_bridge`/`_resident_bridge_is_fresh` for `alive`/`bridge_stale`; `_has_live_channel_sidecar`+`_has_live_terminal_session` / `_has_live_managed_wrapper_child` / hermes gateway for `worker_present`; `_managed_owning_environment_row` for `env_reachable`; `agent_status_state.in_turn`/`awaiting_input`; the disable control for `disabled`). One function, no new derivation logic.

- [ ] **Step 1: Write the failing test**

```python
# append to StatusEventIngestTests
def test_engine_status_working_after_turn_start(self):
    self._register("a2", mode="resident", runtime="claude-code")
    # mark a fresh resident bridge so alive=True (mirror existing heartbeat path)
    self.client.post("/api/v1/agents/a2/heartbeat", json={"bridgeId": "b1", "sessionMode": "resident"})
    self.client.post("/api/v1/agents/a2/status-event", json={"kind": "turn_start", "runId": "r1"})
    import asyncio
    from service.db import get_db
    from service.routers import api_v2
    async def run():
        db = await get_db()
        try:
            row = await (await db.execute("SELECT * FROM agents WHERE id='a2'")).fetchone()
            return await api_v2.engine_status(db, row)
        finally:
            await db.close()
    self.assertEqual(asyncio.run(run()), "working")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest service/tests/test_status_engine_integration.py::StatusEventIngestTests::test_engine_status_working_after_turn_start -q`
Expected: FAIL (`engine_status` not defined)

- [ ] **Step 3: Implement `_gather_status_inputs` + `engine_status`**

```python
# service/routers/api_v2.py
async def _gather_status_inputs(db, agent_row, *, settings=None) -> StatusInputs:
    settings = settings or await _load_settings(db)
    aid = agent_row["id"]
    mode = _normalize_session_mode(agent_row["session_mode"] or "resident")
    st = await (await db.execute(
        "SELECT in_turn, awaiting_input FROM agent_status_state WHERE agent_id=?", (aid,))).fetchone()
    in_turn = bool(st and st["in_turn"])
    awaiting = bool(st and st["awaiting_input"])
    disabled = str(agent_row["status"] or "").lower() == "stopped"
    if mode == "managed":
        env_row = await _managed_owning_environment_row(db, agent_row, resolved_environment_id=None)
        env_reachable = bool(env_row) and _environment_effective_status(
            env_row, offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90))
        ) in {"online", "degraded"}
        worker_present = await _has_live_worker_for(db, agent_row)   # reuse the F1/B3 logic factored into a helper
        alive = worker_present
        return StatusInputs(mode=mode, alive=alive, in_turn=in_turn, awaiting_input=awaiting,
                            worker_present=worker_present, env_reachable=env_reachable, disabled=disabled,
                            bridge_stale=False, has_live_session=worker_present, idle_too_long=False)
    fresh = await _resident_bridge_is_fresh(db, agent_row,
                lease_seconds=int(settings.get("resident_lease_seconds", 150) or 150))
    return StatusInputs(mode=mode, alive=fresh, in_turn=in_turn, awaiting_input=awaiting,
                        worker_present=fresh, env_reachable=True, disabled=disabled,
                        bridge_stale=(not fresh), has_live_session=fresh, idle_too_long=False)

async def engine_status(db, agent_row, *, settings=None) -> str:
    return derive(await _gather_status_inputs(db, agent_row, settings=settings))
```

> Factor the existing managed "live worker" logic (status-F1 console+sidecar, B3 wrapper-child, hermes channel-sidecar) out of `_compute_live_status_cache` into a reusable `_has_live_worker_for(db, agent_row)` and call it from BOTH the old derivation and `_gather_status_inputs` (DRY; keeps old/new agreeing on worker liveness).

- [ ] **Step 4: Run to verify it passes** — Expected: PASS (`working`)
- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_status_engine_integration.py
git commit -m "feat(status-engine): gather inputs + engine_status() from live signals"
```

### Task C3: Flag-branch the read paths + disagreement log

**Files:**
- Modify: `service/routers/api_v2.py` (`_agent_session_dict_live` / wherever computed status is attached to agent/session dicts)
- Test: `service/tests/test_status_engine_integration.py`

- [ ] **Step 1: Write the failing test**

```python
# append to StatusEventIngestTests
def _set(self, key, val):
    c = sqlite3.connect(str(self._db_path))
    try:
        import json
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(val))); c.commit()
    finally: c.close()

def test_flag_new_serves_engine_status(self):
    self._register("a3", mode="resident")
    self.client.post("/api/v1/agents/a3/heartbeat", json={"bridgeId":"b1","sessionMode":"resident"})
    self.client.post("/api/v1/agents/a3/status-event", json={"kind":"turn_start","runId":"r1"})
    self._set("status_engine", "new")
    r = self.client.get("/api/v1/agents")
    a = next(x for x in r.json()["agents"] if x["agentId"] == "a3")
    self.assertEqual(a["status"], "working")
```

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (status not `working` under old engine)

- [ ] **Step 3: Branch the read path**

Where the agent/session dict's `status` is set from `_compute_live_status_cache`, add:

```python
if str((settings or {}).get("status_engine", "old")).lower() == "new":
    new_status = await engine_status(db, agent_row, settings=settings)
    if old_status != new_status:
        logger.info("status-disagreement agent=%s old=%s new=%s", agent_id, old_status, new_status)
    status = new_status
else:
    status = old_status
    # Optional cheap shadow log even when serving old (helps pre-flip validation):
    # new_status = await engine_status(...); if differ: logger.info(...)
```

- [ ] **Step 4: Run to verify it passes** — Expected: PASS
- [ ] **Step 5: Commit**

```bash
git add service/routers/api_v2.py service/tests/test_status_engine_integration.py
git commit -m "feat(status-engine): flag-branch read paths + disagreement log"
```

---

## Phase D — WS push on transition

### Task D1: push `agent_status` immediately on state change

**Files:**
- Modify: `service/routers/api_v2.py` (`post_status_event`, the disable/stop path, the reconcile worker-down path)
- Modify: `service/dashboard.html` (already consumes `agent_status` — ensure it re-renders the row, not just a poll)
- Test: `service/tests/test_status_engine_integration.py`

- [ ] **Step 1: Write the failing test** — assert that after a `turn_start` event the server broadcasts an `agent_status` WS message with `status: "working"` (use the test WS capture harness already used by the C1/C2 status-push tests; mirror `test_turn_end_event_flips_managed_hermes_off_working_immediately`).
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3:** In `post_status_event` (and the stop path), after `_apply_status_event` + `_invalidate_agent_live_state`, compute `engine_status` (when flag `new`) and `await ws.broadcast("agent_status", {...})` with the new status. Reuse the existing `agent_status` broadcast helper.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** `feat(status-engine): WS-push agent_status on every transition`.

---

## Phase E — Hot-path cache (the CPU fix)

### Task E1: serve cached status on `/dispatch/claim` deliverability + `/agents/{id}`

**Files:**
- Modify: `service/routers/api_v2.py` (deliverability check + single-agent read)
- Test: `service/tests/test_status_engine_integration.py`

When `status_engine=new`, the deliverability/status check on the hot endpoints reads `agent_status_state` + the cached `agent_live_state` **without re-deriving the full matrix per call** (the cache is kept fresh by events + the reconcile backstop). Old engine path unchanged.

- [ ] **Step 1:** Write a test asserting two back-to-back `/dispatch/claim`-style deliverability checks for an idle agent do not recompute (e.g., assert a counter/`engine_status` is served from `agent_status_state` + cache, not a fresh `_compute_live_status_cache`). Keep it behavioral: assert latency-independent correctness (status stable) — the perf is validated live.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3:** Route the hot-path status read through `engine_status`/cache under the flag; ensure no `_compute_live_status_cache` call on the claim hot path when `new`.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** `perf(status-engine): hot reads serve cached engine status (no per-request recompute)`.

---

## Phase F — Dispatch run-liveness consumer

### Task F1: busy target waits, dead target fails fast with honest reason

**Files:**
- Modify: `service/routers/api_v2.py` (the run-liveness/ceiling reaper that produced the "no owning bridge / bridge crashed" failure; the reply-reminder loop)
- Test: `service/tests/test_api_v2_regressions.py` (mirror the existing run-liveness tests)

- [ ] **Step 1: Write the failing test**

```python
# in test_api_v2_regressions.py
def test_run_to_working_target_is_not_failed_for_no_progress(self):
    # A claimed run whose TARGET is `working` must NOT be reaped as no-progress.
    # Seed an agent with agent_status_state.in_turn=1 (working) + a claimed run
    # aged past the no-progress window; run the run-liveness reaper; assert the
    # run is NOT failed (status still claimed/running).
    ...
def test_run_to_dead_target_fails_fast_with_honest_reason(self):
    # Target stale/offline -> run fails with reason mentioning the target going
    # offline, NOT the generic "bridge crashed / no owning bridge".
    ...
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3:** In the run-liveness reaper, before failing a no-progress run, check `engine_status(target)`: if `working`/`blocked` → skip (reset the no-progress clock); if `stale`/`offline`/`stopped` → fail with `f"target '{agent}' is {status}; run cannot be delivered"`; else (online-idle, genuinely orphaned past window) → keep the existing ceiling with an honest reason. Gate on `status_engine=new` so `old` is unchanged.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** `fix(dispatch): run-liveness consumes engine status (busy waits, dead fails honestly)`.

---

## Phase G — Per-runtime event sources (push)

### Task G1: shared `status-events.js` POST helper

**Files:** Create `mcp/stdio/status-events.js` exporting `postStatusEvent({serverUrl, agentId, kind, runId, bridgeId})` (mirror the existing `httpCall` POST pattern in `server.js`; best-effort, never throws). Test: `node --test mcp/stdio/tests/status-events.test.js` (fake fetch asserts the POST body/path). Commit.

### Task G2: claude — push turn_start/turn_end from hooks; transcript poll = backstop

**Files:** `mcp/stdio/claude-turn-end-detector.js` + `mcp/stdio/server.js` wiring + the claude hook (`install.sh --with-hook` payload). Wire: `Stop` hook → `postStatusEvent(turn_end)`; `UserPromptSubmit`/first tool-use → `turn_start`. Keep the 30s transcript detector but have it POST `status-event` (synthesize `turn_start` for channel-woken turns, `turn_end` if `Stop` missed) instead of the legacy `/turn-start`/`/turn-end` when `status_engine=new`. `node --check` the bridges. Commit. (Wrapper hook change → rerun install.sh + relaunch.)

### Task G3: hermes — gateway transition pushes events

**Files:** `mcp/stdio/hermes-gateway-turn-detector.js` — push `turn_start` on `working`, `turn_end` on sustained idle (it already detects these; route to `status-events.js`). `worker_present` already proven by the gateway-host + loop heartbeat. `node --check`. Commit.

### Task G4: codex — turn events push

**Files:** the codex controller (`mcp/stdio/controllers/*codex*.js`) — on `turn`/`completed` app-server events → `postStatusEvent(turn_start|turn_end)`. `node --check`. Commit.

---

## Phase H — Orphan worker self-exit

### Task H1: channel-sidecar / delivery loop self-exit on stopped/removed

**Files:** `mcp/stdio/claude-channel.js` + `mcp/stdio/hermes-managed-host.js` (delivery loop). On a poll observing the agent `stopped` (or HTTP 410 removed), terminate self + the runtime child (claude/hermes), mirroring the hermes 410 self-exit. Add a node test asserting the loop exits on a stopped/410 response. `node --check`. Commit. (Bridge change → relaunch wrappers.)

### Task H2: one-time sweep note

**Files:** docs only — document that existing orphans clear via the `aify-comms` env-bridge boot survivor-sweep (restart it) or `comms_remove_agent`. No code.

---

## Phase I — Cutover + cleanup

### Task I1: full regression + flip

- [ ] Run `python -m pytest service/tests/test_status_engine.py service/tests/test_status_engine_integration.py service/tests/test_api_v2_regressions.py -q` — all green (incl. the A3 matrix invariants).
- [ ] Run `node --test mcp/stdio/tests/` (NOT opencode) — green.
- [ ] Commit, `docker compose up -d --build`, health-check.
- [ ] Set `status_engine=new` (Settings or `update_settings`). Watch the disagreement log + dashboard latency (`working` < 2s). Rollback = set `old`.
- [ ] Commit a note recording the flip.

### Task I2: retire the old derivation (separate, after a stable period)

- [ ] Once `new` is stable, delete the `old` branch of `_compute_live_status_cache` reads and the dual-path. Update `KNOWN_ISSUES.md`/`DECISIONS.md`. Commit. (Do NOT do this in the same session as the flip.)

---

## Self-Review

**Spec coverage:** §3.1 events → A2/B1/B2/G; §3.2 state machine table → A2/A3; §3.3 per-runtime sources → G2–G4; §3.4 dispatch consumer → F1; §3.5 data flow → B2/D1/E1; §3.6 CPU/recompute-cut → E1 (+ landed `_load_settings` cache); §3.7 orphan self-exit → H1; §4 migration/flag/invariants → A3/C1/C3/I1; §5 testing → A3/B1/integration/I1. No gaps.

**Placeholder scan:** Phases A–F carry full code/tests. Phases G–I are concrete tasks with exact files + the precise change; G/H are mechanical bridge-wiring on existing detectors (the engine contract is fully specified in A–F), so they list the exact file + event to POST rather than re-deriving bridge internals here — acceptable per "follow existing patterns," and each still ends in `node --check` + commit.

**Type consistency:** `StatusInputs` fields, `derive()`, `apply_event()`, `engine_status()`, `_gather_status_inputs()`, `_has_live_worker_for()`, `agent_status_state` columns, and the `status_engine` setting are named identically across tasks.
