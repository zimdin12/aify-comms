"""The analytics message tile counts the window the chart draws (2026-10-14 audit, L7).

The tile counted a rolling now-minus-24h while the chart's 24 buckets are hour-aligned, so live the tile
read 1629 above a chart summing to 1617. The clock is fixed at half past an hour, so the message 23h59m
old is inside the rolling window and outside the aligned one on every run, not by luck of the minute.
"""

from __future__ import annotations

import asyncio
import time
from unittest import mock

from service.tests._base import FastApiTestCase

NOW = (int(time.time()) // 3600) * 3600 - 1800  # half past the previous hour


class TheMessageTileCountsTheChartsWindow(FastApiTestCase):
    def _seed(self, mid: str, at_s: int) -> None:
        async def go():
            from service.db import get_db

            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, timestamp) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (mid, "a", "b", "direct", "info", "s", "b", "normal", at_s * 1000),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def test_the_tile_is_the_sum_of_the_chart(self) -> None:
        self._seed("rolling-only", NOW - 24 * 3600 + 60)
        self._seed("both", NOW - 3600)
        with mock.patch("time.time", return_value=float(NOW)):
            body = self.client.get("/api/v1/analytics", params={"range": "hour"}).json()
        drawn = sum(b["count"] for b in body["messagesPerHour"])
        self.assertEqual(drawn, 1, "control: the chart draws the message an hour old and not the 23h59m one")
        self.assertEqual(body["messageTotal"], drawn, "the tile counted a window the chart does not draw")
