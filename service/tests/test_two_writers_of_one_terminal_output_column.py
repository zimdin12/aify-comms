"""Terminal output has two writers, and both must take the same lock.

WHY THIS EXISTS, measured 2026-09-03. `_append_terminal_output` is a READ-MODIFY-WRITE: it takes
`current` from the row its caller hands it, concatenates the chunk, trims and UPDATEs. There are two
live writers of that one column and only one of them was serialised --

    streamed PTY output   routers/terminals.py -> TERMINAL_OUTPUT_WRITES.enqueue -> _write_lock
    control completion    routers/terminal_controls.py -> _append_terminal_output   (no lock)

-- so a control reporting output while a flush was in flight discarded one side's bytes entirely.
Not a torn write and not a dropped chunk: one writer's whole append vanished, because the other had
already read `current` and overwrote the column from that older value.

IT IS THE CONTROL THAT MAKES THE FIRST TEST EVIDENCE. "Two appends survived" proves nothing on its
own -- it is what a function that cannot append twice would fail, and what a serialised pair passes
for free. So the unlocked helper is driven the same way, and it must LOSE one. That asymmetry is the
whole finding, and it is why the lock is load-bearing rather than defensive.

WHY NOT ATOMIC SQL, which is the usual answer to a lost update. `output = substr(output || ?, -N)`
removes the read-modify-write, but `_trim_terminal_output` does not slice -- it slices AND drops the
first partial line, because a raw tail cuts mid-ANSI-escape and the dashboard then seeds a fresh
xterm with a broken escape. That was fixed 2026-06-07 and substr would bring it back.

WHY NOT ROUTE THE CONTROL PATH THROUGH THE QUEUE, which is tidier and would also close it: it turns
control output from immediate into batched, on the hottest write path in the service. Taking the
same lock changes nothing else.
"""
from __future__ import annotations

import asyncio
import unittest

from service.api_core.terminal_output import _append_terminal_output
from service.clock import now as _now
from service.db import get_db
from service.terminal_write_queue import TERMINAL_OUTPUT_WRITES
from service.tests._base import FastApiTestCase

TERMINAL = "term-two-writers"


class TwoWritersOfOneTerminalOutputColumnTests(FastApiTestCase):
    def _seed(self) -> None:
        async def go():
            db = await get_db()
            try:
                fresh = _now()
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    "INSERT OR REPLACE INTO terminal_sessions (id, agent_id, session_id, "
                    "environment_id, runtime, bridge_id, command, workspace, status, output, "
                    "error, output_seq, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (TERMINAL, "a", "s", "e", "hermes", "b", "cmd", "C:/w", "attached",
                     "", "", 0, fresh, fresh),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(go())

    def _output(self, db=None) -> str:
        async def go():
            conn = await get_db()
            try:
                row = await (await conn.execute(
                    "SELECT output FROM terminal_sessions WHERE id = ?", (TERMINAL,))).fetchone()
                return (row["output"] if row else "") or ""
            finally:
                await conn.close()
        return asyncio.run(go())

    async def _row(self, db):
        return await (await db.execute(
            "SELECT id, output, status, output_seq, cols, rows FROM terminal_sessions WHERE id = ?",
            (TERMINAL,))).fetchone()

    def setUp(self):
        super().setUp()
        self._seed()

    def test_TWO_CONCURRENT_APPENDS_THROUGH_THE_LOCK_KEEP_BOTH(self):
        """The fix. Both callers of the one column now serialise on the queue's write lock."""
        async def go():
            db = await get_db()
            try:
                async def writer(chunk: str):
                    # THE CALLER READS THE ROW FIRST, exactly as the controls route does -- and
                    # then hands over the ID, not that row. If the method appended to what the
                    # caller read, this test would fail even with the lock held, which is how the
                    # first version of the fix was caught.
                    row = await self._row(db)
                    # The yield that exists in production between a caller's SELECT and the UPDATE
                    # inside the append; in the real code it comes from the awaits themselves.
                    await asyncio.sleep(0)
                    await TERMINAL_OUTPUT_WRITES.append_outside_the_queue(
                        db, TERMINAL, chunk, fallback=row,
                    )
                    await db.commit()

                await asyncio.gather(writer("AAAA"), writer("BBBB"))
            finally:
                await db.close()
        asyncio.run(go())

        out = self._output()
        self.assertIn("AAAA", out, f"the first writer's bytes were lost: {out!r}")
        self.assertIn("BBBB", out, f"the second writer's bytes were lost: {out!r}")

    def test_THE_UNLOCKED_HELPER_LOSES_ONE_which_is_why_the_lock_exists(self):
        """THE CONTROL, and the reason the test above is evidence rather than a tautology.

        Driven identically, the raw helper keeps only one of the two appends. If this ever starts
        passing, the read-modify-write has been made safe some other way and the lock above may be
        reconsidered -- but until then, removing it re-opens a silent data loss.
        """
        async def go():
            db = await get_db()
            try:
                async def writer(chunk: str):
                    row = await self._row(db)
                    await asyncio.sleep(0)
                    await _append_terminal_output(db, row, chunk)
                    await db.commit()

                await asyncio.gather(writer("AAAA"), writer("BBBB"))
            finally:
                await db.close()
        asyncio.run(go())

        out = self._output()
        self.assertFalse(
            "AAAA" in out and "BBBB" in out,
            "the unlocked helper kept both appends. Either the read-modify-write is gone or the "
            "interleaving no longer happens -- re-derive whether the lock above is still needed "
            f"rather than assuming: {out!r}",
        )

    def test_SERIALISED_APPENDS_ACCUMULATE(self):
        """POSITIVE CONTROL for both tests above: the helper CAN append twice.

        Without this, a function that simply overwrote on every call would satisfy the control test
        and make the fix look effective when nothing had been fixed.
        """
        async def go():
            db = await get_db()
            try:
                row = await self._row(db)
                await _append_terminal_output(db, row, "CCCC")
                await db.commit()
                row = await self._row(db)
                await _append_terminal_output(db, row, "DDDD")
                await db.commit()
            finally:
                await db.close()
        asyncio.run(go())

        self.assertEqual(self._output(), "CCCCDDDD")


if __name__ == "__main__":
    unittest.main()
