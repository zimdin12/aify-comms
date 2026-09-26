"""One short note to a claude agent whose turn was interrupted: it was a stop, not a refusal (v0.7.4).

Claude Code tells the model "The user doesn't want to proceed with this tool use" when a turn is
interrupted, the words it also uses for a declined permission prompt, and an agent interrupted live
concluded a permission gate existed (KNOWN_ISSUES, 2026-08-25). The wording is Claude Code's, so the
service adds the fact it holds: who stopped the agent, and when. Unread and never dispatched: the
operator stopped the agent on purpose, so the note waits for its next turn.
"""

from __future__ import annotations

import time
import uuid

from service.api_core.message_view import SERVICE_SENDER
from service.api_core.runtime import _normalize_runtime


async def note_the_interrupt(db, *, agent_id: str, stopped_by: str, at: str) -> str:
    """Store the note for a claude agent and return its id; "" when the agent is not claude."""
    row = await (await db.execute("SELECT runtime FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if not row or _normalize_runtime(row["runtime"] or "") != "claude-code":
        return ""
    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    body = (
        f"Your last action was stopped by {stopped_by or 'the operator'} at {at}. Claude Code words a stop "
        "as \"doesn't want to proceed with this tool use\": this was a stop, not a permission refusal."
    )
    await db.execute(
        "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority,"
        " dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (message_id, SERVICE_SENDER, agent_id, "direct", "info", "Your last action was stopped", body,
         "normal", 0, None, ts),
    )
    return message_id
