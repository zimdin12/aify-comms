"""A terminal that ends any way at all eventually releases its 64 KB tail buffer.

R9-M4, external review 2026-09-06. `forget()` releases the in-memory tail, and it has exactly ONE
caller: the ending branch of `_append_terminal_output`. Measured the same day: 108 places in the
service write a terminal status. So a terminal ended by a reaper, a bridge supersede, a lifecycle
stop, a session teardown or the retention wipe kept its buffer -- up to 64 KB each, for the life of
the process.

THE FIX IS STATE-BASED, WHICH IS THIS REPO'S OWN RULE for cleanup that has to hold on every path.
Adding `forget()` to each of the 108 sites is a fix that lasts until the 109th, and the leak would
come back with nothing to notice it. The sweep asks which terminals are still ACTIVE and releases
everything else, so a new write site cannot bypass it.

These tests drive the real sweep against a real database.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core import terminal_tail_buffer as tail
from service.reconcilers.terminal_consistency import _release_tail_buffers_for_dead_terminals
from service.schema import SCHEMA


def _required_columns() -> list[str]:
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


async def _db(rows):
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    for terminal_id, status in rows:
        values = {c: "x" for c in _required_columns()}
        values.update({
            "id": terminal_id, "status": status, "output": "", "output_seq": 0,
            "created_at": "2026-09-06T00:00:00Z", "updated_at": "2026-09-06T00:00:00Z",
        })
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        await db.execute(f"INSERT INTO terminal_sessions ({cols}) VALUES ({marks})", tuple(values.values()))
    await db.commit()
    return db


class DeadTerminalReleasesItsTailTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        tail.reset_for_tests()
        self.addCleanup(tail.reset_for_tests)

    async def test_POSITIVE_CONTROL_the_sweep_can_see_a_held_tail(self) -> None:
        """Every assertion below is "the buffer is gone", which an empty buffer set satisfies for
        free -- and an empty set is what a broken accessor returns."""
        tail.record("t-live", "bytes", 1)
        self.assertEqual(tail.held_ids(), {"t-live"})

    async def test_A_STOPPED_TERMINAL_RELEASES_ITS_BUFFER(self) -> None:
        db = await _db([("t-dead", "stopped")])
        try:
            tail.record("t-dead", "the last screen", 7)
            released = await _release_tail_buffers_for_dead_terminals(db)
            self.assertEqual(released, 1)
            self.assertEqual(tail.held_ids(), set(), "a dead terminal is still holding 64 KB")
        finally:
            await db.close()

    async def test_A_LIVE_TERMINAL_KEEPS_ITS_BUFFER(self) -> None:
        """The control that makes the release safe. Dropping a live terminal's tail would lose every
        chunk since its last flush -- the lazy tail's whole durability window."""
        db = await _db([("t-running", "running"), ("t-attached", "attached")])
        try:
            tail.record("t-running", "mid-turn output", 3)
            tail.record("t-attached", "mid-turn output", 4)
            released = await _release_tail_buffers_for_dead_terminals(db)
            self.assertEqual(released, 0)
            self.assertEqual(tail.held_ids(), {"t-running", "t-attached"})
        finally:
            await db.close()

    async def test_EVERY_ENDED_STATUS_RELEASES_not_just_stopped(self) -> None:
        """The vocabulary has six ending members, and reading only `stopped` is the bug this repo has
        already fixed once in `_append_terminal_output` itself."""
        ended = ["stopped", "failed", "lost", "ended", "completed", "cancelled"]
        db = await _db([(f"t-{status}", status) for status in ended])
        try:
            for status in ended:
                tail.record(f"t-{status}", "held", 1)
            released = await _release_tail_buffers_for_dead_terminals(db)
            self.assertEqual(released, len(ended))
            self.assertEqual(tail.held_ids(), set())
        finally:
            await db.close()

    async def test_A_TERMINAL_WHOSE_ROW_IS_GONE_RELEASES_TOO(self) -> None:
        """The retention wipe deletes rows outright. A buffer for a row that no longer exists can
        never be released by an event, because there is nothing left to end."""
        db = await _db([("t-alive", "running")])
        try:
            tail.record("t-deleted", "orphaned bytes", 1)
            tail.record("t-alive", "live bytes", 2)
            released = await _release_tail_buffers_for_dead_terminals(db)
            self.assertEqual(released, 1)
            self.assertEqual(tail.held_ids(), {"t-alive"})
        finally:
            await db.close()

    async def test_it_costs_nothing_when_nothing_is_held(self) -> None:
        """It runs every 60s on every service. A sweep that queries regardless would be a query per
        minute forever to learn there was nothing to do."""
        db = await _db([("t-1", "running")])
        try:
            self.assertEqual(await _release_tail_buffers_for_dead_terminals(db), 0)
        finally:
            await db.close()


if __name__ == "__main__":
    unittest.main()
