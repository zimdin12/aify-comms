r"""The default runs listing walks an index instead of reading and sorting every row.

MEASURED on the operator's database, 2026-08-29, with `EXPLAIN QUERY PLAN` -- which reports what
SQLite decided rather than how long it took, and so is usable on a host whose wall clock is not.

    dispatch_runs                                        21,781 rows
    rows matching the listing's filter                   21,778
    rows the dashboard's default page returns                 81

    BEFORE   SCAN dispatch_runs
             USE TEMP B-TREE FOR ORDER BY
    AFTER    SCAN dispatch_runs USING INDEX idx_dispatch_runs_requested

`dispatch_runs` had three indexes and every one leads with a column the DEFAULT view does not filter
on -- `target_agent`, `from_agent`, `status`. So `GET /dispatch/runs` with no filters, which is what
the dashboard polls, had nothing to walk: it read all 21,778 matching rows and sorted them to return
81. That route logged 4,210 SLOW-REQ warnings in the 8.5 hours to 2026-08-29 07:56, median 1,499ms.

THE FILTERED FORMS MUST NOT REGRESS, which is the risk a new index carries: `status = ?` still plans
as `SEARCH dispatch_runs USING INDEX idx_dispatch_runs_status_requested`, checked in the same probe
and asserted below. An index that stole the filtered plans would trade a fixed cost for a variable
one.

THE PLAN IS TAKEN FROM THE QUERY THE ROUTE ACTUALLY ISSUES, captured by patching
`aiosqlite.Connection.execute` around a real request, rather than from a copy of the SQL retyped
here. A retyped query drifts from the handler and then this file pins the plan of something nobody
runs -- and the handler builds its WHERE clause conditionally, so the retyped version would have to
guess which branches fired.
"""
from __future__ import annotations

import asyncio

from service.db import get_db
from service.tests._base import FastApiTestCase


class TheRunsListingDoesNotSortTheWholeTable(FastApiTestCase):
    def _captured_runs_query(self, path: str) -> tuple[str, tuple]:
        """The `dispatch_runs` SELECT the route issues for `path`, with its parameters."""
        import aiosqlite

        captured: list[tuple[str, tuple]] = []
        real_execute = aiosqlite.Connection.execute

        async def execute(self, sql, parameters=None, *args, **kwargs):
            text = " ".join(str(sql).split())
            if text.upper().startswith("SELECT") and "FROM dispatch_runs" in text:
                captured.append((text, tuple(parameters or ())))
            if parameters is None:
                return await real_execute(self, sql, *args, **kwargs)
            return await real_execute(self, sql, parameters, *args, **kwargs)

        aiosqlite.Connection.execute = execute
        try:
            response = self.client.get(path)
        finally:
            aiosqlite.Connection.execute = real_execute
        self.assertEqual(response.status_code, 200, response.text)
        listing = [(sql, params) for sql, params in captured if "ORDER BY requested_at" in sql]
        self.assertEqual(len(listing), 1, (
            f"expected exactly one ordered dispatch_runs listing query, captured {len(listing)}: "
            f"{[sql[:80] for sql, _ in listing]}"
        ))
        return listing[0]

    def _plan(self, sql: str, params: tuple) -> list[str]:
        async def run():
            db = await get_db()
            try:
                rows = await (await db.execute("EXPLAIN QUERY PLAN " + sql, params)).fetchall()
                return [str(row[-1]) for row in rows]
            finally:
                await db.close()

        return asyncio.run(run())

    def test_THE_DEFAULT_LISTING_WALKS_AN_INDEX(self):
        sql, params = self._captured_runs_query("/api/v1/dispatch/runs?limit=80")
        plan = self._plan(sql, params)
        self.assertTrue(
            any("idx_dispatch_runs_requested" in line for line in plan),
            f"the default listing is not using the requested_at index:\n  " + "\n  ".join(plan),
        )

    def test_IT_DOES_NOT_SORT_THE_WHOLE_TABLE(self):
        """The half that costs. A temp b-tree here means every matching row is read and sorted to
        return one page -- 21,778 rows for 81 on the operator's database."""
        sql, params = self._captured_runs_query("/api/v1/dispatch/runs?limit=80")
        plan = self._plan(sql, params)
        self.assertFalse(
            any("TEMP B-TREE" in line.upper() for line in plan),
            "the listing sorts the whole table to return one page:\n  " + "\n  ".join(plan),
        )

    def test_THE_FILTERED_LISTING_STILL_USES_THE_STATUS_INDEX(self):
        """The regression a new index can cause. `status=` is the dashboard's Status dropdown, and
        `idx_dispatch_runs_status_requested` serves it with a SEARCH -- far better than walking the
        whole table in date order looking for matches."""
        sql, params = self._captured_runs_query("/api/v1/dispatch/runs?limit=80&status=completed")
        plan = self._plan(sql, params)
        self.assertTrue(
            any("idx_dispatch_runs_status_requested" in line for line in plan),
            "the status filter stopped using its own index:\n  " + "\n  ".join(plan),
        )

    def test_THE_CAPTURE_IS_OF_A_REAL_QUERY(self):
        """POSITIVE CONTROL. Every assertion above is about a captured string; a capture that
        returned something harmless -- a COUNT, or a query against another table -- would plan
        cleanly and prove nothing about the listing."""
        sql, params = self._captured_runs_query("/api/v1/dispatch/runs?limit=80")
        self.assertIn("FROM dispatch_runs", sql)
        self.assertIn("ORDER BY requested_at DESC", sql)
        self.assertIn("LIMIT ?", sql)
        self.assertEqual(params[-1], 81, "the one-row-wider read is gone; the page cannot say it is one")

    def test_THE_PLANNER_CAN_STILL_SAY_SCAN(self):
        """NEGATIVE CONTROL. Two of the assertions above are 'this string is absent', which an
        EXPLAIN that returned nothing would satisfy. A query with no usable index must still report
        a plain SCAN, so the probe is shown to distinguish the two answers."""
        plan = self._plan("SELECT * FROM dispatch_runs WHERE body = ?", ("nothing",))
        self.assertTrue(plan, "EXPLAIN QUERY PLAN returned no rows at all")
        self.assertTrue(
            any(line.startswith("SCAN") and "USING INDEX" not in line for line in plan),
            f"the probe cannot report an unindexed query: {plan}",
        )
