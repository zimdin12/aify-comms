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
        # BOTH COLUMNS, because the absence is measured from `last_present_at` and every row written
        # before that column existed falls back to `last_seen`.
        self._run(("UPDATE agents SET last_seen = ?, last_present_at = ? WHERE id = ?",
                   (_iso_hours_ago(hours), _iso_hours_ago(hours), AGENT)))

    def _an_operator_touches_the_row(self) -> None:
        """What Stop, Resume, a favourite toggle and a description edit all do: stamp `last_seen`.

        Named for what it MEANS rather than which endpoint does it, because four of them do it and
        the briefing must survive all four. `last_present_at` is deliberately not touched: the agent
        said nothing, somebody pressed something.
        """
        self._run(("UPDATE agents SET last_seen = ? WHERE id = ?", (_iso_hours_ago(0), AGENT)))

    def _message_at(self, message_id: str, *, hours_ago: float, to: str = "") -> None:
        at_ms = int((time.time() - hours_ago * 3600) * 1000)
        self._run(("INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, timestamp)"
                   " VALUES (?,?,?,?,?,?,?,?,?)",
                   (message_id, TEAMMATE, to or None, None, "direct", "info", "update", "older news", at_ms)))

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


class AnAbsenceIsMeasuredFromWhenTheAgentWasHere(FastApiTestCase):
    """EXTERNAL REVIEW, 2026-09-21, finding 6. Two defects, one feature that never fired.

    The absence was measured from `agents.last_seen`, which Stop, Resume, a description edit and a
    favourite toggle all stamp -- so the canonical flow, Stop overnight and Resume in the morning,
    was the one case that never briefed: the Resume refreshed the very timestamp the absence was
    computed from. And the unread half of the query had no time window at all, so an unread message
    from long before the absence was reported under "Since then" and an absence with nothing new in
    it still dispatched a briefing.
    """

    _register = AReturningAgentIsToldWhatItMissed._register
    _run = AReturningAgentIsToldWhatItMissed._run
    _away_for = AReturningAgentIsToldWhatItMissed._away_for
    _an_operator_touches_the_row = AReturningAgentIsToldWhatItMissed._an_operator_touches_the_row
    _message = AReturningAgentIsToldWhatItMissed._message
    _message_at = AReturningAgentIsToldWhatItMissed._message_at
    _briefings = AReturningAgentIsToldWhatItMissed._briefings

    def setUp(self) -> None:
        super().setUp()
        self._register()

    def test_an_operator_pressing_something_mid_absence_does_not_erase_it(self) -> None:
        self._away_for(10)
        self._message("m-unread", to=AGENT)
        self._an_operator_touches_the_row()
        self._register()
        self.assertEqual(len(self._briefings()), 1,
                         "a favourite toggle is not the agent saying it was here")

    def test_CONTROL_the_same_absence_without_the_touch_still_briefs(self) -> None:
        self._away_for(10)
        self._message("m-unread", to=AGENT)
        self._register()
        self.assertEqual(len(self._briefings()), 1)

    def test_CONTROL_a_short_absence_is_still_not_briefed_after_a_touch(self) -> None:
        """The touch must not become a way IN either: presence still decides, and it is recent."""
        self._away_for(1)
        self._message("m-unread", to=AGENT)
        self._an_operator_touches_the_row()
        self._register()
        self.assertEqual(self._briefings(), [])

    def test_an_unread_message_from_before_the_absence_is_not_news(self) -> None:
        self._away_for(10)
        self._message_at("m-old-unread", hours_ago=30, to=AGENT)
        self._register()
        self.assertEqual(self._briefings(), [],
                         "an absence with nothing new in it must send nothing, as the feature says")

    def test_CONTROL_an_unread_message_from_during_the_absence_is(self) -> None:
        self._away_for(10)
        self._message_at("m-new-unread", hours_ago=5, to=AGENT)
        self._register()
        self.assertEqual(len(self._briefings()), 1)

    def test_a_row_with_no_presence_recorded_falls_back_to_last_seen(self) -> None:
        """Every row written before the column existed, which is all of them on a real deploy.

        Without the fallback the absence would be measured from the epoch and the whole fleet would
        be briefed about its entire history on the first registration after the upgrade.
        """
        self._run(("UPDATE agents SET last_seen = ?, last_present_at = '' WHERE id = ?",
                   (_iso_hours_ago(10), AGENT)))
        self._message("m-unread", to=AGENT)
        self._register()
        self.assertEqual(len(self._briefings()), 1)


class WhatTheAgentDoesIsPresenceAndWhatIsDoneToItIsNot(FastApiTestCase):
    """An agent that works without heartbeating was briefed "while you were away" mid-work.

    Presence moved only on heartbeat and registration, and not every agent heartbeats: an SSE client
    never does, and a stdio bridge started without `AIFY_AGENT_ID` skips its liveness beat. Its sends,
    turn signals and inbox reads stamped `last_seen` only, so after a day of work its next
    registration measured an absence from the one before and woke it with a briefing.

    The rule the rows below hold: an action the agent AUTHORS is presence; an action an operator takes
    ON the agent is not (finding 6, which is why the column exists).
    """

    _register = AReturningAgentIsToldWhatItMissed._register
    _run = AReturningAgentIsToldWhatItMissed._run
    _away_for = AReturningAgentIsToldWhatItMissed._away_for
    _message_at = AReturningAgentIsToldWhatItMissed._message_at
    _briefings = AReturningAgentIsToldWhatItMissed._briefings

    def setUp(self) -> None:
        super().setUp()
        self._register()

    def _briefed_after(self, act) -> bool:
        self._run(("DELETE FROM messages", ()))
        self._away_for(10)
        response = act()
        self.assertLess(response.status_code, 300, response.text)
        self._message_at("m-while-working", hours_ago=2, to=AGENT)
        self._register()
        return bool(self._briefings())

    def test_an_agent_that_just_sent_a_message_is_not_briefed_as_away(self) -> None:
        briefed = self._briefed_after(lambda: self.client.post("/api/v1/messages/send", json={
            "from_agent": AGENT, "to": TEAMMATE, "subject": "progress", "body": "still working"}))
        self.assertFalse(briefed, "an agent that sent a message seconds ago was briefed as away")

    def test_each_kind_of_write_counts_as_presence_only_when_the_agent_authored_it(self) -> None:
        def mid_turn_end():
            # The fast path skips the write when no turn is open, so open one without a presence write.
            self._run(("INSERT OR REPLACE INTO agent_turn_state (agent_id, turn_busy, turn_updated_at)"
                       " VALUES (?, 1, ?)", (AGENT, _iso_hours_ago(0))))
            return self.client.post(f"/api/v1/agents/{AGENT}/turn-end")

        authored = {
            "heartbeat": lambda: self.client.post(f"/api/v1/agents/{AGENT}/heartbeat", json={}),
            "turn start": lambda: self.client.post(f"/api/v1/agents/{AGENT}/turn-start"),
            "turn end": mid_turn_end,
            "inbox read": lambda: self.client.get(f"/api/v1/messages/inbox/{AGENT}"),
            "listen": lambda: self.client.get(f"/api/v1/agents/{AGENT}/listen?timeout=1"),
            "own status": lambda: self.client.patch(f"/api/v1/agents/{AGENT}", json={"status": "idle"}),
        }
        done_to_it = {
            "favourite": lambda: self.client.patch(f"/api/v1/agents/{AGENT}/favorite", json={"favorited": True}),
            "description": lambda: self.client.patch(f"/api/v1/agents/{AGENT}/description",
                                                     json={"description": "edited by the operator"}),
        }
        for label, act in authored.items():
            with self.subTest(authored=label):
                self.assertFalse(self._briefed_after(act), f"{label}: the agent acted, so it was here")
        for label, act in done_to_it.items():
            with self.subTest(done_to_it=label):
                self.assertTrue(self._briefed_after(act), f"{label}: an operator's press is not presence")
