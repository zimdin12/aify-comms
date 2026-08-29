"""Making a worker EXIST so a queued dispatch can be claimed: ensure a managed PTY, or cold-start a
spawn request when there is nothing to launch into.

The subject seed the reviewer confirmed for this slice, and the last of the spawn/pty group. Its
closure is exactly these three functions — 394 lines — because the two things that used to hold it in
the carrier were moved out first: workspace resolution (`api_core/workspace.py`) and the
agent-to-terminal binding (`api_core/terminal_ownership.py`). Every remaining dependency was already a
leaf. That is what bottom-up buys: by the time the big function moves, there is nothing left to argue
about.

THE TWO FAILURE MODES THESE EXIST FOR, both reproduced live, both worth keeping in one place:

`_ensure_managed_pty_for_dispatch` takes `for_session_id` because a managed RESTART creates a new
spawn and a new session, and the new spawn reaches `running` roughly two seconds BEFORE the old
worker's terminal is torn down. Unscoped, `_active_terminal_for_agent` returns the agent's
most-recently-seen session with a terminal — at that instant still the OLD one — so this function
concludes a PTY already exists and creates nothing. The restart then kills that terminal and the
agent is left with no worker at all.

`_coldstart_spawn_request_for_dispatch` covers the case where a managed agent has NO live
agent_sessions row: there is nothing to launch into, so the PTY path returns None and the dispatch
sits queued with nothing that will ever claim it. It creates a spawn_request through the same
mechanism as `create_spawn_request` so a bridge claims it, registers a session, and the
PATCH->running eager-spawn brings up the wrapper PTY.

`_coldstart_refusal` records WHY cold-start refused before returning the caller's expected falsey.
It is 5 lines and it is here because a bare `False` returned for five distinct causes is
indistinguishable in a log — the refusal reason is the only thing that makes the queued dispatch
explicable afterwards.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback — the caller owns the
transaction, which is what makes 394 lines of spawn logic movable at all.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional


from service.api_core.agent_sessions import _session_handle_live_owner
from service.api_core.dispatch_text import COLDSTART_REFUSED_PREFIX
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
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.workspace import _workspace_for_environment
from service.clock import now as _now


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
) -> bool:
    """Cold-start a managed worker on the send path.

    When a managed agent has no live agent_sessions row, _ensure_managed_pty_for_dispatch
    cannot build a PTY (it has nothing to launch into) and returns None — the dispatch
    then sits queued with nothing that will ever claim it (root cause G). This creates a
    spawn_request through the SAME mechanism as create_spawn_request so a bridge claims it,
    registers a session, and the PATCH->running eager-spawn brings up the wrapper PTY.

    Idempotent: returns False (creating nothing) when a claimable spawn_request
    (queued/claimed) already exists for the agent, or when no environment/runtime can be
    resolved. Returns True when a new spawn_request was inserted.

    G1 (2026-06-03): the inserted spawn_request now carries the agent's current
    native session_handle so the managed worker RESUMES the existing native session
    rather than starting fresh (resident->managed no longer loses the thread).

    G3 (2026-06-03): when `warnings` is provided and the bound handle is already
    owned by a DIFFERENT live agent, an advisory (non-blocking) warning string is
    appended for the caller to surface.
    """
    normalized_runtime = _normalize_runtime(runtime or "")
    if normalized_runtime not in {"claude-code", "codex", "hermes", "opencode", "pi"}:
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
    _agent_row = await (await db.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if _agent_row is not None and str(_agent_row["session_mode"] or "").strip().lower() == "resident":
        return _coldstart_refusal(
            warnings,
            "this agent is RESIDENT — auto-cold-start is refused so a managed twin cannot be "
            "forked beside a live resident session. Switch it to managed first if that is intended")

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
    prior_spec = None

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
            ):
                environment = candidate
                fallback_workspace = session["workspace"] or ""
                prior_spec_id = str(session["spawn_spec_id"] or "").strip()
                if prior_spec_id:
                    prior_spec = await (await db.execute(
                        "SELECT * FROM spawn_specs WHERE id = ?", (prior_spec_id,)
                    )).fetchone()

    # Phase 2 auto-bind: a managed agent with NO usable session env (never run,
    # or no env binding at all) gets bound to the freshest ONLINE environment
    # that advertises the runtime so an `available` agent can be woken on first
    # message — mirroring comms_spawn's env-omission auto-select. Without this
    # the send path rejects with "cannot start live work now" (operator-reported
    # sc-coder bug). No online env supports the runtime → decline (caller
    # surfaces a clear "no environment available" rejection).
    #
    # NOTE: an agent whose SPECIFIC bound env is merely offline does NOT reach
    # this fallback via the send path — preflight (_managed_environment_unavailable_reason)
    # rejects it first, and that is deliberate: a managed agent's workspace lives
    # on its bound env's machine, so it should wait for that env to return rather
    # than silently migrating to a different machine where its workspace may not
    # exist. This fallback only fires when there is no usable bound env to wait for.
    if environment is None:
        environment = await _select_online_environment_for_runtime(
            db, normalized_runtime, offline_seconds=offline_seconds
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

    workspace, workspace_root = _workspace_for_environment(environment, None, fallback_workspace)

    # G1 (2026-06-03): carry the agent's CURRENT native session handle into the
    # cold-start spawn_request so the managed worker RESUMES the existing native
    # session (codex thread / hermes gateway session / claude transcript) instead
    # of starting a fresh one. Without this the resident->managed switch (which
    # cold-starts via this helper) silently loses the live native session — the
    # dedicated restart path already carries session["session_handle"] for the
    # same reason. Sourced from the agents row (the durable pinned handle), with a
    # fallback to the prior session row's handle when the agent row has none.
    agent_row = await (await db.execute(
        "SELECT session_handle FROM agents WHERE id = ?", (agent_id,)
    )).fetchone()
    coldstart_session_handle = str(
        (agent_row["session_handle"] if agent_row else "")
        or (session["session_handle"] if session else "")
        or ""
    ).strip()

    # G3 (2026-06-03): warn (do NOT block) when the handle we're about to bind is
    # already owned by a DIFFERENT live agent — two live agents must not share one
    # native session id (e.g. lc-coder + lc-tech-lead on one codex thread). The
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

    now = _now()
    spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    await db.execute(
        """
        INSERT INTO spawn_specs (
            id, agent_id, environment_id, runtime, workspace, model, profile, mode,
            system_prompt, standing_instructions, env_vars, channel_ids, budget_policy,
            context_policy, restart_policy, metadata, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            spec_id,
            agent_id,
            environment_id,
            normalized_runtime,
            workspace,
            str(prior_spec["model"] or "") if prior_spec else "",
            str(prior_spec["profile"] or "") if prior_spec else "",
            "managed-warm",
            str(prior_spec["system_prompt"] or "") if prior_spec else "",
            str(prior_spec["standing_instructions"] or "") if prior_spec else "",
            str(prior_spec["env_vars"] or "{}") if prior_spec else "{}",
            str(prior_spec["channel_ids"] or "[]") if prior_spec else "[]",
            str(prior_spec["budget_policy"] or "{}") if prior_spec else "{}",
            str(prior_spec["context_policy"] or "{}") if prior_spec else "{}",
            str(prior_spec["restart_policy"] or "{}") if prior_spec else "{}",
            str(prior_spec["metadata"] or "{}") if prior_spec else "{}",
            now,
            now,
        ),
    )
    await db.execute(
        """
        INSERT INTO spawn_requests (
            id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
            workspace, workspace_root, initial_message, priority, subject, mode,
            resume_policy, status, session_handle, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            request_id,
            spec_id,
            requested_by or "dispatch-coldstart",
            environment_id,
            agent_id,
            "coder",
            agent_id,
            normalized_runtime,
            workspace,
            workspace_root,
            "",
            "normal",
            f"Cold-start for {agent_id}",
            "managed-warm",
            "native_first",
            "queued",
            coldstart_session_handle,
            now,
            now,
        ),
    )
    return True


# _ensure_managed_pty_for_dispatch moved to service/api_core/managed_pty_for_dispatch.py in
# v0.5.4 — five modules import it and none of them is this one, so a file about starting
# dispatches was the wrong place to look for it.
