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
from service.api_core.dispatch_text import _MERGED_DISPATCH_HEADER
from service.api_core.events import _append_terminal_event
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS
from service.api_core.live_process_probes import ACTIVE_RUN_BRIDGE_STALE_SECONDS

from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _dedupe_preserve, _json_loads_or
from service.api_core.settings import _load_settings
from service.clock import iso_to_epoch as _iso_to_epoch, now as _now
from service.env_status import environment_effective_status as _environment_effective_status
from service.models import DispatchClaimRequest


#: The longest a turn may hold delivery even while a live bridge keeps renewing it.
#:
#: A renewable lease with no ceiling is the permanent strand again wearing a better hat: a bridge
#: that heartbeats for ever would hold queued work for ever. Four hours is far clear of the longest
#: turn actually observed on this fleet (47 minutes, a review), so it bounds the pathological case
#: without touching a real one. It is deliberately NOT `TURN_BUSY_BACKSTOP_SECONDS`: that one bounds
#: an UNVERIFIED claim and wants to be short, this one bounds a verified one and wants to be long.
TURN_LEASE_ABSOLUTE_MAX_SECONDS = 4 * 60 * 60

#: How far ahead of us a bridge's `last_seen` may be and still count as a live heartbeat.
#:
#: NOT ZERO, deliberately. `aify-comms doctor`'s `env-bridge` check once reported every environment
#: dead because the CONTAINER clock ran 4.1 seconds ahead of the host, so every fresh heartbeat
#: looked future-dated -- a false RED produced by exactly the "reject anything in the future" rule
#: that looks obviously correct. The real defect a bound is needed for is a WILDLY wrong stamp: a
#: `last_seen` hours ahead satisfies `> now - stale` for ever, so a dead bridge would renew a lease
#: permanently. Two minutes separates ordinary skew from a stamp nothing legitimate produces.
BRIDGE_CLOCK_SKEW_TOLERANCE_SECONDS = 120








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
    # (`routers/agents/listen.py`: LEFT JOIN ... WHERE r.message_id IS NULL), so a receipt the agent
    # never earned SUPPRESSES that message from `comms_listen`. The same ids are also an exclusion
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
    # vanished from `comms_listen`. The anchor cannot save this: a sender can put the line at
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


async def _turn_lease_is_renewable(db, agent_id: str, bridge_id: str) -> bool:
    """Is something INDEPENDENTLY OBSERVABLE still claiming this turn?

    True only when `turn_bridge_id` names a bridge row that exists, belongs to THIS agent, and has
    heartbeated inside the same staleness window the dead-bridge sweep uses. Ownership is checked
    because matching on id alone would let any live bridge on the host renew any agent's turn.

    The hook marker and the empty owner are not bridges and can never be verified, which is exactly
    why they get the strict anchor instead of a renewable lease.
    """
    owner = str(bridge_id or "").strip()
    if not owner or owner == "user-prompt-submit":
        return False
    try:
        row = await (await db.execute(
            """
            SELECT 1 FROM bridge_instances
            WHERE id = ? AND COALESCE(agent_id, '') = ?
              -- SUPERSEDED IS NOT AN OWNER, and a superseded bridge can still be beating:
              -- supersession is a server-side fact and a replaced bridge is never told it lost, so
              -- it keeps heartbeating and re-stamping. Existence plus freshness alone would let the
              -- one thing that marks it as no longer the owner be the one thing this never reads.
              -- Same clause as the live-wrapper predicate in `agent_sessions.py`.
              AND COALESCE(superseded_by, '') = ''
              AND datetime(last_seen) > datetime('now', ?)
              -- ...and not WILDLY ahead of us. `> now - stale` is satisfied for ever by a stamp
              -- hours in the future, so a bad write would make a dead bridge renewable permanently.
              -- The tolerance is what keeps ordinary container/host skew from reading as bogus.
              AND datetime(last_seen) <= datetime('now', ?)
            """,
            (
                owner, agent_id,
                f"-{ACTIVE_RUN_BRIDGE_STALE_SECONDS} seconds",
                f"+{BRIDGE_CLOCK_SKEW_TOLERANCE_SECONDS} seconds",
            ),
        )).fetchone()
    except Exception:
        # Unreadable bridge state is not evidence of a live claim. Fall back to the strict anchor,
        # which is the safe direction: it releases work rather than stranding it.
        return False
    return row is not None


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

    THE CEILING MEASURES FROM WHEN THE TURN BEGAN, and the sentence that used to stand
    here is why it has to. It read: "A genuinely long turn is unaffected: the bridge turn
    detectors KEEP-FRESH re-stamp turn-start, so turn_updated_at keeps advancing for as
    long as real work runs. Only an ABANDONED flag ages out." The second half does not
    follow from the first. An abandoned flag ages out only if nothing is still stamping
    it — and the things that stamp it run on TIMERS, not on whether work is real.

    MEASURED 2026-08-30, on the operator's fleet. A managed hermes agent held every queued
    dispatch for 38 minutes. Its `pre_llm_call` hook POSTs /turn-start before every model
    call, so two reads 45s apart showed turn_updated_at advancing 18:23:11Z → 18:23:56Z:
    the age this function computed was permanently ~45s and the 1800s ceiling never fired.
    The dead-bridge sweep could not rescue it either — it deliberately skips the hook
    marker the agent was stamped with, to avoid wiping genuine hook-driven turns (#233).
    Both nets were down at once, each for its own good reason.

    The same shape was found and patched once before, narrowly: a superseded claude
    turn-detector re-stamping every 45s, closed in `turn_boundaries.py` by refusing that
    one poster. Naming posters does not scale, and the hook is deliberately exempt there.
    Anchoring to `turn_started_at` — written on the not-busy→busy transition and untouched
    for the rest of the turn — retires the class: no re-stamp can move it, so the bound
    holds regardless of who is beating.

    ONLY A VERIFIABLE RENEWAL MAY EXTEND A TURN. The distinction is WHO SAID SO, not how
    long ago, and it is what lets both failures be avoided at once:

      * `turn_bridge_id` names a bridge row that EXISTS, belongs to this agent, and is
        heartbeating -> the lease renews against `turn_updated_at`, exactly as it always
        did. Something independently observable is still claiming the turn, so a re-stamp
        is evidence. `TURN_LEASE_ABSOLUTE_MAX_SECONDS` still bounds it, because a
        renewable lease with no ceiling is the permanent strand again in a better hat.
      * Anything else — the hook marker, an empty owner, a bridge that is gone or stale ->
        the strict anchor, measured from `turn_started_at`. Nothing checkable is claiming
        this turn, so re-stamps prove nothing.

    That asymmetry is exactly why the hook defeated the old ceiling: it re-stamped the
    column the bound was measured against while naming an owner that is not a bridge at
    all, so there was never anything to check the claim against.

    AN EARLIER VERSION OF THIS DOCSTRING WAS WRONG TWICE and the corrections are the
    reason the shape above exists. It said a long turn was "still unaffected" — it was
    not; a hard start-anchored bound cut off a 47-minute review that really was running.
    And it said the status engine "has ALREADY stopped reporting `working`" past the
    ceiling, so releasing merely made the two agree. It has not: the `in_turn` clamp
    (`status_inputs.py:95`, `:519`) ages against `agent_status_state.last_event_at`, which
    the SAME hook refreshes. Delivery and status DISAGREE on that path, and moving status
    onto the same evidence is still open — see Row 8 in
    `docs/superpowers/plans/2026-08-30-v0.6.1-roadmap.md`.
    """
    try:
        row = await (await db.execute(
            "SELECT turn_busy, turn_updated_at, turn_started_at, turn_bridge_id "
            "FROM agent_turn_state WHERE agent_id = ?",
            (agent_id,),
        )).fetchone()
    except Exception:
        # Unreadable turn state must never block delivery — better to deliver.
        return False
    if not row or not int((row["turn_busy"] if "turn_busy" in row.keys() else 0) or 0):
        return False
    # Prefer the START anchor; fall back to the last-touch column for rows written before the anchor
    # existed. The fallback is deliberately NOT "release when unanchored": that would deliver
    # mid-turn to every legacy agent at once. Boot backfills the anchor, so the fallback is a
    # transitional path, not the steady state.
    _keys = row.keys()
    _started = _iso_to_epoch(str((row["turn_started_at"] if "turn_started_at" in _keys else "") or ""))
    _touched = _iso_to_epoch(str(row["turn_updated_at"] or ""))
    _owner = str((row["turn_bridge_id"] if "turn_bridge_id" in _keys else "") or "")

    if await _turn_lease_is_renewable(db, agent_id, _owner):
        # A verified claim: age against the last renewal, and bound the whole turn absolutely.
        if _started and (datetime.now(timezone.utc).timestamp() - _started) > TURN_LEASE_ABSOLUTE_MAX_SECONDS:
            return False
        seen = _touched or _started
    else:
        # Nothing checkable is claiming this. The start anchor is the bound; fall back to the
        # last-touch column only for rows written before the anchor existed.
        seen = _started or _touched
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
