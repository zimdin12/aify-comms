"""_reconcile_resurrected_managed_consoles: un-reap a managed console that was ghost-reaped on
an INFERRED death but is provably alive again (live channel-sidecar + fresh output), so the agent
recovers `online` instead of staying stranded `available` while it works headless.

Regression for the next-manager incident (2026-06-08). Strictly scoped — every negative edge
below must leave the row stopped.
"""

import asyncio
import sqlite3
import datetime as dt

import aiosqlite


from service.tests._base import FastApiTestCase
from service.reconcilers.terminals import _reconcile_resurrected_managed_consoles

GHOST = "reconciled_managed_ghost_console_dead_worker"


def _iso(delta_s: float = 0.0) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delta_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


class ResurrectManagedConsoleTests(FastApiTestCase):
    def _seed(self, *, status="stopped", error=GHOST, output_age=10, sidecar_age=10,
              sidecar=True, extra_live_terminal=False, session_mode="managed", agent="rmc",
              session_status="running"):
        con = sqlite3.connect(str(self._db_path))
        con.execute("PRAGMA foreign_keys=OFF")
        now = _iso()
        env, sess, term = f"env_{agent}", f"sess_{agent}", f"term_{agent}"
        con.execute("INSERT INTO environments (id, machine_id, bridge_id, registered_at, last_seen) VALUES (?,?,?,?,?)",
                    (env, "h", f"br_{agent}", now, now))
        con.execute("INSERT INTO agents (id, role, name, runtime, session_mode, status, registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                    (agent, "coder", agent, "claude-code", session_mode, "available", now, now))
        con.execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, workspace, started_at, last_seen, terminal_id, terminal_status, owner_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (sess, agent, env, "claude-code", session_status, "/w", now, now, "", "", "managed"))
        # the ghost-reaped console row
        con.execute("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command, status, requested_by, created_at, updated_at, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (term, sess, agent, env, f"br_{agent}", "claude-code", "/w", "claude-aify", status, "dashboard", now, _iso(-output_age), error))
        if extra_live_terminal:
            con.execute("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command, status, requested_by, created_at, updated_at, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f"{term}_new", sess, agent, env, f"br_{agent}", "claude-code", "/w", "claude-aify", "attached", "dashboard", now, now, ""))
        if sidecar:
            con.execute("INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen, superseded_by, bridge_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"side_{agent}", agent, "h", "claude-code", "managed", now, _iso(-sidecar_age), "", "channel-sidecar"))
        con.commit()
        con.close()
        return term, sess

    def _run(self):
        async def go():
            db = await aiosqlite.connect(str(self._db_path)); db.row_factory = aiosqlite.Row
            try:
                n = await _reconcile_resurrected_managed_consoles(db)
                # The reconcile loop commits once after all passes (main.py); mirror that here so
                # the function's UPDATEs persist for the assertions.
                await db.commit()
                return n
            finally:
                await db.close()
        return asyncio.run(go())

    def _row(self, term):
        con = sqlite3.connect(str(self._db_path)); con.row_factory = sqlite3.Row
        try:
            return con.execute("SELECT status, error FROM terminal_sessions WHERE id=?", (term,)).fetchone()
        finally:
            con.close()

    def _binding(self, sess):
        con = sqlite3.connect(str(self._db_path))
        try:
            return con.execute("SELECT terminal_id FROM agent_sessions WHERE id=?", (sess,)).fetchone()[0]
        finally:
            con.close()

    def _session_status(self, sess):
        con = sqlite3.connect(str(self._db_path))
        try:
            return con.execute("SELECT status FROM agent_sessions WHERE id=?", (sess,)).fetchone()[0]
        finally:
            con.close()

    # --- positive ---------------------------------------------------------
    def test_alive_ghost_console_is_resurrected_and_rebound(self):
        term, sess = self._seed(agent="rmc-ok")
        # Realistic ghost-reap row: stopped_at set by the reap, with REAL output streamed
        # SINCE the reap (updated_at newer than stopped_at).
        con = sqlite3.connect(str(self._db_path))
        con.execute("UPDATE terminal_sessions SET stopped_at = ? WHERE id = ?", (_iso(-30), term))
        con.commit(); con.close()
        healed = self._run()
        self.assertEqual(healed, 1)
        r = self._row(term)
        self.assertEqual(r["status"], "attached", "alive ghost console re-activated")
        self.assertEqual(r["error"], "", "ghost error cleared")
        self.assertEqual(self._binding(sess), term, "session re-bound to the resurrected console")

    def test_resurrection_promotes_a_dead_session_denorm(self):
        # The session row was downgraded to 'stopped' when the old backing died; re-binding the
        # resurrected live console must promote it back to 'running', else the Console label
        # reads "Console stopped" over a live attached terminal (cms-manager, 2026-06-10).
        term, sess = self._seed(agent="rmc-promote", session_status="stopped")
        self.assertEqual(self._run(), 1)
        self.assertEqual(self._row(term)["status"], "attached")
        self.assertEqual(self._session_status(sess), "running", "dead session denorm promoted on bind")

    # --- negative edges (must stay stopped) -------------------------------
    def test_stale_output_not_resurrected(self):
        term, _ = self._seed(agent="rmc-stale", output_age=200)  # > MANAGED_ORPHAN_GRACE_SECONDS
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._row(term)["status"], "stopped")

    def test_no_live_sidecar_not_resurrected(self):
        term, _ = self._seed(agent="rmc-nosc", sidecar=False)
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._row(term)["status"], "stopped")

    def test_stale_sidecar_not_resurrected(self):
        term, _ = self._seed(agent="rmc-staleside", sidecar_age=300)  # > CHANNEL_SIDECAR_STALE_SECONDS
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._row(term)["status"], "stopped")

    def test_operator_stop_reason_not_resurrected(self):
        term, _ = self._seed(agent="rmc-opstop", error="Session stop from dashboard.")
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._row(term)["status"], "stopped", "an explicit stop is authoritative — never un-reaped")

    def test_reap_only_timestamp_not_resurrected(self):
        # The reap itself writes updated_at = stopped_at = now, which satisfies the freshness
        # gate for ~90s — but it is NOT real output. A row with updated_at <= stopped_at (no
        # output SINCE the reap) must stay dead, else a dead PTY whose separate sidecar
        # recovered would be resurrected and shield itself from both reapers (review M2).
        term, _ = self._seed(agent="rmc-reaponly")
        con = sqlite3.connect(str(self._db_path))
        con.execute("UPDATE terminal_sessions SET stopped_at = updated_at WHERE id = ?", (term,))
        con.commit(); con.close()
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._row(term)["status"], "stopped")

    def test_operator_stopped_agent_not_resurrected(self):
        # agents.status='stopped' is an operator disable — the autonomous reconciler must not
        # resurrect its console (review S3).
        term, _ = self._seed(agent="rmc-disabled")
        con = sqlite3.connect(str(self._db_path))
        con.execute("UPDATE agents SET status = 'stopped' WHERE id = 'rmc-disabled'")
        con.execute("UPDATE terminal_sessions SET stopped_at = NULL WHERE id = ?", (term,))
        con.commit(); con.close()
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._row(term)["status"], "stopped")

    def test_agent_with_a_new_live_console_not_resurrected(self):
        # A new console already attached → agent recovered; the old ghost row must NOT be revived
        # (would duplicate the console + clobber the new binding).
        term, _ = self._seed(agent="rmc-hasnew", extra_live_terminal=True)
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._row(term)["status"], "stopped")
