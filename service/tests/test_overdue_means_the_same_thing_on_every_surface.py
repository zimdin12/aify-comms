"""Every surface that reports an overdue reply uses the operator's window, not a literal.

FOUR SURFACES, ONE QUESTION. The reminder sweep decides when to nag, `GET /contracts?state=overdue`
decides what the Work Loop lists, and `/analytics` and `/analytics/pulse` each report a count the
operator reads as a number of overdue contracts. The first two read `reply_reminder_minutes`. The two
analytics endpoints hardcoded `30 * 60`.

MEASURED 2026-08-28: the live setting is 10. So the hardcoded 30 was already wrong by a factor of
three -- a contract owed for fifteen minutes got a reminder, appeared in the Work Loop as overdue, and
was NOT counted by either analytics tile. Two numbers on two screens, both labelled "overdue".

IT SHOWED AS NOTHING, which is why it survived. Open reply contracts were zero at the time of
measurement, so both surfaces reported 0 and agreed. The disagreement needed only one contract to sit
unanswered for eleven minutes.

The derivation now has one home, `reply_contract.reply_reminder_minutes`, and had been written out
three times before this: twice as the overdue cutoff and once in the settings block the dashboard
reads back. A fourth and fifth copy is what this change would have added.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.api_core.reply_contract import reply_reminder_minutes
from service.tests._base import FastApiTestCase

#: Older than a 10-minute window, younger than a 30-minute one. The whole disagreement lives here.
OWED_MINUTES = 15


class OverdueMeansTheSameThingOnEverySurfaceTests(FastApiTestCase):
    LEGACY_SETTINGS = {"reply_reminder_minutes": 10}

    def setUp(self) -> None:
        super().setUp()
        registered = self.client.post("/api/v1/agents", json={
            "agentId": "overdue-target", "role": "coder", "runtime": "claude-code",
            "sessionMode": "resident",
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._seed_owed_contract()

    def _seed_owed_contract(self) -> None:
        """One open, reply-required run, requested OWED_MINUTES ago."""
        import asyncio
        from datetime import datetime, timedelta, timezone

        from service.clock import ISO_SECONDS
        from service.db import get_db

        requested = (datetime.now(timezone.utc) - timedelta(minutes=OWED_MINUTES)).strftime(ISO_SECONDS)

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, "
                    "priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("msg-owed", "operator", "overdue-target", "direct", "request",
                     "please answer", "body", "normal", int(time.time() * 1000)),
                )
                await db.execute(
                    "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, "
                    "message_type, subject, body, priority, status, require_reply, requested_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("run-owed", "msg-owed", "operator", "overdue-target", "request",
                     "please answer", "body", "normal", "queued", 1, requested),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _analytics_overdue(self) -> int:
        response = self.client.get("/api/v1/analytics")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("overdueReplyContracts", "overdue", "overdueContracts"):
            if key in body:
                return int(body[key])
        fleet = body.get("fleet") or {}
        for key in ("overdueReplyContracts", "overdue"):
            if key in fleet:
                return int(fleet[key])
        self.fail(f"no overdue count in the analytics payload: {sorted(body)}")

    def _pulse_overdue(self) -> int:
        response = self.client.get("/api/v1/analytics/pulse")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("overdue", "overdueReplyContracts"):
            if key in body:
                return int(body[key])
        self.fail(f"no overdue count in the pulse payload: {sorted(body)}")

    def _contracts_overdue(self) -> int:
        response = self.client.get("/api/v1/contracts?state=overdue&limit=200")
        self.assertEqual(response.status_code, 200, response.text)
        return len(response.json()["contracts"])

    def test_the_helper_reads_the_setting_and_falls_back(self) -> None:
        """The control. If the helper ignored its argument, every surface would agree on a constant
        and the assertions below would pass while proving the opposite of what they claim."""
        self.assertEqual(reply_reminder_minutes({"reply_reminder_minutes": 10}), 10)
        self.assertEqual(reply_reminder_minutes({"reply_reminder_minutes": 45}), 45)
        # 0 falls back to the DEFAULT, not to 1. The `or DEFAULT_SETTINGS[...]` makes zero falsy
        # before `max(1, ...)` ever sees it -- behaviour inherited unchanged from the three copies
        # this helper replaced, and pinned here because I asserted the opposite first and the
        # control is what corrected me.
        self.assertEqual(reply_reminder_minutes({"reply_reminder_minutes": 0}), 10)
        self.assertEqual(reply_reminder_minutes({"reply_reminder_minutes": -5}), 1, "negative floors to 1")
        self.assertGreater(reply_reminder_minutes({}), 0, "an unset value must fall back, not crash")

    def test_the_fixture_is_actually_overdue_by_the_configured_window(self) -> None:
        """Second control. A contract that is not overdue at all makes every count below zero, and
        three zeros agree perfectly while measuring nothing."""
        self.assertGreater(OWED_MINUTES, reply_reminder_minutes({"reply_reminder_minutes": 10}))
        self.assertLess(OWED_MINUTES, 30, "the fixture must sit BETWEEN the two windows to separate them")
        self.assertEqual(self._contracts_overdue(), 1, "the contracts endpoint does not see it as overdue")

    def test_every_surface_counts_the_same_overdue_contract(self) -> None:
        """The defect: with the window at 10 and the literal at 30, a contract owed for 15 minutes was
        overdue on two surfaces and invisible on the other two."""
        counts = {
            "contracts": self._contracts_overdue(),
            "analytics": self._analytics_overdue(),
            "pulse": self._pulse_overdue(),
        }
        self.assertEqual(
            set(counts.values()), {1},
            "the surfaces disagree about how many replies are overdue, so the operator reads two "
            f"different numbers for one question: {counts}",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
