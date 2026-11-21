"""Queue an automatic lifecycle start when dispatch has no managed backing to reuse.

Cold dispatch records the owning definition and why admission refused, if it did. Env decides
whether it can run the request against its current lifetime and operator hold. The caller owns
commit and rollback.
"""

from __future__ import annotations

from service.api_core.vocabulary import LAUNCHABLE_RUNTIMES

import json
import uuid
from typing import Any, Optional


from fastapi import HTTPException

from service.api_core.agent_sessions import _session_handle_live_owner
from service.api_core.channel_delivery import _has_live_worker_for, live_managed_worker, live_resident_session
from service.api_core.definition_start import (
    CHANGED_WHILE_STARTING,
    StartRefused,
    undefined_refusal,
    automatic_resume_handle,
    spawn_request_guard,
    start_binding,
)
from service.api_core.dispatch_text import COLDSTART_REFUSED_PREFIX
from service.api_core import agent_lifecycle_requests, lifecycle_delegation, agent_state_mirror as state_mirror
from service.api_core.managed_env import (
    _has_pending_or_booting_spawn_request,
    _select_online_environment_for_runtime,
)
from service.api_core.records import _environment_record_to_dict
from service.api_core.runtime import (
    _normalize_runtime,
    _runtime_capability_for_environment,
    _runtime_unlaunchable_reason,
)
from service.api_core.settings import _load_settings
from service.api_core.workspace import _workspace_for_environment
from service.clock import now as _now
from service.lifecycle_models import AUTOMATIC_START_INTENT


def _coldstart_refusal(warnings: Optional[list[str]], reason: str) -> bool:
    """Record WHY cold-start refused, then return False (the caller's expected falsey)."""
    if warnings is not None:
        warnings.append(f"{COLDSTART_REFUSED_PREFIX}{reason}")
    return False


def _why_no_environment_can_start(environments, runtime: str) -> str:
    """What an ONLINE environment said about why it cannot launch `runtime`, or "" if none said so.

    PURE: rows in, sentence out. It was written async and taking `db`, which made testing it require a
    second event loop beside the one the client holds -- the tell that it does no I/O and should not
    have been asking for a connection.

    Only online environments are consulted. An offline host's opinion about its wrappers is a stale
    reading, and quoting it would send an operator to fix a machine that is simply not running.

    ONE REASON, NOT A LIST. With several hosts the messages differ per machine, and pasting all of them
    into a single refusal buries the answer; the first online host that advertises the runtime and
    explains itself is the one to read.

    Returns "" rather than filler when nothing explained itself, so the caller keeps its own wording
    for the genuinely different case of "no environment advertises this runtime at all".
    """
    for environment in environments or []:
        if str(environment.get("status") or "").lower() != "online":
            continue
        reason = _runtime_unlaunchable_reason(environment, runtime)
        if reason:
            return f'no environment can start "{runtime}". {environment.get("id") or "a host"} says: {reason}'
    return ""


async def _coldstart_spawn_request_for_dispatch(
    db,
    agent_id: str,
    *,
    runtime: str,
    settings: dict[str, Any],
    requested_by: str,
    warnings: Optional[list[str]] = None,
    refusal: str = "",
) -> bool:
    """Queue one cold automatic start, from the host's exact managed definition.

    Check current backing first. Retain the existing environment and workspace admission and native
    handle collision warning; then reserve the start in the dispatch transaction. Env checks its
    durable operator hold before it prepares a launch. False means no new request was queued.
    """
    if refusal:
        return _coldstart_refusal(warnings, refusal)
    # A DEFINED AGENT STARTS FROM ITS DEFINITION (P0 C7), and a withdrawn one not at all (C6): its
    # harness, workspace and spec come from the revision recorded on the request, not from history.
    try:
        binding = await start_binding(db, agent_id)
    except StartRefused as refused:
        return _coldstart_refusal(warnings, str(refused))
    # STOP IS DURABLE (D9): only an explicit start brings an operator-stopped agent back. Its host refuses
    # such a start too; asking here keeps the send from queueing a spawn that can only fail.
    if binding is not None:
        owner = await lifecycle_delegation.owner_of(db, agent_id)
        if owner and lifecycle_delegation.stopped_by_operator(owner, agent_id, state_mirror.mirror.view(), state_mirror.clock()):
            return _coldstart_refusal(warnings, "stopped by the operator; start it explicitly to deliver")
    normalized_runtime = binding.runtime if binding else _normalize_runtime(runtime or "")
    if normalized_runtime not in LAUNCHABLE_RUNTIMES:
        return _coldstart_refusal(
            warnings, f"runtime {normalized_runtime or '(unset)'!r} is not cold-startable")

    # Resident-safety (2026-07-06): NEVER auto-cold-start a MANAGED worker for a
    # session_mode='resident' agent — regardless of whether its resident bridge is
    # fresh or disconnected. resident<->managed is OPERATOR-ONLY (manual ownership
    # model); an automatic switch is wrong in both states:
    #   * resident LIVE  → its own claude-channel.js sidecar claims the channel
    #     dispatch; a managed worker would be a DUPLICATE beside the resident
    #     (operator-reported "2 aicm-lc-managers": lc's reply spawned a managed twin).
    #   * resident DISCONNECTED → auto-switching to managed SPLITS delivery: the
    #     resident (on reconnect) still sends, but replies land on the managed twin,
    #     so "none come back to the actual agent" (operator-rejected). The dispatch
    #     should queue for the resident and deliver when it reconnects, not fork a
    #     managed identity behind the operator's back.
    # A deliberate Switch-to-managed flips session_mode to 'managed' BEFORE cold-start,
    # so this never blocks an intentional resident->managed transition.
    _agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if binding is None:
        if _agent_row is not None and str(_agent_row["session_mode"] or "").strip().lower() == "resident":
            return _coldstart_refusal(
                warnings,
                "this agent is RESIDENT — auto-cold-start is refused so a managed twin cannot be "
                "forked beside a live resident session. Switch it to managed first if that is intended")
    elif twin := await twin_refusal(db, _agent_row):
        return _coldstart_refusal(warnings, twin)
    if binding is None:
        return _coldstart_refusal(warnings, undefined_refusal(agent_id))
    # A LIVE WORKER IS NOT STARTED AGAIN (2026-10-27). Its host refuses a second worker beside a live one, and
    # settling that refused spawn ended the live session's row: developer's bridge stopped reporting while
    # its terminal and session stayed live. The same answer, given here, creates and ends nothing.
    if _agent_row is not None and await live_managed_worker(db, _agent_row):
        live = await (await db.execute(
            "SELECT id FROM terminal_sessions WHERE agent_id = ? AND status NOT IN ('stopped', 'failed') "
            "ORDER BY updated_at DESC LIMIT 1", (agent_id,))).fetchone()
        return _coldstart_refusal(
            warnings,
            f"its worker is still live{f' on terminal {live[0]}' if live else ''}, so a second one would be refused; "
            "the message waits for that worker's comms bridge to report again")

    # Don't pile up duplicate cold-starts — a queued/claimed/recently-running spawn_request
    # is already a (possibly mid-boot) backing for this agent. Bug D fix (2026-07-02): the
    # live repro created a duplicate 41s after the first while the worker was still booting,
    # and the duplicate's kill-prior can murder the booting worker.
    existing = await _has_pending_or_booting_spawn_request(db, agent_id)
    if existing:
        return _coldstart_refusal(
            warnings,
            "a spawn for this agent is ALREADY IN FLIGHT (queued/claimed/starting/running) — "
            "waiting for it rather than starting a second one that could kill the booting worker. "
            "If it never becomes live, that spawn request is the thing to inspect")

    # Resolve environment/runtime/workspace from the agent's most-recent session
    # (any status). A previously-managed agent always leaves one behind.
    session = await (await db.execute(
        """
        SELECT *
        FROM agent_sessions
        WHERE agent_id = ?
        ORDER BY last_seen DESC
        LIMIT 1
        """,
        (agent_id,),
    )).fetchone()

    offline_seconds = settings.get("environment_offline_seconds", 90)
    environment = None
    fallback_workspace = ""

    # Prefer the agent's prior-session environment when it is still ONLINE and
    # still advertises the runtime — preserves workspace + spawn_spec continuity.
    if session and str(session["environment_id"] or "").strip():
        env_row = await (await db.execute(
            "SELECT * FROM environments WHERE id = ?", (str(session["environment_id"]).strip(),)
        )).fetchone()
        if env_row:
            candidate = _environment_record_to_dict(env_row, offline_seconds=offline_seconds)
            if (
                str(candidate.get("status") or "").lower() == "online"
                and _runtime_capability_for_environment(candidate, normalized_runtime)
                and candidate.get("machineId") == binding.machine_id
            ):
                environment = candidate
                fallback_workspace = session["workspace"] or ""
    # Phase 2 auto-bind: a managed agent with NO usable session env (never run,
    # or no env binding at all) gets bound to the freshest ONLINE environment
    # that advertises the runtime so an `available` agent can be woken on first
    # message — mirroring comms_spawn's env-omission auto-select. Without this
    # the send path rejects with "cannot start live work now" (operator-reported
    # coder bug). No online env supports the runtime → decline (caller
    # surfaces a clear "no environment available" rejection).
    #
    # NOTE: an agent whose SPECIFIC bound env is merely offline does NOT reach
    # this fallback via the send path — preflight (_managed_environment_unavailable_reason)
    # rejects it first, and that is deliberate: a managed agent's workspace lives
    # on its bound env's machine, so it should wait for that env to return rather
    # than silently migrating to a different machine where its workspace may not
    # exist. This fallback only fires when there is no usable bound env to wait for.
    if environment is None:
        # A defined agent runs on the machine that defines it, which is where its workspace is.
        environment = await _select_online_environment_for_runtime(
            db, normalized_runtime, offline_seconds=offline_seconds,
            machine_id=binding.machine_id,
        )
        if environment is None:
            # WHY, when a host has already said why. An environment that advertises the runtime and
            # reports it unlaunchable is skipped above -- correctly, since a spawn there would be
            # refused by the tier that runs launchers -- and without this the operator is told only
            # that nothing resolved.
            cursor = await db.execute("SELECT * FROM environments ORDER BY last_seen DESC")
            refusal = _why_no_environment_can_start(
                [_environment_record_to_dict(row, offline_seconds=offline_seconds)
                 for row in await cursor.fetchall()],
                normalized_runtime,
            )
            return _coldstart_refusal(
                warnings,
                refusal or "the environment bound to this agent could not be resolved")

    environment_id = str(environment.get("id") or "").strip()
    if not environment_id:
        return _coldstart_refusal(warnings, "the resolved environment has no id (corrupt row)")

    try:
        _workspace_for_environment(environment, binding.workspace, fallback_workspace)
    except HTTPException as outside:
        return _coldstart_refusal(warnings, f"its defined workspace cannot be used on {environment_id}: {outside.detail}")

    coldstart_session_handle = await automatic_resume_handle(db, agent_id, normalized_runtime, _agent_row)

    # G3 (2026-06-03): warn (do NOT block) when the handle we're about to bind is
    # already owned by a DIFFERENT live agent — two live agents must not share one
    # native session id (e.g. coder + lead on one codex thread). The
    # caller surfaces this via the returned warning; cold-start still proceeds.
    if coldstart_session_handle:
        _settings_g3 = settings if isinstance(settings, dict) else await _load_settings(db)
        _owner_g3 = await _session_handle_live_owner(
            db, coldstart_session_handle, exclude_agent_id=agent_id,
            lease_seconds=_settings_g3.get("resident_lease_seconds", 150),
        )
        if _owner_g3 and warnings is not None:
            warnings.append(
                f"session id '{coldstart_session_handle}' is already owned by live agent "
                f"'{_owner_g3['agentId']}' ({_owner_g3['sessionMode']}); two live agents "
                "should not share one native session."
            )

    return await _queue_automatic_start(db, agent_id, binding, requested_by, warnings)


async def _queue_automatic_start(db, agent_id, binding, requested_by, warnings):
    # Callers normally already wrote in this transaction. If they have not, acquire the write
    # lock before rechecking the legacy/lifecycle reservation and exact definition.
    if not db.in_transaction:
        await db.execute('BEGIN IMMEDIATE')
        if await _has_pending_or_booting_spawn_request(db, agent_id):
            return _coldstart_refusal(warnings, 'a start for this agent is ALREADY IN FLIGHT')
    guard, params = spawn_request_guard(agent_id, binding)
    held = await (await db.execute(f'SELECT * FROM agent_definitions WHERE agent_id = ? AND {guard}',
                                  (agent_id, *params))).fetchone()
    if held is None:
        return _coldstart_refusal(warnings, CHANGED_WHILE_STARTING)
    actor = requested_by or 'dispatch-coldstart'
    intent = json.dumps({'agentId': agent_id, 'action': 'start', 'requestedBy': actor,
                         'expectedLifetime': None, 'freshContext': False,
                         'startIntent': AUTOMATIC_START_INTENT}, sort_keys=True)
    try:
        await agent_lifecycle_requests.queue_lifecycle(
            db, f'auto-{uuid.uuid4().hex}', held, action='start', actor=actor,
            expected_lifetime=None, fresh_context=False, intent=intent, now=_now(),
            start_intent=AUTOMATIC_START_INTENT,
        )
    except HTTPException as conflict:
        if conflict.status_code != 409:
            raise
        reason = conflict.detail['message'] if isinstance(conflict.detail, dict) else conflict.detail
        return _coldstart_refusal(warnings, reason)
    return True


async def twin_refusal(db, agent_row) -> str:
    """Why a managed worker may not start beside this agent's live resident session, or "".

    A DEFINED agent's mode is its definition's, which `start_binding` has read as managed. Its row may say resident
    until a managed worker registers, or managed once it followed its definition (B3) while a resident session it
    did not see was live, so the twin question is asked of the process either way."""
    if agent_row is None:
        return ""
    resident = str(agent_row["session_mode"] or "").strip().lower() == "resident"
    if (await _has_live_worker_for(db, agent_row) if resident
            else await live_resident_session(db, agent_row["id"])):
        return ("a resident session of this agent is live, and a managed worker beside it would be a twin; "
                "stop the resident first")
    return ""


# _ensure_managed_pty_for_dispatch moved to service/api_core/managed_pty_for_dispatch.py in
# v0.5.4 — five modules import it and none of them is this one, so a file about starting
# dispatches was the wrong place to look for it.


ROLE_SEND_STARTS_NOTHING = (
    "a send to a role wakes only agents already running, and this one is not; the message is in its inbox"
)


def role_send_refusal(req) -> str:
    """A send to a role reaches every agent holding it, and one stray role send cold-started sixteen idle agents on
    2026-10-28. So a role send starts nobody: an idle holder keeps the message in its inbox, and a holder the send can
    reach without a new start is still woken."""
    return ROLE_SEND_STARTS_NOTHING if getattr(req, "toRole", None) else ""
