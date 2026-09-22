"""`worker_idle_close_enabled` was merged into `worker_idle_close_minutes` (0 = off) on 2026-09-19.

The migration must keep a host that had the toggle OFF from starting to close idle workers, and must
run once: a later restart cannot zero minutes the operator set since.

A MISSING ROW IS THE COMMONEST SHAPE, and the first version of this migration did not handle it.
Settings rows exist only where somebody SET a value, and the toggle's default was False -- so a host
with minutes set and the toggle never touched had idle-closing OFF, and keying the fold on a row
reading 'false' skipped exactly those hosts and switched it ON for them. The DELETE meant there was
no second chance. External review, 2026-09-21, finding 9.
"""
import asyncio
import unittest

import aiosqlite

from service.db import _IDLE_CLOSE_MERGE_MARK, _clear_stuck_internal_settings, _migrate_settings_rows


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
        rows_after = dict(await cursor.fetchall())
        # The mark is bookkeeping, not a setting; every case below asserts the SETTINGS it left.
        rows_after.pop(_IDLE_CLOSE_MERGE_MARK, None)
        return rows_after


class IdleCloseToggleMigrationTest(unittest.TestCase):
    def test_a_disabled_toggle_becomes_zero_minutes(self):
        after = asyncio.run(_run([("worker_idle_close_enabled", "false"), ("worker_idle_close_minutes", "45")]))
        self.assertEqual(after, {"worker_idle_close_minutes": "0"})

    def test_a_host_that_never_touched_the_toggle_was_off_and_stays_off(self):
        # The default was False. No row means nobody changed it, which means idle-closing was off --
        # and this is the host the first version of the migration switched on.
        after = asyncio.run(_run([("worker_idle_close_minutes", "45")]))
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

    def test_a_later_restart_keeps_minutes_on_a_host_that_had_no_toggle_row(self):
        # The same guarantee where there is no toggle row to consume: without the mark, every
        # restart would zero whatever the operator had set since, for ever.
        after = asyncio.run(_run(
            [("worker_idle_close_minutes", "45")],
            passes=3, set_between=("worker_idle_close_minutes", "30"),
        ))
        self.assertEqual(after, {"worker_idle_close_minutes": "30"})

    def test_a_fresh_install_has_nothing_to_migrate(self):
        self.assertEqual(asyncio.run(_run([])), {})

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

if __name__ == "__main__":
    unittest.main()
