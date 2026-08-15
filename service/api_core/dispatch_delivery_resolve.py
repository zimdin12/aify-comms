"""Deciding HOW a dispatch reaches one recipient — console, channel, or refusal.

Moved out of `service/api_core/dispatch_start.py` in v0.5.4, byte-identical. That module was 943 lines
and this was the largest piece with a single importer, so it is the cheapest honest reduction: one
import to repoint, not ten.

It still calls `_ensure_managed_pty_for_dispatch`, which stays where it is — it has roughly ten
importers across the routers, so moving THAT is a much larger change and a separate decision. This
module therefore imports dispatch_start; dispatch_start does not import this one, so no cycle.
"""
from __future__ import annotations

from fastapi import HTTPException

from service.api_core.managed_pty_for_dispatch import _ensure_managed_pty_for_dispatch
from service.api_core.capabilities import _managed_via_wrapper_for_runtime
from service.api_core.channel_delivery import _CHANNEL_MANAGED_RUNTIMES, _insert_messages_via_console
from service.api_core.dispatch_hint import _dispatch_fix_hint
from service.api_core.execution_mode import (
    _agent_execution_mode,
    _auto_return_resident_to_managed_if_possible,
)
from service.api_core.managed_env import _managed_environment_unavailable_reason
from service.api_core.runtime import _NATIVE_MANAGED_RUNTIMES, _normalize_runtime
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _managed_terminal_backing_enabled
from service.api_core.terminal_ownership import _active_terminal_for_agent

async def _resolve_dispatch_recipient_delivery(console_recipients, db, launchable_recipients, not_started, recipient_rows, recipients, req, settings) -> None:
    """Resolve how each recipient of a dispatch will be reached, and make it reachable.

    v0.5.4, extracted verbatim out of the 320-line `create_dispatch`. Sibling of
    `_launch_recipients_for_dispatch` above, which came out of `send_message` in the same release.

    THE TWO ARE NOT THE SAME FUNCTION AND ARE DELIBERATELY NOT MERGED. They answer the same question for
    the two entry points — `POST /dispatch` and the trigger path of `comms_send` — over different inputs
    and with different fallbacks. Merging them would be a behaviour change dressed as deduplication, and
    this series does not change behaviour. That they are similar is worth a reader's attention, which is
    why it is written here rather than left to be rediscovered; unifying them is a separate decision with
    its own risk.

    IT MUTATES ITS ARGUMENTS: the caller's collections are appended to and read afterwards. The gate's
    live-out check proves no name bound inside the loop is read after it, which is what makes lifting it
    verbatim safe.
    """
    for recipient_id in recipients:
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
        row = await cursor.fetchone()
        if row:
            row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
        if row:
            recipient_rows[recipient_id] = row
            # Plan 2 (2026-05-25) pi flip: reject new dispatches to a pi
            # agent that's currently mid-flip from resident -> managed.
            # _drain_and_flip_pi_resident_agents will migrate it once
            # any active runs drain (~5s). The operator should retry
            # after the flip completes. Without this gate, dispatches
            # queue against a session_mode the runtime no longer
            # supports.
            if _normalize_runtime(row["runtime"] or "") == "pi":
                _rs = _json_loads_or(row["runtime_state"], {})
                if _rs.get("pi_resident_pending_flip"):
                    raise HTTPException(
                        409,
                        f'Agent "{recipient_id}" is migrating from resident '
                        f"to managed (pi flip pending). Retry in a few "
                        f"seconds — the drain loop will flip the agent "
                        f"once any active runs complete."
                    )
        execution_mode = None
        reason = None if row else "agent is not registered"
        if row:
            runtime = _normalize_runtime(row["runtime"] or "generic")
            if req.requestedRuntime and _normalize_runtime(req.requestedRuntime) != runtime:
                reason = f'requested runtime "{req.requestedRuntime}" does not match registered runtime "{runtime}"'
            elif runtime in _NATIVE_MANAGED_RUNTIMES:
                # Plan 5 (2026-05-25): pass settings so
                # _agent_execution_mode can detect wrapper-backed managed
                # runtimes (managed_via_wrapper) and return
                # execution_mode='channel'. Without settings the helper
                # short-circuits to 'managed' (line 1065) and the
                # wrapper-backed dispatch path never fires.
                execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                # Plan 5 follow-up (2026-05-26): the PTY-input
                # (console_recipients) downgrade below MUST only fire
                # for execution_mode='managed'. When the helper returns
                # 'channel' for wrapper-backed codex/hermes, leave
                # the run as channel-mode so the wrapper PTY's child
                # bridge can pick it up. Falling through to
                # console_recipients would route the message via raw PTY
                # keystrokes — the scrambled-text failure mode the
                # operator explicitly banned.
                if (
                    not reason
                    and execution_mode == "channel"
                    and _managed_terminal_backing_enabled(settings)
                    and _managed_via_wrapper_for_runtime(settings, runtime)
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
                    if not console_terminal:
                        reason = f"Managed {runtime} wrapper PTY is unavailable; recover or restart the environment-managed session."
                if not reason and execution_mode == "managed":
                    if (
                        _managed_terminal_backing_enabled(settings)
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
                            execution_mode = None
                        else:
                            reason = await _managed_environment_unavailable_reason(db, row)
            elif runtime in _CHANNEL_MANAGED_RUNTIMES:
                # Plan 5 (2026-05-25): pass settings (parity with the
                # NATIVE_MANAGED branch above). _agent_execution_mode
                # uses settings to gate the wrapper-backed channel route.
                execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                if not reason and execution_mode == "channel":
                    reason = await _managed_environment_unavailable_reason(db, row)
                if not reason and execution_mode == "channel" and _insert_messages_via_console(settings):
                    # PTY-input path — only the opt-in via-console
                    # delivery mode goes through here. Default-false
                    # routing leaves the run launchable and the post-
                    # create _apply_channel_routing_to_claude_runs
                    # flips execution_mode='channel' so claude-channel.js
                    # claims it.
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
                        execution_mode = None
                    else:
                        reason = "Claude claude-aify backing PTY is unavailable; restart the environment bridge or recover the session."
                elif reason:
                    execution_mode = None
            else:
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
                    # Plan 5 (2026-05-25): pass settings — see sibling
                    # branches above for rationale.
                    execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                    if not reason and execution_mode:
                        reason = await _managed_environment_unavailable_reason(db, row)
        if reason or not execution_mode:
            if recipient_id not in console_recipients:
                not_started.append(_dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable"))
        else:
            launchable_recipients.append((recipient_id, execution_mode))
