"""An agent that comes back after a long absence is told what changed while it was gone.

WHY THIS EXISTS. The operator saw agents return after hours away and act on the state they left:
answering threads that had moved on, redoing work a teammate had finished. The only guidance was a
manager-side "rebrief" line in the lead skill, which depends on someone noticing the agent was gone.
The agent coming back is the one party that always knows it just started, so the service tells it.

HOW. On registration, the agent's PREVIOUS `last_seen` is compared with now. Past the
`away_briefing_hours` setting (0 turns it off), the service gathers what arrived for the agent while
it was away -- unread direct messages by sender, and new messages in the channels it belongs to --
and sends it one `info` message from `aify-comms`. It is sent through the ordinary dispatch path, so
every harness receives it the way it receives any message, and the bridge batches it with whatever
work is already queued, so it arrives alongside the first task rather than as a turn of its own.

NOTHING NEW, NOTHING SENT. An agent that was away but missed nothing is not woken to be told so.
An agent registering for the first time has no previous `last_seen` and is not briefed; a second
registration moments later sees a fresh `last_seen` and is not briefed twice.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from service.api_core.channel_delivery import _apply_channel_routing_to_claude_runs
from service.api_core.dispatch_run_state import _finalize_dispatch_runs
from service.api_core.dispatch_runs import _create_dispatch_runs
from service.api_core.send_preflight import _preflight_live_send_recipients
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.clock import iso_to_epoch
from service.db import get_db

logger = logging.getLogger("aify_comms.api_core.away_briefing")

#: Who the briefing is from. Not an agent: it is the service reporting on itself.
SENDER = "aify-comms"
#: How many senders and channels are named before the rest are summarised as a count.
LISTED = 5


def away_seconds(last_seen: str, now_epoch: float) -> float:
    """Seconds since `last_seen`, or 0 when there is none (a first registration is not a return)."""
    seen = iso_to_epoch(last_seen)
    return max(0.0, now_epoch - seen) if seen else 0.0


def briefing_due(away: float, hours: float) -> bool:
    """Whether an absence of `away` seconds earns a briefing. `hours` of 0 or less turns it off."""
    return hours > 0 and away >= hours * 3600


def _duration(seconds: float) -> str:
    hours = int(seconds // 3600)
    if hours >= 48:
        return f"{hours // 24} days"
    if hours >= 1:
        return f"{hours} h"
    return f"{int(seconds // 60)} min"


def _listed(pairs: list[tuple[str, int]], noun: str) -> str:
    shown = ", ".join(f"{name} ({count})" for name, count in pairs[:LISTED])
    rest = len(pairs) - LISTED
    return f"{shown}, and {rest} more {noun}" if rest > 0 else shown


class AwayBriefing:
    """What arrived for one agent while it was away."""

    def __init__(self, agent_id: str, away: float, since: str,
                 unread: list[tuple[str, int]], channels: list[tuple[str, int]]) -> None:
        self.agent_id = agent_id
        self.away = away
        self.since = since
        self.unread = unread
        self.channels = channels

    @property
    def has_news(self) -> bool:
        return bool(self.unread or self.channels)

    @property
    def subject(self) -> str:
        return f"While you were away ({_duration(self.away)})"

    def body(self) -> str:
        lines = [f"You were last active {_duration(self.away)} ago ({self.since}). Since then:"]
        if self.unread:
            total = sum(count for _, count in self.unread)
            lines.append(f"- {total} unread direct message(s), from {_listed(self.unread, 'senders')}.")
        if self.channels:
            lines.append(f"- new messages in your channels: {_listed(self.channels, 'channels')}.")
        lines.append(
            "Before acting on anything you remember from before, catch up: read your inbox headers "
            "(comms_inbox mode=headers) and the channels above, and ask your lead about anything that "
            "is still unclear. This is an automatic notice; do not reply to it."
        )
        return "\n".join(lines)


async def gather(db, agent_id: str, last_seen: str, away: float) -> AwayBriefing:
    """Read what arrived for `agent_id` since `last_seen`.

    BOTH HALVES ARE WINDOWED. The channel query always was; the unread-DM query was not, so an
    unread message from twenty hours before the absence began was reported under "Since then" and
    an absence with nothing new in it still dispatched a briefing -- which is exactly what this
    feature's own commit said it would not do (external review 2026-09-21, finding 6). An old
    unread message is still unread; it is just not something that arrived while this agent was away,
    and the agent is told to read its inbox headers regardless.
    """
    since_ms = int(iso_to_epoch(last_seen) * 1000)
    unread = await (await db.execute(
        """
        SELECT m.from_agent, COUNT(*) FROM messages m
        WHERE m.to_agent = ? AND m.from_agent != ?
          AND m.timestamp > ?
          AND NOT EXISTS (SELECT 1 FROM read_receipts r WHERE r.message_id = m.id AND r.agent_id = ?)
        GROUP BY m.from_agent ORDER BY COUNT(*) DESC, m.from_agent
        """,
        (agent_id, SENDER, since_ms, agent_id),
    )).fetchall()
    channels = await (await db.execute(
        """
        SELECT m.channel, COUNT(*) FROM messages m
        JOIN channel_members c ON c.channel_name = m.channel AND c.agent_id = ?
        WHERE m.timestamp > ? AND m.from_agent != ?
        GROUP BY m.channel ORDER BY COUNT(*) DESC, m.channel
        """,
        (agent_id, since_ms, agent_id),
    )).fetchall()
    return AwayBriefing(agent_id, away, last_seen,
                        [(row[0], row[1]) for row in unread], [(row[0], row[1]) for row in channels])


async def _send(db, briefing: AwayBriefing) -> Optional[str]:
    launchable, not_started = await _preflight_live_send_recipients(
        db, [briefing.agent_id], allow_steer=True, allow_queue_busy=True,
    )
    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    await db.execute(
        "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority,"
        " dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (message_id, SENDER, briefing.agent_id, "direct", "info", briefing.subject, briefing.body(),
         "normal", 1 if launchable else 0, None, ts),
    )
    if launchable:
        runs = await _create_dispatch_runs(
            db, [recipient for recipient, _ in launchable],
            from_agent=SENDER, message_type="info", subject=briefing.subject, body=briefing.body(),
            priority="normal", in_reply_to=None, dispatch_mode="start_if_possible",
            execution_mode="managed", requested_runtime=None, message_id=message_id,
            steer=False, require_reply=False,
        )
        await _apply_channel_routing_to_claude_runs(db, runs, await _load_settings(db))
        await _finalize_dispatch_runs(db, runs, launchable, not_started)
    return message_id


async def brief_returning_agent(agent_id: str, previous_last_seen: str) -> Optional[str]:
    """Send `agent_id` its briefing if it has been away long enough and missed something.

    Returns the message id, or None when nothing was sent. Never raises: a registration must not fail
    because its briefing could not be written.
    """
    try:
        db = await get_db()
        try:
            settings = await _load_settings(db)
            briefing_hours = float(settings.get("away_briefing_hours", DEFAULT_SETTINGS["away_briefing_hours"]) or 0)
            away = away_seconds(previous_last_seen, time.time())
            if not briefing_due(away, briefing_hours):
                return None
            briefing = await gather(db, agent_id, previous_last_seen, away)
            if not briefing.has_news:
                return None
            message_id = await _send(db, briefing)
            await db.commit()
            return message_id
        finally:
            await db.close()
    except Exception:  # noqa: BLE001 -- the registration already succeeded; a briefing is best-effort
        logger.exception("away briefing for %s failed", agent_id)
        return None
