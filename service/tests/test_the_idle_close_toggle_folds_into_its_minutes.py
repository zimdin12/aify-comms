"""`worker_idle_close_enabled` was merged into `worker_idle_close_minutes` (0 = off) on 2026-09-19.

The migration must keep a host whose toggle said 'false' from starting to close idle workers, and must
run once: a later restart cannot zero minutes the operator set since.

ONLY AN EXPLICIT 'false' FOLDS. Every dashboard Save since 2026-05-27 sent the toggle with the rest of
the form, so minutes with NO toggle row is either a v0.6.16-18 host whose old migration already ate the
row, or a <=0.6.15 host whose minutes came from a raw partial PUT. The DB cannot tell them apart. The
2026-09-21 version folded that shape and zeroed every upgrading 0.6.16-18 host (idle-close off, workers
stay open); leaving it alone is wrong only for the rare raw-PUT host (idle workers start closing).
"""
import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import aiosqlite

import service.db as db_module
from service.db import _IDLE_CLOSE_MERGE_MARK, _clear_stuck_internal_settings, _migrate_settings_rows


async def _run(rows, *, passes=1, set_between=None):
    async with aiosqlite.connect(":memory:") as db:
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.executemany("INSERT INTO settings VALUES (?, ?)", rows)
        await _migrate_settings_rows(db)
        if set_between:
            await db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", set_between)
        for _ in range(passes - 1):
            await _migrate_settings_rows(db)
        cursor = await db.execute("SELECT key, value FROM settings ORDER BY key")
        rows_after = dict(await cursor.fetchall())
        # The mark is bookkeeping, not a setting; every case below asserts the SETTINGS it left.
        rows_after.pop(_IDLE_CLOSE_MERGE_MARK, None)
        return rows_after


class IdleCloseToggleMigrationTest(unittest.TestCase):
    def test_a_disabled_toggle_becomes_zero_minutes(self):
        after = asyncio.run(_run([("worker_idle_close_enabled", "false"), ("worker_idle_close_minutes", "45")]))
        self.assertEqual(after, {"worker_idle_close_minutes": "0"})

    def test_minutes_with_no_toggle_row_are_left_alone(self):
        # The ambiguous shape (see the header). Most hosts reaching it ran v0.6.16-18 and are running
        # these minutes today; zeroing them was the 2026-09-21 regression.
        after = asyncio.run(_run([("worker_idle_close_minutes", "45")]))
        self.assertEqual(after, {"worker_idle_close_minutes": "45"})

    def test_an_enabled_toggle_keeps_its_minutes(self):
        after = asyncio.run(_run([("worker_idle_close_enabled", "true"), ("worker_idle_close_minutes", "45")]))
        self.assertEqual(after, {"worker_idle_close_minutes": "45"})

    def test_a_later_restart_keeps_minutes_set_after_the_migration(self):
        after = asyncio.run(_run(
            [("worker_idle_close_enabled", "false"), ("worker_idle_close_minutes", "45")],
            passes=2, set_between=[("worker_idle_close_minutes", "30")],
        ))
        self.assertEqual(after, {"worker_idle_close_minutes": "30"})

    def test_a_toggle_row_that_comes_back_does_not_zero_minutes_set_since(self):
        # What the mark is for, now that a second run finds no toggle row to fold: `import_v2` writes
        # whatever settings a bundle holds, so an old host's bundle can put `false` back.
        after = asyncio.run(_run(
            [("worker_idle_close_minutes", "45")],
            passes=2, set_between=[("worker_idle_close_minutes", "30"), ("worker_idle_close_enabled", "false")],
        ))
        self.assertEqual(after.get("worker_idle_close_minutes"), "30")

    def test_a_fresh_install_has_nothing_to_migrate(self):
        self.assertEqual(asyncio.run(_run([])), {})

    def test_a_host_that_ran_0_6_18_keeps_its_minutes_on_the_0_6_19_boot(self):
        # v0.6.16-18's migration had already consumed the toggle row and wrote no mark, so the first
        # 0.6.19 boot finds minutes with no toggle and no mark. The first 0.6.19 migration zeroed it,
        # including hosts that had idle-close ON (toggle true + 45 -> 0) -- measured 2026-09-23 by
        # running v0.6.18's real migration and then this one. Goes through init_db, the real boot.
        from service.schema import SCHEMA

        original_path = db_module._db_path
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "v0618.db"
                with closing(sqlite3.connect(path)) as conn:
                    conn.executescript(SCHEMA)
                    conn.execute("INSERT INTO settings VALUES ('worker_idle_close_minutes', '45')")
                    conn.commit()
                asyncio.run(db_module.init_db(path))
                with closing(sqlite3.connect(path)) as conn:
                    after = dict(conn.execute("SELECT key, value FROM settings"))
        finally:
            db_module._db_path = original_path
        self.assertEqual(after.get("worker_idle_close_minutes"), "45", "the 0.6.19 boot zeroed a 0.6.18 host")
        self.assertIn(_IDLE_CLOSE_MERGE_MARK, after, "control: the boot ran the migration at all")

    def test_the_mark_is_written_so_the_fold_happens_once(self):
        async def go():
            async with aiosqlite.connect(":memory:") as db:
                await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
                await _migrate_settings_rows(db)
                cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (_IDLE_CLOSE_MERGE_MARK,))
                return await cursor.fetchone()

        self.assertIsNotNone(asyncio.run(go()), "a migration with nothing to fold must still record that it ran")



class AHiddenSettingCannotStayStuckOff(unittest.TestCase):
    """`managed_terminal_backing_enabled` is on no panel, and under aify-env a host holding `false`
    starts NO managed workers -- with no control anywhere to change it back.

    External review, 2026-09-21, finding 12. The fix removes the stored value so the code default
    applies; it never writes one, and a row reading `true` is left alone.
    """

    @staticmethod
    def _run(rows, *, passes=1):
        async def go():
            async with aiosqlite.connect(":memory:") as db:
                await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
                await db.executemany("INSERT INTO settings VALUES (?, ?)", rows)
                for _ in range(passes):
                    await _clear_stuck_internal_settings(db)
                cursor = await db.execute("SELECT key, value FROM settings ORDER BY key")
                return dict(await cursor.fetchall())

        return asyncio.run(go())

    def test_a_stored_false_is_removed_so_the_default_applies(self):
        after = self._run([("managed_terminal_backing_enabled", "false")])
        self.assertEqual(after, {}, "a host stuck with no managed workers must recover on restart")

    def test_CONTROL_a_stored_true_is_left_exactly_where_it_is(self):
        after = self._run([("managed_terminal_backing_enabled", "true")])
        self.assertEqual(after, {"managed_terminal_backing_enabled": "true"})

    def test_CONTROL_no_other_setting_is_touched(self):
        after = self._run([("away_briefing_hours", "4"), ("managed_pty_eager_spawn", "false")])
        self.assertEqual(after, {"away_briefing_hours": "4", "managed_pty_eager_spawn": "false"})

    def test_running_it_every_start_is_safe(self):
        after = self._run([("managed_terminal_backing_enabled", "false")], passes=3)
        self.assertEqual(after, {})

    def test_startup_actually_runs_both_settings_migrations(self):
        # The cases above call the functions directly, so deleting their CALLS from init_db left the
        # whole suite green while the stuck host stayed stuck (measured 2026-09-23). This one goes
        # through the only path a real host takes.
        original_path = db_module._db_path
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "upgrading.db"
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    conn.execute("INSERT INTO settings VALUES ('managed_terminal_backing_enabled', 'false')")
                    conn.commit()
                asyncio.run(db_module.init_db(path))
                with closing(sqlite3.connect(path)) as conn:
                    keys = {row[0] for row in conn.execute("SELECT key FROM settings")}
        finally:
            db_module._db_path = original_path
        self.assertNotIn("managed_terminal_backing_enabled", keys, "init_db no longer clears the stuck row")
        self.assertIn(_IDLE_CLOSE_MERGE_MARK, keys, "init_db no longer runs the idle-close fold")

if __name__ == "__main__":
    unittest.main()
