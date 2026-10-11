"""Typing into an agent's live console for a caller: who may, and the audited write.

Two routes write here. `POST /agents/{id}/console/input` types raw keystrokes (recovery-only), and
`POST /agents/{id}/compact/native` types the runtime's own compaction command. Both record the caller
twice -- the terminal control's `requested_by` and an `agent_console_input` audit event -- and that
audit is the only account of who moved an agent's keyboard, so the caller is checked BEFORE anything
is queued, never recorded as empty afterwards.

THE DASHBOARD IS NOT A REGISTERED AGENT, and only the compaction route lets it through. Raw console
input stays agent-only, as it always was; the dashboard types into a console through its own terminal
routes. Native compaction is a dashboard action by the operator's request, so that route opts in.
"""

from __future__ import annotations

import json

from fastapi import HTTPException

from service.api_core.events import _append_terminal_control, _append_terminal_event

#: The requester the dashboard names itself.
DASHBOARD_CALLER = "dashboard"


async def require_console_caller(db, caller, *, allow_dashboard: bool = False) -> str:
    """The caller, stripped, or the refusal: 400 for no caller at all, 403 for an unregistered one.

    Two codes on purpose: a missing field is a malformed request, an unknown id is a REFUSED one,
    and only the second belongs in an audit log.
    """
    who = str(caller or "").strip()
    if not who:
        raise HTTPException(400, "console input requires a `from` caller (the requesting agent id)")
    if allow_dashboard and who == DASHBOARD_CALLER:
        return who
    row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (who,))).fetchone()
    if not row:
        raise HTTPException(403, f"caller '{who}' is not a registered agent")
    return who


async def queue_console_input(db, terminal, *, caller: str, body: str, purpose: str = "") -> str:
    """Queue `body` as an input control on `terminal` and audit it. Returns the control id.

    QUEUED is all it proves: a completed control means the bytes reached the PTY, not that the
    runtime acted on them. The caller commits.
    """
    control_id = await _append_terminal_control(
        db,
        terminal_id=terminal["id"],
        environment_id=terminal["environment_id"],
        bridge_id=terminal["bridge_id"] or "",
        action="input",
        requested_by=caller,
        body=body,
    )
    audit = {"from": caller, "controlId": control_id, "bytes": len(body)}
    if purpose:
        audit["purpose"] = purpose
    await _append_terminal_event(db, terminal["id"], "agent_console_input", json.dumps(audit))
    return control_id
