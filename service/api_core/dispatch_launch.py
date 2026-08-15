"""Making a worker EXIST so a dispatch has somewhere to go.

Moved out of `service/api_core/dispatch_start.py` in v0.5.4, byte-identical. At 302 lines it was the
largest single declaration in that module and had exactly one importer, which makes it the cheapest
honest reduction available there: one import to repoint.

It still calls `_coldstart_spawn_request_for_dispatch` and `_ensure_managed_pty_for_dispatch`, which
stay put — the second has roughly ten importers across the routers and moving it is a much larger
change. So this module imports dispatch_start; dispatch_start does not import back, and the cycle
smoke checks that by importing both.
"""
from __future__ import annotations

from service.api_core.managed_pty_for_dispatch import _ensure_managed_pty_for_dispatch
from service.api_core.capabilities import _managed_via_wrapper_for_runtime
from service.api_core.channel_delivery import _CHANNEL_MANAGED_RUNTIMES, _insert_messages_via_console
from service.api_core.claim_gating import _turn_busy_holds_delivery
from service.api_core.dispatch_hint import _dispatch_fix_hint
from service.api_core.dispatch_start import (
    _coldstart_spawn_request_for_dispatch,
)
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.dispatch_text import COLDSTART_REFUSED_PREFIX, _coldstart_refusal_message
from service.api_core.execution_mode import _auto_return_resident_to_managed_if_possible
from service.api_core.live_process_probes import _has_live_managed_wrapper_child
from service.api_core.runtime import _NATIVE_MANAGED_RUNTIMES, _normalize_runtime
from service.api_core.settings import _managed_terminal_backing_enabled
from service.api_core.spawn_request_state import _has_claimable_spawn_request
from service.api_core.terminal_ownership import _active_terminal_for_agent

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
            await _back_managed_claude_with_a_console(
                db, req, row, runtime, settings,
                recipient_id, not_started, channel_backing_failed, _execution_mode,
            )
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


async def _back_managed_claude_with_a_console(
    db, req, row, runtime, settings,
    recipient_id, not_started, channel_backing_failed, _execution_mode,
):
            """Give a managed-claude recipient a console to receive on, when the operator asked for one.

            Extracted from `_launch_recipients_for_dispatch` in v0.5.4. This is the legacy via-console
            delivery path: it only fires when `insert_messages_via_console` is on AND managed-terminal
            backing is enabled. The DEFAULT route is the channel branch below it, where the run is left
            launchable with execution_mode='channel' for claude-channel.js to claim.

            It records a coldstart REFUSAL rather than raising: a recipient that cannot be given a
            console is reported in `not_started`, so the caller can tell the sender which recipients
            were not woken instead of failing the whole send.

            `_execution_mode` keeps its underscore because the parameter must be named exactly as the
            argument: inline-back splices the body over the call WITHOUT substituting arguments, so a
            renamed parameter cannot be verified. It reads as a private name and is a caller local.
            """
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
