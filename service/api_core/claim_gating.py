"""Whether a bridge may claim a dispatch run right now — and when it may not, WHY.

Nine functions, ~460 lines, assembled from TWO source modules because the subject was split across them:
five bodies lived in `routers/dispatch_messages/shared.py` and four in the control plane, and
`_claim_dispatch_once` reached all nine. That is the reason this module exists before the claim funnel
moves: the reviewer's condition for relocating a transaction-owning funnel is that it must not import the
route layer, and twelve of its dependencies were route-layer names. Seven of those twelve turned out to be
re-exports of api_core leaves — `shared.py` was only forwarding them — so the real work was these nine.

`_bridge_claim_block_reason` is 208 lines and it is a REASON function, not a boolean. That shape is
deliberate and worth preserving: a claim that silently returns "no work" is indistinguishable from a
claim blocked by a stale console owner, a not-yet-ready wrapper terminal, a superseded bridge, or a
turn-busy hold. Every one of those was diagnosed from production by reading the reason it produced.

TURN_BUSY_BACKSTOP_SECONDS IS A SAFETY BOUND, not a tuning value, and it lives in
`api_core/liveness.py` with the other liveness thresholds rather than here. It MUST equal the status
engine's `in_turn` clamp: when the two disagreed, queued work stranded and agents went permanently DEAF
because a turn_busy gate never expired. It has four carrier readers (three in the status cache, one in
`_gather_status_inputs`), so it is a neutral owner, and the invariant only holds because there is exactly
one of it.

DB ACCESS: `db` is passed in throughout. No connection opened, no commit, no rollback — every one of
these is a QUESTION, and the caller that acts on the answer owns the transaction. That separation is what
lets the claim funnel keep BEGIN IMMEDIATE to itself.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from service.api_core.capabilities import _row_capabilities
from service.api_core.events import _append_terminal_event
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS
from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _dedupe_preserve, _json_loads_or
from service.api_core.settings import _load_settings
from service.clock import iso_to_epoch as _iso_to_epoch, now as _now
from service.env_status import environment_effective_status as _environment_effective_status
from service.models import DispatchClaimRequest








def _dispatch_source_message_ids(row) -> list[str]:
    ids = []
    primary = str((row["message_id"] if row and "message_id" in row.keys() else "") or "").strip()
    if primary:
        ids.append(primary)
    body = str((row["body"] if row and "body" in row.keys() else "") or "")
    ids.extend(match.group(1).strip() for match in re.finditer(r"\bMessage\s*Id:\s*([^\s]+)", body, re.IGNORECASE))
    return _dedupe_preserve([message_id for message_id in ids if message_id])




async def _dispatch_conversation_context(db, row, *, limit: int = 8) -> list[dict[str, Any]]:
    from_agent = str((row["from_agent"] if row else "") or "").strip()
    target_agent = str((row["target_agent"] if row else "") or "").strip()
    if not from_agent or not target_agent:
        return []
    current_message_ids = set(_dispatch_source_message_ids(row))
    cursor = await db.execute(
        """
        SELECT id, from_agent, to_agent, type, subject, body, priority, timestamp, in_reply_to
        FROM messages
        WHERE source = 'direct'
          AND (
            (from_agent = ? AND to_agent = ?)
            OR (from_agent = ? AND to_agent = ?)
          )
        ORDER BY timestamp DESC, rowid DESC
        LIMIT ?
        """,
        (from_agent, target_agent, target_agent, from_agent, max(1, int(limit or 8)) + len(current_message_ids)),
    )
    rows = await cursor.fetchall()
    context = []
    for message in reversed(rows):
        if message["id"] in current_message_ids:
            continue
        context.append({
            "id": message["id"],
            "from": message["from_agent"],
            "to": message["to_agent"],
            "type": message["type"],
            "subject": message["subject"],
            "body": message["body"] or "",
            "priority": message["priority"],
            "timestamp": message["timestamp"],
            "inReplyTo": message["in_reply_to"],
        })
        if len(context) >= limit:
            break
    return context


async def _has_claimable_steerable_run(
    db,
    *,
    agent_row,
    supported_modes: set[str],
    agent_runtime: str,
) -> bool:
    """True when the turn-busy claim gate should be BYPASSED because a queued
    channel/resident run can be steered (injected) into a mid-turn target.

    Used only by the /dispatch/claim turn-busy gate (send-deadlock fix,
    2026-06-02). The carve-out fires when BOTH hold:

      * the TARGET can accept a mid-turn inject — `steer` is in its computed
        capabilities (_row_capabilities). For claude that means a managed or
        channelEnabled-resident session; a plain resident claude without
        channelEnabled, or a resident codex/opencode/pi, has no `steer` and is
        NOT bypassed. This is the SAME predicate the send-time steer path uses
        (line ~6770: `active_run and "steer" in capabilities`), so the gate and
        the steer route agree on who is injectable.
      * there is at least one QUEUED run in channel/resident execution mode that
        this bridge's supported_modes can actually claim. A managed (headless)
        run is never injectable, so it stays queued behind the turn as before.

    Returning False preserves the original "wait for the turn to end" behavior.
    """
    capabilities = _row_capabilities(agent_row)
    if "steer" not in capabilities:
        return False
    target_agent = str((agent_row["id"] if agent_row else "") or "")
    if not target_agent:
        return False
    cursor = await db.execute(
        """
        SELECT execution_mode, requested_runtime, queue_if_busy, steer_if_busy
        FROM dispatch_runs
        WHERE target_agent = ? AND status = 'queued'
        ORDER BY requested_at ASC
        LIMIT 25
        """,
        (target_agent,),
    )
    for run in await cursor.fetchall():
        if bool(run["queue_if_busy"]) or not bool(run["steer_if_busy"]):
            continue
        run_execution_mode = str((run["execution_mode"] or "managed")).strip().lower()
        if run_execution_mode not in {"channel", "resident"}:
            continue
        if supported_modes and run_execution_mode not in supported_modes:
            continue
        requested_runtime = str(run["requested_runtime"] or "").strip()
        if requested_runtime and _normalize_runtime(requested_runtime) != agent_runtime:
            continue
        return True
    return False


async def _release_stale_console_owner_for_claim(db, owner_session, req: DispatchClaimRequest) -> Optional[dict[str, Any]]:
    terminal_id = str(owner_session["terminal_id"] or "").strip()
    terminal_status = str(owner_session["terminal_status"] or "").strip().lower()
    terminal = None
    if terminal_id:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if terminal:
            terminal_status = str(terminal["status"] or terminal_status or "").strip().lower()

    settings = await _load_settings(db)
    stale_after = max(30, int(settings.get("environment_offline_seconds", 90) or 90))
    terminal_bridge_id = str((terminal["bridge_id"] if terminal else "") or "").strip()
    env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (owner_session["environment_id"],))).fetchone()
    env_status = _environment_effective_status(env_row, offline_seconds=stale_after) if env_row else "offline"
    bridge_current = bool(
        env_row
        and env_status in {"online", "degraded"}
        and terminal_bridge_id
        and terminal_bridge_id == str(env_row["bridge_id"] or "").strip()
    )
    active_status = terminal_status in {"starting", "attached", "running", "active", "idle"}
    # Keep a live Console owner regardless of how long it has been QUIET. Liveness is
    # bridge_current (the owning env bridge is online AND still owns this terminal's
    # bridge_id) + active_status (the PTY has not posted a terminal/exit status) — NOT
    # output age. An alive-but-quiet managed worker (idle between turns, or mid-turn
    # not printing) legitimately emits nothing for minutes; releasing it on a ~90s
    # output-age then respawned a fresh PTY on the NEXT dispatch — the terminal-churn
    # / "terminal closes constantly" + accumulating terminal_sessions rows incident
    # (2026-06-06). Age is not liveness; the env-offline + bridge-mismatch checks below
    # (real liveness) still release a genuinely-dead owner.
    if terminal and active_status and bridge_current:
        return {
            "reason": "console_owner_active",
            "sessionId": owner_session["id"],
            "terminalId": terminal_id,
            "terminalStatus": terminal_status,
            "hint": "Console owns this runtime handle. Stop or return Console to managed before claiming managed Messenger work.",
        }

    now = _now()
    reason = "Released stale Console owner before managed dispatch claim."
    await db.execute(
        """
        UPDATE agent_sessions
        SET owner_mode = 'managed',
            terminal_status = 'failed',
            last_seen = ?
        WHERE id = ?
        """,
        (now, owner_session["id"]),
    )
    if terminal:
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
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_owner_released",
            json.dumps({
                "reason": "stale Console owner",
                "requestedByBridge": req.bridgeId or "",
                "previousBridge": terminal_bridge_id,
                "environmentBridge": str(env_row["bridge_id"] or "").strip() if env_row else "",
                "terminalStatus": terminal_status,
            }),
        )
    return None


async def _turn_busy_holds_delivery(db, agent_id: str) -> bool:
    """True when the RAW turn_busy flag may still hold delivery back.

    The delivery gates (send-time queue decision + /dispatch/claim) key on the raw
    harness signal on purpose: "explicit queue" means exactly "after this turn", and
    re-deriving that through status or a short window is what made queued sends land
    mid-turn (#236). So this helper does NOT reinterpret the signal — it only applies
    the SAME anti-strand ceiling the status engine already applies to `in_turn`
    (TURN_BUSY_BACKSTOP_SECONDS, see the constant's own note).

    Why a ceiling is required (regression found 2026-07-26): the gates are pure-raw,
    but nothing guarantees turn_busy is ever cleared.
      * The dead-bridge sweeper (_clear_turn_busy_for_dead_bridges) deliberately
        SKIPS turn_bridge_id IN ('', 'user-prompt-submit') — i.e. every hook-driven
        resident-claude turn — and skips any turn whose bridge is still alive.
      * A missed turn-END (killed harness, hook error, or a transcript classifier
        that keeps reading in-flight) therefore latches turn_busy=1 permanently.
    Past the ceiling, status ALREADY stops reporting `working` (derive() clamps
    in_turn in both the push and poll paths). Holding delivery past that point makes
    the two disagree permanently: the dashboard shows an idle agent whose queued work
    can never be claimed. For a target WITHOUT `steer` the claim gate returns early,
    so that agent goes permanently deaf to every dispatch.

    A genuinely long turn is unaffected: the bridge turn detectors KEEP-FRESH re-stamp
    turn-start, so turn_updated_at keeps advancing for as long as real work runs. Only
    an ABANDONED flag ages out — which is exactly the strand this bounds.
    """
    try:
        row = await (await db.execute(
            "SELECT turn_busy, turn_updated_at FROM agent_turn_state WHERE agent_id = ?",
            (agent_id,),
        )).fetchone()
    except Exception:
        # Unreadable turn state must never block delivery — better to deliver.
        return False
    if not row or not int((row["turn_busy"] if "turn_busy" in row.keys() else 0) or 0):
        return False
    seen = _iso_to_epoch(str(row["turn_updated_at"] or ""))
    if not seen:
        # MISSING/UNPARSEABLE timestamp → do NOT hold (fixed 2026-07-26, review follow-up).
        # The first cut returned True here "to trust the raw flag", which quietly reproduced the
        # exact strand this helper exists to prevent: a latched turn_busy=1 whose turn_updated_at
        # is empty or malformed has NOTHING to age against, so it would hold delivery forever and
        # a non-steer target would stay permanently deaf — with no ceiling to rescue it.
        #
        # Releasing is the correct asymmetry. Every writer stamps turn_updated_at via _now()
        # (the /turn-start, /heartbeat and reconcile paths all do), so a blank or unparseable
        # value means a corrupt row, not a live turn. The worst case from releasing is ONE
        # message delivered mid-turn, which the harness queues or the reply reconciles; the worst
        # case from holding is an agent that never receives work again. Prefer the recoverable
        # failure.
        return False
    # A FUTURE timestamp must not hold either (review R4, 2026-07-26). `now - seen` goes NEGATIVE
    # for a clock-skewed or bad write, which trivially satisfies `<= CEILING` — so the flag would
    # hold delivery forever, the exact permanent strand this ceiling exists to bound. Requiring a
    # non-negative age closes it: only an age genuinely inside the window holds.
    age = datetime.now(timezone.utc).timestamp() - seen
    return 0 <= age <= TURN_BUSY_BACKSTOP_SECONDS


# v0.5.4: `_mark_dispatch_source_messages_read` arrived from the control plane. It is the one WRITE in
# this module, and it is here because `_dispatch_source_message_ids` — which decides what to mark — is
# already here: separating the question from the single act that consumes its answer would put a
# two-function pair in two files for no gain. It still takes `db` and commits nothing.

async def _mark_dispatch_source_messages_read(db, row, agent_id: str, read_at: str) -> int:
    message_ids = _dispatch_source_message_ids(row)
    if not message_ids:
        return 0
    placeholders = ",".join("?" for _ in message_ids)
    cursor = await db.execute(
        f"SELECT id FROM messages WHERE id IN ({placeholders})",
        message_ids,
    )
    existing_ids = {str(existing["id"]) for existing in await cursor.fetchall()}
    if not existing_ids:
        return 0
    for message_id in message_ids:
        if message_id not in existing_ids:
            continue
        await db.execute(
            "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (message_id, agent_id, read_at),
        )
    return len(existing_ids)

# Joins `_dispatch_source_message_ids` in this module, v0.5.4: that function BUILDS the per-recipient
# map and this one READS it, so they answer the same question from opposite ends.
def _dispatch_message_id_for_recipient(
    recipient_id: str,
    *,
    message_id: Optional[str],
    source_message_ids: Optional[dict[str, str]] = None,
) -> str:
    return str((source_message_ids or {}).get(recipient_id, message_id or "") or "").strip()


# _bridge_claim_block_reason and its three wrapper-terminal helpers moved to
# service/api_core/claim_block_reason.py in v0.5.4 — they called only each other, nothing
# outside this module called the three helpers, and the entry point has one importer.
