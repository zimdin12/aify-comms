"""The two things a session-handle update does that are not the update itself.

Extracted from `update_agent_session_handle` in `service/routers/agents/session_mode.py` in v0.5.4;
`test_update_agent_session_handle_split_is_inert.py` inlines both back and AST-compares against the
pre-split fixture. Bodies are at their original 8-space column.

ONE DECIDES WHETHER TO TRUST A REPORTED ID; THE OTHER MIRRORS AN ACCEPTED ONE. They sit a hundred
lines apart in the handler and share only their subject, which is what the module is named for.

THE FRESH-START GUARD (2026-06-12, the ci-manager lost-context incident) is the subtle one.
Auto-adopting a self-reported session id is SAFE when the new id carries the old context -- a
compaction or a resume. It is destructive when the live terminal started FRESH, because then the
reported id names an EMPTY session, adopting it overwrites the pinned handle of the real
context-bearing one, and every later Restart "correctly" resumes the empty session. The tell is the
terminal's own command: no `--resume` means a fresh start, so the id is parked for manual Confirm
even when auto-confirm is on.

IT FAILS TOWARD False on any exception, which is NOT automatically the safe direction -- False means
"no evidence of a fresh start", so the caller proceeds on its other guards. That is worth knowing
before changing either side.

THE MIRROR keeps `agent_sessions` agreeing with `agents`. A handle set on the agent but not on its
live session row means the next resume reads the old id from the session while the dashboard shows
the new one from the agent -- two answers to "which session is this", which is the class of bug the
handle guards exist to prevent in the first place.
"""
from __future__ import annotations

import json

from service.api_core.serialization import _json_loads_or
from service.api_core.session_capabilities import _session_capabilities_replacing_handle


async def _detect_fresh_start_terminal(
    db, agent_id, _auto_confirm_sid, requested_by, session_handle, persisted_handle
):
        """Did the agent's live terminal start FRESH rather than resume? Parking depends on it.

        Every argument is passed under the caller's own name: the extract-method gate splices this
        body back over its call without substituting arguments, so it refuses a call whose argument
        name differs from the parameter it fills.
        """
        # FRESH-START GUARD (2026-06-12, the ci-manager lost-context incident): auto-adopt
        # exists for SAFE self-changes (a compaction/resume issues a new id that CARRIES the
        # context). But when the live terminal started FRESH (its command has no --resume —
        # e.g. the wrapper dropped an unresumable handle after days offline), the reported id
        # is an EMPTY session: adopting it overwrites the pinned handle of the real
        # context-bearing session, and every later Restart then "correctly" resumes the empty
        # one. Park such ids for manual Confirm instead, even when auto-confirm is ON.
        _fresh_start_terminal = False
        if (
            _auto_confirm_sid
            and requested_by == "bridge-heartbeat"
            and session_handle
            and persisted_handle
            and session_handle != persisted_handle
        ):
            try:
                _lt = await (await db.execute(
                    "SELECT command FROM terminal_sessions WHERE agent_id = ? "
                    "AND status IN ('starting','attached','running','active','idle') "
                    "AND id NOT LIKE 'vterm_%' ORDER BY datetime(COALESCE(updated_at, created_at)) DESC LIMIT 1",
                    (agent_id,),
                )).fetchone()
                if _lt is not None:
                    _fresh_start_terminal = "--resume" not in str(_lt["command"] or "")
            except Exception:
                _fresh_start_terminal = False
        return _fresh_start_terminal


async def _mirror_handle_onto_live_session(
    db, agent_id, runtime, session_handle, registered_handle, now
) -> None:
        """Copy the accepted handle, its capabilities and its telemetry onto the live session row."""
        latest_session = await (await db.execute(
            """
            SELECT id, capabilities, telemetry
            FROM agent_sessions
            WHERE agent_id = ?
              AND runtime = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, runtime),
        )).fetchone()
        if latest_session:
            session_telemetry = _json_loads_or(latest_session["telemetry"], {})
            if registered_handle:
                session_telemetry["registeredHandle"] = registered_handle
            else:
                session_telemetry.pop("registeredHandle", None)
            session_capabilities = _session_capabilities_replacing_handle(latest_session["capabilities"], session_handle)
            await db.execute(
                """
                UPDATE agent_sessions
                SET session_handle = ?,
                    capabilities = ?,
                    telemetry = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    session_handle,
                    json.dumps(session_capabilities),
                    json.dumps(session_telemetry),
                    now,
                    latest_session["id"],
                ),
            )
