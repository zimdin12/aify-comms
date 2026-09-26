"""`/listen` notices a caller that has gone, through the production app and its middleware.

v0.7.1 review (S2). 0.7.0 guarded the read receipts with `request.is_disconnected()`, and its tests
called the handler directly. In production the app is wrapped in `BaseHTTPMiddleware` subclasses, and
under them `is_disconnected()` always answers False, so a caller that had left still had its messages
marked read, returned to nobody. These tests go through `service.main.app`, middleware included, as raw
ASGI calls whose `receive` hands the route an `http.disconnect`.
"""

from __future__ import annotations

import asyncio
import sqlite3

from service.tests._base import FastApiTestCase

AGENT = "listener"


async def _call(path: str, *, disconnect_after: float | None):
    from service.main import app

    delivered = {"request": False}

    async def receive():
        if not delivered["request"]:
            delivered["request"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600 if disconnect_after is None else disconnect_after)
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    route, _, query = path.partition("?")
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": route, "raw_path": route.encode(), "query_string": query.encode(),
        # A trusted Host: the cross-site middleware refuses an untrusted one with a 403 before the route
        # runs, which made this file's first draft pass its disconnect tests without reaching /listen.
        "headers": [(b"host", b"127.0.0.1:8800")], "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80), "root_path": "",
    }
    await app(scope, receive, send)
    statuses = [m.get("status") for m in sent if m["type"] == "http.response.start"]
    assert statuses == [200], f"the route did not answer 200, so nothing below was tested: {sent[:2]}"
    return sent


class ListenSeesACallerThatLeftTests(FastApiTestCase):
    DB_NAME = "aify-listen-disconnect.db"

    def _seed_message(self, message_id="m-1"):
        with sqlite3.connect(self._db_path) as db:
            db.execute("INSERT INTO messages (id, from_agent, to_agent, subject, body, timestamp) "
                       "VALUES (?, 'sender', ?, 's', 'b', 1)", (message_id, AGENT))
        # `with` commits but does not close
        db.close()

    def _receipts(self):
        db = sqlite3.connect(self._db_path)
        try:
            return [row[0] for row in db.execute("SELECT message_id FROM read_receipts WHERE agent_id = ?", (AGENT,))]
        finally:
            db.close()

    def test_control_a_connected_caller_gets_the_message_and_it_is_marked_read(self):
        self._seed_message()
        asyncio.run(_call(f"/api/v1/agents/{AGENT}/listen?timeout=2", disconnect_after=None))
        self.assertEqual(self._receipts(), ["m-1"])

    def test_a_caller_already_gone_leaves_the_message_unread(self):
        self._seed_message()
        asyncio.run(_call(f"/api/v1/agents/{AGENT}/listen?timeout=2", disconnect_after=0))
        self.assertEqual(self._receipts(), [], "a message was marked read for a caller that had left")

    def test_a_caller_that_leaves_while_parked_ends_the_wait_and_reads_nothing(self):
        async def scenario():
            call = asyncio.create_task(_call(f"/api/v1/agents/{AGENT}/listen?timeout=5", disconnect_after=0.1))
            await asyncio.sleep(0.5)
            finished_early = call.done()
            self._seed_message()
            await asyncio.wait_for(call, 10)
            return finished_early
        self.assertTrue(asyncio.run(scenario()), "the wait did not end when the caller left")
        self.assertEqual(self._receipts(), [])
