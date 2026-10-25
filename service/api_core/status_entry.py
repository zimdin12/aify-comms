"""One agent's status-cache entry, before the operational view puts its word in (0.9.9 S3b).

Since the 0.9.8 switch the word, its note and its last-known come from `switched_view.word_into`. What the entry
carries besides is what its readers take: the operator's stop and its note (served_note's D8 row 1), the bound
environment, session and terminal (the registration gate's managed-environment fallback, the console), the active run
(the view's row 5, without a second read) and when the entry expires. It replaced the legacy cascade
(`status_inputs._compute_live_status_cache`), which derived a whole legacy word from turn, console and liveness
signals only for the switch to discard it.

THE EXPIRY IS A BACKSTOP. Every input the view reads moves through an event that invalidates the entry: a publication
re-derives it, a run start or end, a stop, a registration. Report age is the one input that moves with time, and
`word_into` caps the expiry at the moment a fresh holder goes stale. The backstop bounds any invalidation a writer
forgot, at the agent-liveness window the cascade used for live agents, counted from now so a long-dead agent is not
expired on every read.
"""
from __future__ import annotations

from typing import Any, Optional

from service.api_core.active_run_lookup import _current_active_run_row
from service.api_core.agent_sessions import _current_agent_session_row
from service.api_core.manual_status import _MANUAL_STATUSES
from service.api_core.records import _row_status_note
from service.api_core.serialization import _iso_add_seconds, _json_loads_or
from service.api_core.settings import _load_settings
from service.clock import now as _now

#: An operator stop changes only through an action that invalidates the entry, so it never expires on its own.
NEVER = "9999-12-31T23:59:59Z"


async def status_entry(db, agent_row, *, settings: Optional[dict[str, Any]] = None,
                       now: Optional[str] = None) -> dict[str, Any]:
    """The entry for one agent row, with no word in it yet unless the operator stopped the agent."""
    now = now or _now()
    manual = str(agent_row["status"] or "").strip().lower()
    if manual in _MANUAL_STATUSES:
        return {"status": manual, "reason": _row_status_note(agent_row), "environment_id": "", "session_id": "",
                "terminal_id": "", "active_run_id": "", "active_run_status": "", "refresh_after": NEVER,
                "updated_at": now}
    settings = settings or await _load_settings(db)
    session = await _current_agent_session_row(db, agent_row["id"])
    active_run = await _current_active_run_row(db, agent_row["id"])
    keys = session.keys() if session else ()
    runtime_state = _json_loads_or(agent_row["runtime_state"], {})
    return {
        "status": "",
        "reason": "",
        "environment_id": str((session["environment_id"] if session else "") or runtime_state.get("environmentId") or "").strip(),
        "session_id": str((session["id"] if session else "") or "").strip(),
        "terminal_id": str((session["terminal_id"] if "terminal_id" in keys else "") or "").strip(),
        "active_run_id": str((active_run["id"] if active_run else "") or "").strip(),
        "active_run_status": str((active_run["status"] if active_run else "") or ""),
        "refresh_after": _iso_add_seconds(now, int(settings.get("agent_liveness_seconds", 90) or 90)),
        "updated_at": now,
    }
