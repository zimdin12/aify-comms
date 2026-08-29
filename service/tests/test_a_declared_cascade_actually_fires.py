r"""`ON DELETE CASCADE` is enforced, not merely declared.

WHY THIS EXISTS, measured on the operator's database 2026-08-29. Of 719 `dispatch_controls` rows,
**574 reference a `dispatch_runs` row that no longer exists** -- 80% of the table. The relationship is
declared `FOREIGN KEY (run_id) REFERENCES dispatch_runs(id) ON DELETE CASCADE`, so those children
should have gone with their parents.

    orphaned dispatch_controls   574 of 719
      by status                  518 completed, 56 failed, 0 pending or claimed
      by age                     3 at 60-89 days, 547 at 90-119, 24 at 120-149
      by action                  563 steer, 11 interrupt

NOTHING IS STUCK, and that is worth saying rather than leaving to be inferred: every orphan is
settled and none is recent. They cluster around the bulk database cleanup of 2026-05-30, so the most
likely story is a delete run with foreign keys off. Twelve other relationships were checked the same
way and every one is clean, so this is not "enforcement is off everywhere".

WHAT NOTHING PROVED UNTIL NOW. `_apply_connection_pragmas` issues `PRAGMA foreign_keys=ON` inside an
`executescript`, and several tests REASON from the declaration -- "its table declares ON DELETE
CASCADE, so deleting the old row removes it". None of them watched a delete and checked the child was
gone. A pragma that stopped taking effect would leave every one of those tests green and every
cascade silently inert, which is exactly the state 574 rows are evidence of.

`executescript` is not an idle worry here: SQLite ignores `PRAGMA foreign_keys` inside a transaction,
and `executescript` commits before running its body. The pragma survives that today. The point is
that it is now CHECKED rather than assumed.

SCOPE. The declared population is derived from `service/schema.py` and its size is pinned, so a
cascade added or lost is visible. The EFFECT is executed for `dispatch_controls` -- the relationship
with 574 real orphans -- rather than for all 21, because seeding a valid parent and child for each
means twenty-one fixtures against NOT NULL columns and their own foreign keys, and a generic seeder
that got one wrong would prove less than this does.
"""
from __future__ import annotations

import asyncio
import re
import unittest
from pathlib import Path

from service.db import get_db
from service.tests._base import FastApiTestCase

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "service" / "schema.py"

CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", re.DOTALL)
CASCADE = re.compile(r"FOREIGN KEY \((\w+)\) REFERENCES (\w+)\((\w+)\) ON DELETE CASCADE")

#: MEASURED 2026-08-29 by the scan below. A ratchet in both directions: a cascade that disappears is
#: as interesting as one that arrives, and either should be a deliberate line in a diff.
DECLARED_CASCADES = 21


def declared_cascades() -> list[tuple[str, str, str, str]]:
    """(child table, child column, parent table, parent key) for every declared cascade."""
    found = []
    for table, body in CREATE_TABLE.findall(SCHEMA.read_text(encoding="utf-8")):
        for column, parent, key in CASCADE.findall(body):
            found.append((table, column, parent, key))
    return found


class ADeclaredCascadeActuallyFires(FastApiTestCase):
    def _run(self, coro_factory):
        async def go():
            db = await get_db()
            try:
                return await coro_factory(db)
            finally:
                await db.close()

        return asyncio.run(go())

    def test_THE_SCAN_FINDS_THE_DECLARED_CASCADES(self):
        """POSITIVE CONTROL. An empty scan makes the count assertion the only thing standing, and a
        regex that stopped matching would report a schema with no cascades at all."""
        cascades = declared_cascades()
        self.assertEqual(len(cascades), DECLARED_CASCADES, (
            f"the schema declares {len(cascades)} cascades, not {DECLARED_CASCADES}. Update the "
            "number in the same change that adds or removes one -- both directions matter."
        ))
        self.assertIn(("dispatch_controls", "run_id", "dispatch_runs", "id"), cascades)
        self.assertIn(("terminal_events", "terminal_id", "terminal_sessions", "id"), cascades)

    def test_FOREIGN_KEYS_ARE_ON_for_a_service_connection(self):
        """The statement. `_apply_connection_pragmas` sets it on every `get_db()` connection, and
        every cascade in the schema is inert without it."""
        async def probe(db):
            return (await (await db.execute("PRAGMA foreign_keys")).fetchone())[0]

        self.assertEqual(self._run(probe), 1)

    def test_THE_PRAGMA_SURVIVES_THE_EXECUTESCRIPT_IT_IS_SET_IN(self):
        """SQLite ignores `PRAGMA foreign_keys` inside a transaction, and `executescript` commits
        before running its body -- so "the line is there" and "the setting took" are two different
        claims. This asserts the second, on a connection built the way the service builds them."""
        async def probe(db):
            await db.executescript("PRAGMA busy_timeout=1000;PRAGMA foreign_keys=ON;")
            return (await (await db.execute("PRAGMA foreign_keys")).fetchone())[0]

        self.assertEqual(self._run(probe), 1, (
            "setting the pragma through executescript no longer takes effect; every ON DELETE "
            "CASCADE in the schema is decoration"
        ))

    def test_THE_EFFECT_deleting_a_run_removes_its_controls(self):
        """The one that 574 rows say was not happening. Executed rather than argued: insert a run and
        a control, delete the run, and require the control to be gone."""
        async def seed(db):
            await db.execute(
                "INSERT INTO dispatch_runs (id, target_agent, from_agent, message_type, subject,"
                " body, priority, status, requested_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("run-cascade", "agent-x", "sender", "request", "s", "b", "normal", "queued",
                 "2026-08-29T00:00:00Z"),
            )
            await db.execute(
                "INSERT INTO dispatch_controls (id, run_id, action, status, requested_at)"
                " VALUES (?,?,?,?,?)",
                ("control-cascade", "run-cascade", "steer", "pending", "2026-08-29T00:00:00Z"),
            )
            await db.commit()

        async def delete_run(db):
            await db.execute("DELETE FROM dispatch_runs WHERE id = ?", ("run-cascade",))
            await db.commit()

        async def count_controls(db):
            row = await (await db.execute(
                "SELECT COUNT(*) FROM dispatch_controls WHERE run_id = ?", ("run-cascade",),
            )).fetchone()
            return int(row[0])

        self._run(seed)
        self.assertEqual(self._run(count_controls), 1, "the fixture did not take")
        self._run(delete_run)
        self.assertEqual(self._run(count_controls), 0, (
            "the control outlived its run. 574 of the operator's 719 dispatch_controls rows are in "
            "exactly this state, and the cascade that should have prevented it is declared"
        ))

    def test_THE_CASCADE_IS_WHAT_REMOVED_IT_not_the_delete_statement(self):
        """NEGATIVE CONTROL. The test above passes just as well if something else deletes controls,
        or if the insert silently failed. With foreign keys OFF on this connection the control must
        SURVIVE -- which is both proof that the cascade did the work, and a reproduction of how 574
        rows came to exist."""
        async def probe(db):
            await db.execute("PRAGMA foreign_keys=OFF")
            await db.execute(
                "INSERT INTO dispatch_runs (id, target_agent, from_agent, message_type, subject,"
                " body, priority, status, requested_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("run-nofk", "agent-x", "sender", "request", "s", "b", "normal", "queued",
                 "2026-08-29T00:00:00Z"),
            )
            await db.execute(
                "INSERT INTO dispatch_controls (id, run_id, action, status, requested_at)"
                " VALUES (?,?,?,?,?)",
                ("control-nofk", "run-nofk", "steer", "pending", "2026-08-29T00:00:00Z"),
            )
            await db.execute("DELETE FROM dispatch_runs WHERE id = ?", ("run-nofk",))
            await db.commit()
            row = await (await db.execute(
                "SELECT COUNT(*) FROM dispatch_controls WHERE run_id = ?", ("run-nofk",),
            )).fetchone()
            return int(row[0])

        self.assertEqual(self._run(probe), 1, (
            "the control vanished with foreign keys OFF, so the test above is not proving that the "
            "cascade did it"
        ))


if __name__ == "__main__":
    unittest.main()
