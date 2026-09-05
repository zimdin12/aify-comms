"""A tail write that throws must leave the buffer exactly as it found it.

R9-M2, external review 2026-09-06, verified at the cited lines. `_append_terminal_output` folds the
chunk into the held tail (`record`), marks the flush interval, and on an ending status `forget()`s
the buffer -- all BEFORE the UPDATE runs. `terminal_write_queue._requeue_front` puts the SAME chunk
back at the front when the write raises, which is the documented behaviour of the
`database is locked` family past the 5s busy timeout.

So the retry read a `current_tail` that already contained the chunk and appended it AGAIN, while
`record` answered False because the interval had just been marked -- duplicated bytes in the stored
tail, and no write to carry them. On the ending path the same failure lost the buffer outright: the
final screen of a worker that died, which is the one an operator reads to find out why.

WHY A ROLLBACK RATHER THAN A REORDER. Moving every mutation after the UPDATE would put the
live-screen feed after `_answer_console_prompt`, which reads that screen, and would split `record`'s
hold from its due-decision on the hottest path in the service. Restoring touches one call site.

These tests drive the real `_append_terminal_output` against a database that refuses the UPDATE.
"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest

import aiosqlite

from service.api_core import terminal_tail_buffer as tail
from service.api_core.terminal_output import _append_terminal_output
from service.schema import SCHEMA

TERMINAL_ID = "term-r9m2"


def _required_columns() -> list[str]:
    """Columns `terminal_sessions` declares NOT NULL with no default, read from the real DDL."""
    body = SCHEMA.split("CREATE TABLE IF NOT EXISTS terminal_sessions (", 1)[1].split(");", 1)[0]
    required = []
    for line in body.splitlines():
        part = line.strip().rstrip(",")
        if not part or part.startswith(("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "--")):
            continue
        upper = part.upper()
        if "NOT NULL" in upper and "DEFAULT" not in upper:
            required.append(part.split()[0])
    assert required, "the DDL parse found no NOT NULL columns, so this fixture proves nothing"
    return required


class _RefusingDb:
    """A connection whose terminal_sessions UPDATE always raises, like a lock that never clears."""

    def __init__(self, inner):
        self._inner = inner
        self.attempts = 0

    async def execute(self, sql, params=()):
        if sql.strip().upper().startswith("UPDATE TERMINAL_SESSIONS"):
            self.attempts += 1
            raise sqlite3.OperationalError("database is locked")
        return await self._inner.execute(sql, params)

    async def commit(self):
        return await self._inner.commit()

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def _seeded():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    values = {c: "x" for c in _required_columns()}
    values.update({
        "id": TERMINAL_ID, "status": "running", "output": "", "output_seq": 0,
        "created_at": "2026-09-06T00:00:00Z", "updated_at": "2026-09-06T00:00:00Z",
    })
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    await db.execute(f"INSERT INTO terminal_sessions ({cols}) VALUES ({marks})", tuple(values.values()))
    await db.commit()
    return db


async def _row(db):
    cur = await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (TERMINAL_ID,))
    return await cur.fetchone()


class FailedTailWriteDoesNotDuplicateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        tail.reset_for_tests()
        self.addCleanup(tail.reset_for_tests)

    async def test_POSITIVE_CONTROL_a_working_write_accumulates_once(self) -> None:
        """If the ordinary path did not accumulate, the duplication below could not be detected."""
        db = await _seeded()
        try:
            await _append_terminal_output(db, await _row(db), "AAA", status="running", seq=1)
            await _append_terminal_output(db, await _row(db), "BBB", status="running", seq=2)
            self.assertEqual(tail.current_tail(TERMINAL_ID, ""), "AAABBB")
        finally:
            await db.close()

    async def test_POSITIVE_CONTROL_the_refusing_db_really_refuses(self) -> None:
        db = await _seeded()
        refusing = _RefusingDb(db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                await _append_terminal_output(refusing, await _row(db), "AAA", status="running", seq=1)
            self.assertEqual(refusing.attempts, 1, "the UPDATE was never reached, so nothing was tested")
        finally:
            await db.close()

    async def test_THE_CHUNK_IS_NOT_APPENDED_TWICE_WHEN_THE_WRITE_FAILS(self) -> None:
        """The defect, driven exactly as the write queue drives it: fail, requeue the same chunk, retry."""
        db = await _seeded()
        try:
            await _append_terminal_output(db, await _row(db), "AAA", status="running", seq=1)
            self.assertEqual(tail.current_tail(TERMINAL_ID, ""), "AAA")

            refusing = _RefusingDb(db)
            with self.assertRaises(sqlite3.OperationalError):
                await _append_terminal_output(refusing, await _row(db), "BBB", status="running", seq=2)

            # The queue requeues the SAME chunk and the next flush retries it.
            await _append_terminal_output(db, await _row(db), "BBB", status="running", seq=2)

            self.assertEqual(
                tail.current_tail(TERMINAL_ID, ""), "AAABBB",
                "the chunk was folded in before the UPDATE and again on the retry, so the stored tail "
                "carries it twice",
            )
        finally:
            await db.close()

    async def test_THE_RETRY_STILL_WRITES(self) -> None:
        """`record` marks the flush interval before the UPDATE. If that mark survives a failure, the
        retry answers False, skips the write, and the bytes never reach the row at all."""
        tail.set_flush_interval_for_tests(0.0)
        self.addCleanup(tail.set_flush_interval_for_tests, None)
        db = await _seeded()
        try:
            refusing = _RefusingDb(db)
            with self.assertRaises(sqlite3.OperationalError):
                await _append_terminal_output(refusing, await _row(db), "HELLO", status="running", seq=1)

            await _append_terminal_output(db, await _row(db), "HELLO", status="running", seq=1)
            row = await _row(db)
            self.assertEqual(row["output"], "HELLO", "the retry wrote nothing; the bytes are lost")
            self.assertEqual(int(row["output_seq"]), 1)
        finally:
            await db.close()

    async def test_AN_ENDING_TERMINAL_KEEPS_ITS_FINAL_SCREEN_WHEN_THE_WRITE_FAILS(self) -> None:
        """`forget()` runs on the ending path before the UPDATE. A failure there dropped the last
        screen of a dead worker -- the one `terminal_diagnostics` reads to say what killed it."""
        db = await _seeded()
        try:
            await _append_terminal_output(db, await _row(db), "the last thing it printed", status="running", seq=1)

            refusing = _RefusingDb(db)
            with self.assertRaises(sqlite3.OperationalError):
                await _append_terminal_output(refusing, await _row(db), "", status="stopped", seq=2)

            self.assertEqual(
                tail.current_tail(TERMINAL_ID, ""), "the last thing it printed",
                "the ending write threw after forget() and took the final screen with it",
            )
        finally:
            await db.close()


if __name__ == "__main__":
    unittest.main()
