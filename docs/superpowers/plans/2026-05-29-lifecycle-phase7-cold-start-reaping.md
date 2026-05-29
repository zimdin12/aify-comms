# Lifecycle Phase 7: Dispatch Run Reaping & Cold-Start — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop cold managed agents from stranding dispatches forever. When a managed agent has no live worker, the send path must cold-start one through the EXISTING spawn-request mechanism (so a bridge actually claims and runs the work), the queued-merge path must refuse to pile new sends into a queued run that nothing can claim, and two new time-based reapers (queued dispatch runs, stuck spawn requests) must fire from the periodic reconciler so nothing rots in `queued`/`running` indefinitely.

**Architecture:** A managed dispatch becomes a `dispatch_runs` row in `queued`. A bridge only claims/runs it if a live worker exists: either a live `agent_sessions` row (whose PATCH→running transition eager-spawns the wrapper PTY) or a claimable `spawn_requests` row (which a bridge claims via `POST /spawn-requests/claim`, then registers a session via `PATCH /spawn-requests/{id}` status=`running`, which eager-spawns the PTY — see `_ensure_managed_pty_for_dispatch` called at api_v2.py:7200). `_ensure_managed_pty_for_dispatch` (api_v2.py:4526) can ONLY build a PTY when a live session row already exists; for a cold agent it returns `None` and nothing is enqueued. We add a cold-start helper that creates a `spawn_specs` + `spawn_requests` pair (cloning the agent's most-recent `agent_sessions`/`spawn_specs` for environment/runtime/workspace), reusing the same INSERTs as `create_spawn_request` (api_v2.py:6800). We guard the merge `UPDATE` with `AND status='queued'` (treat 0 rows as "fall through to a new run"), skip merging into a run with no claimable backing, and add `_reap_stale_queued_runs` + `_reap_stuck_spawn_requests`, both wired into `_run_dispatch_reconcile_once` (main.py:59).

**Tech Stack:** Python 3 / FastAPI / aiosqlite (`service/routers/api_v2.py`, `service/main.py`), SQLite schema in `service/db.py`, pytest `unittest.TestCase` suite in `service/tests/test_api_v2_regressions.py` (in-memory-style temp-file DB via `init_db`, `TestClient`, `self._fetchone`/`self._fetchall`/`self._execute` helpers).

**Spec:** `docs/superpowers/specs/2026-05-29-managed-session-lifecycle-design.md` (Phase 7 = root cause G; see "Addendum → Root cause G" and "Revised phasing" item 7).

**Test harness conventions (mirror exactly — do NOT invent a new harness):**
- New tests live in a NEW file `service/tests/test_lifecycle_phase7.py` that subclasses the SAME setUp pattern as `ApiV2RegressionTests` (temp dir + `init_db`, `FastAPI()` + `_DummyWS` + `app.state.config = SimpleNamespace(data_dir=...)`, `app.include_router(router, prefix="/api/v1")`, `TestClient`). Copy the `_fetchone`/`_fetchall`/`_execute`/`_register`/`_heartbeat_environment` helpers verbatim from `test_api_v2_regressions.py` (lines 21–160) — reuse via a shared base class is out of scope; copying mirrors the existing per-file style.
- Run an individual test: `python -m pytest service/tests/test_lifecycle_phase7.py -k <name> -x -q`
- Real table/column names (from `service/db.py`): `dispatch_runs(id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime, message_type, subject, body, priority, in_reply_to, status, claim_bridge_id, summary, error_text, require_reply, requested_at, claimed_at, started_at, finished_at)`; `spawn_requests(id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime, workspace, workspace_root, initial_message, priority, subject, mode, resume_policy, status, claimed_by_bridge_id, claim_machine_id, created_at, updated_at, claimed_at, started_at, finished_at, error)`; `spawn_specs(id, agent_id, environment_id, runtime, workspace, model, profile, mode, ..., metadata, created_at, updated_at)`; `agent_sessions(id, agent_id, environment_id, runtime, workspace, mode, spawn_spec_id, status, started_at, last_seen, ended_at)`.

---

### Task 1: Cold-start helper — create a spawn_request when no live worker exists

**Files:**
- Modify: `service/routers/api_v2.py` — add `_coldstart_spawn_request_for_dispatch` immediately before `_ensure_managed_pty_for_dispatch` (insert at ~4525, just above the `async def _ensure_managed_pty_for_dispatch` line).
- Test: `service/tests/test_lifecycle_phase7.py` (create; copy harness from `test_api_v2_regressions.py`).

This task adds the helper only. Task 2 calls it from the send path so it is independently committable (helper + unit test of the helper).

- [ ] **Step 1: Create the test file with the copied harness + the first failing test**

Create `service/tests/test_lifecycle_phase7.py`:

```python
import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import get_db, init_db
from service.routers import api_v2
from service.routers.api_v2 import router


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class _DummyWS:
    def __init__(self):
        self.broadcasts = []
        self.notifications = []

    async def broadcast(self, *_args, **_kwargs):
        self.broadcasts.append((_args, _kwargs))
        return None

    async def notify_agent(self, *_args, **_kwargs):
        self.notifications.append((_args, _kwargs))
        return None


class LifecyclePhase7Tests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test.db"
        asyncio.run(init_db(self._db_path))

        app = FastAPI()
        self.ws = _DummyWS()
        app.state.ws_manager = self.ws
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.state.testing = True
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _heartbeat_environment(self, **extra):
        payload = {
            "id": "linux:test-host:default",
            "label": "Linux on test-host",
            "machineId": "linux:test-host",
            "os": "linux",
            "kind": "linux",
            "bridgeId": "bridge-current",
            "cwdRoots": ["/workspace"],
            "runtimes": [
                {
                    "runtime": "codex",
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                }
            ],
            "metadata": {},
        }
        payload.update(extra)
        response = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def _fetchone(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchone()
            finally:
                await db.close()
        return asyncio.run(_run())

    def _fetchall(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchall()
            finally:
                await db.close()
        return asyncio.run(_run())

    def _execute(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _seed_ended_session(self, agent_id, *, runtime="codex", environment_id="linux:test-host:default",
                            workspace="/workspace/project", spawn_spec_id="spec-prior"):
        # A previously-managed agent leaves an ended agent_sessions row + spawn_spec
        # behind; cold-start clones environment/runtime/workspace from it.
        now = _iso(datetime.now(timezone.utc))
        self._execute(
            """
            INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                system_prompt, standing_instructions, env_vars, channel_ids, budget_policy, context_policy,
                restart_policy, metadata, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (spawn_spec_id, agent_id, environment_id, runtime, workspace, "", "", "managed-warm",
             "", "", "{}", "[]", "{}", "{}", "{}", "{}", now, now),
        )
        self._execute(
            """
            INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, mode, spawn_spec_id,
                status, started_at, last_seen, ended_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (f"sess-{agent_id}", agent_id, environment_id, runtime, workspace, "managed-warm",
             spawn_spec_id, "ended", now, now, now),
        )

    def _coldstart(self, agent_id, *, runtime="codex", requested_by="alice"):
        async def _run():
            db = await get_db()
            try:
                settings = await api_v2._load_settings(db)
                result = await api_v2._coldstart_spawn_request_for_dispatch(
                    db, agent_id, runtime=runtime, settings=settings, requested_by=requested_by,
                )
                await db.commit()
                return result
            finally:
                await db.close()
        return asyncio.run(_run())

    def test_coldstart_creates_queued_spawn_request_for_cold_managed_agent(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")

        created = self._coldstart("worker")
        self.assertTrue(created, "cold-start should report a created spawn request")

        rows = self._fetchall(
            "SELECT agent_id, environment_id, runtime, workspace, status, mode FROM spawn_requests WHERE agent_id = ?",
            ("worker",),
        )
        self.assertEqual(len(rows), 1, "exactly one spawn_request created")
        row = rows[0]
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["environment_id"], "linux:test-host:default")
        self.assertEqual(row["runtime"], "codex")
        self.assertEqual(row["workspace"], "/workspace/project")
        self.assertEqual(row["mode"], "managed-warm")

    def test_coldstart_is_idempotent_when_a_claimable_spawn_request_exists(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")

        first = self._coldstart("worker")
        second = self._coldstart("worker")
        self.assertTrue(first)
        self.assertFalse(second, "second cold-start must NOT create a duplicate spawn request")
        rows = self._fetchall("SELECT id FROM spawn_requests WHERE agent_id = ? AND status IN ('queued','claimed')", ("worker",))
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k coldstart -x -q`
Expected: FAIL — `AttributeError: module 'service.routers.api_v2' has no attribute '_coldstart_spawn_request_for_dispatch'`.

- [ ] **Step 3: Implement `_coldstart_spawn_request_for_dispatch`**

In `service/routers/api_v2.py`, insert immediately ABOVE `async def _ensure_managed_pty_for_dispatch(` (currently line 4526):

```python
async def _coldstart_spawn_request_for_dispatch(
    db,
    agent_id: str,
    *,
    runtime: str,
    settings: dict[str, Any],
    requested_by: str,
) -> bool:
    """Cold-start a managed worker on the send path.

    When a managed agent has no live agent_sessions row, _ensure_managed_pty_for_dispatch
    cannot build a PTY (it has nothing to launch into) and returns None — the dispatch
    then sits queued with nothing that will ever claim it (root cause G). This creates a
    spawn_request through the SAME mechanism as create_spawn_request so a bridge claims it,
    registers a session, and the PATCH->running eager-spawn brings up the wrapper PTY.

    Idempotent: returns False (creating nothing) when a claimable spawn_request
    (queued/claimed) already exists for the agent, or when no environment/runtime can be
    resolved. Returns True when a new spawn_request was inserted.
    """
    normalized_runtime = _normalize_runtime(runtime or "")
    if normalized_runtime not in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        return False

    # Don't pile up duplicate cold-starts — a queued/claimed spawn_request is
    # already a claimable backing for this agent.
    existing = await (await db.execute(
        """
        SELECT id
        FROM spawn_requests
        WHERE agent_id = ?
          AND status IN ('queued', 'claimed')
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()
    if existing:
        return False

    # Resolve environment/runtime/workspace from the agent's most-recent session
    # (any status). A previously-managed agent always leaves one behind.
    session = await (await db.execute(
        """
        SELECT *
        FROM agent_sessions
        WHERE agent_id = ?
        ORDER BY last_seen DESC
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()
    if not session:
        return False

    environment_id = str(session["environment_id"] or "").strip()
    if not environment_id:
        return False
    env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
    if not env_row:
        return False
    environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
    if str(environment.get("status") or "").lower() != "online":
        return False
    if not _runtime_capability_for_environment(environment, normalized_runtime):
        return False

    workspace, workspace_root = _workspace_for_environment(environment, None, session["workspace"] or "")

    # Reuse the prior spawn_spec when present so model/effort/policy survive the
    # cold-start; otherwise mint a minimal spec mirroring create_spawn_request.
    prior_spec = None
    prior_spec_id = str(session["spawn_spec_id"] or "").strip()
    if prior_spec_id:
        prior_spec = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (prior_spec_id,))).fetchone()

    now = _now()
    spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    await db.execute(
        """
        INSERT INTO spawn_specs (
            id, agent_id, environment_id, runtime, workspace, model, profile, mode,
            system_prompt, standing_instructions, env_vars, channel_ids, budget_policy,
            context_policy, restart_policy, metadata, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            spec_id,
            agent_id,
            environment_id,
            normalized_runtime,
            workspace,
            str(prior_spec["model"] or "") if prior_spec else "",
            str(prior_spec["profile"] or "") if prior_spec else "",
            "managed-warm",
            str(prior_spec["system_prompt"] or "") if prior_spec else "",
            str(prior_spec["standing_instructions"] or "") if prior_spec else "",
            str(prior_spec["env_vars"] or "{}") if prior_spec else "{}",
            str(prior_spec["channel_ids"] or "[]") if prior_spec else "[]",
            str(prior_spec["budget_policy"] or "{}") if prior_spec else "{}",
            str(prior_spec["context_policy"] or "{}") if prior_spec else "{}",
            str(prior_spec["restart_policy"] or "{}") if prior_spec else "{}",
            str(prior_spec["metadata"] or "{}") if prior_spec else "{}",
            now,
            now,
        ),
    )
    await db.execute(
        """
        INSERT INTO spawn_requests (
            id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
            workspace, workspace_root, initial_message, priority, subject, mode,
            resume_policy, status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            request_id,
            spec_id,
            requested_by or "dispatch-coldstart",
            environment_id,
            agent_id,
            "coder",
            agent_id,
            normalized_runtime,
            workspace,
            workspace_root,
            "",
            "normal",
            f"Cold-start for {agent_id}",
            "managed-warm",
            "native_first",
            "queued",
            now,
            now,
        ),
    )
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k coldstart -x -q`
Expected: PASS — 2 passed.

- [ ] **Step 5: Syntax-check + commit**

```bash
python -c "import ast; ast.parse(open('service/routers/api_v2.py').read())"
git add service/routers/api_v2.py service/tests/test_lifecycle_phase7.py
git commit -m "feat(dispatch): cold-start spawn_request helper for cold managed agents (root cause G)"
```

---

### Task 2: Wire cold-start into the send path

**Files:**
- Modify: `service/routers/api_v2.py` — the wrapper-backed channel branch in the `/dispatch` send loop where `_ensure_managed_pty_for_dispatch` returns None (the `channel_backing_failed.add(recipient_id)` block at ~4499–4507, inside the `execution_mode == "channel" and _managed_via_wrapper_for_runtime(...)` branch).
- Test: `service/tests/test_lifecycle_phase7.py` (extend).

The cold-start belongs in the wrapper-backed channel branch (api_v2.py ~10485) because that branch leaves the run queued for the wrapper's child bridge to claim — it is exactly the path that strands a cold agent. We cold-start a spawn_request instead of marking `channel_backing_failed`, so the run stays launchable/queued and a bridge claim + register brings up the worker.

- [ ] **Step 1: Write the failing test**

Add to `service/tests/test_lifecycle_phase7.py`:

```python
    def test_dispatch_to_cold_managed_wrapper_agent_creates_spawn_request(self):
        # Wrapper-backed managed runtime (codex) + cold agent: the send path must
        # cold-start a spawn_request instead of stranding the run.
        self._heartbeat_environment(
            metadata={"terminal": True, "pty": True, "terminalRuntimes": ["codex"]},
        )
        self.client.put("/api/v1/settings", json={"managed_via_wrapper": ["codex", "hermes"]})
        self._register("sender")
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")

        resp = self.client.post(
            "/api/v1/dispatch",
            json={
                "from_agent": "sender",
                "to": ["worker"],
                "subject": "do the thing",
                "body": "please run",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        spawn_rows = self._fetchall(
            "SELECT status FROM spawn_requests WHERE agent_id = ? AND status = 'queued'",
            ("worker",),
        )
        self.assertEqual(len(spawn_rows), 1, "cold dispatch should have cold-started a spawn_request")
        # The run is still queued (not failed) so the spawned worker can claim it.
        run = self._fetchone(
            "SELECT status FROM dispatch_runs WHERE target_agent = ? ORDER BY requested_at DESC LIMIT 1",
            ("worker",),
        )
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "queued")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k cold_managed_wrapper -x -q`
Expected: FAIL — `spawn_rows` is empty (no cold-start yet); the run is stranded with no spawn_request and the recipient is recorded in `channel_backing_failed`.

- [ ] **Step 3: Cold-start in the wrapper-backed channel branch**

In `service/routers/api_v2.py`, in the `/dispatch` send loop's wrapper-backed channel branch (~10491), replace this block:

```python
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if not console_terminal:
                            not_started.append(
                                _dispatch_fix_hint(
                                    recipient_id,
                                    row,
                                    f"Managed {runtime} wrapper PTY is unavailable; recover or restart the environment-managed session.",
                                )
                            )
                            channel_backing_failed.add(recipient_id)
```

with:

```python
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if not console_terminal:
                            # Cold agent: no live session means _ensure_managed_pty_for_dispatch
                            # has nothing to build a PTY from, so the run would sit queued
                            # forever (root cause G). Cold-start a spawn_request through the
                            # existing mechanism so a bridge claims it, registers a session,
                            # and the eager PTY spawn brings up the wrapper. Leave the run
                            # launchable/queued so the spawned worker can claim it.
                            coldstarted = await _coldstart_spawn_request_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                            if not coldstarted:
                                not_started.append(
                                    _dispatch_fix_hint(
                                        recipient_id,
                                        row,
                                        f"Managed {runtime} wrapper PTY is unavailable; recover or restart the environment-managed session.",
                                    )
                                )
                                channel_backing_failed.add(recipient_id)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k cold_managed_wrapper -x -q`
Expected: PASS — 1 passed.

- [ ] **Step 5: Full file regression check + commit**

```bash
python -m pytest service/tests/test_api_v2_regressions.py -q
python -m pytest service/tests/test_lifecycle_phase7.py -q
git add service/routers/api_v2.py service/tests/test_lifecycle_phase7.py
git commit -m "feat(dispatch): cold-start spawn_request on cold managed wrapper send (root cause G)"
```

---

### Task 3: Refuse to merge into an unclaimable queued run + status-guard the merge UPDATE

**Files:**
- Modify: `service/routers/api_v2.py` — `_find_mergeable_queued_run` (4976) gains a claimable-backing check; the merge `UPDATE` (5407) gains `AND status='queued'` with 0-rows-affected fall-through to a new run (5344–5437).
- Test: `service/tests/test_lifecycle_phase7.py` (extend).

- [ ] **Step 1: Write the failing tests**

Add to `service/tests/test_lifecycle_phase7.py`:

```python
    def _seed_queued_run(self, run_id, *, target, from_agent="sender", requested_at=None):
        requested_at = requested_at or _iso(datetime.now(timezone.utc))
        self._execute(
            """
            INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status, require_reply, requested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (run_id, None, from_agent, target, "start_if_possible", "channel", "request",
             "first", "body one", "normal", "queued", 0, requested_at),
        )

    def test_find_mergeable_skips_run_with_no_claimable_backing(self):
        # Cold agent, no session, no spawn_request: an existing queued run is
        # unclaimable, so merge must refuse it (return None) so the caller
        # cold-starts + opens a fresh, claimable run.
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_queued_run("run-dead", target="worker")

        async def _run():
            db = await get_db()
            try:
                row = await api_v2._find_mergeable_queued_run(db, recipient_id="worker", from_agent="sender")
                return row
            finally:
                await db.close()
        self.assertIsNone(asyncio.run(_run()))

    def test_find_mergeable_returns_run_with_claimable_backing(self):
        # Same queued run but now a queued spawn_request exists -> claimable -> merge OK.
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")
        self._seed_queued_run("run-live", target="worker")
        self._execute(
            """
            INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            ("spec-live", "worker", "linux:test-host:default", "codex",
             _iso(datetime.now(timezone.utc)), _iso(datetime.now(timezone.utc))),
        )
        self._execute(
            """
            INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            ("spawn-live", "spec-live", "linux:test-host:default", "worker", "codex", "queued",
             _iso(datetime.now(timezone.utc)), _iso(datetime.now(timezone.utc))),
        )

        async def _run():
            db = await get_db()
            try:
                row = await api_v2._find_mergeable_queued_run(db, recipient_id="worker", from_agent="sender")
                return row
            finally:
                await db.close()
        row = asyncio.run(_run())
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], "run-live")

    def test_merge_update_is_status_guarded(self):
        # If the run flips out of 'queued' (e.g. claimed) between the read and the
        # guarded UPDATE, the UPDATE affects 0 rows and the merge falls through.
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")
        # Live spawn_request so the run is claimable (passes the Task-3 backing check).
        self._execute(
            """
            INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            ("spec-g", "worker", "linux:test-host:default", "codex",
             _iso(datetime.now(timezone.utc)), _iso(datetime.now(timezone.utc))),
        )
        self._execute(
            """
            INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            ("spawn-g", "spec-g", "linux:test-host:default", "worker", "codex", "queued",
             _iso(datetime.now(timezone.utc)), _iso(datetime.now(timezone.utc))),
        )
        self._seed_queued_run("run-claimed", target="worker")

        async def _run():
            db = await get_db()
            try:
                mergeable = await api_v2._find_mergeable_queued_run(db, recipient_id="worker", from_agent="sender")
                self.assertIsNotNone(mergeable)
                # Simulate a concurrent claim AFTER the mergeable read.
                await db.execute("UPDATE dispatch_runs SET status = 'claimed' WHERE id = ?", ("run-claimed",))
                # Status-guarded UPDATE must affect 0 rows.
                cursor = await db.execute(
                    "UPDATE dispatch_runs SET subject = ? WHERE id = ? AND status = 'queued'",
                    ("merged", "run-claimed"),
                )
                await db.commit()
                return cursor.rowcount
            finally:
                await db.close()
        self.assertEqual(asyncio.run(_run()), 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k "mergeable or merge_update" -x -q`
Expected: FAIL — `test_find_mergeable_skips_run_with_no_claimable_backing` returns the row (no backing check yet), so the assertion `assertIsNone` fails. (`test_merge_update_is_status_guarded` exercises the guarded SQL directly and may already pass; that is fine — Step 3 wires the same guard into production. `test_find_mergeable_returns_run_with_claimable_backing` may pass pre-implementation; re-run after Step 3 to confirm no regression.)

- [ ] **Step 3a: Add the claimable-backing check to `_find_mergeable_queued_run`**

In `service/routers/api_v2.py`, replace `_find_mergeable_queued_run` (4976–4996):

```python
async def _find_mergeable_queued_run(
    db,
    *,
    recipient_id: str,
    from_agent: str,
):
    # Keep queued merge ownership scoped to one sender. Cross-sender merge
    # loses the contract owner and makes handoff replies go to the wrong agent.
    cursor = await db.execute(
        """
        SELECT *
        FROM dispatch_runs
        WHERE target_agent = ?
          AND from_agent = ?
          AND status = 'queued'
        ORDER BY requested_at ASC
        LIMIT 1
        """,
        (recipient_id, from_agent),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    # Root cause G: never merge a new send into a queued run that nothing can
    # claim. A run is claimable only if the agent has a live worker (a live
    # agent_sessions row) OR a claimable spawn_request (queued/claimed). Merging
    # into an unclaimable run buries every later send in a dead run forever.
    if not await _agent_has_claimable_backing(db, recipient_id):
        return None
    return row


async def _agent_has_claimable_backing(db, agent_id: str) -> bool:
    """True when a managed agent has something that will actually claim a queued
    run: a live agent_sessions row, or a queued/claimed spawn_request."""
    live_session = await (await db.execute(
        """
        SELECT 1
        FROM agent_sessions
        WHERE agent_id = ?
          AND status IN ('starting', 'running', 'recovering', 'restarting')
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()
    if live_session:
        return True
    claimable_spawn = await (await db.execute(
        """
        SELECT 1
        FROM spawn_requests
        WHERE agent_id = ?
          AND status IN ('queued', 'claimed')
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()
    return bool(claimable_spawn)
```

- [ ] **Step 3b: Status-guard the merge UPDATE with 0-rows fall-through**

In `service/routers/api_v2.py`, in `_create_dispatch_runs`'s merge branch (5407), replace:

```python
            merged_body, merged_count = merge_result
            # Keep message_id and in_reply_to pointing at the FIRST item that
            # opened this buffered run. Per-item ids are preserved in the body
            # text so the receiver can still pull each original from inbox.
            await db.execute(
                """
                UPDATE dispatch_runs
                SET subject = ?, body = ?, priority = ?, dispatch_mode = ?, message_type = ?, require_reply = ?
                WHERE id = ?
                """,
                (
                    _build_pending_dispatch_subject(merged_count, subject),
                    merged_body,
                    _stronger_priority(mergeable_run["priority"], priority),
                    "require_start" if mergeable_run["dispatch_mode"] == "require_start" or dispatch_mode == "require_start" else mergeable_run["dispatch_mode"],
                    message_type,
                    1 if (bool(mergeable_run["require_reply"]) or require_reply) else 0,
                    mergeable_run["id"],
                ),
            )
            await _append_dispatch_event(
                db,
                mergeable_run["id"],
                "merged",
                f"Buffered update from {from_agent}: {subject}",
            )
            runs.append({
                "runId": mergeable_run["id"],
                "targetAgentId": recipient_id,
                "status": "queued",
                "merged": True,
                "mergedCount": merged_count,
                "requireReply": bool(mergeable_run["require_reply"]) or require_reply,
            })
            continue
```

with:

```python
            merged_body, merged_count = merge_result
            # Keep message_id and in_reply_to pointing at the FIRST item that
            # opened this buffered run. Per-item ids are preserved in the body
            # text so the receiver can still pull each original from inbox.
            #
            # Status-guard the UPDATE (root cause G): if the run left 'queued'
            # between _find_mergeable_queued_run and here (a bridge claimed it,
            # or a reaper failed it), the UPDATE affects 0 rows and we must NOT
            # report a merge — fall through to open a fresh run below instead of
            # clobbering a claimed/failed run.
            update_cursor = await db.execute(
                """
                UPDATE dispatch_runs
                SET subject = ?, body = ?, priority = ?, dispatch_mode = ?, message_type = ?, require_reply = ?
                WHERE id = ? AND status = 'queued'
                """,
                (
                    _build_pending_dispatch_subject(merged_count, subject),
                    merged_body,
                    _stronger_priority(mergeable_run["priority"], priority),
                    "require_start" if mergeable_run["dispatch_mode"] == "require_start" or dispatch_mode == "require_start" else mergeable_run["dispatch_mode"],
                    message_type,
                    1 if (bool(mergeable_run["require_reply"]) or require_reply) else 0,
                    mergeable_run["id"],
                ),
            )
            if update_cursor.rowcount and update_cursor.rowcount > 0:
                await _append_dispatch_event(
                    db,
                    mergeable_run["id"],
                    "merged",
                    f"Buffered update from {from_agent}: {subject}",
                )
                runs.append({
                    "runId": mergeable_run["id"],
                    "targetAgentId": recipient_id,
                    "status": "queued",
                    "merged": True,
                    "mergedCount": merged_count,
                    "requireReply": bool(mergeable_run["require_reply"]) or require_reply,
                })
                continue
            # 0 rows affected: the run is no longer queued. Do not merge; fall
            # through to open a new run for this send.
```

(The existing `run_id = f"run_..."` new-run INSERT at 5439 immediately follows, so the fall-through opens a fresh queued run.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k "mergeable or merge_update" -x -q`
Expected: PASS — 3 passed.

- [ ] **Step 5: Full regression + commit**

```bash
python -m pytest service/tests/test_api_v2_regressions.py -q
python -m pytest service/tests/test_lifecycle_phase7.py -q
git add service/routers/api_v2.py service/tests/test_lifecycle_phase7.py
git commit -m "fix(dispatch): refuse merge into unclaimable queued run + status-guard merge UPDATE (root cause G)"
```

---

### Task 4: Queued-run age sweep reaper

**Files:**
- Modify: `service/routers/api_v2.py` — add `_reap_stale_queued_runs` near the other reapers (place it immediately after `_close_orphaned_managed_runs`, ~12330) and add a `queued_run_stale_minutes` default to `DEFAULT_SETTINGS` (112).
- Modify: `service/main.py` — call it from `_run_dispatch_reconcile_once` (59).
- Test: `service/tests/test_lifecycle_phase7.py` (extend).

- [ ] **Step 1: Write the failing test**

Add to `service/tests/test_lifecycle_phase7.py`:

```python
    def test_reap_stale_queued_runs_fails_old_unclaimable_run(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        old = _iso(datetime.now(timezone.utc) - timedelta(minutes=45))
        self._seed_queued_run("run-old", target="worker", requested_at=old)
        # No live session, no spawn_request -> unclaimable -> reapable.

        async def _run():
            db = await get_db()
            try:
                n = await api_v2._reap_stale_queued_runs(db, limit=100)
                await db.commit()
                return n
            finally:
                await db.close()
        reaped = asyncio.run(_run())
        self.assertEqual(len(reaped), 1)
        run = self._fetchone("SELECT status, error_text FROM dispatch_runs WHERE id = ?", ("run-old",))
        self.assertEqual(run["status"], "failed")
        self.assertTrue(run["error_text"])

    def test_reap_stale_queued_runs_skips_fresh_and_claimable(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        self._seed_ended_session("worker")
        # Fresh queued run -> not old enough.
        self._seed_queued_run("run-fresh", target="worker",
                              requested_at=_iso(datetime.now(timezone.utc)))
        # Old queued run but with a claimable spawn_request -> spared.
        old = _iso(datetime.now(timezone.utc) - timedelta(minutes=45))
        self._seed_queued_run("run-old-claimable", target="worker", requested_at=old)
        self._execute(
            "INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("spec-c", "worker", "linux:test-host:default", "codex",
             _iso(datetime.now(timezone.utc)), _iso(datetime.now(timezone.utc))),
        )
        self._execute(
            "INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("spawn-c", "spec-c", "linux:test-host:default", "worker", "codex", "queued",
             _iso(datetime.now(timezone.utc)), _iso(datetime.now(timezone.utc))),
        )

        async def _run():
            db = await get_db()
            try:
                n = await api_v2._reap_stale_queued_runs(db, limit=100)
                await db.commit()
                return n
            finally:
                await db.close()
        reaped = asyncio.run(_run())
        self.assertEqual(len(reaped), 0)
        statuses = {r["id"]: r["status"] for r in self._fetchall(
            "SELECT id, status FROM dispatch_runs WHERE target_agent = ?", ("worker",))}
        self.assertEqual(statuses["run-fresh"], "queued")
        self.assertEqual(statuses["run-old-claimable"], "queued")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k reap_stale_queued -x -q`
Expected: FAIL — `AttributeError: module 'service.routers.api_v2' has no attribute '_reap_stale_queued_runs'`.

- [ ] **Step 3: Add `queued_run_stale_minutes` default**

In `service/routers/api_v2.py` `DEFAULT_SETTINGS` (after the `active_managed_run_stale_minutes` entry at line 135), add:

```python
    # Root cause G: a queued dispatch_run with no claimable backing (no live
    # session and no queued/claimed spawn_request) older than this is failed by
    # the periodic reaper so a cold send never strands forever. Default 30 min.
    "queued_run_stale_minutes": 30,
```

- [ ] **Step 4: Implement `_reap_stale_queued_runs`**

In `service/routers/api_v2.py`, immediately AFTER `_close_orphaned_managed_runs` returns (find the end of that function, ~12330), add:

```python
async def _reap_stale_queued_runs(db, *, limit: int = 200) -> list[dict[str, str]]:
    """Fail queued dispatch_runs that are older than queued_run_stale_minutes and
    have no claimable backing (root cause G).

    A queued run only progresses when a bridge claims it — which requires a live
    agent_sessions row or a queued/claimed spawn_request for the target. Without
    one, the run sits in 'queued' forever (the existing reconcilers only handle
    claimed/running/delivered). This sweep fails such runs so the sender stops
    waiting; the cold-start path (send-time + Task 2) is the re-kick mechanism, so
    this reaper only TERMINALIZES — it does not re-spawn.
    """
    settings = await _load_settings(db)
    stale_minutes = int(settings.get("queued_run_stale_minutes", 30) or 30)
    stale_seconds = max(60, stale_minutes * 60)
    cutoff_param = f"-{stale_seconds} seconds"
    cursor = await db.execute(
        """
        SELECT id, target_agent, subject, requested_at
        FROM dispatch_runs r
        WHERE r.status = 'queued'
          AND datetime(r.requested_at) <= datetime('now', ?)
          AND NOT EXISTS (
            SELECT 1 FROM agent_sessions s
            WHERE s.agent_id = r.target_agent
              AND s.status IN ('starting', 'running', 'recovering', 'restarting')
          )
          AND NOT EXISTS (
            SELECT 1 FROM spawn_requests sr
            WHERE sr.agent_id = r.target_agent
              AND sr.status IN ('queued', 'claimed')
          )
        ORDER BY r.requested_at ASC
        LIMIT ?
        """,
        (cutoff_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    reaped: list[dict[str, str]] = []
    now = _now()
    for row in rows:
        reason = (
            f"Queued run had no claimable backing (no live session, no queued/claimed "
            f"spawn_request) and exceeded {stale_minutes} min."
        )
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'failed', summary = ?, error_text = ?, finished_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (
                "Queued dispatch failed: no worker ever started to claim it.",
                reason,
                now,
                row["id"],
            ),
        )
        await _append_dispatch_event(db, row["id"], "reaped_queued", reason)
        await _invalidate_agent_live_state(db, row["target_agent"])
        reaped.append({"runId": row["id"], "targetAgent": row["target_agent"], "subject": row["subject"] or ""})
    return reaped
```

(`_invalidate_agent_live_state` is already used by `_fail_stale_active_run` at 5055 — confirm with `grep -n "async def _invalidate_agent_live_state" service/routers/api_v2.py`.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k reap_stale_queued -x -q`
Expected: PASS — 2 passed.

- [ ] **Step 6: Wire into `_run_dispatch_reconcile_once`**

In `service/main.py`, extend the import block (61–69) — add `_reap_stale_queued_runs` to the `from service.routers.api_v2 import (...)` tuple:

```python
    from service.routers.api_v2 import (
        _close_idle_virtual_rpc_workers,
        _close_orphaned_managed_runs,
        _close_reconcilable_delivered_runs,
        _prune_terminal_history,
        _reap_stale_queued_runs,
        _reconcile_stale_managed_terminals_for_resident_agents,
        _repair_unusable_active_runs,
        _run_contract_reminders_once,
    )
```

Then in the body, immediately after `closed_orphaned_managed = await _close_orphaned_managed_runs(db, limit=200)` (95), add:

```python
        reaped_queued = await _reap_stale_queued_runs(db, limit=200)
```

and add to the returned dict (after `"orphaned_managed_runs_closed": ...,` at 104):

```python
            "queued_runs_reaped": len(reaped_queued),
```

- [ ] **Step 7: Verify the wiring imports + reconcile runs**

Run: `python -c "import ast; ast.parse(open('service/main.py').read())"`
Expected: no output (exit 0).

Run: `python -m pytest service/tests/test_api_v2_regressions.py -q`
Expected: PASS (no regression from the new DEFAULT_SETTINGS key — note `test_api_v2_regressions.py` does not pin the settings key set; if a settings-shape test exists it must already accept new keys).

- [ ] **Step 8: Commit**

```bash
python -c "import ast; ast.parse(open('service/routers/api_v2.py').read())"
git add service/routers/api_v2.py service/main.py service/tests/test_lifecycle_phase7.py
git commit -m "feat(dispatch): reap stale unclaimable queued runs from periodic reconcile (root cause G)"
```

---

### Task 5: Time-based spawn_request reaping

**Files:**
- Modify: `service/routers/api_v2.py` — add `_reap_stuck_spawn_requests` near `_repair_spawn_requests_from_initial_dispatch_failures` (2921) and a `spawn_request_stale_minutes` default in `DEFAULT_SETTINGS` (112).
- Modify: `service/main.py` — call it from `_run_dispatch_reconcile_once` (59).
- Test: `service/tests/test_lifecycle_phase7.py` (extend).

`_repair_spawn_requests_from_initial_dispatch_failures` only fails a `running` spawn_request when it can correlate a failed initial-dispatch run, and it's only called from `GET /spawn-requests`. This adds a TIME-based net for `running`/`queued`/`claimed` spawn_requests that never reach a session, wired into the periodic reconciler so it runs without a dashboard GET.

- [ ] **Step 1: Write the failing test**

Add to `service/tests/test_lifecycle_phase7.py`:

```python
    def _seed_spawn_request(self, request_id, *, agent_id="worker", status="running",
                            created_at=None, updated_at=None, started_at=None):
        ts = _iso(datetime.now(timezone.utc))
        spec_id = f"spec-{request_id}"
        self._execute(
            "INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (spec_id, agent_id, "linux:test-host:default", "codex", ts, ts),
        )
        self._execute(
            """
            INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime, status,
                created_at, updated_at, started_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (request_id, spec_id, "linux:test-host:default", agent_id, "codex", status,
             created_at or ts, updated_at or ts, started_at),
        )

    def test_reap_stuck_spawn_requests_fails_old_running_without_session(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        old = _iso(datetime.now(timezone.utc) - timedelta(minutes=20))
        self._seed_spawn_request("spawn-stuck", status="running",
                                 created_at=old, updated_at=old, started_at=old)
        # No agent_sessions row references this spawn_request -> stuck.

        async def _run():
            db = await get_db()
            try:
                n = await api_v2._reap_stuck_spawn_requests(db, limit=100)
                await db.commit()
                return n
            finally:
                await db.close()
        reaped = asyncio.run(_run())
        self.assertEqual(reaped, 1)
        row = self._fetchone("SELECT status, error FROM spawn_requests WHERE id = ?", ("spawn-stuck",))
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["error"])

    def test_reap_stuck_spawn_requests_skips_fresh_and_running_with_session(self):
        self._heartbeat_environment()
        self._register("worker", runtime="codex", sessionMode="managed")
        # Fresh queued request -> spared.
        self._seed_spawn_request("spawn-fresh", status="queued")
        # Old running request but a live session references it -> spared.
        old = _iso(datetime.now(timezone.utc) - timedelta(minutes=20))
        self._seed_spawn_request("spawn-live", agent_id="worker2", status="running",
                                 created_at=old, updated_at=old, started_at=old)
        self._register("worker2", runtime="codex", sessionMode="managed")
        now = _iso(datetime.now(timezone.utc))
        self._execute(
            """
            INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, spawn_request_id,
                status, started_at, last_seen)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            ("sess-live", "worker2", "linux:test-host:default", "codex", "spawn-live",
             "running", now, now),
        )

        async def _run():
            db = await get_db()
            try:
                n = await api_v2._reap_stuck_spawn_requests(db, limit=100)
                await db.commit()
                return n
            finally:
                await db.close()
        self.assertEqual(asyncio.run(_run()), 0)
        statuses = {r["id"]: r["status"] for r in self._fetchall(
            "SELECT id, status FROM spawn_requests WHERE id IN ('spawn-fresh','spawn-live')")}
        self.assertEqual(statuses["spawn-fresh"], "queued")
        self.assertEqual(statuses["spawn-live"], "running")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k reap_stuck_spawn -x -q`
Expected: FAIL — `AttributeError: module 'service.routers.api_v2' has no attribute '_reap_stuck_spawn_requests'`.

- [ ] **Step 3: Add `spawn_request_stale_minutes` default**

In `service/routers/api_v2.py` `DEFAULT_SETTINGS`, immediately after the `queued_run_stale_minutes` entry added in Task 4, add:

```python
    # Root cause G: a spawn_request stuck in queued/claimed/running past this
    # window with no agent_sessions row ever registered is failed by the periodic
    # reaper so a dead spawn never keeps an agent "starting" forever. Default 15 min.
    "spawn_request_stale_minutes": 15,
```

- [ ] **Step 4: Implement `_reap_stuck_spawn_requests`**

In `service/routers/api_v2.py`, immediately AFTER `_repair_spawn_requests_from_initial_dispatch_failures` ends (2975), add:

```python
async def _reap_stuck_spawn_requests(db, *, limit: int = 200) -> int:
    """Fail spawn_requests stuck in queued/claimed/running past
    spawn_request_stale_minutes with no live session ever registered (root cause G).

    _repair_spawn_requests_from_initial_dispatch_failures only fails a 'running'
    request when it can correlate a failed initial dispatch, and only runs on
    GET /spawn-requests. This is the time-based net wired into the periodic
    reconciler: a bridge claimed (or was supposed to claim) the request but never
    brought up a session, so nothing will ever satisfy the cold-started run.
    """
    settings = await _load_settings(db)
    stale_minutes = int(settings.get("spawn_request_stale_minutes", 15) or 15)
    stale_seconds = max(60, stale_minutes * 60)
    cutoff_param = f"-{stale_seconds} seconds"
    cursor = await db.execute(
        """
        SELECT id, agent_id, status
        FROM spawn_requests sr
        WHERE sr.status IN ('queued', 'claimed', 'running')
          AND datetime(COALESCE(NULLIF(sr.started_at, ''), NULLIF(sr.updated_at, ''), sr.created_at))
              <= datetime('now', ?)
          AND NOT EXISTS (
            SELECT 1 FROM agent_sessions s
            WHERE s.spawn_request_id = sr.id
              AND s.status IN ('starting', 'running', 'recovering', 'restarting')
          )
        ORDER BY sr.created_at ASC
        LIMIT ?
        """,
        (cutoff_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    if not rows:
        return 0
    now = _now()
    reaped = 0
    for row in rows:
        result = await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'failed',
                error = ?,
                finished_at = COALESCE(NULLIF(finished_at, ''), ?),
                updated_at = ?
            WHERE id = ? AND status IN ('queued', 'claimed', 'running')
            """,
            (
                f"Spawn request stuck in '{row['status']}' past {stale_minutes} min with no session; reaped.",
                now,
                now,
                row["id"],
            ),
        )
        if result.rowcount and result.rowcount > 0:
            reaped += 1
    return reaped
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py -k reap_stuck_spawn -x -q`
Expected: PASS — 2 passed.

- [ ] **Step 6: Wire into `_run_dispatch_reconcile_once`**

In `service/main.py`, add `_reap_stuck_spawn_requests` to the import tuple (alphabetical, after `_reap_stale_queued_runs`):

```python
        _reap_stale_queued_runs,
        _reap_stuck_spawn_requests,
        _reconcile_stale_managed_terminals_for_resident_agents,
```

Then, immediately after `reaped_queued = await _reap_stale_queued_runs(db, limit=200)` (added in Task 4), add:

```python
        reaped_spawn_requests = await _reap_stuck_spawn_requests(db, limit=200)
```

and add to the returned dict (after `"queued_runs_reaped": len(reaped_queued),`):

```python
            "spawn_requests_reaped": reaped_spawn_requests,
```

- [ ] **Step 7: Verify + full regression**

Run: `python -c "import ast; ast.parse(open('service/main.py').read())"`
Expected: no output (exit 0).

Run: `python -m pytest service/tests/test_api_v2_regressions.py -q && python -m pytest service/tests/test_lifecycle_phase7.py -q`
Expected: PASS for both.

- [ ] **Step 8: Commit**

```bash
python -c "import ast; ast.parse(open('service/routers/api_v2.py').read())"
git add service/routers/api_v2.py service/main.py service/tests/test_lifecycle_phase7.py
git commit -m "feat(spawn): time-based stuck spawn_request reaping in periodic reconcile (root cause G)"
```

---

### Task 6: Full Phase 7 test sweep

- [ ] **Step 1: Run the whole new suite + the regression suite**

Run: `python -m pytest service/tests/test_lifecycle_phase7.py service/tests/test_api_v2_regressions.py -q`
Expected: all pass.

- [ ] **Step 2: Confirm the reconciler one-pass runs end to end (smoke)**

Run:
```bash
python - <<'PY'
import asyncio, tempfile
from pathlib import Path
from types import SimpleNamespace
from service.db import init_db
from service import main as service_main

async def go():
    d = tempfile.mkdtemp()
    await init_db(Path(d) / "aify.db")
    # _run_dispatch_reconcile_once reads config via get_config(); set data_dir.
    import service.config as cfg
    cfg.get_config().data_dir = d  # type: ignore[attr-defined]
    res = await service_main._run_dispatch_reconcile_once()
    assert "queued_runs_reaped" in res, res
    assert "spawn_requests_reaped" in res, res
    print("reconcile keys OK:", sorted(res))

asyncio.run(go())
PY
```
Expected: prints `reconcile keys OK:` with `queued_runs_reaped` and `spawn_requests_reaped` present. (If `get_config()` does not expose a settable `data_dir`, skip this smoke step — the pytest wiring in Tasks 4/5 already proves the functions and dict keys; this is a belt-and-suspenders integration check only.)

- [ ] **Step 3: Commit any final notes (if changed)**

```bash
git add -A && git commit -m "test(dispatch): phase-7 cold-start + reaping sweep" || echo "nothing to commit"
```

---

## Self-Review

- **Spec coverage (Phase 7 = root cause G):**
  - Scope item 1 (cold-start on the send path) → **Task 1** (helper `_coldstart_spawn_request_for_dispatch` using the existing `spawn_specs`/`spawn_requests` INSERTs, mirroring `create_spawn_request`) + **Task 2** (call it in the wrapper-backed channel branch where `_ensure_managed_pty_for_dispatch` returns None for a cold agent). The cold-start uses the EXISTING spawn mechanism: a bridge claims via `POST /spawn-requests/claim` (`claim_spawn_request`, 6919) and the PATCH→running transition (7143–7209) eager-spawns the PTY.
  - Scope item 2 (refuse merge into unclaimable + status-guard the UPDATE) → **Task 3** (`_find_mergeable_queued_run` gains `_agent_has_claimable_backing`; merge UPDATE gains `AND status='queued'` with 0-rows fall-through to a new run).
  - Scope item 3 (queued-run age sweep) → **Task 4** (`_reap_stale_queued_runs`, wired into `_run_dispatch_reconcile_once`).
  - Scope item 4 (spawn_request time-based reaping) → **Task 5** (`_reap_stuck_spawn_requests`, wired into `_run_dispatch_reconcile_once`).
- **Placeholder scan:** none. Every code step shows the full function/edit; every test step shows the full test; every run step gives the exact command and expected PASS/FAIL.
- **Type/name consistency:** New symbols — `_coldstart_spawn_request_for_dispatch`, `_agent_has_claimable_backing`, `_reap_stale_queued_runs`, `_reap_stuck_spawn_requests` — are defined in api_v2.py and imported into main.py exactly as named (Tasks 4/5 import block). Settings keys `queued_run_stale_minutes` / `spawn_request_stale_minutes` are added to `DEFAULT_SETTINGS` and read via `settings.get(<key>, <same default>)`. Table/column names verified against `service/db.py`: `dispatch_runs.status/requested_at/error_text/summary/finished_at/target_agent`, `spawn_requests.status/agent_id/spawn_spec_id/started_at/updated_at/created_at/finished_at/error`, `agent_sessions.status/agent_id/spawn_request_id/spawn_spec_id/environment_id/workspace/last_seen`, `spawn_specs` full column list. Helpers reused as-is: `_now()` (88), `_load_settings` (3548), `_append_dispatch_event(db, run_id, event_type, body)` (3850), `_invalidate_agent_live_state` (used at 5055), `_environment_record_to_dict` (2156), `_runtime_capability_for_environment` (3235), `_workspace_for_environment` (3289), `_normalize_runtime` (549). Reaper return-shape (`list[dict[str, str]]` for queued runs, `int` for spawn requests) mirrors the existing `_close_orphaned_managed_runs` (list) and `_repair_spawn_requests_from_initial_dispatch_failures` (int) so `len(...)`/raw-int usage in the reconcile dict is correct.
- **Sequencing:** Tasks 1–3 (cold-start + merge-guard) land before the reapers (Tasks 4–5), so the strand is closed by re-kick before the time-based net is added — matching the spec's "cold-start + merge-guard land before/with the reapers." Each task is independently committable with its own test + commit.

## Open questions / risks surfaced while reading the code

1. **Cold-start = spawn_request, NOT eager PTY (resolved).** `_ensure_managed_pty_for_dispatch` (4526) is fundamentally a *PTY builder that requires a live `agent_sessions` row* — it SELECTs a session in `('running','recovering')` (4534–4545) to read `environment_id`, `runtime`, `workspace`, and `bridge_id`, then INSERTs a `terminal_sessions` row and a `start` terminal_control. For a cold agent there is no live session, so it returns `None` (4547) — it literally has nothing to launch into. Therefore the correct cold-start is a **spawn_request**, not an eager PTY: a bridge claims the spawn_request (`claim_spawn_request`, 6919), registers an `agent_sessions` row on the PATCH→running transition (7088–7130), and THAT transition's own eager-spawn block (7198–7209) calls `_ensure_managed_pty_for_dispatch` to bring up the PTY. This reuses the entire existing chain rather than duplicating PTY logic on the cold path.
2. **Environment resolution for a truly-never-spawned agent.** The cold-start clones environment/runtime/workspace from the agent's most-recent `agent_sessions` row (any status). An agent that registered managed but NEVER had a session (no row at all) cannot be cold-started by this helper (returns False) — it falls through to the existing `channel_backing_failed` not-started hint. This is acceptable for root cause G (the strand case is a *previously-warm* agent gone cold), but if the operator wants pure-registration cold-start, the agent's `runtime_state.environmentId`/registration env would need to be consulted too. Flagged for review; not in Phase 7 scope as written.
3. **Merge status-guard relies on `cursor.rowcount`.** aiosqlite exposes `rowcount` on the cursor returned by `db.execute`. The plan reads `update_cursor.rowcount` immediately after the UPDATE (before any other execute on the same connection) which is the safe window. If a future refactor reuses the connection between the UPDATE and the rowcount read, the guard could misreport — keep them adjacent.
4. **`_reap_stale_queued_runs` terminalizes, it does not re-kick.** The spec says "fails or re-kicks." I chose fail-only because the send-time cold-start (Task 2) is the natural re-kick, and a reaper that re-spawns risks a respawn loop against a genuinely-offline environment (the same class of bug as root cause D's managed-warm persistence). If the team prefers re-kick, the reaper could call `_coldstart_spawn_request_for_dispatch` instead of failing when the environment is online — flagged as a deliberate, reviewable choice.
5. **No `terminal_sessions(agent_id)` partial unique index yet (Phase 2).** Cold-start creates a spawn_request, not a terminal directly, so it does not by itself risk a duplicate PTY. But if Phase 2 has not landed, two near-simultaneous cold sends could each create a spawn_request before the dedup SELECT in `_coldstart_spawn_request_for_dispatch` sees the other (read-then-insert race, no `BEGIN IMMEDIATE`). The idempotency check narrows the window but does not close it; Phase 2's serialized spawn path is the real fix. Flagged.
6. **`DEFAULT_SETTINGS` additions.** Two new keys are added. `service/tests/test_api_v2_regressions.py` and `test_default_settings_plan4.py` assert specific existing keys, not the absence of new ones, so adding keys should not regress them — but Task 4 Step 7 / Task 5 Step 7 re-run the full regression suite to confirm.
