"""Terminal resize is clamped to sane maxima before it is recorded/forwarded (2026-07-19).

An absurd winsize crashes node-pty's TIOCSWINSZ ioctl (Hermes' WSL2 `columns=131072`
incident). We clamp at the service so a bad value can never reach any bridge — even one
running older bridge code. A 0 stays 0 (the bridge substitutes its own default).
"""
import asyncio

from service.db import get_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

from service.tests._base import FastApiTestCase
from service.clock import now as _now


class TerminalResizeClampTests(FastApiTestCase):
    DB_NAME = "aify-terminal-resize-clamp-test.db"

    def _seed_terminal(self, terminal_id="term-1", agent_id="sc-agent"):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    "INSERT INTO environments (id, registered_at, last_seen) VALUES (?,?,?)",
                    ("env-1", _now(), _now()),
                )
                await db.execute(
                    """
                    INSERT INTO terminal_sessions (id, agent_id, environment_id, runtime, status, session_id, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (terminal_id, agent_id, "env-1", "claude-code", "attached", "sess-1", _now(), _now()),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _control_dims(self, terminal_id="term-1"):
        async def _run():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT cols, rows FROM terminal_controls WHERE terminal_id = ? AND action = 'resize' "
                    "ORDER BY requested_at DESC LIMIT 1",
                    (terminal_id,),
                )).fetchone()
                return (int(row["cols"]), int(row["rows"])) if row else None
            finally:
                await db.close()
        return asyncio.run(_run())

    def _resize(self, terminal_id, cols, rows):
        return self.client.post(f"/api/v1/terminals/{terminal_id}/resize",
                                json={"cols": cols, "rows": rows, "requestedBy": "test"})

    def test_an_absurd_winsize_is_clamped_to_the_renderer_grid(self):
        """The endpoint and the live-screen renderer share ONE ceiling, read here rather than typed.

        This test pinned 2000 while the renderer clamped to 500 (C1, 2026-07-26), which is exactly
        how the two drifted apart and a console wider than the renderer's grid got a snapshot at the
        wrong width. A row count of 1 is not absurd and passes through (only 0 means "no size")."""
        from service.terminal_snapshot import TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS, _clamp_grid

        self.assertEqual(_clamp_grid(99999, 99999), (TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS))
        self._seed_terminal()
        for (cols, rows), expected in (
            ((131072, 1), (TERMINAL_MAX_COLS, 1)),
            ((99999, 99999), (TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS)),
        ):
            with self.subTest(cols=cols, rows=rows):
                r = self._resize("term-1", cols, rows)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(self._control_dims("term-1"), expected)

    def test_sane_winsize_passes_through(self):
        self._seed_terminal()
        r = self._resize("term-1", 120, 40)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._control_dims("term-1"), (120, 40))

    def test_zero_stays_zero(self):
        # 0 means "no explicit size" — the bridge substitutes its own default; must NOT become a max.
        self._seed_terminal()
        r = self._resize("term-1", 0, 0)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._control_dims("term-1"), (0, 0))
