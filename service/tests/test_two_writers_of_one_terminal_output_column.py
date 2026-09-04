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

import pathlib

import asyncio
import unittest

from service.api_core.terminal_tail_buffer import reset_for_tests, set_flush_interval_for_tests
from service.api_core.terminal_output import _append_terminal_output
from service.clock import now as _now
from service.db import get_db
from service.terminal_write_queue import TERMINAL_OUTPUT_WRITES
from service.tests._base import FastApiTestCase

TERMINAL = "term-two-writers"


#: Read as SOURCE, because the property is the code's shape and not its behaviour.
_TERMINAL_OUTPUT_MODULE = (
    pathlib.Path(__file__).resolve().parents[1] / "api_core" / "terminal_output.py"
)

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
        # EVERY WRITE DURABLE, because this file's property is that bytes SURVIVE two writers
        # -- not when they land. Since 2026-09-04 the tail is flushed on a slower cadence than
        # the stream (`terminal_tail_buffer`), which is a durability window and not a change to
        # who wins a race. Reading the column with the cadence in force would test the cadence.
        set_flush_interval_for_tests(0.0)
        self.addCleanup(reset_for_tests)

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

    def test_THE_READ_MODIFY_WRITE_DOES_NOT_YIELD_which_is_why_the_race_is_gone(self):
        """WHAT REPLACED THE UNLOCKED-LOSES-ONE CONTROL, on 2026-09-04, and why.

        That control drove two unlocked writers and required one to lose its bytes -- which is what
        made the locked test evidence rather than a tautology. Its docstring pre-registered the
        outcome: "If this ever starts passing, the read-modify-write has been made safe some other
        way and the lock above may be reconsidered." It started passing, so the question was
        re-derived rather than assumed.

        IT PASSES BECAUSE THE CRITICAL SECTION STOPPED YIELDING. The old read was
        `await self._row(db)`, so two coroutines could interleave between reading the column and
        writing it back and the second overwrote the first. The tail now comes from a process-shared
        buffer and the whole read-modify-write is synchronous, so under asyncio it cannot be
        preempted: the interleaving is impossible by construction rather than unlikely by timing.

        A CONTROL OVER A RACE THAT CANNOT HAPPEN PROVES NOTHING, so this guards the property the
        safety now rests on instead. One `await` added between the read and the write would restore
        the old defect silently, and nothing else in the suite would notice.

        THE LOCK STAYS, decided rather than inherited: SQLite has one writer and the lock is what
        stops a retry storm against the database lock, and the atomicity below is a property of the
        code's SHAPE that an edit could remove.
        """
        import ast

        source = pathlib.Path(_TERMINAL_OUTPUT_MODULE).read_text(encoding="utf-8")
        fn = next(
            n for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_append_terminal_output"
        )
        read_at = write_at = None
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "current_tail":
                    read_at = node.lineno
                if node.func.id == "record":
                    write_at = node.lineno
        self.assertIsNotNone(read_at, "`current_tail` is gone; the tail no longer comes from the "
                                      "shared buffer and this whole argument needs re-deriving")
        self.assertIsNotNone(write_at, "`record` is gone; same")
        self.assertLess(read_at, write_at, "the buffer is written before it is read")

        yields = [n.lineno for n in ast.walk(fn)
                  if isinstance(n, ast.Await) and read_at < n.lineno < write_at]
        self.assertEqual(
            yields, [],
            f"`_append_terminal_output` now awaits at line(s) {yields}, between reading the shared "
            "tail and writing it back. That reopens exactly the interleaving D13 was about: two "
            "writers both read the same tail, and the second overwrites the first's bytes. The "
            "buffer made this race impossible by never yielding -- an await here makes it possible "
            "again, silently.",
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
