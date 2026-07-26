"""Stop worker must stop the REAL wrapper PTY, not only Pi's virtual RPC terminal.

R2 (review 2026-07-26). `/agents/{id}/stop-worker` enqueued a bridge-side stop for
`runtime_state.virtualTerminalId` only. For every wrapper-backed runtime — claude-aify,
hermes-aify, codex-aify — it tore down DB state and returned success while the actual process kept
running. The dashboard's "Stop worker" button therefore LIED on a destructive action.

The machinery was already correct and already present in that same function; only the target was
wrong. `_append_terminal_control(action="stop")` is what session-control relies on, and host-side
`TERMINAL_MANAGER.stop` escalates SIGTERM→SIGKILL.
"""
import asyncio

from service.db import get_db
from service.routers import api_v2

from service.tests._base import FastApiTestCase


class StopWorkerStopsRealTerminalsTests(FastApiTestCase):
    DB_NAME = "aify-stop-worker-real-terminal-test.db"

    def _register(self, agent_id, runtime="claude-code"):
        r = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder",
            "runtime": runtime, "sessionMode": "managed",
        })
        self.assertEqual(r.status_code, 200, r.text)

    def _seed_env(self):
        """terminal_controls FKs to environments(id) — production relies on that, so the test
        must satisfy it rather than switch the constraint off."""
        async def _run():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO environments (id, status, bridge_id, registered_at, last_seen) "
                    "VALUES (?,?,?,?,?)",
                    ("env-1", "online", "bridge-1", api_v2._now(), api_v2._now()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _seed_terminal(self, terminal_id, agent_id, status="attached"):
        self._seed_env()

        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO terminal_sessions
                        (id, session_id, agent_id, environment_id, bridge_id, runtime, status,
                         created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (terminal_id, f"sess-for-{terminal_id}", agent_id, "env-1", "bridge-1",
                     "claude-code", status, api_v2._now(), api_v2._now()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _rows(self, sql, params=()):
        async def _run():
            db = await get_db()
            try:
                return [dict(r) for r in await (await db.execute(sql, params)).fetchall()]
            finally:
                await db.close()

        return asyncio.run(_run())

    def _stop(self, agent_id):
        return self.client.post(
            f"/api/v1/agents/{agent_id}/stop-worker",
            json={"requestedBy": "dashboard"},
        )

    def test_real_terminal_gets_a_bridge_side_stop_control(self):
        """THE REGRESSION. DB state alone cannot stop a process on the owning host."""
        self._register("sw-real")
        self._seed_terminal("term_real_1", "sw-real", status="attached")
        r = self._stop("sw-real")
        self.assertEqual(r.status_code, 200, r.text)

        controls = self._rows(
            "SELECT terminal_id, action, status FROM terminal_controls WHERE terminal_id = ?",
            ("term_real_1",),
        )
        self.assertTrue(
            any(c["action"] == "stop" for c in controls),
            "a stop control must be queued for the REAL terminal so the host kills the process; "
            f"got {controls}",
        )

    def test_real_terminal_row_is_marked_STOPPING_not_stopped(self):
        """TRANSITIONAL, not terminal (review 2026-07-26). The stop is only QUEUED here — the host
        has not acknowledged it — so writing 'stopped' asserts a process death that has not
        happened, which is the same "state that lies" defect this release exists to remove. The
        shared session-control path already uses 'stopping' (api_v2.py:12407), and a wedged
        'stopping' row is caught by the STUCK_STOPPING_GRACE_SECONDS reaper."""
        self._register("sw-mark")
        self._seed_terminal("term_real_2", "sw-mark", status="running")
        self._stop("sw-mark")
        rows = self._rows("SELECT status FROM terminal_sessions WHERE id = ?", ("term_real_2",))
        self.assertEqual(
            rows[0]["status"], "stopping",
            "the row must reflect a queued stop, not an unconfirmed process death",
        )

    def test_every_live_terminal_for_the_agent_is_stopped(self):
        """A leaked second PTY must not survive the stop — that is how orphans accumulate."""
        self._register("sw-many")
        for i, st in enumerate(("attached", "running", "idle")):
            self._seed_terminal(f"term_many_{i}", "sw-many", status=st)
        self._stop("sw-many")
        for i in range(3):
            controls = self._rows(
                "SELECT action FROM terminal_controls WHERE terminal_id = ?", (f"term_many_{i}",)
            )
            self.assertTrue(any(c["action"] == "stop" for c in controls),
                            f"term_many_{i} was left running")

    def test_already_dead_terminals_are_not_re_stopped(self):
        """Only LIVE terminals are targeted — re-stopping a dead row is pointless write churn."""
        self._register("sw-dead")
        self._seed_terminal("term_dead", "sw-dead", status="stopped")
        self._stop("sw-dead")
        controls = self._rows(
            "SELECT action FROM terminal_controls WHERE terminal_id = ?", ("term_dead",)
        )
        self.assertEqual(controls, [], "a stopped terminal needs no new stop control")

    def test_virtual_terminals_are_not_double_stopped(self):
        """`vterm_%` rows are handled by the virtual branch; the real-terminal sweep must skip
        them so a Pi virtual terminal never gets two stop controls."""
        self._register("sw-virtual", runtime="pi")
        self._seed_terminal("vterm_pi_1", "sw-virtual", status="attached")
        self._stop("sw-virtual")
        controls = self._rows(
            "SELECT action FROM terminal_controls WHERE terminal_id = ?", ("vterm_pi_1",)
        )
        self.assertLessEqual(
            len([c for c in controls if c["action"] == "stop"]), 1,
            f"a virtual terminal must not be stopped twice; got {controls}",
        )
