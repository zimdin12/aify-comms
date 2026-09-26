"""`GET /messages/inbox/{agent}` pages with `offset`, so a reader can reach every unread message.

The notify hook peeks the unread population and skips what it already showed. With only the newest
window to read, every unread message older than that window stayed unread and was never shown: 21
unread, none read, the hook showed 21..2 and then nothing, forever (v0.7 review). Pages must cover the
population exactly: no row on two pages, no row on none, even when timestamps tie.
"""
from __future__ import annotations

from service.tests._base import FastApiTestCase

COUNT = 25


class TheInboxPagesTheWholeUnreadPopulationTests(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        for agent_id in ("reader", "sender"):
            registered = self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            })
            self.assertEqual(registered.status_code, 200, registered.text)
        self.sent = []
        for index in range(COUNT):
            sent = self.client.post("/api/v1/messages/send", json={
                "from_agent": "sender", "to": "reader", "type": "info", "subject": f"s{index}", "body": "b",
            })
            self.assertEqual(sent.status_code, 200, sent.text)
            self.sent.append(sent.json()["messageId"])
        # Every row gets ONE timestamp: the hardest case for "no row on two pages". Measured: this still
        # passes with the `m.id` tie-break removed, because SQLite scans one plan in a stable order. So
        # this proves the paging, not the tie-break; the tie-break declares an order rather than
        # leaving it to the plan.
        import sqlite3
        db = sqlite3.connect(self._db_path)
        try:
            tied = db.execute("UPDATE messages SET timestamp = (SELECT MAX(timestamp) FROM messages) WHERE to_agent = 'reader'")
            db.commit()
            self.assertEqual(tied.rowcount, COUNT)
        finally:
            db.close()

    def _page(self, query: str) -> dict:
        response = self.client.get(f"/api/v1/messages/inbox/reader?peek=true&{query}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_consecutive_pages_cover_every_unread_message_once(self):
        pages = [self._page(f"limit=10&offset={offset}") for offset in (0, 10, 20)]
        ids = [m["id"] for page in pages for m in page["messages"]]
        self.assertEqual(len(ids), COUNT, "a row appeared on two pages or on none")
        self.assertEqual(set(ids), set(self.sent))
        self.assertEqual(ids, [m["id"] for m in self._page(f"limit={COUNT}")["messages"]],
                         "the pages, joined, are not the one-page order")
        self.assertTrue(all(page["total"] == COUNT for page in pages), "total is the population, not the page")

    def test_control_offset_zero_is_the_page_a_reader_without_offset_gets(self):
        self.assertEqual(self._page("limit=10&offset=0")["messages"], self._page("limit=10")["messages"])

    def test_past_the_end_is_an_empty_page(self):
        self.assertEqual(self._page(f"limit=10&offset={COUNT}")["messages"], [])


class AnOffsetNeedsAReadThatDoesNotShiftThePopulationTests(FastApiTestCase):
    """v0.7.1 review (W08). A NON-peek unread read marks its page read, which shifts the unread
    population under the next page: 6,5 read at offset 0, then offset 2 returned 2,1 and skipped 4,3,
    which stayed unread and unseen. Paging unread needs peek; a non-peek read pages by repeating offset 0."""

    def setUp(self) -> None:
        super().setUp()
        for agent_id in ("reader", "sender"):
            self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "resident"})
        for index in range(6):
            self.client.post("/api/v1/messages/send", json={
                "from_agent": "sender", "to": "reader", "type": "info", "subject": f"s{index}", "body": "b"})

    def _status(self, query: str) -> int:
        return self.client.get(f"/api/v1/messages/inbox/reader?{query}").status_code

    def test_a_non_peek_unread_read_with_an_offset_is_refused(self):
        response = self.client.get("/api/v1/messages/inbox/reader?limit=2&offset=2")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("peek", response.text)
        self.assertEqual(self._status("limit=10&peek=true"), 200)
        unread = self.client.get("/api/v1/messages/inbox/reader?limit=10&peek=true").json()["total"]
        self.assertEqual(unread, 6, "the refused read marked something read")

    def test_controls_the_reads_whose_population_does_not_shift_still_page(self):
        self.assertEqual(self._status("limit=2&offset=2&peek=true"), 200)
        self.assertEqual(self._status("limit=2&offset=2&filter=all"), 200)
        self.assertEqual(self._status("limit=2&offset=0"), 200)
