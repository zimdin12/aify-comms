"""Two bridges claiming the same agent in the same mode: refusing the second.

RELOCATED from `service/api_core/registration_gates.py` in v0.5.4, byte-identical. A pair that calls
only each other — the gate, and the freshness test that decides whether the incumbent still counts.
Nothing outside the module called the freshness test; only the registration route calls the gate.

SAME-MODE IS THE CASE THAT IS *NOT* ALLOWED TO SUPERSEDE. A registration in the OTHER mode is a
deliberate handover and the newcomer wins. A registration in the SAME mode is two processes both
believing they drive one agent, which is the one-driver invariant this codebase is built around — so
the second is refused rather than allowed to take over silently.

FRESHNESS IS WHAT MAKES THE REFUSAL SAFE. A dead bridge that never deregistered must not lock an
agent out forever, so the incumbent only counts while its heartbeat is inside the lease. Refusing on a
stale row would turn a crash into a permanent outage, which is why the two travel together: the gate
is only correct in company with the test that bounds it.
"""
from __future__ import annotations

import time

from fastapi import HTTPException

from service.api_core.resume_command import _resume_command_for
from service.api_core.runtime import _normalize_session_mode
from service.api_core.serialization import _normalize_machine_id
from service.api_core.settings import _load_settings
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now


async def _fresh_same_mode_bridge_conflict(
    db,
    *,
    agent_id: str,
    machine_id: str,
    new_bridge_id: str,
    session_mode: str,
    lease_seconds: int,
):
    """Return a LIVE same-mode bridge that a new registration would race.

    Phase 4 race guard (2026-05-31, operator-chosen hard-error model). A fresh,
    non-superseded bridge for the SAME (agent, machine) and the SAME resident
    session_mode, owned by a DIFFERENT bridge_id, means a second live wrapper is
    about to claim an identity already being driven — silently superseding it
    would kill the first wrapper's work. We surface that as a 409 (unless the
    caller passes force=true to take over deliberately).

    Scope is RESIDENT-only: managed bridges intentionally use latest-launch-wins
    to reap zombie wrappers, and the visible-TUI managed model runs a legitimate
    sidecar + wrapper-child pair concurrently — neither should trip this guard.
    Returns the conflicting bridge row, or None when there is no live conflict.
    """
    if _normalize_session_mode(session_mode or "") != "resident":
        return None
    normalized_machine = _normalize_machine_id(machine_id)
    cutoff = max(15, int(lease_seconds or 150))
    cursor = await db.execute(
        """
        SELECT id, last_seen, bridge_kind, session_handle
        FROM bridge_instances
        WHERE agent_id = ?
          AND machine_id = ?
          AND id != ?
          AND session_mode = 'resident'
          AND COALESCE(bridge_kind, '') != 'channel-sidecar'
          AND COALESCE(superseded_by, '') = ''
        ORDER BY last_seen DESC
        """,
        (agent_id, normalized_machine, str(new_bridge_id or "").strip()),
    )
    for bridge in await cursor.fetchall():
        seen_s = _iso_to_epoch((bridge["last_seen"] or ""))
        if seen_s and (time.time() - seen_s) <= cutoff:
            return bridge
    return None


async def _enforce_same_mode_bridge_gate(
    db, req, row, bridge_id, normalized_runtime, normalized_session_mode, logger
) -> None:
    """Refuse a registration whose mode is still held by a LIVE bridge — unless it is a relaunch.

    v0.5.4, extracted verbatim out of the 684-line `register_agent`. This is the decision
    `_fresh_same_mode_bridge_conflict` above exists to inform, so it belongs beside it: that predicate
    answers "is there a conflicting bridge", and this answers "then what", including the takeover
    carve-out and the 409 the operator actually reads. Splitting the question from the answer across two
    modules is what made this 65 lines of a route body instead of a named gate.

    `logger` IS A PARAMETER ON PURPOSE. Giving this module its own logger would change the logger NAME
    on the takeover record from `aify_comms.routers.agents.identity` to this module's — same message,
    different field, and observable to anyone filtering logs. This series does not change behaviour, so
    the caller keeps supplying its logger.

    PLAIN POSITIONAL PARAMETERS, and NOT because that is the nicer signature — seven positional
    arguments is worse to read than seven keyword-only ones. The extract-method gate's dialect is
    deliberately narrow on the reviewer's recommendation (no defaults, no *args/**kwargs, no
    positional-only or keyword-only parameters) and its docstring says outright that it "will
    false-reject some safe shapes." A keyword-only version of this signature was refused. The proof is
    worth more than the ergonomics, so the signature fits the dialect rather than the gate being widened
    to fit the signature.

    THE SQL LITERAL BELOW IS INDENTED FOUR SPACES DEEPER THAN ITS SURROUNDING CODE. That is not sloppy
    and must not be tidied. The block was dedented by one level on the way out of `register_agent`, but
    the interior lines of a triple-quoted string are DATA, not indentation — dedenting them changes the
    constant's value. The first attempt did exactly that and the inline-back proof failed on the
    resulting AST, which is how this was caught rather than shipped. `tokenize` identifies which lines
    are string interior; only the code lines moved.

    Raises HTTPException(409) — the established pattern for gates in this module. Returns None when the
    registration may proceed. Writes (the supersede UPDATE) are left uncommitted for the caller's
    transaction, per the DB-leaf rule in this module's docstring.
    """
    if row and bridge_id and not bool(getattr(req, "force", False)):
        settings_for_guard = await _load_settings(db)
        conflict = await _fresh_same_mode_bridge_conflict(
            db,
            agent_id=req.agentId,
            machine_id=req.machineId or "",
            new_bridge_id=bridge_id,
            session_mode=normalized_session_mode,
            lease_seconds=settings_for_guard.get("resident_lease_seconds", 150),
        )
        # SAME-SESSION RELAUNCH TAKEOVER (2026-06-13, the sc-manager stale+deaf
        # incident): a quick close-and-relaunch of a resident wrapper ALWAYS hit this
        # guard — kill-prior killed the old session seconds before the new bridge
        # booted, but the dead bridge's heartbeat lease (150s) made it look like a
        # "LIVE owner", the auto-register was 409'd (never retried), and the session
        # ran for hours with no binding file: sidecar mute (no inbound delivery, no
        # sidecar liveness) + runtime_state pinned to the dead bridge → `stale`.
        # When the incoming registration RESUMES the very session handle the
        # conflicting bridge holds, it is a relaunch of that same native session —
        # one session can only have one living process — so take over: supersede the
        # old bridge and proceed. A conflict with a DIFFERENT (or unknown) session
        # stays hard-409 (the real Phase-4 duplicate-identity protection).
        incoming_handle = str(req.sessionHandle or "").strip()
        conflict_handle = str(
            (conflict["session_handle"] if conflict and "session_handle" in conflict.keys() else "") or ""
        ).strip()
        if conflict and incoming_handle and incoming_handle == conflict_handle:
            # IN-FLIGHT PROTECTION (the Phase-4 operator-chosen invariant stays): a
            # prior bridge actively driving a claimed/running run is genuinely-live
            # evidence — never silently supersede it; the hard 409 below stands and
            # the bridge-side retry waits it out. Only an IDLE same-session owner
            # (the killed-prior relaunch case) is taken over.
            in_flight = await (await db.execute(
                """
                    SELECT COUNT(*) FROM dispatch_runs
                    WHERE target_agent = ? AND status IN ('claimed', 'running')
                    """,
                (req.agentId,),
            )).fetchone()
            if not int(in_flight[0] or 0):
                await db.execute(
                    "UPDATE bridge_instances SET superseded_by = ?, superseded_at = ? WHERE id = ?",
                    (bridge_id, _now(), conflict["id"]),
                )
                logger.info(
                    "same-session relaunch takeover: agent=%s handle=%s superseded=%s by=%s",
                    req.agentId, incoming_handle, conflict["id"], bridge_id,
                )
                conflict = None
        if conflict:
            seen_s = _iso_to_epoch((conflict["last_seen"] or ""))
            ago = int(max(0, time.time() - seen_s)) if seen_s else 0
            resume_command = _resume_command_for(
                row["runtime"] or normalized_runtime,
                row["session_handle"] or "",
                req.agentId,
            )
            detail = (
                f"agent '{req.agentId}' already has a LIVE {normalized_session_mode} "
                f"bridge (seen {ago}s ago). Stop that instance first, or pass force=true "
                f"(AIFY_FORCE_REGISTER=1) to take over."
            )
            if resume_command:
                detail += f" To resume after taking over: {resume_command}"
            raise HTTPException(409, detail)
