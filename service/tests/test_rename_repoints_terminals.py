"""A rename must not leave a terminal naming an id the same transaction tombstoned.

v0.6 Phase 4, the third item that needed a ruling. `terminal_sessions` is the ONLY table with an
`agent_id` the rename neither repoints nor cascades — it has no foreign key to `agents`, so deleting
the old row does not reach it. Its rows kept naming an id that no longer exists, so
`_active_terminal_for_agent(new_id)` found nothing and the renamed agent looked consoleless while a
dead id owned a live-looking terminal.

RULED: repoint it, with the parent row it already belongs to.

I first ruled the other way — stop the terminals rather than move them — reasoning that the PTY is
owned by a bridge that still knows the old id, so a repointed row would show the new identity a
console its bridge has never heard of. The schema says otherwise, and the schema wins:

    terminal_sessions.session_id -> agent_sessions(id) ON DELETE CASCADE

and `agent_sessions.agent_id` IS repointed by the rename. So the terminal's own session already
belongs to the new agent. Leaving `terminal_sessions.agent_id` behind does not preserve a truth, it
creates a row whose session says one agent and whose `agent_id` says another — an inconsistency
inside a single transaction that is supposed to move every reference at once.

The orphaned-bridge problem is real and unchanged by this, which is the point: rename is DB-only, the
route already tells the operator the live session is orphaned and must be relaunched, and the
terminal reconcilers key on STATE rather than on this column to decide a terminal is dead. Stopping
the rows here would have destroyed information the sweep is entitled to correct, and diverged the
child from its parent to do it.
"""

from __future__ import annotations

import sqlite3
import unittest

from service.tests._base import FastApiTestCase


class RenameRepointsTerminals(FastApiTestCase):
    DB_NAME = "aify-rename-terminals.db"

    def _execute(self, q, params=()):
        """Raw sqlite3, as the other terminal-seeding tests do.

        Not laziness: `agent_sessions.spawn_spec_id` and `spawn_request_id` are `DEFAULT ''` with
        foreign keys to their spawn tables, so a row seeded through the app connection (which
        enforces FKs) needs parent rows for two columns this test has no opinion about. sqlite3
        leaves `PRAGMA foreign_keys` off, which is the same environment every existing terminal
        fixture in this suite is written against.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(q, params)
            conn.commit()
        finally:
            conn.close()

    def _terminals(self, agent_id):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, agent_id, status FROM terminal_sessions WHERE agent_id = ? ORDER BY id",
                (agent_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _seed_environment(self, env_id="env-1"):
        # `registered_at` and `last_seen` are NOT NULL. An earlier version of this seed used
        # `INSERT OR IGNORE` without them, which SWALLOWED the violation — the environment never
        # existed and the failure surfaced two statements later as a foreign-key error on the
        # terminal. OR IGNORE hides exactly the mistake it looks like it is protecting against.
        self._execute(
            "INSERT INTO environments (id, label, machine_id, registered_at, last_seen) "
            "VALUES (?,?,?,?,?)",
            (env_id, "test env", "win32:test", "2026-08-19T00:00:00Z", "2026-08-19T00:00:00Z"),
        )

    def _seed_terminal(self, agent_id, terminal_id, status="running"):
        """A terminal row with no session, which is the shape that exposes the column under test.

        `session_id` is NOT NULL but carries no FK enforcement when the referenced row is absent only
        if foreign keys are off; the tests here seed a real session so the row is legal either way.
        """
        session_id = f"sess-{terminal_id}"
        self._execute(
            "INSERT INTO agent_sessions (id, agent_id, environment_id, mode, runtime, started_at, last_seen) "
            "VALUES (?,?,?,?,?,?,?)",
            (session_id, agent_id, "env-1", "managed", "claude-code",
             "2026-08-19T00:00:00Z", "2026-08-19T00:00:00Z"),
        )
        self._execute(
            """
            INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime,
                status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (terminal_id, session_id, agent_id, "env-1", "claude-code", status,
             "2026-08-19T00:00:00Z", "2026-08-19T00:00:00Z"),
        )

    def setUp(self):
        super().setUp()
        self._seed_environment()
        r = self.client.post("/api/v1/agents", json={
            "agentId": "old-name", "role": "coder", "runtime": "claude-code",
        })
        self.assertEqual(r.status_code, 200, r.text)

    def _rename(self, old="old-name", new="new-name"):
        r = self.client.post(f"/api/v1/agents/{old}/rename", json={"newAgentId": new})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_a_terminal_follows_the_rename(self):
        self._seed_terminal("old-name", "term-1", status="running")
        self._rename()
        self.assertEqual(
            [r["id"] for r in self._terminals("new-name")], ["term-1"],
            "the terminal's own session was repointed; its agent_id must move with it",
        )

    def test_nothing_is_left_naming_the_tombstoned_id(self):
        self._seed_terminal("old-name", "term-1", status="running")
        self._rename()
        self.assertEqual(
            self._terminals("old-name"), [],
            "a row naming an id the same transaction tombstoned is the defect being fixed",
        )

    def test_the_terminal_status_is_carried_across_untouched(self):
        # Repoint, not restart. The reconcilers own whether a terminal is alive; a rename has no
        # opinion about it and must not invent one.
        self._seed_terminal("old-name", "term-1", status="running")
        self._rename()
        self.assertEqual([r["status"] for r in self._terminals("new-name")], ["running"])

    def test_terminals_belonging_to_other_agents_are_untouched(self):
        # Anti-vacuity: an unfiltered UPDATE would move every terminal in the table.
        r = self.client.post("/api/v1/agents", json={
            "agentId": "bystander", "role": "coder", "runtime": "claude-code",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self._seed_terminal("bystander", "term-other", status="running")
        self._seed_terminal("old-name", "term-1", status="running")
        self._rename()
        self.assertEqual(
            [r["id"] for r in self._terminals("bystander")], ["term-other"],
            "one agent's rename must not reach another agent's console",
        )


if __name__ == "__main__":
    unittest.main()
