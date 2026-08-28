r"""A live session cannot be pushed off `GET /sessions` by dead history.

THE ENDPOINT'S OWN DOCSTRING NAMED THIS AND DID NOT CLOSE IT. It records two latent bugs behind the
2026-07-26 change, the second being: "the dashboard requests `limit=80`. With 449 rows, one agent's
dead history could push another agent's LIVE session out of the window entirely, making it invisible."
The fix hid three statuses -- `ended`, `completed`, `cancelled` -- and stopped there, for a reason
stated right above them: `stopped`, `failed` and `lost` are KEPT on purpose, because Restart, Reset
and Compact are exactly the actions an operator takes on them.

So the crowding-out stayed reachable through the statuses that were deliberately retained.

MEASURED ON THE LIVE DATABASE, 2026-08-28:

    510 agent_sessions rows
    303 survive the default filter
      1 `running` session, at POSITION 160 under `last_seen DESC`
    160 stopped/failed/lost rows carrying a NEWER last_seen than it
     80 the dashboard's limit

The only live session on the whole deployment was invisible to the page whose job is showing it.

WHY THE TIMESTAMP DID NOT SAVE IT, which is the part worth remembering: the old ordering assumed a
live row is a recent row. That live session's `last_seen` was three days old and its status was still
`running`. A dead row's timestamp is stamped when it stops, so a burst of recent stops outranks a live
session whose heartbeat has lapsed -- and a lapsed heartbeat is exactly when an operator most needs to
see it.

Ordering on LIVENESS first needs no timestamp to be trustworthy. It is a property of the row, so a
bounded page can only ever lose history, never the thing it exists to show.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from service.tests._base import FastApiTestCase

from service.api_core.liveness import _LIVE_SESSION_STATUSES
from service.routers.sessions import SESSION_CLEAN_HISTORY_STATUSES


class ALiveSessionIsNeverPushedOffThePage(FastApiTestCase):
    DB_NAME = "session-ordering.db"

    ENVIRONMENT = "windows:host:default"

    def setUp(self) -> None:
        super().setUp()
        registered = self.client.post("/api/v1/agents", json={
            "agentId": "an-agent", "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
        })
        self.assertEqual(registered.status_code, 200, registered.text)

    def _seed(self, rows) -> None:
        """Insert sessions directly: the creation paths run through spawn and registration, which is a
        great deal of machinery for a question about ORDER BY.

        `spawn_spec_id` and `spawn_request_id` are passed NULL rather than omitted -- they DEFAULT to
        `''`, which under `PRAGMA foreign_keys=ON` is a value the constraint enforces against rows that
        never exist.
        """
        import asyncio

        from service.db import get_db

        async def write():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO environments (id, machine_id, status, last_seen, registered_at) "
                    "VALUES (?,?,?,?,?)",
                    (self.ENVIRONMENT, "test-machine", "online", "2026-08-28T00:00:00Z",
                     "2026-08-28T00:00:00Z"),
                )
                for row in rows:
                    await db.execute(
                        "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                        "started_at, last_seen, spawn_spec_id, spawn_request_id) "
                        "VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                        (row["id"], "an-agent", self.ENVIRONMENT, "claude-code", row["status"],
                         "2026-08-01T00:00:00Z", row["last_seen"]),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(write())

    def test_the_live_session_survives_a_page_full_of_newer_dead_ones(self) -> None:
        """THE MEASURED CASE, reproduced at a size a test can hold: one live session with an OLD
        timestamp, buried under stops that all happened since."""
        rows = [{"id": "live", "status": "running", "last_seen": "2026-08-25T04:22:11Z"}]
        rows += [
            {"id": f"dead-{i:03d}", "status": "stopped", "last_seen": f"2026-08-28T{i // 60:02d}:{i % 60:02d}:00Z"}
            for i in range(30)
        ]
        self._seed(rows)

        body = self.client.get("/api/v1/sessions", params={"limit": 5}).json()
        ids = [s["id"] for s in body["sessions"]]
        self.assertIn(
            "live", ids,
            "the only live session fell off a bounded page, which is the bug this endpoint's docstring "
            f"already described. Page held: {ids}",
        )
        self.assertEqual(ids[0], "live", "a dead session outranked a live one")

    def test_history_is_what_a_bounded_page_loses(self) -> None:
        """The other half: the page is still bounded, and what it drops is the oldest history. A fix
        that returned everything would trade an invisible session for an unbounded response."""
        rows = [{"id": "live", "status": "running", "last_seen": "2026-08-01T00:00:00Z"}]
        rows += [
            {"id": f"dead-{i:02d}", "status": "stopped", "last_seen": f"2026-08-2{i % 9}T00:00:00Z"}
            for i in range(10)
        ]
        self._seed(rows)
        body = self.client.get("/api/v1/sessions", params={"limit": 3}).json()
        self.assertEqual(len(body["sessions"]), 3)
        self.assertEqual(body["sessions"][0]["id"], "live")

    def test_live_sessions_are_ordered_among_themselves_by_recency(self) -> None:
        """Liveness decides the GROUP, not the order within it. Two live sessions must still read
        newest-first, or the fix would trade one arbitrary order for another."""
        self._seed([
            {"id": "older-live", "status": "running", "last_seen": "2026-08-20T00:00:00Z"},
            {"id": "newer-live", "status": "starting", "last_seen": "2026-08-27T00:00:00Z"},
            {"id": "dead", "status": "stopped", "last_seen": "2026-08-28T00:00:00Z"},
        ])
        ids = [s["id"] for s in self.client.get("/api/v1/sessions").json()["sessions"]]
        self.assertEqual(ids[:2], ["newer-live", "older-live"])
        self.assertEqual(ids[2], "dead")

    def test_every_live_status_counts_as_live(self) -> None:
        """DERIVED FROM THE SHARED SET, not a second copy. A status the rest of the service treats as
        live and this ordering does not would put exactly those sessions back at risk -- and the
        constant-coincidence gate caught precisely that mistake one commit earlier."""
        self._seed(
            [{"id": f"live-{status}", "status": status, "last_seen": "2026-08-01T00:00:00Z"}
             for status in sorted(_LIVE_SESSION_STATUSES)]
            + [{"id": "dead", "status": "stopped", "last_seen": "2026-08-28T00:00:00Z"}]
        )
        ids = [s["id"] for s in self.client.get("/api/v1/sessions").json()["sessions"]]
        self.assertEqual(
            ids[-1], "dead",
            f"a status in {sorted(_LIVE_SESSION_STATUSES)} was not treated as live by the ordering; "
            f"page order was {ids}",
        )

    def test_the_cleanly_finished_statuses_are_still_hidden_by_default(self) -> None:
        """The control for the change: reordering must not quietly widen what the page returns."""
        self._seed(
            [{"id": f"gone-{status}", "status": status, "last_seen": "2026-08-28T00:00:00Z"}
             for status in sorted(SESSION_CLEAN_HISTORY_STATUSES)]
            + [{"id": "live", "status": "running", "last_seen": "2026-08-01T00:00:00Z"}]
        )
        ids = [s["id"] for s in self.client.get("/api/v1/sessions").json()["sessions"]]
        self.assertEqual(ids, ["live"], f"a cleanly-finished session reappeared: {ids}")

    def test_includeEnded_still_returns_history_with_the_live_one_first(self) -> None:
        """History has a caller, and it must keep working -- with the same guarantee applied."""
        self._seed([
            {"id": "live", "status": "running", "last_seen": "2026-08-01T00:00:00Z"},
            {"id": "gone", "status": "ended", "last_seen": "2026-08-28T00:00:00Z"},
        ])
        ids = [s["id"] for s in
               self.client.get("/api/v1/sessions", params={"includeEnded": "true"}).json()["sessions"]]
        self.assertEqual(ids, ["live", "gone"])


if __name__ == "__main__":
    unittest.main()
