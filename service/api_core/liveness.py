"""Liveness: how long before something counts as dead, and the predicates that decide. Leaf module.

THE OLDEST OUTSTANDING ITEM IN THE SERIES. docs/ROADMAP.md has carried "Post-v0.5 — the consolidation
the borrows are waiting for" since v0.5.0, reviewer-ordered as **liveness family first**. This is it.

TEN predicates and the FOUR staleness thresholds they apply, which until now lived up to 4,900 lines
apart in the carrier — thresholds declared near the top, readers scattered through the middle, so
answering "how long until a bridge is considered stale" meant reading two distant parts of a
6,500-line module.

(That sentence said "Seven predicates and the three staleness thresholds" until slice 10 added three
more of each and made it false. Counts in prose go stale on the next commit; this one is now stated
without line numbers for the same reason, since those moved too.)

Owning the thresholds AND the predicates together is the point. A staleness constant separated from
the predicate that applies it is how the two drift, and drift here means an agent that reads live on
one code path and dead on another — the class behind the status-flap and stranded-claim bugs.

DB ACCESS: every predicate takes `db` explicitly and issues reads only. None opens a connection,
commits, or rolls back, so each joins whatever transaction its caller already has. That is what makes
them movable under the reviewer's rule for DB-touching helpers rather than pure ones.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from service.api_core.terminal_status import TERMINAL_LIVE_FILTER_SQL
from service.api_core.runtime import _normalize_launch_mode, _normalize_runtime, _normalize_session_mode
from service.api_core.terminal_text import _terminal_prompt_hint_from_raw
from service.api_core.vocabulary import LAUNCHABLE_RUNTIMES as _LAUNCHABLE_RUNTIMES
from service.api_core.settings import _load_settings
from service.api_core.capabilities import (
    _has_codex_live_app_server,
    _has_hermes_gateway_url,
    _row_capabilities,
)
from service.api_core.serialization import _json_loads_or
from service.clock import iso_to_epoch as _iso_to_epoch
# The six process probes and their two staleness constants left for `live_process_probes.py` in
# v0.5.4. This module is their only reader and now their CALLER — the direction the split made
# explicit rather than created.
from service.api_core.live_process_probes import (
    _agent_has_live_terminal,
    _has_live_channel_sidecar,
    _resident_bridge_is_fresh,
)


CONSOLE_WORKING_LEASE_SECONDS = 20
async def _claimer_lease_row(db, agent_id: str):
    """WS5 Task 5.1: fetch the agent's single claimer-lease row (or None when no
    lease has EVER been recorded). Returns the raw row so callers can distinguish
    'no lease ever' (fall back to the sidecar/bridge check — lazy-claim contract)
    from 'lease present' (the lease is authoritative)."""
    if db is None:
        return None
    try:
        cursor = await db.execute(
            "SELECT agent_id, bridge_id, state, updated_at FROM claimer_leases WHERE agent_id = ?",
            (agent_id,),
        )
        return await cursor.fetchone()
    except Exception:
        return None


async def _console_working_lease_fresh(db, agent_id: str) -> bool:
    """True when the agent's console-working spinner lease (agent_console_signal.working_at)
    is within CONSOLE_WORKING_LEASE_SECONDS. Both StatusInputs builders OR this (gated on a
    live worker) into in_turn so the spinner-driven `working` is identical on the served
    byproduct path AND the WS-push path (_gather_status_inputs) — without this, /console-working
    pushes `online` while the next poll serves `working` (push/poll flicker)."""
    if db is None:
        return False
    try:
        row = await (await db.execute(
            "SELECT working_at FROM agent_console_signal WHERE agent_id = ?", (agent_id,)
        )).fetchone()
    except Exception:
        return False
    if not row:
        return False
    seen = _iso_to_epoch(str((row["working_at"] if "working_at" in row.keys() else "") or "").strip())
    return bool(seen and datetime.now(timezone.utc).timestamp() - seen <= CONSOLE_WORKING_LEASE_SECONDS)

# ---------------------------------------------------------------------------------------------------
# v0.5.4 slice 10 completes the family. ROADMAP's post-v0.5 item named six predicates; four came in
# slice 6 and these two are the rest that layer 0 can reach. `_agent_liveness` is NOT here: it calls
# other carrier helpers, so it is layer 1+ and moving it is a separate decision, not an omission.
# `CLAIMER_LEASE_STALE_SECONDS` came with its only reader.
# ---------------------------------------------------------------------------------------------------

CLAIMER_LEASE_STALE_SECONDS = 240


async def _has_live_claimer_lease(db, agent_id: str) -> bool:
    """WS5 Task 5.1 (2026-06-02): True when the agent has a currently-LIVE
    delivery-loop claimer lease.

    A lease is the POSITIVE "a loop is a live claimer RIGHT NOW" signal the
    delivery loop POSTs on becoming ready (`claimer-acquire`) and clears on
    teardown (`claimer-release`). This is the disambiguator that unblocks the
    Task 5.1b deaf-target fail-fast: unlike the channel-sidecar heartbeat (which
    a not-yet-polled healthy loop has not written yet), a lease is set the moment
    the loop is ready and cleared the moment it exits.

    True ONLY when the lease state is 'acquired' AND its last refresh is within
    CLAIMER_LEASE_STALE_SECONDS (backstop for a missed release after a crash).
    A 'released' lease ⇒ False IMMEDIATELY (no staleness wait). No lease row ⇒
    False (caller must treat absence-of-lease as 'fall back to the sidecar check',
    NOT as deaf — see `_has_recorded_claimer_lease`).
    """
    row = await _claimer_lease_row(db, agent_id)
    if not row:
        return False
    if str(row["state"] or "").strip().lower() != "acquired":
        return False
    updated = _iso_to_epoch(str(row["updated_at"] or ""))
    if not updated:
        return False
    age = datetime.now(timezone.utc).timestamp() - updated
    return age <= CLAIMER_LEASE_STALE_SECONDS


async def _has_recorded_claimer_lease(db, agent_id: str) -> bool:
    """WS5 Task 5.1: True when a lease has EVER been recorded for this agent
    (acquired OR released). Used to decide whether the lease is AUTHORITATIVE
    (so a released/stale lease ⇒ deaf) vs whether to fall back to the
    sidecar/bridge-freshness check (no lease ever ⇒ pre-existing/older loop or a
    lazy claimer that has not polled — must NOT be treated as deaf)."""
    return await _claimer_lease_row(db, agent_id) is not None


# v0.5.4: `TURN_BUSY_BACKSTOP_SECONDS` arrived from the control plane, as a NEUTRAL owner rather than a
# follower — four carrier readers remain (three in `_compute_live_status_cache`, one in
# `_gather_status_inputs`), so no single consumer owns it.
#
# It belongs with the liveness thresholds because it IS one, and because the invariant attached to it is
# cross-module: it must equal the status engine's `in_turn` clamp. When those disagreed, queued work
# stranded and agents went permanently DEAF — a turn_busy delivery gate that never expired. Two readers
# agreeing is only meaningful while there is exactly one declaration, which is why this is a move and
# never a copy.

TURN_BUSY_BACKSTOP_SECONDS = 30 * 60


# v0.5.4: moved out of the control plane with `_has_live_worker_for`, its reader there. It is READ ON BOTH
# SIDES — `_compute_live_status_cache` still uses it — so this is a deliberate owner chosen by subject
# rather than by direction, and the control plane now imports it.
#
# IT SITS BESIDE `LIVE_SESSION_STATUSES` (imported above from api_core/tuning.py) ON PURPOSE, and the
# two are NOT the same set. That one is the WIDER session-row liveness set the reconcilers use; this one is
# the narrower agent-status-engine gate, which treats attached/active/idle as worker DETAIL rather than as
# session-live. They were 3,000 lines apart and the distinction was recorded only in a comment beside the
# other one. Collapsing them would change the agent-status engine.
_LIVE_SESSION_STATUSES = {"starting", "running", "recovering", "restarting", "cli-takeover"}


def _agent_wake_mode(row) -> str:
    runtime = _normalize_runtime((row["runtime"] if row else "") or "generic")
    session_mode = _normalize_session_mode((row["session_mode"] if row else "") or "resident")
    session_handle = str((row["session_handle"] if row else "") or "").strip()
    capabilities = _row_capabilities(row) if row else []
    runtime_config = _json_loads_or(row["runtime_config"], {}) if row else {}

    if _normalize_launch_mode(row["launch_mode"]) == "none":
        return "disabled"
    if session_mode == "managed" and "managed-run" in capabilities:
        return "managed-worker"
    if session_mode == "resident" and runtime == "claude-code" and "resident-run" in capabilities:
        return "claude-live"
    if session_mode == "resident" and runtime == "codex" and "resident-run" in capabilities and session_handle and _has_codex_live_app_server(runtime_config):
        return "codex-live"
    if session_mode == "resident" and runtime == "codex" and "resident-run" in capabilities and session_handle:
        return "codex-thread-resume"
    # Plan 4 Task 17: resident hermes uses gateway path (hermes-live) — the
    # bridge captures gatewayUrl via discoverSessionId after hermes-aify starts,
    # so resident hermes wake-mode is always gateway-channel. The legacy
    # hermes-session-resume mode (spawn fresh hermes with provider config) is
    # dead code post Plan 4 Task 7; gateway is the single source.
    if session_mode == "resident" and runtime == "hermes" and "resident-run" in capabilities and _has_hermes_gateway_url(runtime_config):
        return "hermes-live"
    if session_mode == "resident" and runtime in {"opencode", "pi"}:
        return "presence-only"
    if session_mode == "resident" and runtime == "codex" and not session_handle:
        return "codex-missing-handle"
    if session_mode == "resident" and runtime == "hermes" and not _has_hermes_gateway_url(runtime_config):
        return "hermes-missing-handle"
    if session_mode == "resident" and runtime == "opencode" and not session_handle:
        return "opencode-missing-handle"
    if session_mode == "resident" and runtime == "pi" and not session_handle:
        return "pi-missing-handle"
    if session_mode == "resident" and runtime == "claude-code":
        return "claude-needs-channel"
    return "message-only"


async def _agent_liveness(db, agent_id: str, *, agent_row=None) -> dict[str, bool]:
    """ONE liveness predicate computed from terminal_sessions + bridge_instances.

    Returns {worker_live, console_live, resident_bridge_fresh, sidecar_live}
    using the EXACT lease/window the existing helpers use (resident_lease_seconds
    default 150; the channel-sidecar stale window). Used by
    _compute_session_display_status so the derived session badge reads the same
    live truth as the agent dot.
    """
    agent_id = str(agent_id or "").strip()
    out = {
        "worker_live": False,
        "console_live": False,
        "resident_bridge_fresh": False,
        "sidecar_live": False,
    }
    if db is None or not agent_id:
        return out
    if agent_row is None:
        try:
            agent_row = await (
                await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
            ).fetchone()
        except Exception:
            agent_row = None
    # console/worker truth: a live, non-synth terminal_sessions row.
    out["console_live"] = await _agent_has_live_terminal(db, agent_id)
    out["worker_live"] = out["console_live"]
    # channel-sidecar deliverability (claude-channel.js / hermes delivery loop).
    out["sidecar_live"] = await _has_live_channel_sidecar(db, agent_id)
    # resident bridge freshness — only meaningful for a resident agent. Reuse the
    # exact helper + lease the status engine uses.
    if agent_row is not None:
        try:
            settings = await _load_settings(db)
            lease = int(settings.get("resident_lease_seconds", 150) or 150)
        except Exception:
            lease = 150
        try:
            out["resident_bridge_fresh"] = await _resident_bridge_is_fresh(
                db, agent_row, lease_seconds=lease
            )
        except Exception:
            out["resident_bridge_fresh"] = False
    return out

# Two status INPUTS that were left in the control plane, moved here in v0.5.4. Both answer a
# question about one agent from the database, which is what every function above them does:
# `_agent_awaiting_input` reads the live console, `_agent_config_defect` reads the identity. The
# second calls `_agent_wake_mode` declared above, so this is where its closure already was.
async def _agent_awaiting_input(db, agent_id: str) -> bool:
    """WS-5 (2026-06-17): True when the agent's live console tail looks like it is
    awaiting operator input/a decision (the `_terminal_awaiting_input_hint` signal).

    This is the engine input that makes `blocked` reachable under status_engine=new:
    derive() returns `blocked` for `in_turn AND live AND awaiting_input`. Callers gate
    the call on in_turn (a turn must be in flight for `blocked` to apply), so the
    terminal read happens only for the few agents currently mid-turn. Both StatusInputs
    build sites (_gather_status_inputs and the _compute_live_status_cache byproduct)
    call THIS helper so they derive the same value (the byproduct-parity promise)."""
    if db is None:
        return False
    try:
        row = await (await db.execute(
            f"""
            SELECT output, cols, runtime FROM terminal_sessions
            WHERE agent_id = ?
              AND status IN {TERMINAL_LIVE_FILTER_SQL}
              AND id NOT LIKE 'vterm_%'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (agent_id,),
        )).fetchone()
    except Exception:
        return False
    if not row:
        return False
    # The screen patterns below model Claude Code's interactive permission,
    # resume, and compaction prompts. Hermes/Codex/Pi terminal output includes
    # the model's own prose; phrases such as "which option" or "say the word"
    # there are ordinary output, not proof that the harness is waiting for an
    # operator. Their controllers report turn state through native events.
    if _normalize_runtime(str(row["runtime"] or "")) != "claude-code":
        return False
    keys = row.keys()
    return bool(_terminal_prompt_hint_from_raw(
        f"agent:{agent_id}",
        row["output"] if "output" in keys else "",
        row["cols"] if "cols" in keys else 0,
    ))


async def _agent_config_defect(db, agent_row, mode: str, *, missing_handle: bool = False) -> str:
    """Why this identity can NEVER be started, or "" if it can.

    Operator-requested 2026-08-03. Both fallthroughs this feeds used to report a state that
    quietly promises recovery: a managed identity with nothing to spawn from reported
    `available`, which tells the operator "just send to it and it will cold-start", and a
    resident with no wake handle reported `offline`, which reads as "not here right now".
    Both are false, in the direction that costs the most — the operator hunts a delivery bug
    that does not exist. This returns the DEFECT so status can say so instead.

    Deliberately narrow: only conditions under which starting is structurally impossible.
    A status that cried misconfigured on a recoverable agent would be worse than the promise it
    replaces — and the first cut of this DID. It also flagged a managed agent with no spawn spec,
    which is wrong: the cold-start path synthesises a spawn request from the environment, so a
    spec-less agent starts fine. An existing parity test caught it. What remains are the two
    conditions that no start path can route around: an unlaunchable runtime, and a resident with
    no wake handle.
    """
    if mode != "managed":
        if missing_handle:
            return f"no usable wake handle (wakeMode={_agent_wake_mode(agent_row) or 'unknown'})"
        return ""
    runtime = _normalize_runtime(agent_row["runtime"] or "") if "_normalize_runtime" in globals() else str(agent_row["runtime"] or "").strip().lower()
    if runtime not in _LAUNCHABLE_RUNTIMES:
        return f"runtime {runtime or '(unset)'!r} cannot be launched — no adapter can start it"
    return ""
