"""An agent registering after a long absence gets one message saying what arrived while it was gone.

The operator saw agents come back after hours and act on the state they left. The service now
briefs them at registration (service/api_core/away_briefing.py). Driven through the real route:
the briefing must name what is unread and which channels moved, and must NOT be sent for a short
absence, for an absence in which nothing arrived, or when the setting is off.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

AGENT = "returning-agent"
TEAMMATE = "busy-teammate"
CHANNEL = "team-room"


def _iso_hours_ago(hours: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))


class AReturningAgentIsToldWhatItMissed(FastApiTestCase):
    def _register(self) -> None:
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "codex", "sessionMode": "resident",
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _run(self, *statements) -> list:
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                rows = []
                for sql, params in statements:
                    cursor = await db.execute(sql, params)
                    rows = await cursor.fetchall()
                await db.commit()
                return rows
            finally:
                await db.close()

        return asyncio.run(go())

    def _away_for(self, hours: float) -> None:
        self._run(("UPDATE agents SET last_seen = ? WHERE id = ?", (_iso_hours_ago(hours), AGENT)))

    def _message(self, message_id: str, *, to: str = "", channel: str = "", read: bool = False) -> None:
        now_ms = int(time.time() * 1000)
        self._run(("INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, timestamp)"
                   " VALUES (?,?,?,?,?,?,?,?,?)",
                   (message_id, TEAMMATE, to or None, channel or None, "channel" if channel else "direct",
                    "info", "update", "the plan changed", now_ms)))
        if read:
            self._run(("INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                       (message_id, AGENT, _iso_hours_ago(0))))

    def _briefings(self) -> list:
        return self._run(("SELECT subject, body FROM messages WHERE from_agent = 'aify-comms' AND to_agent = ?",
                          (AGENT,)))

    def setUp(self) -> None:
        super().setUp()
        self._register()
        self._run(("INSERT INTO channels (name, created_by, created_at) VALUES (?,?,?)",
                   (CHANNEL, TEAMMATE, _iso_hours_ago(20))),
                  ("INSERT INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
                   (CHANNEL, AGENT, _iso_hours_ago(20))))

    def test_a_long_absence_with_news_is_briefed(self) -> None:
        self._away_for(10)
        self._message("m-unread-1", to=AGENT)
        self._message("m-unread-2", to=AGENT)
        self._message("m-already-read", to=AGENT, read=True)
        self._message("m-channel", channel=CHANNEL)
        self._register()
        briefings = self._briefings()
        self.assertEqual(len(briefings), 1, "a returning agent with news got no briefing")
        subject, body = briefings[0]
        self.assertIn("While you were away (10 h)", subject)
        self.assertIn(f"2 unread direct message(s), from {TEAMMATE} (2)", body,
                      "the briefing must count unread messages only, by sender")
        self.assertIn(f"{CHANNEL} (1)", body)

    def test_a_second_registration_moments_later_is_not_briefed_again(self) -> None:
        self._away_for(10)
        self._message("m-unread", to=AGENT)
        self._register()
        self._register()
        self.assertEqual(len(self._briefings()), 1)

    def test_CONTROL_a_short_absence_is_not_briefed(self) -> None:
        self._away_for(1)
        self._message("m-unread", to=AGENT)
        self._register()
        self.assertEqual(self._briefings(), [])

    def test_CONTROL_nothing_new_means_nothing_sent(self) -> None:
        self._away_for(10)
        self._register()
        self.assertEqual(self._briefings(), [], "an agent that missed nothing was woken to be told so")

    def test_CONTROL_the_setting_turns_it_off(self) -> None:
        self._run(("INSERT OR REPLACE INTO settings (key, value) VALUES ('away_briefing_hours', '0')", ()))
        self._away_for(10)
        self._message("m-unread", to=AGENT)
        self._register()
        self.assertEqual(self._briefings(), [])
