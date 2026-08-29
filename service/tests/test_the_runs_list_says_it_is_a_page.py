r"""`GET /dispatch/runs` says whether the caller got the whole answer.

MEASURED ON THE LIVE DATABASE, 2026-08-29. A `limit=80` page reached back to 2026-08-26T13:28 and a
`limit=200` page -- the API's ceiling -- was also full, so the window is a window at every size the
endpoint allows.

WHY IT IS SHARPER HERE THAN FOR `/sessions`. The dashboard's Runs page builds its From, To and runtime
dropdowns FROM THE ROWS IT RECEIVED (`syncRunFilterOptions(..., state.runs.map(runFrom), ...)`). On the
live data that page offered ONE distinct sender. So an agent whose last run fell off the page is not
merely missing from the list: it cannot be selected at all -- and the empty state invited the operator
to "Adjust the filters above if you expected to see some". Only `status` is in the query string; From,
To, runtime and search are applied client-side.

`/contracts`, `/terminals` and now `/sessions` all report `truncated`. This endpoint did not, so the
page had nothing to key on.

The read is ONE ROW WIDER than the page rather than a second COUNT query: a count taken separately is a
count of a different moment, and this table is written on every dispatch.
"""
from __future__ import annotations

from service.tests._base import FastApiTestCase


class RunsListSaysItIsAPageTests(FastApiTestCase):
    def _agents(self) -> None:
        for agent_id in ("runner-from", "runner-to"):
            registered = self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code",
                "sessionMode": "resident",
            })
            self.assertEqual(registered.status_code, 200, registered.text)

    def _runs(self, count: int, *, status: str = "completed") -> None:
        """Seed `count` dispatch runs. Through the DB: the point is a LIST longer than a page."""
        import asyncio

        from service.db import get_db

        async def seed():
            db = await get_db()
            try:
                for index in range(count):
                    # The message first: `dispatch_runs.message_id` is a FOREIGN KEY.
                    message_id = f"msg-{index:04d}"
                    # `timestamp` is an INTEGER column, not an ISO string: this table stores epoch
                    # milliseconds while `dispatch_runs.requested_at` stores ISO text, and a seeder
                    # that assumes one shape for both is refused outright by the schema.
                    await db.execute(
                        "INSERT INTO messages (id, from_agent, to_agent, type, subject, body, "
                        "timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (message_id, "runner-from", "runner-to", "request", "s", "b",
                         1787000000000 + index),
                    )
                    await db.execute(
                        "INSERT INTO dispatch_runs (id, message_id, target_agent, from_agent, "
                        "status, requested_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (f"run-{index:04d}", message_id, "runner-to", "runner-from", status,
                         f"2026-08-0{1 + index % 9}T00:00:{index % 60:02d}Z"),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())

    def test_a_full_page_is_reported_as_truncated(self):
        self._agents()
        self._runs(12)
        body = self.client.get("/api/v1/dispatch/runs?limit=5").json()
        self.assertEqual(len(body["runs"]), 5, "the page must still honour the limit")
        self.assertTrue(body["truncated"], (
            "the response did not say there were more rows, so a dashboard that renders exactly what "
            "it got -- and builds its filter dropdowns from those rows -- has no way to tell a short "
            "list from a page"
        ))
        self.assertEqual(body["limit"], 5)

    def test_a_list_that_fits_is_NOT_truncated(self):
        """THE CONTROL. A flag that is always true is the same as no flag, and would put a note on
        every render until an operator stopped reading it."""
        self._agents()
        self._runs(2)
        body = self.client.get("/api/v1/dispatch/runs?limit=50").json()
        self.assertFalse(body["truncated"])

    def test_exactly_a_full_page_with_nothing_behind_it_is_not_truncated(self):
        """The off-by-one this shape invites: reading `limit + 1` and comparing `> limit` gets it
        right, `>=` would call every exactly-full page truncated."""
        self._agents()
        self._runs(5)
        body = self.client.get("/api/v1/dispatch/runs?limit=5").json()
        self.assertEqual(len(body["runs"]), 5)
        self.assertFalse(body["truncated"])

    def test_the_status_filter_still_narrows_on_the_server(self):
        """The one filter that DOES reach the server, asserted because the dashboard's new note
        promises exactly that and nothing else. If status stopped re-querying, the note would be
        advice that cannot work -- which is the defect it was written to remove."""
        self._agents()
        self._runs(6, status="completed")
        self._runs(0)
        body = self.client.get("/api/v1/dispatch/runs?limit=50&status=queued").json()
        self.assertEqual(body["runs"], [], "a status nothing matches must return nothing")
        self.assertFalse(body["truncated"])

        everything = self.client.get("/api/v1/dispatch/runs?limit=50&status=completed").json()
        self.assertEqual(len(everything["runs"]), 6, (
            "the status filter returned nothing for a status that DOES match, so the test above "
            "proves nothing about filtering"
        ))
