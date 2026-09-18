"""`worker_idle_close_enabled` was merged into `worker_idle_close_minutes` (0 = off) on 2026-09-19.

The migration must keep a host that had the toggle OFF from starting to close idle workers, and must
run once: it removes the old row, so a later restart cannot zero minutes the operator set since.
"""
import asyncio
import unittest

import aiosqlite

from service.db import _migrate_settings_rows


async def _run(rows, *, passes=1, set_between=None):
    async with aiosqlite.connect(":memory:") as db:
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.executemany("INSERT INTO settings VALUES (?, ?)", rows)
        await _migrate_settings_rows(db)
        if set_between:
            await db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", set_between)
        for _ in range(passes - 1):
            await _migrate_settings_rows(db)
        cursor = await db.execute("SELECT key, value FROM settings ORDER BY key")
        return dict(await cursor.fetchall())


class IdleCloseToggleMigrationTest(unittest.TestCase):
    def test_a_disabled_toggle_becomes_zero_minutes(self):
        after = asyncio.run(_run([("worker_idle_close_enabled", "false"), ("worker_idle_close_minutes", "45")]))
        self.assertEqual(after, {"worker_idle_close_minutes": "0"})

    def test_an_enabled_toggle_keeps_its_minutes(self):
        after = asyncio.run(_run([("worker_idle_close_enabled", "true"), ("worker_idle_close_minutes", "45")]))
        self.assertEqual(after, {"worker_idle_close_minutes": "45"})

    def test_a_later_restart_keeps_minutes_set_after_the_migration(self):
        after = asyncio.run(_run(
            [("worker_idle_close_enabled", "false"), ("worker_idle_close_minutes", "45")],
            passes=2, set_between=("worker_idle_close_minutes", "30"),
        ))
        self.assertEqual(after, {"worker_idle_close_minutes": "30"})


if __name__ == "__main__":
    unittest.main()
