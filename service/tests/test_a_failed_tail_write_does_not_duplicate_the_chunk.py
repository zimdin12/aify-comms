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

from unittest.mock import patch

from service import terminal_snapshot as snapshot
from service import terminal_write_queue as write_queue
from service.api_core import terminal_tail_buffer as tail
from service.api_core.terminal_output import _append_terminal_output
from service.schema import SCHEMA
from service.terminal_write_queue import TerminalOutputWriteQueue


def _handing_back(db):
    """A `get_db` replacement that answers a FRESH awaitable each call.

    `return_value=<coroutine>` hands back the same object every time, and awaiting one twice
    raises -- which is what a second write in one test does.
    """
    async def ready():
        return db
    return ready

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


class TheLiveScreenIsRolledBackWithTheTailTests(unittest.IsolatedAsyncioTestCase):
    """The SCREEN is speculative state too, and it was the half nothing put back.

    `_append_terminal_output` feeds the live screen before it attempts the UPDATE. `restore` puts the
    TAIL back when that throws; a pyte grid has no undo, so the retry painted the chunk a second time
    and the console carried a duplicate for the life of the session.

    A GUARD IN `feed_live_screen` WAS THE FIRST ANSWER AND REVIEW BROKE IT, which is why these tests
    are here and not there. It refused a chunk with the same sequence and the same bytes as the last
    one -- and `_requeue_front` PREPENDS the failed chunk to a NEWER pending batch, so a failed B
    comes back as BC. Review drove the real queue and witnessed a raw tail of AABC beside a screen of
    AABBC. No comparison against the last chunk can recognise that; the state has to be retired where
    it was created.
    """

    ESC = chr(27)

    def setUp(self) -> None:
        tail.reset_for_tests()
        self.addCleanup(tail.reset_for_tests)
        snapshot.drop_live_screen(TERMINAL_ID)
        self.addCleanup(snapshot.drop_live_screen, TERMINAL_ID)

    def _screen(self) -> str:
        rendered = snapshot.render_live_screen(TERMINAL_ID)
        return "" if rendered is None else rendered[0]

    async def test_POSITIVE_CONTROL_the_screen_tracks_the_tail_when_nothing_fails(self) -> None:
        """Every assertion below compares a screen against a tail. If they never agreed in the
        ordinary case, a disagreement would say nothing about a failed write."""
        db = await _seeded()
        try:
            await _append_terminal_output(db, await _row(db), self.ESC + "[HAA", status="running", seq=1)
            await _append_terminal_output(db, await _row(db), "B", status="running", seq=2)
            self.assertEqual(tail.current_tail(TERMINAL_ID, ""), self.ESC + "[HAAB")
            self.assertIn("AAB", self._screen())
        finally:
            await db.close()

    async def test_THE_SCREEN_DOES_NOT_KEEP_A_CHUNK_THE_TAIL_ROLLED_BACK(self) -> None:
        """Review's approved case: fail B, retry the SAME bytes. Raw AAB, screen AAB."""
        db = await _seeded()
        try:
            await _append_terminal_output(db, await _row(db), self.ESC + "[HAA", status="running", seq=1)

            refusing = _RefusingDb(db)
            with self.assertRaises(sqlite3.OperationalError):
                await _append_terminal_output(refusing, await _row(db), "B", status="running", seq=2)

            await _append_terminal_output(db, await _row(db), "B", status="running", seq=2)
            self.assertEqual(tail.current_tail(TERMINAL_ID, ""), self.ESC + "[HAAB")
            self.assertNotIn("AABB", self._screen(),
                             "the screen kept the failed chunk and the retry painted it again")
            self.assertIn("AAB", self._screen())
        finally:
            await db.close()

    async def test_AND_NOT_WHEN_THE_RETRY_COALESCES_WITH_NEWER_OUTPUT(self) -> None:
        """Review's failing case, and the one a last-chunk comparison cannot see.

        `_requeue_front` prepends the failed chunk to whatever arrived while it was failing, so the
        retry is BC where the failure was B. Witnessed: raw AABC, screen AABBC.
        """
        db = await _seeded()
        try:
            await _append_terminal_output(db, await _row(db), self.ESC + "[HAA", status="running", seq=1)

            refusing = _RefusingDb(db)
            with self.assertRaises(sqlite3.OperationalError):
                await _append_terminal_output(refusing, await _row(db), "B", status="running", seq=2)

            # C arrived while B was failing; the queue hands back one coalesced batch.
            await _append_terminal_output(db, await _row(db), "BC", status="running", seq=3)
            self.assertEqual(tail.current_tail(TERMINAL_ID, ""), self.ESC + "[HAABC")
            self.assertNotIn("AABBC", self._screen(),
                             "the coalesced retry painted the failed chunk a second time")
            self.assertIn("AABC", self._screen())
        finally:
            await db.close()

    async def test_A_SCREEN_RETIRED_BY_A_FAILURE_COMES_BACK_FROM_THE_STORED_TAIL(self) -> None:
        """NEGATIVE CONTROL for the rollback: retiring the screen must not leave the console blank.

        The next chunk builds a fresh screen SEEDED from the stored tail, which `restore` has just
        made correct again. A rollback that simply deleted the picture would trade a duplicate for an
        empty console, which is worse.
        """
        db = await _seeded()
        try:
            await _append_terminal_output(db, await _row(db), self.ESC + "[HAA", status="running", seq=1)
            refusing = _RefusingDb(db)
            with self.assertRaises(sqlite3.OperationalError):
                await _append_terminal_output(refusing, await _row(db), "B", status="running", seq=2)

            self.assertEqual(self._screen(), "", "the screen survived a failed write")
            await _append_terminal_output(db, await _row(db), "B", status="running", seq=2)
            self.assertIn("AAB", self._screen(),
                          "the screen did not come back from the stored tail after the rollback")
        finally:
            await db.close()


class _HeldOpenDb:
    """The queue's connection, kept open, and optionally refusing one statement AFTER the UPDATE.

    The UPDATE is the failure everybody thinks of, and it is the one already guarded. Review's
    finding is the two that are not: the event INSERT that follows it, and the COMMIT that ends the
    transaction. Both leave the durable row rolled back when the connection closes, and both used to
    leave the in-memory tail claiming the bytes anyway.

    HELD OPEN BECAUSE THE REAL PATH CLOSES. `_write_terminal_output` closes the connection in its
    `finally`, and closing is what makes the rollback happen; a test that wants to read what survived
    has to do the rollback itself and keep reading. The healthy control uses this wrapper too, so the
    two differ in one thing.
    """

    def __init__(self, inner, refuse: str = ""):
        self._inner = inner
        self._refuse = refuse
        self.attempts = 0

    async def execute(self, sql, params=()):
        if self._refuse == "event" and sql.strip().upper().startswith("INSERT INTO TERMINAL_EVENTS"):
            self.attempts += 1
            raise sqlite3.OperationalError("database is locked")
        return await self._inner.execute(sql, params)

    async def commit(self):
        if self._refuse == "commit":
            self.attempts += 1
            raise sqlite3.OperationalError("database is locked")
        return await self._inner.commit()

    async def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TheTRANSACTIONOwnsTheRollbackTests(unittest.IsolatedAsyncioTestCase):
    """`_append_terminal_output` guards its own UPDATE. The transaction is bigger than that.

    FOUND BY REVIEW, executed rather than inferred. Seed AA, append B, refuse EITHER the event INSERT
    or the COMMIT, and let the connection roll back: the durable row returns to AA while the held
    cache still says AAB. The retry then appends B to a tail that already claimed it and persists
    AABB. Wrong bytes on disk is worse than a wrong screen, because nothing later repaints it.

    TWO POPULATIONS, AND MERGING THEM WAS MY ERROR. I first recorded this whole finding as
    long-standing because it "reproduces against v0.6.1". Review corrected it against the baseline
    JSON and the correction is the useful half:

      * the SCREEN duplication is pre-existing -- the original base shows durable AAB and screen AABB
      * the DURABLE duplication is a REGRESSION of the held-tail work, which put an in-memory cache
        in front of the column: the original base has no cache to disagree with the row, so its
        durable bytes stayed correct

    So the rollback added here repairs a regression on the durable side and a pre-existing defect on
    the screen side, and calling both "long-standing" would have retired a regression by vocabulary.
    """

    ESC = chr(27)

    def setUp(self) -> None:
        tail.reset_for_tests()
        self.addCleanup(tail.reset_for_tests)
        # EVERY WRITE DURABLE, because this class compares the ROW against the held tail and the
        # lazy flush makes the row lag by up to a second BY DESIGN. Without this the positive
        # control fails for a reason that is not a defect -- which it did, and the reading was the
        # tail buffer working exactly as its own module documents.
        tail.set_flush_interval_for_tests(0.0)
        self.addCleanup(tail.set_flush_interval_for_tests, None)
        snapshot.drop_live_screen(TERMINAL_ID)
        self.addCleanup(snapshot.drop_live_screen, TERMINAL_ID)

    async def _drive(self, refuse: str):
        """Run one queue write against a connection that refuses `refuse`, and answer what survived."""
        db = await _seeded()
        try:
            queue = TerminalOutputWriteQueue()
            with patch.object(write_queue, "get_db", _handing_back(_HeldOpenDb(db))):
                await queue._write_terminal_output(TERMINAL_ID, self.ESC + "[HAA", status="running", seq=1)
            refusing = _HeldOpenDb(db, refuse)
            with patch.object(write_queue, "get_db", _handing_back(refusing)):
                with self.assertRaises(sqlite3.OperationalError):
                    await queue._write_terminal_output(TERMINAL_ID, "B", status="running", seq=2)
            await db.rollback()
            row = await _row(db)
            return row, tail.current_tail(TERMINAL_ID, "")
        finally:
            await db.close()

    async def test_POSITIVE_CONTROL_a_working_queue_write_persists_and_holds_the_same_bytes(self) -> None:
        db = await _seeded()
        try:
            queue = TerminalOutputWriteQueue()
            with patch.object(write_queue, "get_db", _handing_back(_HeldOpenDb(db))):
                await queue._write_terminal_output(TERMINAL_ID, self.ESC + "[HAA", status="running", seq=1)
                await queue._write_terminal_output(TERMINAL_ID, "B", status="running", seq=2)
            row = await _row(db)
            self.assertEqual(row["output"], self.ESC + "[HAAB")
            self.assertEqual(tail.current_tail(TERMINAL_ID, ""), self.ESC + "[HAAB")
        finally:
            await db.close()

    async def test_A_REFUSED_EVENT_INSERT_ROLLS_THE_HELD_TAIL_BACK_TOO(self) -> None:
        row, held = await self._drive("event")
        self.assertEqual(row["output"], self.ESC + "[HAA", "the durable row did not roll back")
        self.assertEqual(held, self.ESC + "[HAA",
                         "the held tail kept bytes the transaction rolled back, so the retry will "
                         "append them a second time and persist the duplicate")

    async def test_A_REFUSED_COMMIT_ROLLS_THE_HELD_TAIL_BACK_TOO(self) -> None:
        row, held = await self._drive("commit")
        self.assertEqual(row["output"], self.ESC + "[HAA", "the durable row did not roll back")
        self.assertEqual(held, self.ESC + "[HAA",
                         "the held tail kept bytes the commit never made durable")

    async def test_THE_SCREEN_IS_RETIRED_AT_THE_SAME_BOUNDARY(self) -> None:
        """The screen is speculative state too, and it is fed before any of these statements run.

        `_append_terminal_output` retires it when its OWN update throws. A failure further along --
        the event insert, the commit -- never reaches that handler, so the transaction owner has to
        do it as well or the retry paints the chunk onto a screen that already has it.
        """
        db = await _seeded()
        try:
            queue = TerminalOutputWriteQueue()
            with patch.object(write_queue, "get_db", _handing_back(_HeldOpenDb(db))):
                await queue._write_terminal_output(TERMINAL_ID, self.ESC + "[HAA", status="running", seq=1)
            refusing = _HeldOpenDb(db, "commit")
            with patch.object(write_queue, "get_db", _handing_back(refusing)):
                with self.assertRaises(sqlite3.OperationalError):
                    await queue._write_terminal_output(TERMINAL_ID, "B", status="running", seq=2)
            await db.rollback()

            rendered = snapshot.render_live_screen(TERMINAL_ID)
            self.assertIsNone(rendered, "the speculative screen survived a rolled-back transaction")

            with patch.object(write_queue, "get_db", _handing_back(_HeldOpenDb(db))):
                await queue._write_terminal_output(TERMINAL_ID, "B", status="running", seq=2)
            screen = snapshot.render_live_screen(TERMINAL_ID)[0]
            self.assertIn("AAB", screen)
            self.assertNotIn("AABB", screen, "the retry painted the chunk onto a screen that had it")
        finally:
            await db.close()

    async def test_AND_THE_RETRY_THEN_PERSISTS_THE_BYTES_ONCE(self) -> None:
        """The consequence, which is what makes the two assertions above matter."""
        db = await _seeded()
        try:
            queue = TerminalOutputWriteQueue()
            with patch.object(write_queue, "get_db", _handing_back(_HeldOpenDb(db))):
                await queue._write_terminal_output(TERMINAL_ID, self.ESC + "[HAA", status="running", seq=1)
            refusing = _HeldOpenDb(db, "commit")
            with patch.object(write_queue, "get_db", _handing_back(refusing)):
                with self.assertRaises(sqlite3.OperationalError):
                    await queue._write_terminal_output(TERMINAL_ID, "B", status="running", seq=2)
            await db.rollback()
            with patch.object(write_queue, "get_db", _handing_back(_HeldOpenDb(db))):
                await queue._write_terminal_output(TERMINAL_ID, "B", status="running", seq=2)
            row = await _row(db)
            self.assertEqual(row["output"], self.ESC + "[HAAB",
                             "the retry persisted the chunk twice")
        finally:
            await db.close()


if __name__ == "__main__":
    unittest.main()


class SettleDoesNotRegressTheSeqTests(unittest.IsolatedAsyncioTestCase):
    """A settle flushes BYTES; it must not renumber them.

    External review 2026-09-06 (LOW). `settle_terminal_tail` passed
    `seq=int(held.get("seq") or 0)`, and `_append_terminal_output` writes the column for any non-None
    value -- so a held tail carrying no sequence wrote a literal 0 and the row's `output_seq` went
    BACKWARDS. A client seeds `lastSeq` from that, and the next live frame reads as a gap: R9-H1's
    repaint storm arriving through a different door.
    """

    def setUp(self) -> None:
        tail.reset_for_tests()
        self.addCleanup(tail.reset_for_tests)

    async def test_a_settle_with_no_held_seq_leaves_the_column_alone(self) -> None:
        db = await _seeded()
        try:
            await _append_terminal_output(db, await _row(db), "AAA", status="running", seq=41)
            self.assertEqual(int((await _row(db))["output_seq"]), 41)

            # A chunk held with NO sequence, then a settle. The row must keep 41.
            tail.record(TERMINAL_ID, "AAABBB", None)
            await _append_terminal_output(db, await _row(db), "", status="", seq=None, settle=True)

            self.assertEqual(
                int((await _row(db))["output_seq"]), 41,
                "the settle wrote a sequence it did not have and the row went backwards",
            )
        finally:
            await db.close()

    async def test_a_settle_WITH_a_held_seq_still_writes_it(self) -> None:
        """The control: skipping the column unconditionally would strand the sequence a real settle
        exists to carry."""
        db = await _seeded()
        try:
            await _append_terminal_output(db, await _row(db), "AAA", status="running", seq=41)
            tail.record(TERMINAL_ID, "AAABBB", 42)
            await _append_terminal_output(db, await _row(db), "", status="", seq=42, settle=True)
            self.assertEqual(int((await _row(db))["output_seq"]), 42)
        finally:
            await db.close()
