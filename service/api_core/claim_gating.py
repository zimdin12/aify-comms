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

DB ACCESS: `db` is passed in throughout. No connection opened, no commit, no rollback — every one of
these is a QUESTION, and the caller that acts on the answer owns the transaction. That separation is what
lets the claim funnel keep BEGIN IMMEDIATE to itself.
"""

from __future__ import annotations


import json
import re
from typing import Any, Optional

from service.api_core.capabilities import _row_capabilities
from service.api_core.dispatch_text import _MERGED_DISPATCH_HEADER
from service.api_core.events import _append_terminal_event

from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _dedupe_preserve
from service.api_core.settings import _load_settings
from service.clock import now as _now
from service.env_status import environment_effective_status as _environment_effective_status
from service.models import DispatchClaimRequest


def _dispatch_source_message_ids(row) -> list[str]:
    ids = []
    primary = str((row["message_id"] if row and "message_id" in row.keys() else "") or "").strip()
    if primary:
        ids.append(primary)
    body = str((row["body"] if row and "body" in row.keys() else "") or "")
    # STRUCTURAL LINES ONLY. This scan exists to recover the source ids of a MERGED buffer, whose
    # items `_render_pending_dispatch_item` and `_queue_console_dispatch_inputs` write as a whole
    # line, `MessageId: <id>`. It used to be `\bMessage\s*Id:\s*(\S+)` with IGNORECASE and no
    # anchor, so it also matched PROSE anywhere in a body — and a body is free text written by the
    # SENDING agent.
    #
    # What that bought: every id it returns is fed to `_mark_dispatch_source_messages_read`, which
    # INSERTs a read receipt for the CLAIMING agent against any matching row in `messages` (the
    # lookup is `WHERE id IN (...)`, unscoped). Unread is computed as the ABSENCE of a receipt
    # (`routers/dispatch_messages/inbox.py`: LEFT JOIN ... WHERE r.message_id IS NULL), so a receipt the
    # agent never earned SUPPRESSES that message from its unread inbox. The same ids are also an exclusion
    # set in `_dispatch_conversation_context`, dropping a real message from the context window.
    # Agents quote message ids in bodies routinely, so the accidental case needs no ill intent.
    #
    # Anchored to line-start with the exact spelling both producers emit, which keeps merged-buffer
    # recovery working and stops an id mentioned in a sentence from counting. Bodies that forge the
    # whole structural line are handled at render time — see `_neutralise_buffer_markers`.
    #
    # …AND THE ANCHOR ALONE WAS NOT ENOUGH, reported by a reviewer on another instance 2026-08-18.
    # The two halves named above are the anchored parser and render-time neutralisation, and the
    # note "neither is sufficient alone" was exactly right — but the neutralising half only ran on
    # the MERGED render path. A fresh SINGLE dispatch stores the sender's body verbatim, and this
    # scan then ran on it unconditionally, so a body with a line-leading `MessageId: <victim-id>`
    # minted a receipt for the claiming agent against a message it never read, and that message
    # vanished from its unread inbox. The anchor cannot save this: a sender can put the line at
    # column 0 as easily as anywhere else.
    #
    # Two changes close it, and the storage-side one is the load-bearing half (see
    # `dispatch_runs.py`: sender bodies are neutralised when the row is created, so no STORED body
    # carries a structural marker unless the service wrote it). This gate is the structural half:
    # only a body that IS a merged buffer can contain ids to recover, and `startswith(HEADER)` is
    # already the predicate `_append_pending_dispatch_body` trusts for exactly that question.
    if body.startswith(_MERGED_DISPATCH_HEADER):
        ids.extend(
            match.group(1).strip()
            for match in re.finditer(r"^MessageId:[ \t]*(\S+)[ \t]*$", body, re.MULTILINE)
        )
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


# v0.5.4: `_mark_dispatch_source_messages_read` arrived from the control plane. It is the one WRITE in
# this module, and it is here because `_dispatch_source_message_ids` — which decides what to mark — is
# already here: separating the question from the single act that consumes its answer would put a
# two-function pair in two files for no gain. It still takes `db` and commits nothing.

async def _mark_dispatch_source_messages_read(db, row, agent_id: str, read_at: str) -> int:
    """Returns HOW MANY RECEIPTS WERE ACTUALLY WRITTEN — not how many messages were considered.

    It used to return `len(existing_ids)`, the count of source messages that still exist, which is
    the same number whether every receipt was new or every one was already there. Both callers
    report that number to a human: the claim path logs "Marked N dispatched source messages read",
    and `/contracts/hygiene/repair-read-receipts` answers `{"repaired": N}`. An operator who runs the
    repair twice saw the same N both times and had no way to tell a real backlog from a no-op — the
    count said work had been done that had not.

    `INSERT OR IGNORE` makes the distinction available for free: rowcount is 1 on an insert and 0 on
    an ignore. Nothing about WHICH receipts are written changes.
    """
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
    inserted = 0
    for message_id in message_ids:
        if message_id not in existing_ids:
            continue
        result = await db.execute(
            "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (message_id, agent_id, read_at),
        )
        inserted += int(getattr(result, "rowcount", 0) or 0)
    return inserted

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
