r"""`GET /sessions` says whether the caller got the whole answer.

MEASURED ON THE LIVE DATABASE, 2026-08-28: 510 rows in `agent_sessions`, 303 surviving the default
filter, and the dashboard asks for 80. So 223 sessions were absent from the page whose job is listing
them, and nothing in the response or on screen said so. An operator searching for one of them saw an
empty result, and the empty state read "No sessions yet -- spawn a managed session from Environments",
which sent them to start a SECOND session for an agent that already had one running.

Live-first ordering (`8e0f...`, same file) fixed the half where the one `running` session sat at
position 160 behind 160 stopped rows with newer timestamps. It cannot fix this half: a bounded page
still LOOKS like the whole list.

`/contracts` and `/terminals` both already report `truncated`, and the dashboard already renders a
"partial scan" note for contracts. This endpoint never carried the flag, so the note had nothing to
key on.

THE READ IS ONE ROW WIDER THAN THE PAGE, rather than a second COUNT query. A count taken separately
from the page is a count of a different moment, and on a table this service writes on every heartbeat
that difference is not theoretical.
"""
from __future__ import annotations

from service.tests._base import FastApiTestCase


class SessionsListSaysItIsAPageTests(FastApiTestCase):
    def _agent(self, agent_id: str) -> None:
        registered = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "claude-code",
            "sessionMode": "resident",
        })
        self.assertEqual(registered.status_code, 200, registered.text)

    def _sessions(self, agent_id: str, count: int, *, status: str = "stopped") -> None:
        """Seed `count` session rows. Seeded through the DB: the point is a LIST longer than a page,
        and the register path creates one session per agent."""
        import asyncio

        from service.db import get_db

        async def seed():
            db = await get_db()
            try:
                # A REAL ENVIRONMENT ROW. `agent_sessions.environment_id` is a FOREIGN KEY, so an
                # invented id is refused outright -- which is the schema telling a seeder to seed the
                # shape the service actually writes rather than a convenient one.
                environment_id = "env-page-test"
                await db.execute(
                    "INSERT OR IGNORE INTO environments (id, label, machine_id, registered_at, "
                    "last_seen) VALUES (?, ?, ?, ?, ?)",
                    (environment_id, "page test", "test-host", "2026-08-01T00:00:00Z",
                     "2026-08-01T00:00:00Z"),
                )
                for index in range(count):
                    await db.execute(
                        # spawn_spec_id / spawn_request_id passed as NULL EXPLICITLY. Both DEFAULT
                        # to '' and both are FOREIGN KEYS, so omitting them inserts an empty string
                        # that matches no row and the insert is refused. Production passes None for
                        # exactly this reason; a seeder that omits them hits a constraint failure
                        # that reads like a bug in the table.
                        "INSERT INTO agent_sessions (id, agent_id, environment_id, status, "
                        "runtime, started_at, last_seen, spawn_spec_id, spawn_request_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                        (f"{agent_id}-s{index}", agent_id, environment_id, status, "claude-code",
                         "2026-08-01T00:00:00Z",
                         f"2026-08-2{index % 9}T00:00:0{index % 10}Z"),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())

    def test_a_full_page_is_reported_as_truncated(self):
        self._agent("pager")
        self._sessions("pager", 12)
        body = self.client.get("/api/v1/sessions?limit=5").json()
        self.assertEqual(len(body["sessions"]), 5, "the page must still honour the limit")
        self.assertTrue(body["truncated"], (
            "the response did not say there were more rows, so a dashboard rendering exactly what it "
            "got has no way to tell a short list from a page"
        ))
        self.assertEqual(body["limit"], 5, (
            '"there are more" without "more than what" leaves a reader unable to ask for the next '
            "page or to judge how much is missing"
        ))

    def test_a_list_that_fits_is_NOT_truncated(self):
        """THE CONTROL. A flag that is always true is the same as no flag, and it would put a
        "more exist" note on every render until an operator stopped reading it."""
        self._agent("small")
        self._sessions("small", 2)
        body = self.client.get("/api/v1/sessions?limit=50").json()
        self.assertFalse(body["truncated"])

    def test_exactly_a_full_page_with_nothing_behind_it_is_not_truncated(self):
        """The off-by-one this shape invites. Reading `limit + 1` and comparing `> limit` is the
        version that gets it right; `>=` would call every exactly-full page truncated."""
        self._agent("exact")
        self._sessions("exact", 5)
        body = self.client.get("/api/v1/sessions?limit=5").json()
        self.assertEqual(len(body["sessions"]), 5)
        self.assertFalse(body["truncated"], (
            "a page that exactly exhausts the table was reported as truncated, which would show the "
            "note forever on any deployment whose session count happens to equal the limit"
        ))

    def test_the_page_still_puts_live_sessions_first(self):
        """The other half of this endpoint's paging story, asserted here because the two are only
        correct together: truncation is safe ONLY because a bounded page can lose history and never
        the live row."""
        self._agent("mixed")
        self._sessions("mixed", 10, status="stopped")
        self._agent("mixed-live")
        self._sessions("mixed-live", 1, status="running")
        body = self.client.get("/api/v1/sessions?limit=3").json()
        # BY ROW ID, not by the served status. The served status is DERIVED from live truth -- a
        # seeded `running` row with no terminal behind it correctly reads as stopped -- while the
        # ORDER BY keys on the STORED status. Asserting on the derived value would test the deriver
        # and quietly stop testing the ordering, which is what this is here for.
        ids = [str(session.get("id") or "") for session in body["sessions"]]
        self.assertIn("mixed-live-s0", ids, (
            "the only session stored live fell off a three-row page behind stopped rows with newer "
            "timestamps, which is the exact defect measured on the live database"
        ))
