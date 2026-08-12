"""Getting a managed run's final text in front of the operator — in dashboard chat, as a real message.

Two functions, 202 lines, and they are together because they answer the same question for two different
askers: a run STARTED from the dashboard, and an async run a manager-style coordinator kicked off.

WHY THIS IS A BACKEND INVARIANT AND NOT AGENT ETIQUETTE. The bridge already captures a managed runtime's
final text as the run summary. Whether the operator ever SEES it used to depend on the agent choosing to
call `comms_send(to="dashboard")` — which older running agents, launched before the prompt that says so,
simply do not do. Making it a backend step is the difference between "the coordinator usually reports
back" and "the report exists".

REPLY DEBT AND VISIBILITY ARE SEPARATE CONCERNS, which is why `_mirror_dashboard_run_summary_to_chat`
exists alongside the contract machinery rather than inside it. A routine dashboard `info` ask must NOT
become a reply contract — that would make every operator question something an agent owes an answer to —
but its final text still has to land in dashboard chat.

A delivery RECEIPT is not a reply, and this module asks `_is_delivery_only_claude_run`
(api_core/dispatch_state.py) rather than deciding locally. Getting that wrong is what once persisted
"Delivered to Claude channel session; awaiting explicit reply" as a fake `Re:` response.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from service.api_core.dispatch_state import _is_delivery_only_claude_run
from service.api_core.dispatch_text import _auto_handoff_subject_for_run
from service.api_core.events import _append_dispatch_event
from service.api_core.serialization import _row_require_reply
from service.clock import iso_to_epoch as _iso_to_epoch


async def _maybe_report_async_manager_result_to_dashboard(db, row) -> Optional[str]:
    """Store manager/operator async run summaries in dashboard chat.

    The bridge already captures managed runtime final text as the run summary.
    Older running agents may not have the latest prompt/skill telling them to
    call comms_send(to="dashboard") after teammate replies arrive, so make the
    operator-visible report a backend invariant for manager-style coordinators.
    """
    if not row:
        return None
    if _row_require_reply(row):
        return None
    if str((row["from_agent"] if "from_agent" in row.keys() else "") or "").strip() == "dashboard":
        return None
    if str((row["status"] if "status" in row.keys() else "") or "").strip().lower() != "completed":
        return None

    summary = str((row["summary"] if "summary" in row.keys() else "") or "").strip()
    if not summary:
        return None

    target_agent = str((row["target_agent"] if "target_agent" in row.keys() else "") or "").strip()
    if not target_agent:
        return None

    event_cursor = await db.execute(
        "SELECT 1 FROM dispatch_events WHERE run_id = ? AND event_type = 'dashboard_report' LIMIT 1",
        (row["id"],),
    )
    if await event_cursor.fetchone():
        return None

    agent_cursor = await db.execute("SELECT role FROM agents WHERE id = ?", (target_agent,))
    agent_row = await agent_cursor.fetchone()
    role = str((agent_row["role"] if agent_row else "") or "").strip().lower()
    if role not in {"manager", "operator", "lead", "coordinator"}:
        return None

    start_ms = int(
        _iso_to_epoch(
            (row["started_at"] if "started_at" in row.keys() else "")
            or (row["claimed_at"] if "claimed_at" in row.keys() else "")
            or (row["requested_at"] if "requested_at" in row.keys() else "")
        )
        * 1000
    )
    source_message_id = str((row["message_id"] if "message_id" in row.keys() else "") or "").strip()
    if source_message_id:
        source_cursor = await db.execute("SELECT timestamp FROM messages WHERE id = ? LIMIT 1", (source_message_id,))
        source_row = await source_cursor.fetchone()
        if source_row:
            start_ms = max(start_ms, int(source_row["timestamp"] or 0))
    explicit_cursor = await db.execute(
        """
        SELECT 1
        FROM messages
        WHERE from_agent = ?
          AND to_agent = 'dashboard'
          AND source = 'direct'
          AND timestamp >= ?
        LIMIT 1
        """,
        (target_agent, max(0, start_ms)),
    )
    if await explicit_cursor.fetchone():
        await _append_dispatch_event(
            db,
            row["id"],
            "dashboard_report_skipped",
            "Skipped async dashboard summary mirror because an explicit dashboard message already exists for this run window.",
        )
        return None

    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    subject = str((row["subject"] if "subject" in row.keys() else "") or "").strip()
    if subject and not subject.lower().startswith(("re:", "update:")):
        subject = f"Update: {subject}"
    elif not subject:
        subject = "Update from managed run"

    await db.execute(
        """
        INSERT INTO messages (
            id, from_agent, to_agent, source, type, subject, body, priority,
            dispatch_requested, in_reply_to, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id,
            target_agent,
            "dashboard",
            "direct",
            "info",
            subject,
            summary,
            row["priority"] or "normal",
            0,
            row["message_id"],
            ts,
        ),
    )
    await _append_dispatch_event(
        db,
        row["id"],
        "dashboard_report",
        f"Stored async manager/operator report for dashboard as {message_id}",
    )
    return message_id


async def _mirror_dashboard_run_summary_to_chat(db, row) -> Optional[str]:
    """Persist dashboard-started managed run final text as a chat reply.

    Work Loop reply debt and operator-visible chat delivery are separate
    concerns. Routine dashboard `info` asks should not become contracts, but
    their managed runtime final text still needs to land in dashboard chat.
    """
    if not row:
        return None
    if str((row["from_agent"] if "from_agent" in row.keys() else "") or "").strip() != "dashboard":
        return None
    if str((row["status"] if "status" in row.keys() else "") or "").strip().lower() != "completed":
        return None
    if str((row["result_message_id"] if "result_message_id" in row.keys() else "") or "").strip():
        return None
    if _is_delivery_only_claude_run(row):
        return None
    current_cursor = await db.execute("SELECT result_message_id FROM dispatch_runs WHERE id = ?", (row["id"],))
    current_row = await current_cursor.fetchone()
    if str((current_row["result_message_id"] if current_row else "") or "").strip():
        return None

    summary = str((row["summary"] if "summary" in row.keys() else "") or "").strip()
    target_agent = str((row["target_agent"] if "target_agent" in row.keys() else "") or "").strip()
    if not summary or not target_agent:
        return None

    start_ms = int(
        _iso_to_epoch(
            (row["started_at"] if "started_at" in row.keys() else "")
            or (row["claimed_at"] if "claimed_at" in row.keys() else "")
            or (row["requested_at"] if "requested_at" in row.keys() else "")
        )
        * 1000
    )
    source_message_id = str((row["message_id"] if "message_id" in row.keys() else "") or "").strip()
    explicit_cursor = await db.execute(
        """
        SELECT id
        FROM messages
        WHERE from_agent = ?
          AND to_agent = 'dashboard'
          AND source = 'direct'
          AND timestamp >= ?
        ORDER BY timestamp ASC, id ASC
        LIMIT 1
        """,
        (target_agent, max(0, start_ms)),
    )
    explicit = await explicit_cursor.fetchone()
    if explicit:
        message_id = str(explicit["id"] or "").strip()
        await db.execute("UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?", (message_id, row["id"]))
        await _append_dispatch_event(
            db,
            row["id"],
            "handoff",
            f"Linked existing dashboard reply {message_id}",
        )
        return message_id

    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    subject = _auto_handoff_subject_for_run(row)
    await db.execute(
        """
        INSERT INTO messages (
            id, from_agent, to_agent, source, type, subject, body, priority,
            dispatch_requested, in_reply_to, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id,
            target_agent,
            "dashboard",
            "direct",
            "response",
            subject,
            summary,
            row["priority"] or "normal",
            0,
            source_message_id or None,
            ts,
        ),
    )
    await db.execute("UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?", (message_id, row["id"]))
    await _append_dispatch_event(
        db,
        row["id"],
        "handoff",
        f"Stored dashboard-visible final reply as {message_id}",
    )
    return message_id
