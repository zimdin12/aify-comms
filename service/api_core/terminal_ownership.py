"""Terminal ownership: which terminal currently serves an agent, and releasing one that has gone stale.

The SECOND blocker under the spawn/pty group. `_ensure_managed_pty_for_dispatch` asks both of these
questions before deciding whether it must start anything, so neither can stay in the carrier if that
function is to leave.

Distinct from the other two terminal leaves on purpose. `api_core/terminal_status.py` owns the status
vocabulary, `api_core/terminal_output.py` owns writing output to a row, and this owns the
AGENT-to-TERMINAL binding — which row is live for this agent right now, and how to give up a claim
that is not. Three questions, three owners; merging them would produce a `terminal.py` that means
"anything to do with terminals", which is the junk drawer under a better name.

`_release_stale_terminal_owner` marks the terminal failed, hands the session back to `managed` owner
mode, and records a `terminal_owner_released` event. It is a state-based release: it keys on the row
it is handed rather than on an event having fired, which is the rule that exists because a spawn once
sat `running` for 97 minutes waiting for a cleanup call that one of ~26 terminal writers never made.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback — the caller owns the
transaction.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from service.api_core.events import _append_terminal_event
from service.api_core.runtime import _normalize_runtime
from service.api_core.settings import _load_settings
from service.clock import now as _now
from service.env_status import environment_effective_status as _environment_effective_status


async def _release_stale_terminal_owner(db, row, *, reason: str):
    terminal_id = str(row["terminal_id"] or "").strip()
    session_id = str(row["session_id"] or "").strip()
    if not terminal_id or not session_id:
        return
    now = _now()
    await db.execute(
        """
        UPDATE terminal_sessions
        SET status = 'failed',
            updated_at = ?,
            stopped_at = COALESCE(stopped_at, ?),
            error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
        WHERE id = ?
        """,
        (now, now, reason, terminal_id),
    )
    await db.execute(
        """
        UPDATE agent_sessions
        SET owner_mode = 'managed',
            terminal_status = 'failed',
            last_seen = ?
        WHERE id = ?
          AND terminal_id = ?
        """,
        (now, session_id, terminal_id),
    )
    await _append_terminal_event(
        db,
        terminal_id,
        "terminal_owner_released",
        json.dumps({"reason": reason}),
    )


async def _active_terminal_for_agent(db, agent_id: str, *, settings: Optional[dict[str, Any]] = None):
    row = await (await db.execute(
        """
        SELECT
            s.id AS session_id,
            s.environment_id AS session_environment_id,
            s.owner_mode,
            s.terminal_status,
            s.runtime AS session_runtime,
            t.id AS terminal_id,
            t.environment_id,
            t.bridge_id,
            t.runtime,
            t.workspace,
            t.command,
            t.status,
            t.updated_at
        FROM agent_sessions s
        JOIN terminal_sessions t ON t.id = s.terminal_id
        WHERE s.agent_id = ?
          AND COALESCE(s.terminal_id, '') != ''
        ORDER BY s.last_seen DESC
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()
    if not row:
        return None

    status = str(row["status"] or row["terminal_status"] or "").strip().lower()
    if status not in {"starting", "attached", "running", "active", "idle"}:
        return None
    runtime = _normalize_runtime(row["runtime"] or row["session_runtime"] or "")
    command = str(row["command"] or "").strip()
    if runtime == "claude-code" and command and "claude-aify" not in command:
        await _release_stale_terminal_owner(
            db,
            row,
            reason="Released legacy raw Claude terminal before managed channel dispatch; Claude backing must start through claude-aify.",
        )
        return None

    settings = settings or await _load_settings(db)
    # FIX B3 (2026-06-03): raise the Console-owner staleness-release floor to align
    # with resident_lease_seconds (~150s). The 90s environment_offline_seconds
    # default reaped an idle-but-live managed console between turns — a codex
    # worker that finished a turn and is waiting for the next dispatch can sit
    # quiet for >90s, get its terminal released here, and then read `available`
    # mid-work. Floor at resident_lease_seconds so an alive-but-quiet console
    # survives the inter-turn gap.
    stale_after = max(
        30,
        int(settings.get("environment_offline_seconds", 90) or 90),
        int(settings.get("resident_lease_seconds", 150) or 150),
    )
    # Do NOT release on terminal output-age: a live-but-quiet managed worker emits
    # nothing for minutes between/within turns. Liveness is the owning env bridge being
    # online + still owning this terminal's bridge_id (checked below) — not how long
    # since the last PTY byte. (Output-age release caused the terminal churn /
    # accumulating terminal_sessions rows; 2026-06-06.)
    env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (row["environment_id"],))).fetchone()
    env_status = _environment_effective_status(env_row, offline_seconds=stale_after) if env_row else "offline"
    if env_status not in {"online", "degraded"}:
        await _release_stale_terminal_owner(db, row, reason="Released unavailable Console owner before managed PTY dispatch.")
        return None
    if str(row["bridge_id"] or "").strip() != str(env_row["bridge_id"] or "").strip():
        await _release_stale_terminal_owner(db, row, reason="Released stale Console owner before managed PTY dispatch.")
        return None
    return row
