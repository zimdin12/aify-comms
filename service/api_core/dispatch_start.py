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

from fastapi import HTTPException

from service.api_core.agent_sessions import _session_handle_live_owner
from service.api_core.capabilities import (
    _default_console_command,
    _environment_supports_terminal,
    _managed_via_wrapper_for_runtime,
)
from service.api_core.channel_delivery import (
    _CHANNEL_MANAGED_RUNTIMES,
    _insert_messages_via_console,
)
from service.api_core.claim_gating import _turn_busy_holds_delivery
from service.api_core.dispatch_hint import _dispatch_fix_hint
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.dispatch_text import COLDSTART_REFUSED_PREFIX, _coldstart_refusal_message
from service.api_core.events import (
    _append_terminal_control,
    _append_terminal_event,
)
from service.api_core.execution_mode import (
    _agent_execution_mode,
    _auto_return_resident_to_managed_if_possible,
)
from service.api_core.liveness import _has_live_managed_wrapper_child
from service.api_core.managed_env import (
    _has_pending_or_booting_spawn_request,
    _managed_environment_unavailable_reason,
    _select_online_environment_for_runtime,
)
from service.api_core.records import _environment_record_to_dict
from service.api_core.runtime import (
    _NATIVE_MANAGED_RUNTIMES,
    _normalize_runtime,
    _runtime_capability_for_environment,
)
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings, _managed_terminal_backing_enabled
from service.api_core.spawn_request_state import _has_claimable_spawn_request
from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.api_core.workspace import _workspace_for_environment
from service.clock import now as _now


def _coldstart_refusal(warnings: Optional[list[str]], reason: str) -> bool:
    """Record WHY cold-start refused, then return False (the caller's expected falsey)."""
    if warnings is not None:
        warnings.append(f"{COLDSTART_REFUSED_PREFIX}{reason}")
    return False


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
            return _coldstart_refusal(
                warnings, f"the environment bound to this agent could not be resolved")

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


async def _ensure_managed_pty_for_dispatch(
    db, agent_id: str, *, runtime: str, settings: dict[str, Any], requested_by: str,
    for_session_id: str = "",
):
    """`for_session_id` scopes adoption to ONE session, and a restart is why it exists.

    REPRODUCED LIVE 2026-08-11 (restarttest-claude, first attempt, deterministic). A managed
    restart creates a new spawn and a new session, and the new spawn reaches `running` about two
    seconds BEFORE the old worker's terminal is torn down. `_active_terminal_for_agent` picks the
    agent's most-recently-seen session that has a terminal — which at that instant is still the OLD
    one — so this function said "there is already a PTY" and created nothing. The restart then
    killed that terminal, leaving:

        spawn_requests.status = 'running'   with NO terminal at all, ever
        agent status           = available
        the operator            looking at a session that says stopped

    `ef-manager` sat in exactly that state today after `graph-tech-lead` restarted it, and it took a
    cold-start send to recover. The v0.2.0 dead-terminal finalizer cannot clean it up either,
    because that keys on a terminal being DEAD and here none was ever created.

    Adoption across dispatches WITHIN a session is the whole point of this function and is
    unchanged. What is no longer allowed is adopting a terminal belonging to a DIFFERENT session —
    at a restart that terminal is, by definition, the one being destroyed.
    """
    wanted_session = str(for_session_id or "").strip()
    active = await _active_terminal_for_agent(db, agent_id, settings=settings)
    # `active` is a sqlite3.Row, NOT a dict — it has no `.get()`. The first version of this line
    # called `active.get("session_id")`, which raises AttributeError, and the caller's
    # `except Exception: pass` swallowed it whole. Worse than a plain crash: it only triggered when
    # an active terminal EXISTED, i.e. exactly the restart case this function was being fixed for,
    # and only when the outgoing terminal had not yet flipped to `stopped` — so the first live test
    # passed by luck and the second hung with no worker and no log line.
    if active and (not wanted_session or str(active["session_id"] or "") == wanted_session):
        return active
    normalized_runtime = _normalize_runtime(runtime or "")
    if normalized_runtime not in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        return None

    if wanted_session:
        # Use the caller's session outright. Re-deriving it by `last_seen` would land on the
        # outgoing session for the same two seconds that caused the bug above.
        session = await (await db.execute(
            "SELECT * FROM agent_sessions WHERE id = ? AND agent_id = ?", (wanted_session, agent_id)
        )).fetchone()
    else:
        session = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
              AND runtime = ?
              AND status IN ('running', 'recovering')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, normalized_runtime),
        )).fetchone()
    if not session:
        return None
    if normalized_runtime == "pi" and not str(session["session_handle"] or "").strip():
        return None

    env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (session["environment_id"],))).fetchone()
    if not env_row:
        return None
    environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
    if str(environment.get("status") or "").lower() != "online":
        return None
    if not _environment_supports_terminal(environment, session["runtime"]):
        return None

    workspace, _workspace_root = _workspace_for_environment(environment, None, session["workspace"] or "")
    terminal_id = f"term_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    bridge_id = str(environment.get("bridgeId") or "").strip()
    command = _default_console_command(session, workspace)
    now = _now()
    await db.execute(
        """
        INSERT INTO terminal_sessions (
            id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
            output, status, requested_by, created_at, updated_at, stopped_at, error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            terminal_id,
            session["id"],
            agent_id,
            session["environment_id"],
            bridge_id,
            session["runtime"],
            workspace,
            command,
            "",
            "starting",
            requested_by or "dashboard",
            now,
            now,
            None,
            "",
        ),
    )
    await _append_terminal_event(
        db,
        terminal_id,
        "managed_pty_start_requested",
        json.dumps({"requestedBy": requested_by or "dashboard", "sessionId": session["id"], "workspace": workspace, "command": command}),
    )
    await _append_terminal_control(
        db,
        terminal_id=terminal_id,
        environment_id=session["environment_id"],
        bridge_id=bridge_id,
        action="start",
        requested_by=requested_by or "dashboard",
        body=command,
    )
    # Publish the wrapper PTY's terminal_session id into agent.runtime_state.terminalId
    # so the dashboard's chooseSessionConsoleWidget (service/new_dashboard/app.js)
    # can render xterm against it. Without this the row is orphaned from the
    # runtime_state-driven rendering — only ensure_virtual_terminal publishes
    # virtualTerminalId (native RPC adapter path). Operator-reported 2026-05-24:
    # wrapper PTY existed but dashboard couldn't see it.
    agent_runtime_state_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if agent_runtime_state_row:
        _agent_rs = _json_loads_or(agent_runtime_state_row["runtime_state"], {})
        if not isinstance(_agent_rs, dict):
            _agent_rs = {}
        _agent_rs["terminalId"] = terminal_id
        await db.execute(
            "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
            (json.dumps(_agent_rs), now, agent_id),
        )

    await db.execute(
        """
        UPDATE agent_sessions
        SET owner_mode = 'managed',
            owner_bridge_id = ?,
            terminal_id = ?,
            terminal_status = 'starting',
            terminal_command = ?,
            terminal_workspace = ?,
            -- Spawning a NEW managed PTY for this session IS the "backing (re)started" event:
            -- promote a dead-state denorm back to running, else the row keeps the previous
            -- backing's 'stopped' and the Console label reads "Console stopped" for a live
            -- attached terminal forever (cms-manager, 2026-06-10 — the lazy auto-start-on-send
            -- bound a fresh PTY to a session left 'stopped' by the old backing's death; the
            -- display deriver deliberately never promotes, so the bind moment must).
            status = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed')
                          THEN 'running' ELSE status END,
            ended_at = CASE WHEN status IN ('stopped','ended','failed','lost','cancelled','completed')
                            THEN NULL ELSE ended_at END,
            last_seen = ?
        WHERE id = ?
        """,
        (bridge_id, terminal_id, command, workspace, now, session["id"]),
    )
    return await _active_terminal_for_agent(db, agent_id, settings=settings)


async def _launch_recipients_for_dispatch(channel_backing_failed, console_recipients, db, launchable_recipients, not_started, req, settings) -> None:
    """Make each launchable recipient EXIST, so a dispatch to it can be claimed instead of stranded.

    v0.5.4, extracted verbatim out of the 614-line `send_message` — 278 lines, the single largest thing in
    that route and most of the reason it was 614. It belongs in this module because this module's subject
    IS making a worker exist: it already owns `_ensure_managed_pty_for_dispatch` and
    `_coldstart_spawn_request_for_dispatch`, which are the two outcomes this loop spends most of its length
    deciding between.

    WHAT IT DECIDES per recipient, since that is not visible from the length: whether delivery goes through
    the channel sidecar, a wrapper child, a native managed session or a terminal; whether a worker already
    exists or must be cold-started; and when none of that is possible, which refusal to record so the
    sender learns why instead of watching a message sit queued forever.

    IT MUTATES ITS ARGUMENTS, and that is load-bearing rather than sloppy. `not_started`,
    `console_recipients` and `channel_backing_failed` are the CALLER's collections; this loop appends to
    them and the caller reads them afterwards. Rebinding any of them here instead of mutating would
    silently drop every recipient's outcome. The gate's live-out check is what proves the distinction held:
    no name bound inside the loop is read after it.

    THE SQL LITERALS SIT DEEPER than their surrounding code and must not be tidied — the interior lines of
    a triple-quoted string are DATA, and dedenting them changes the constant. `tokenize` identified them;
    only code lines moved.
    """
    for recipient_id, _execution_mode in launchable_recipients:
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))).fetchone()
        if row:
            row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
        if not row:
            continue
        runtime = _normalize_runtime(row["runtime"] or "generic")
        # Queue only waits behind real active/queued work. For an idle
        # terminal-backed target, it should still use the normal live
        # delivery path instead of creating an orphan dispatch queue.
        if bool(req.queueIfBusy):
            dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
            # Three signals of "currently busy":
            # 1. hasActiveRun: tracked dispatch_run in claimed/running
            # 2. queuedRuns > 0: prior queue already pending
            # 3. raw turn_busy=1: the agent is mid-turn even if
            #    no tracked dispatch_run is in flight. Operator-
            #    reported 2026-05-22: queue button sent immediately
            #    because require_reply=0 info messages auto-complete
            #    their dispatch_run on delivery → hasActiveRun goes
            #    false → queue fires the next message immediately
            #    while the assistant is still working. turn_busy
            #    is the harness-level signal that survives the
            #    auto-completion.
            # Raw signal, bounded ONLY by the anti-strand ceiling that also bounds the
            # claim gate — otherwise an abandoned turn_busy=1 makes every later send to
            # this agent queue behind a turn that already ended (and the claim gate then
            # never releases it). See _turn_busy_holds_delivery.
            try:
                is_turn_busy = await _turn_busy_holds_delivery(db, recipient_id)
            except Exception:
                is_turn_busy = False
            if (
                dispatch_state.get("hasActiveRun")
                or int(dispatch_state.get("queuedRuns") or 0) > 0
                or is_turn_busy
            ):
                continue
        execution_mode = str(_execution_mode or "").strip().lower()
        # Native-managed runtimes (codex/pi/opencode/hermes) — only
        # route through PTY-input when the operator opted into
        # the legacy via-console delivery mode AND managed-
        # terminal-backing is enabled. Default
        # (insert_messages_via_console=false) falls through and
        # the dispatch is claimed by the runtime's native RPC
        # adapter (createCodexController, createPiController,
        # opencode SDK) on its /dispatch/claim poll.
        if runtime in _NATIVE_MANAGED_RUNTIMES:
            # Wrapper-backed managed (operator-stated 2026-05-25): if
            # the runtime is in managed_via_wrapper, the wrapper PTY
            # MUST exist to claim — auto-spawn here so an available
            # agent gets its console started on first message arrival
            # (mirror of the operator's "send → console auto-starts
            # → status flips" model).
            if (
                execution_mode == "channel"
                and _managed_terminal_backing_enabled(settings)
                and _managed_via_wrapper_for_runtime(settings, runtime)
            ):
                console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                # FIX SET B2 (2026-06-03): for a wrapper-backed runtime a
                # leftover RESIDENT-mode terminal_session must NOT short-
                # circuit the managed coldstart. _active_terminal_for_agent /
                # _ensure_managed_pty_for_dispatch would re-attach a PTY to
                # that stale resident row (a resident `--resume`, NOT a
                # managed-warm worker), so no `managed-wrapper-child` bridge
                # ever registers and the 'channel' run is rejected
                # `managed_wrapper_child_required` → queued forever (the
                # lc-coder strand). Only a LIVE managed-wrapper-child proves a
                # managed worker is actually backing this agent; absent it,
                # drop the leftover terminal so the coldstart branch below
                # fires and a managed-warm worker is spawned.
                if console_terminal and not await _has_live_managed_wrapper_child(db, recipient_id):
                    console_terminal = None
                if not console_terminal:
                    console_terminal = await _ensure_managed_pty_for_dispatch(
                        db,
                        recipient_id,
                        runtime=runtime,
                        settings=settings,
                        requested_by=req.from_agent,
                    )
                    # The PTY re-attach above can still resolve a leftover
                    # resident row; re-gate on the live wrapper-child so a
                    # non-managed terminal never suppresses the coldstart.
                    if console_terminal and not await _has_live_managed_wrapper_child(db, recipient_id):
                        console_terminal = None
                if not console_terminal:
                    # Phase 2 lazy-autostart: no live wrapper PTY to
                    # back this agent (it was only registered, never
                    # run — the operator's `available` sc-coder case).
                    # Instead of rejecting, cold-start a spawn_request
                    # (auto-binding an online env when none is bound)
                    # so a bridge spawns the wrapper and claims this
                    # dispatch on its next poll. Only reject when no
                    # online environment can host the runtime.
                    # N8: collect WHY so a refusal reports its real cause, not the
                    # environment sentence that fired for all five of them.
                    _cs_reasons: list[str] = []
                    coldstarted = await _coldstart_spawn_request_for_dispatch(
                        db,
                        recipient_id,
                        runtime=runtime,
                        settings=settings,
                        requested_by=req.from_agent,
                        warnings=_cs_reasons,
                    )
                    if not coldstarted and not await _has_claimable_spawn_request(db, recipient_id):
                        not_started.append(
                            _dispatch_fix_hint(
                                recipient_id,
                                row,
                                _coldstart_refusal_message(_cs_reasons, runtime),
                            )
                        )
                        channel_backing_failed.add(recipient_id)
                # Do NOT add to console_recipients (that's the legacy
                # PTY-input delivery path). Wrapper child bridge claims
                # via /dispatch/claim once its in-process MCP boots.
                # Just let the dispatch sit queued; it'll get picked up
                # within a polling cycle (3s) once the wrapper is up.
                continue
            if (
                execution_mode == "managed"
                and _managed_terminal_backing_enabled(settings)
                and _insert_messages_via_console(settings)
                and runtime not in {"pi", "opencode"}
            ):
                console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                if not console_terminal:
                    console_terminal = await _ensure_managed_pty_for_dispatch(
                        db,
                        recipient_id,
                        runtime=runtime,
                        settings=settings,
                        requested_by=req.from_agent,
                    )
                if console_terminal:
                    console_recipients[recipient_id] = console_terminal
                    continue
            continue
        # Managed Claude PTY-input branch — only fires when the
        # operator has opted into the legacy via-console delivery
        # mode (insert_messages_via_console=true). Default-false
        # routing flows through the channel branch below: the run
        # is left launchable with execution_mode='channel' (see
        # _apply_channel_routing_to_claude_runs after
        # _create_dispatch_runs) so claude-channel.js inside the
        # wrapper-hosted claude-aify claims it and emits the
        # message as a channel wake-up event.
        if (
            runtime in _CHANNEL_MANAGED_RUNTIMES
            and _execution_mode == "channel"
            and _insert_messages_via_console(settings)
        ):
            console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
            if not console_terminal:
                console_terminal = await _ensure_managed_pty_for_dispatch(
                    db,
                    recipient_id,
                    runtime=runtime,
                    settings=settings,
                    requested_by=req.from_agent,
                )
            if console_terminal:
                console_recipients[recipient_id] = console_terminal
            else:
                not_started.append(
                    _dispatch_fix_hint(
                        recipient_id,
                        row,
                        "Claude claude-aify backing PTY is unavailable; restart the environment bridge or recover the session.",
                    )
                )
                channel_backing_failed.add(recipient_id)
            continue
        if runtime in _CHANNEL_MANAGED_RUNTIMES:
            # Channel-mode managed Claude (insert_messages_via_console=false)
            # needs a wrapper PTY running so claude-aify's
            # claude-channel.js child actually polls
            # /dispatch/claim for this agent and picks up the
            # channel-routed dispatch. Without it, the run sits
            # queued forever (originally observed in
            # run_1779309370301). We don't inject input — the
            # PTY is the host for the subscriber, not the
            # delivery channel. Existing terminal is reused
            # (slice-3 reuse semantics); only spawned if absent.
            if (
                not _insert_messages_via_console(settings)
                and _managed_terminal_backing_enabled(settings)
                and _execution_mode == "channel"
            ):
                existing = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                # B2 parity (2026-06-12): a leftover non-managed terminal row must not
                # suppress the cold start — only a LIVE managed-wrapper-child proves a
                # worker actually backs this agent (same strand class as lc-coder).
                if existing and not await _has_live_managed_wrapper_child(db, recipient_id):
                    existing = None
                if not existing:
                    started = None
                    try:
                        started = await _ensure_managed_pty_for_dispatch(
                            db,
                            recipient_id,
                            runtime=runtime,
                            settings=settings,
                            requested_by=req.from_agent,
                        )
                    except Exception:
                        started = None
                    if not started:
                        # ROOT-CAUSE-G PARITY (2026-06-12, graph-tech-lead strand):
                        # _ensure_managed_pty_for_dispatch returns None when the agent
                        # has no usable session row to launch into — exactly the state
                        # after an env-bridge restart retires every session. The native
                        # runtimes fall back to a cold-start spawn_request here; managed
                        # claude never did, so the channel run sat queued with a claimer
                        # that could never exist until the 180s backstop FAILED it.
                        coldstarted = False
                        # N8: declared OUTSIDE the try so a reason recorded before an
                        # exception is still reportable.
                        _cs_reasons_b: list[str] = []
                        try:
                            coldstarted = await _coldstart_spawn_request_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                                warnings=_cs_reasons_b,
                            )
                        except Exception as _cs_err:
                            coldstarted = False
                            _cs_reasons_b.append(
                                f"{COLDSTART_REFUSED_PREFIX}cold-start raised: {_cs_err}")
                        if not coldstarted and not await _has_claimable_spawn_request(db, recipient_id):
                            not_started.append(
                                _dispatch_fix_hint(
                                    recipient_id,
                                    row,
                                    _coldstart_refusal_message(_cs_reasons_b, runtime),
                                )
                            )
                            channel_backing_failed.add(recipient_id)
            # Final safety (2026-07-04): a channel-managed claude dispatch must
            # never strand until the 180s queued-run backstop. If — after the
            # terminal reuse / PTY-ensure above — there is STILL no live
            # managed-wrapper-child to run claude-channel.js AND no claimable
            # spawn request, cold-start one now so a bridge spawns the wrapper
            # and claims this run on its next poll (the aicm-lc-manager
            # 'queued, never spawned' strand). Idempotent: a live claimer or a
            # pending spawn short-circuits it, so no duplicate workers.
            if recipient_id not in channel_backing_failed and (
                not await _has_live_managed_wrapper_child(db, recipient_id)
                and not await _has_claimable_spawn_request(db, recipient_id)
            ):
                try:
                    await _coldstart_spawn_request_for_dispatch(
                        db,
                        recipient_id,
                        runtime=runtime,
                        settings=settings,
                        requested_by=req.from_agent,
                    )
                except Exception:
                    pass
            continue
        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
        if not console_terminal:
            console_terminal = await _ensure_managed_pty_for_dispatch(
                db,
                recipient_id,
                runtime=runtime,
                settings=settings,
                requested_by=req.from_agent,
            )
        if console_terminal:
            console_recipients[recipient_id] = console_terminal


