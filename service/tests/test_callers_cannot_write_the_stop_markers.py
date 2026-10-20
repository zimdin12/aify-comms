"""A caller cannot write the operator's stop markers, `status='stopped'` and `launch_mode='none'` (B2, path 5).

Registration stores the caller's `status` and `launchMode`, and a run PATCH stores its `agentStatus` when it is in the
status vocabulary, which includes `stopped`. Either made a stop no stop route wrote and aify-env never heard of. No
bundled caller sends these values; the refusal closes the route, it does not change a known one.

REFUSED BEFORE EVERY EFFECT, so every case compares the whole database before and after. A guard placed only at the
ordinary upsert misses registration's two early returns (console adoption, manual resident takeover), and one placed
at the PATCH's agent write would follow the run update and its settlement. Spellings are normalized the way the
stored values are: case and surrounding whitespace.
"""

from __future__ import annotations

import sqlite3

import service.routers.agents.registration as registration
import service.routers.dispatch_messages.dispatch as dispatch
from service.routers.api_v2 import router  # noqa: F401 -- the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.write_fence_race import dump

AGENT = "marker-agent"
MACHINE = "win32:test-host"
NOW = "2026-10-20T00:00:00Z"
STOPPED = ("stopped", " Stopped ", "STOPPED")
NONE = ("none", " None ", "NONE")


class CallersCannotWriteTheStopMarkers(FastApiTestCase):
    DB_NAME = "aify-b2-stop-markers.db"

    def _execute(self, sql: str, params=()) -> None:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(sql, params)
            db.commit()
        finally:
            db.close()

    def _one(self, sql: str, params=()):
        db = sqlite3.connect(self._db_path)
        try:
            return db.execute(sql, params).fetchone()
        finally:
            db.close()

    def _register(self, **fields):
        body = {"agentId": AGENT, "role": "coder", "runtime": "claude-code", "machineId": MACHINE, **fields}
        return self.client.post("/api/v1/agents", json=body)

    # -- the three registration branches: the upsert and its two early returns -------------------------

    def _as_managed_record(self) -> dict:
        """A resident registering over a managed record takes `_register_via_manual_resident_takeover`."""
        self._execute("UPDATE agents SET session_mode = 'managed' WHERE id = ?", (AGENT,))
        return {"bridgeId": "takeover-bridge"}

    def _with_live_console(self) -> dict:
        """A resident naming a live terminal of its own takes `_register_via_adopted_console_terminal`."""
        self._execute("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, status,"
                      " created_at, updated_at) VALUES ('term-console', 'sess-console', ?, 'env-x', 'claude-code',"
                      " 'running', ?, ?)", (AGENT, NOW, NOW))
        return {"terminalId": "term-console"}

    #: Each branch: what selects it, applied to an existing row before the registration under test.
    BRANCHES = {"ordinary-upsert": lambda self: {}, "manual-takeover": _as_managed_record,
                "console-adoption": _with_live_console}
    #: The function each early-return branch runs, spied in the control so a selector that misses its branch fails.
    BRANCH_FUNCTIONS = {"manual-takeover": "_register_via_manual_resident_takeover",
                        "console-adoption": "_register_via_adopted_console_terminal"}

    def _fresh_branch(self, branch: str) -> dict:
        """An empty database, one existing resident row, then the branch's own selector."""
        for table in ("terminal_sessions", "agent_sessions", "bridge_instances", "agents"):
            self._execute(f"DELETE FROM {table}")
        seeded = self._register(sessionMode="resident", launchMode="detached")
        self.assertEqual(seeded.status_code, 200, seeded.text)
        return self.BRANCHES[branch](self)

    #: What each branch stores for (status, launch_mode) when it admits a request, measured on main before B2: the
    #: upsert takes the caller's values, the manual takeover keeps the row's, console adoption marks it active.
    LEGACY_STORED = {
        "ordinary-upsert": lambda fields: (fields.get("status", "idle"), fields.get("launchMode", "detached")),
        "manual-takeover": lambda fields: ("idle", "detached"),
        "console-adoption": lambda fields: ("active", "detached"),
    }
    ADMITTED = ({}, {"status": "idle", "launchMode": "detached"}, {"status": "working", "launchMode": "managed"})

    def _registering(self, **fields):
        """The registration, with its two non-SQL effects recorded. Every branch expires the status cache before its
        commit, so a refusal that came after the expiry would roll the SQL back but not the cache. It broadcasts after
        its commit, so a refusal must come before the commit to leave no broadcast."""
        from service.reconcilers import status_cache

        expired, real = [], status_cache._live_state_expire

        def expiring(agent_id):
            expired.append(agent_id)
            return real(agent_id)

        broadcasts = len(self.ws.broadcasts)
        status_cache._live_state_expire = expiring
        try:
            answer = self._register(**fields)
        finally:
            status_cache._live_state_expire = real
        return answer, expired, [args[0] for args, _ in self.ws.broadcasts[broadcasts:]]

    def test_every_registration_branch_refuses_a_stop_marker_and_writes_nothing(self):
        for branch in self.BRANCHES:
            for field, values in (("status", STOPPED), ("launchMode", NONE)):
                for value in values:
                    with self.subTest(branch=branch, field=field, value=repr(value)):
                        selector = self._fresh_branch(branch)
                        before = dump(self._db_path)
                        refused, expired, broadcast = self._registering(sessionMode="resident", **selector,
                                                                        **{field: value})
                        self.assertEqual(refused.status_code, 422, refused.text)
                        self.assertEqual(dump(self._db_path), before, "a refused registration wrote to the database")
                        self.assertEqual(expired, [], "a refused registration expired the status cache")
                        self.assertEqual(broadcast, [], "a refused registration broadcast")

    def test_CONTROL_every_registration_branch_admits_omitted_and_ordinary_values(self):
        """The positive half, per branch and per marker: the branch ran (spied), it expired the cache and broadcast
        (so the refusal's empty spies mean something), and it stored what it stored before B2."""
        for branch in self.BRANCHES:
            for fields in self.ADMITTED:
                with self.subTest(branch=branch, fields=repr(fields)):
                    selector = self._fresh_branch(branch)
                    taken = []
                    name = self.BRANCH_FUNCTIONS.get(branch)
                    real = getattr(registration, name) if name else None
                    if name:
                        async def spy(*args, real=real, **kwargs):
                            taken.append(branch)
                            return await real(*args, **kwargs)
                        setattr(registration, name, spy)
                    try:
                        admitted, expired, broadcast = self._registering(sessionMode="resident", **selector, **fields)
                    finally:
                        if name:
                            setattr(registration, name, real)
                    self.assertEqual(admitted.status_code, 200, admitted.text)
                    self.assertEqual(taken, [branch] if name else [], "the selector did not reach its branch")
                    self.assertIn(AGENT, expired, "the cache spy the refusal relies on sees this branch's expiry")
                    self.assertIn("agent_registered", broadcast, "the broadcast the refusal relies on is this branch's")
                    status, launch_mode = self._one("SELECT status, launch_mode FROM agents WHERE id = ?", (AGENT,))
                    self.assertNotEqual(status, "stopped")
                    self.assertNotEqual(launch_mode, "none")
                    self.assertEqual((status, launch_mode), self.LEGACY_STORED[branch](fields))

    # -- the run PATCH ----------------------------------------------------------------------------------

    def _running_run(self) -> None:
        """Seeded directly, not registered, so an arm on the shared refusal reaches the PATCH's own assertion."""
        self._execute("INSERT INTO agents (id, role, name, runtime, machine_id, session_mode, launch_mode, status,"
                      " registered_at, last_seen) VALUES (?, 'coder', ?, 'claude-code', ?, 'managed', 'managed',"
                      " 'active', ?, ?)", (AGENT, AGENT, MACHINE, NOW, NOW))
        self._execute("INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at) VALUES (?,?,?,?,?)",
                      ("run-1", "dashboard", AGENT, "running", NOW))

    def _patch(self, **fields):
        """The PATCH, with its in-memory effect recorded: a refusal after the run update rolls the database back,
        so only the status-cache expiry that update triggers shows a guard placed too late."""
        expired, real = [], dispatch._invalidate_agent_live_state

        async def expiring(db, agent_id):
            expired.append(agent_id)
            return await real(db, agent_id)

        dispatch._invalidate_agent_live_state = expiring
        try:
            answer = self.client.patch("/api/v1/dispatch/runs/run-1",
                                       json={"status": "completed", "summary": "done", **fields})
        finally:
            dispatch._invalidate_agent_live_state = real
        return answer, expired

    def test_a_run_patch_carrying_agent_status_stopped_is_refused_before_the_run_is_touched(self):
        self._running_run()
        for value in STOPPED:
            with self.subTest(value=repr(value)):
                before, broadcasts = dump(self._db_path), len(self.ws.broadcasts)
                refused, expired = self._patch(agentStatus=value)
                self.assertEqual(refused.status_code, 422, refused.text)
                self.assertEqual(dump(self._db_path), before, "a refused PATCH settled the run or wrote the agent")
                self.assertEqual(expired, [], "a refused PATCH updated the run first: it expired the status cache")
                self.assertEqual(len(self.ws.broadcasts), broadcasts, "a refused PATCH broadcast")

    def test_CONTROL_a_run_patch_with_an_ordinary_or_omitted_agent_status_still_settles(self):
        # `working` is in the vocabulary the PATCH writes; the seeded row says `active`, so the write is visible.
        for fields, agent_status in (({"agentStatus": "working"}, "working"), ({}, None)):
            with self.subTest(fields=repr(fields)):
                self._execute("DELETE FROM dispatch_runs")
                self._execute("DELETE FROM agents")
                self._running_run()
                patched, expired = self._patch(**fields)
                self.assertEqual(patched.status_code, 200, patched.text)
                self.assertIn(AGENT, expired, "the spy the refusal relies on sees an applied PATCH's expiry")
                self.assertEqual(self._one("SELECT status FROM dispatch_runs WHERE id = 'run-1'"), ("completed",))
                if agent_status:
                    self.assertEqual(self._one("SELECT status FROM agents WHERE id = ?", (AGENT,)), (agent_status,))
