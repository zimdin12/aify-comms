"""Terminal resize is clamped to sane maxima before it is recorded/forwarded (2026-07-19).

An absurd winsize crashes node-pty's TIOCSWINSZ ioctl (Hermes' WSL2 `columns=131072`
incident). We clamp at the service so a bad value can never reach any bridge — even one
running older bridge code. A 0 stays 0 (the bridge substitutes its own default).
"""
import asyncio

from service.db import get_db
from service.routers import api_v2

from service.tests._base import FastApiTestCase


class TerminalResizeClampTests(FastApiTestCase):
    DB_NAME = "aify-terminal-resize-clamp-test.db"

    def _seed_terminal(self, terminal_id="term-1", agent_id="sc-agent"):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    "INSERT INTO environments (id, registered_at, last_seen) VALUES (?,?,?)",
                    ("env-1", api_v2._now(), api_v2._now()),
                )
                await db.execute(
                    """
                    INSERT INTO terminal_sessions (id, agent_id, environment_id, runtime, status, session_id, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (terminal_id, agent_id, "env-1", "claude-code", "attached", "sess-1", api_v2._now(), api_v2._now()),
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

    def test_absurd_winsize_is_clamped(self):
        self._seed_terminal()
        r = self._resize("term-1", 131072, 1)
        self.assertEqual(r.status_code, 200, r.text)
        # Read the SHARED ceiling rather than hardcoding a number (C1, 2026-07-26). This test
        # pinned 2000 while the renderer clamped to 500, which is exactly how the two drifted
        # apart and let a >500-column console be rendered at the wrong width. The renderer owns
        # the max; this asserts the endpoint agrees with it.
        from service.terminal_snapshot import TERMINAL_MAX_COLS
        self.assertEqual(self._control_dims("term-1"), (TERMINAL_MAX_COLS, 1),
                         "cols capped at the shared renderer max, rows floored kept (1 > 0 passes through)")

    def test_resize_clamp_matches_the_renderer_grid(self):
        """The endpoint and the live-screen renderer must share ONE ceiling. If they diverge, a
        console wider than the renderer's grid gets a snapshot at the wrong width — the garbling
        the server-rendered snapshot exists to prevent."""
        from service.terminal_snapshot import (
            TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS, _clamp_grid,
        )
        self.assertEqual(_clamp_grid(99999, 99999), (TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS))
        self._seed_terminal()
        self._resize("term-1", 99999, 99999)
        self.assertEqual(self._control_dims("term-1"), (TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS))

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
