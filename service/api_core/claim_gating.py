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

from service.api_core.capabilities import (
    _managed_via_wrapper_for_runtime,
    _row_capabilities,
)
from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES
from service.api_core.events import _append_terminal_event
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _dedupe_preserve, _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.api_core.terminal_text import _terminal_text_compact
from service.clock import iso_to_epoch as _iso_to_epoch, now as _now
from service.env_status import environment_effective_status as _environment_effective_status
from service.models import DispatchClaimRequest


async def _active_wrapper_terminal_id(db, agent_id: str, *, settings: dict[str, Any]) -> str:
    terminal = await _active_terminal_for_agent(db, agent_id, settings=settings)
    if not terminal:
        return ""
    try:
        return str(terminal["terminal_id"] or terminal["id"] or "").strip()
    except Exception:
        return str((terminal.get("terminal_id") or terminal.get("id") or "") if isinstance(terminal, dict) else "").strip()


def _hermes_terminal_still_resuming(text: str) -> bool:
    compact = _terminal_text_compact(text)
    if not compact:
        return False
    resume_idx = compact.rfind("resuming")
    if resume_idx < 0:
        return False
    ready_idx = compact.rfind("ready")
    return ready_idx < resume_idx


async def _active_wrapper_terminal_not_ready_reason(db, terminal_id: str, runtime: str) -> str:
    if _normalize_runtime(runtime or "") != "hermes" or not terminal_id:
        return ""
    row = await (await db.execute(
        "SELECT output FROM terminal_sessions WHERE id = ?",
        (terminal_id,),
    )).fetchone()
    if not row:
        return ""
    if _hermes_terminal_still_resuming(str(row["output"] or "")):
        return "Hermes wrapper Console is still resuming a saved session; waiting for ready/heal before claiming channel work."
    return ""


def _dispatch_source_message_ids(row) -> list[str]:
    ids = []
    primary = str((row["message_id"] if row and "message_id" in row.keys() else "") or "").strip()
    if primary:
        ids.append(primary)
    body = str((row["body"] if row and "body" in row.keys() else "") or "")
    ids.extend(match.group(1).strip() for match in re.finditer(r"\bMessage\s*Id:\s*([^\s]+)", body, re.IGNORECASE))
    return _dedupe_preserve([message_id for message_id in ids if message_id])


async def _bridge_claim_block_reason(
    db,
    *,
    bridge_id: str,
    agent_id: str,
    agent_row,
    execution_modes: Optional[list[str]] = None,
    bridge_kind_hint: str = "",
) -> Optional[dict[str, Any]]:
    """Return a blockedBy payload when an old stdio bridge should not claim work.

    `bridge_kind_hint` is the claimant-declared bridge kind from the request
    (DispatchClaimRequest.bridgeKind). Standalone channel sidecars
    (claude-channel.js / hermes-channel.js) declare "channel-sidecar"; it lets
    the wrapper-backed gate below distinguish them from a wrapper-PTY child.
    """
    if not bridge_id:
        return None

    cursor = await db.execute(
        "SELECT superseded_by, bridge_kind, terminal_id FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id)
    )
    row = await cursor.fetchone()
    if row and (row["superseded_by"] or "").strip():
        return {
            "reason": "bridge_superseded",
            "bridgeId": bridge_id,
            "agentId": agent_id,
            "hint": "This bridge has been replaced by a newer registration. Shut it down.",
        }

    runtime = _normalize_runtime((agent_row["runtime"] if agent_row else "") or "generic")
    if runtime not in {"codex", "opencode", "pi", "hermes"}:
        return None

    # Plan 6 follow-up (2026-05-26): wrapper-child bridges (the in-process
    # mcp/stdio/server.js that runs INSIDE a *-aify wrapper PTY) legitimately
    # have a different bridge_id from the environment bridge. They claim
    # channel-mode runs for managed-via-wrapper agents (see _CHANNEL_CLAIM_RUNTIMES
    # at line 290 and dispatch-execution.js supportedExecutionModes). Without
    # this carve-out, every wrapper-child claim hits "environment_bridge_not_current"
    # at line 1701 because the env bridge_id != the wrapper-child bridge_id —
    # and managed codex/hermes dispatches sit queued forever even when the
    # wrapper PTY is alive and its inner MCP server has registered. Detect a
    # wrapper-child claim by: (a) the request includes 'channel' in executionModes;
    # (b) the runtime is in _CHANNEL_CLAIM_RUNTIMES (managed-via-wrapper-eligible);
    # (c) the claimant bridge is registered for this agent (in bridge_instances).
    # Operator-observed 2026-05-26 with graph-tester-pi before Pi was moved
    # back to native RPC: inner MCP bridge
    # `2e8b7d91-...` registered fine, but its claims were silently rejected
    # against the env bridge `e1ef4cae-...`.
    supported_modes = {str(m or "").strip().lower() for m in (execution_modes or []) if str(m or "").strip()}
    bridge_kind = str((row["bridge_kind"] if row and "bridge_kind" in row.keys() else "") or "").strip()
    bridge_terminal_id = str((row["terminal_id"] if row and "terminal_id" in row.keys() else "") or "").strip()
    is_wrapper_child_claim = (
        "channel" in supported_modes
        and runtime in _CHANNEL_CLAIM_RUNTIMES
        and bridge_kind == "managed-wrapper-child"
    )
    # Standalone channel sidecar (Task 1.5/1.5b): the per-agent
    # claude-channel.js / hermes-channel.js process. It is NOT a wrapper-PTY
    # child and owns no visible Console terminal — it drives the agent's own
    # session (claude via MCP push; hermes via the pinned api_server daemon).
    # It declares bridgeKind="channel-sidecar" on the claim. Accept it on the
    # SAME basis claude's standalone sidecar is already accepted (claude
    # bypasses the wrapper-child gate purely by runtime — it is not in the
    # {codex, opencode, pi, hermes} set above). hermes IS in that set (it also
    # has a legacy wrapper-PTY path), so without this signal its standalone
    # sidecar would be wrongly rejected with managed_wrapper_child_required and
    # delivery would silently never happen.
    is_channel_sidecar_claim = (
        "channel" in supported_modes
        and runtime in _CHANNEL_CLAIM_RUNTIMES
        and str(bridge_kind_hint or "").strip().lower() == "channel-sidecar"
    )

    session_mode = _normalize_session_mode((agent_row["session_mode"] if agent_row else "") or "resident")
    runtime_state = _json_loads_or(agent_row["runtime_state"], {}) if agent_row else {}
    current_bridge_id = str(runtime_state.get("bridgeInstanceId") or "").strip()
    runtime_state_environment_id = str(runtime_state.get("environmentId") or "").strip()
    managed_environment_id = runtime_state_environment_id
    if session_mode == "managed" and not managed_environment_id:
        session_cursor = await db.execute(
            """
            SELECT environment_id
            FROM agent_sessions
            WHERE agent_id = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )
        session_row = await session_cursor.fetchone()
        managed_environment_id = str((session_row["environment_id"] if session_row else "") or "").strip()
    # RC1 (2026-06-03): a declared channel-sidecar (hermes-managed-host.js loop /
    # claude-channel.js) is a LEGITIMATELY distinct bridge id from the agent's
    # in-session MCP bridge (runtime_state.bridgeInstanceId). For RESIDENT hermes,
    # delivery is owned by that sidecar (the resident MAIN bridge no longer claims
    # resident hermes — see mcp/stdio/dispatch-execution.js). Without this carve-out
    # the one-current-bridge guard rejects the sidecar's claim with bridge_not_current
    # and the run sits queued forever with no valid claimer. The managed path already
    # exempts the sidecar (below, lines ~2336/2395); the resident path must too.
    if (session_mode != "managed" or not managed_environment_id) and current_bridge_id and current_bridge_id != bridge_id and not is_channel_sidecar_claim:
        return {
            "reason": "bridge_not_current",
            "bridgeId": bridge_id,
            "currentBridgeId": current_bridge_id,
            "agentId": agent_id,
            "hint": "This bridge is not the current stdio bridge for the agent. Restart or shut down stale runtime bridge/wrapper processes such as codex-aify, omp-aify, or pi-aify.",
        }

    if session_mode == "managed":
        settings = await _load_settings(db)
        # A standalone channel sidecar (claude-channel.js / hermes-channel.js)
        # is accepted directly: it owns no wrapper PTY, so the
        # managed-wrapper-child requirement and the PTY-terminal availability /
        # mismatch / readiness checks below do not apply to it. This is the
        # symmetric route — claude's standalone sidecar already bypasses these
        # by runtime (claude is not in the wrapper-backed set); hermes's
        # standalone sidecar bypasses them by declaring bridgeKind=channel-
        # sidecar (hermes ALSO has a legacy wrapper-PTY path, so it can't be
        # carved out by runtime alone). The environment online/bridge checks
        # still run below (the sidecar must not deliver into a dead env).
        wrapper_backed_channel_claim = (
            "channel" in supported_modes
            and runtime in {"codex", "hermes"}
            and _managed_via_wrapper_for_runtime(settings, runtime)
            and not is_channel_sidecar_claim
        )
        if (
            wrapper_backed_channel_claim
            and not is_wrapper_child_claim
        ):
            return {
                "reason": "managed_wrapper_child_required",
                "bridgeId": bridge_id,
                "agentId": agent_id,
                "runtime": runtime,
                "hint": (
                    f"Managed {runtime} is wrapper-backed. The environment bridge must start/reuse the "
                    "*-aify PTY and let that wrapper's child bridge claim channel dispatches."
                ),
            }
        if wrapper_backed_channel_claim and is_wrapper_child_claim:
            active_terminal_id = await _active_wrapper_terminal_id(db, agent_id, settings=settings)
            if not active_terminal_id:
                return {
                    "reason": "managed_wrapper_terminal_unavailable",
                    "bridgeId": bridge_id,
                    "agentId": agent_id,
                    "runtime": runtime,
                    "hint": "Managed wrapper-backed dispatch has no active wrapper PTY. Recover or restart the managed session, then retry.",
                }
            if bridge_terminal_id != active_terminal_id:
                return {
                    "reason": "managed_wrapper_terminal_mismatch",
                    "bridgeId": bridge_id,
                    "agentId": agent_id,
                    "runtime": runtime,
                    "bridgeTerminalId": bridge_terminal_id,
                    "currentTerminalId": active_terminal_id,
                    "hint": "This wrapper child belongs to an old terminal. Stop the stale wrapper and let the current managed PTY child claim the run.",
                }
            not_ready_reason = await _active_wrapper_terminal_not_ready_reason(db, active_terminal_id, runtime)
            if not_ready_reason:
                return {
                    "reason": "managed_wrapper_terminal_not_ready",
                    "bridgeId": bridge_id,
                    "agentId": agent_id,
                    "runtime": runtime,
                    "terminalId": active_terminal_id,
                    "hint": not_ready_reason,
                }
        environment_id = managed_environment_id
        if environment_id:
            env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
            env_row = await env_cursor.fetchone()
            current_environment_bridge = str((env_row["bridge_id"] if env_row else "") or "").strip()
            env_status = _environment_effective_status(
                env_row,
                offline_seconds=settings.get("environment_offline_seconds", 90),
            ) if env_row else "offline"
            if (
                current_environment_bridge
                and current_environment_bridge != bridge_id
                and not is_wrapper_child_claim
                and not is_channel_sidecar_claim
            ):
                return {
                    "reason": "environment_bridge_not_current",
                    "bridgeId": bridge_id,
                    "currentBridgeId": current_environment_bridge,
                    "environmentId": environment_id,
                    "agentId": agent_id,
                    "hint": "This managed agent belongs to an environment whose current bridge is different. Restart or kill the stale aify-comms bridge, then recover/restart the agent from Sessions.",
                }
            if env_status and env_status not in {"online", "degraded"}:
                return {
                    "reason": "environment_not_online",
                    "bridgeId": bridge_id,
                    "environmentId": environment_id,
                    "environmentStatus": env_status,
                    "agentId": agent_id,
                    "hint": "The managed agent's environment is not online. Start the environment bridge or assign the agent to another online environment.",
                }

    return None


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
