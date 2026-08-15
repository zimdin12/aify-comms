"""The Hermes TUI gateway patch: 249 lines of one surface, on its own.

Extracted from `aify_hermes_plugin/patches.py` in v0.5.4. Closure measured before the move: it needs
`ModuleType` and nothing else the old module declared.

EVERY FUNCTION IN THIS PLUGIN PATCHES A DIFFERENT HERMES SURFACE — the CLI main, the CLI web server,
the codex runtime, and this one — and they are independent: `bootstrap.py` maps a module name to
exactly one of them and applies it when that module is imported. Four unrelated patches in one file
is a filing decision, not a coupling, and this one alone was 60% of the file.

WHY A PLUGIN RATHER THAN AN EDIT, since that is the thing a reader needs first: Hermes is a
third-party install, so aify cannot edit its files without the next `hermes update` reverting the
change silently. Patching at import time survives updates and fails loudly when the upstream shape
moves — `_methods` missing raises rather than degrading, because a gateway that half-applied its
patch is worse than one that refused to start.

Body byte-identical to what stood in `patches.py`.
"""

from __future__ import annotations

from types import ModuleType


def patch_gateway_server(module: ModuleType) -> None:
    """Patch Hermes TUI gateway behavior without editing Hermes files."""

    methods = getattr(module, "_methods", None)
    if not isinstance(methods, dict):
        raise RuntimeError("tui_gateway.server has no _methods registry")

    def ensure_mcp_discovered() -> None:
        if getattr(module, "_aify_mcp_discovery_done", False):
            return
        try:
            from tools.mcp_tool import discover_mcp_tools

            discover_mcp_tools()
            setattr(module, "_aify_mcp_discovery_done", True)
        except Exception as exc:
            logger = getattr(module, "logger", None)
            if logger is not None:
                logger.debug("aify MCP discovery failed in TUI gateway: %s", exc)

    original_make_agent = getattr(module, "_make_agent", None)
    if callable(original_make_agent) and not getattr(
        original_make_agent, "_aify_plugin_patch", False
    ):

        def make_agent_with_mcp_discovery(*args, **kwargs):  # type: ignore[no-untyped-def]
            ensure_mcp_discovered()
            return original_make_agent(*args, **kwargs)

        make_agent_with_mcp_discovery._aify_plugin_patch = True  # type: ignore[attr-defined]
        setattr(module, "_make_agent", make_agent_with_mcp_discovery)

    # ── ROOT FIX (#3, 2026-06-03): keep a sidecar prompt.submit from STEALING
    # the visible TUI's streaming transport ──────────────────────────────────
    #
    # The gateway's prompt.submit handler unconditionally does
    #     if (t := current_transport()) is not None:
    #         session["transport"] = t
    # to re-bind streaming to the active request socket (server.py ~3879). When
    # the aify managed/resident delivery LOOP submits a turn for a RESIDENT
    # agent, `current_transport()` is the LOOP's WS socket — so the whole turn
    # (inbound echo, message.start, deltas, the agent's reply) streams to the
    # loop and is discarded, and the operator's visible `hermes --tui`, attached
    # to the SAME session, renders nothing (the headline bug).
    #
    # write_json() routes event frames by `session["transport"]` (server.py
    # ~382), and the agent runs in a background thread that the handler spawns
    # AFTER the rebind. So the fix is: wrap the registered prompt.submit method,
    # and immediately AFTER the inner handler returns (synchronously, before the
    # turn thread emits anything that matters), re-assert
    #     session["transport"] = TeeTransport(primary=visible_TUI, secondary=caller)
    # whenever the caller is a NON-TUI sidecar and the session already had a
    # live primary transport that the handler just replaced. The TUI keeps its
    # stream (the tee primary) AND the loop still receives its copy (the
    # secondary) — delivery is not regressed.
    #
    # Idempotent: tagged with _aify_prompt_submit_tee so a re-import doesn't
    # double-wrap, and a no-op when there is no distinct prior primary (e.g. the
    # operator's OWN TUI submitting its own prompt, where caller IS the primary).
    original_prompt_submit = methods.get("prompt.submit")
    if callable(original_prompt_submit) and not getattr(
        original_prompt_submit, "_aify_prompt_submit_tee", False
    ):

        def prompt_submit_tee_transport(rid, params: dict):  # type: ignore[no-untyped-def]
            sid = str(params.get("session_id") or "")
            sessions = getattr(module, "_sessions", None)
            session = sessions.get(sid) if isinstance(sessions, dict) else None

            # The transport the visible TUI is streaming on, captured BEFORE the
            # handler rebinds it to the caller. None for an unknown session.
            primary = session.get("transport") if isinstance(session, dict) else None

            current_transport = getattr(module, "current_transport", None)
            caller = current_transport() if callable(current_transport) else None

            result = original_prompt_submit(rid, params)

            # Re-assert the tee only when a sidecar caller (the delivery loop)
            # just stole a DISTINCT live primary. If the handler errored early
            # (busy/not-found) it leaves session["transport"] alone, so honor
            # whatever it set: only act when it is now exactly the caller.
            try:
                if (
                    isinstance(session, dict)
                    and primary is not None
                    and caller is not None
                    and primary is not caller
                    and session.get("transport") is caller
                ):
                    tee_transport = getattr(module, "TeeTransport", None)
                    if tee_transport is None:
                        from tui_gateway.transport import (
                            TeeTransport as tee_transport,
                        )
                    session["transport"] = tee_transport(primary, caller)

                    # The gateway worker thread for a warm/resident agent can
                    # emit the FIRST `message.start` BEFORE this tee re-assert
                    # runs — that event then routes only to the loop's socket,
                    # so the visible TUI never sets `ui.busy` (no spinner/verb,
                    # tab stays "ready") even though deltas + message.complete
                    # arrive after the tee and render the reply. Re-emit ONE
                    # `message.start` now that the transport is the tee, so the
                    # TUI primary gets it (the loop sees a harmless duplicate).
                    # Gate on an ACCEPTED NEW streaming turn so the 4009/steer-
                    # busy path can't reset an in-flight turn's busy state.
                    accepted_new_turn = (
                        isinstance(result, dict)
                        and isinstance(result.get("result"), dict)
                        and result["result"].get("status") == "streaming"
                    )
                    emit = getattr(module, "_emit", None)
                    if accepted_new_turn and callable(emit):
                        emit("message.start", sid)
            except Exception as exc:
                logger = getattr(module, "logger", None)
                if logger is not None:
                    logger.debug("aify prompt.submit tee re-assert failed: %s", exc)

            return result

        prompt_submit_tee_transport._aify_prompt_submit_tee = True  # type: ignore[attr-defined]
        prompt_submit_tee_transport._aify_plugin_patch = True  # type: ignore[attr-defined]
        methods["prompt.submit"] = prompt_submit_tee_transport

    def resolve_visible_session(target: str):  # type: ignore[no-untyped-def]
        try:
            snapshot = list(module._sessions.items())
        except Exception as exc:
            return "", None, f"could not enumerate active sessions: {exc}"

        for sid, session in snapshot:
            if sid == target or str(session.get("session_key") or "") == target:
                return sid, session, ""

        active_candidates = [
            (sid, session)
            for sid, session in snapshot
            if isinstance(session, dict)
        ]
        if len(active_candidates) == 1:
            found_sid, found_session = active_candidates[0]
            logger = getattr(module, "logger", None)
            if logger is not None:
                logger.info(
                    "aify visible session fallback: saved handle not active; using sole active session %s key=%s target=%s",
                    found_sid,
                    found_session.get("session_key") or "",
                    target,
                )
            return found_sid, found_session, ""

        active_labels = [
            f"{sid}:{session.get('session_key') or ''}"
            for sid, session in active_candidates
        ]
        return (
            "",
            None,
            "visible session not found"
            + (f"; active sessions: {', '.join(active_labels)}" if active_labels else ""),
        )

    existing_notice = methods.get("aify.session.render_notice")
    if existing_notice is None:

        def render_visible_notice(rid, params: dict) -> dict:  # type: ignore[no-untyped-def]
            target = str(params.get("session_id") or params.get("session_key") or "").strip()
            if not target:
                return module._err(rid, 4006, "session_id required")

            found_sid, found_session, error = resolve_visible_session(target)
            if not found_sid or found_session is None:
                return module._err(rid, 4010, error or "visible session not found")

            transport = found_session.get("transport") or getattr(module, "_stdio_transport", None)
            if transport is None or not hasattr(transport, "write"):
                return module._err(rid, 5000, "visible session transport unavailable")

            notice = str(params.get("notice") or "").strip()
            status = str(params.get("status") or "").strip()

            if notice:
                transport.write(
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "review.summary",
                            "session_id": found_sid,
                            "payload": {"text": notice},
                        },
                    }
                )
            if status:
                transport.write(
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "status.update",
                            "session_id": found_sid,
                            "payload": {"kind": "aify-comms", "text": status},
                        },
                    }
                )

            return module._ok(rid, {"session_id": found_sid, "rendered": True})

        render_visible_notice._aify_plugin_patch = True  # type: ignore[attr-defined]
        methods["aify.session.render_notice"] = render_visible_notice

    existing = methods.get("aify.session.bind_transport")
    if existing is not None:
        # A direct source patch from an older install, or this plugin from an
        # earlier import, is already present. Keep any render_notice patch above.
        return

    def bind_visible_transport(rid, params: dict) -> dict:  # type: ignore[no-untyped-def]
        target = str(params.get("session_id") or params.get("session_key") or "").strip()
        if not target:
            return module._err(rid, 4006, "session_id required")
        found_sid, found_session, error = resolve_visible_session(target)
        if not found_sid or found_session is None:
            return module._err(rid, 4010, error or "visible session not found")

        current_transport = getattr(module, "current_transport", None)
        bridge_transport = current_transport() if callable(current_transport) else None
        primary = found_session.get("transport") or getattr(module, "_stdio_transport", None)
        tee_transport = getattr(module, "TeeTransport", None)
        if bridge_transport is not None and bridge_transport is not primary:
            if tee_transport is None:
                from tui_gateway.transport import TeeTransport as tee_transport

            found_session["transport"] = tee_transport(primary, bridge_transport)

        return module._ok(
            rid,
            {
                "session_id": found_sid,
                "session_key": found_session.get("session_key") or "",
                "mirrored": bridge_transport is not None,
                "running": bool(found_session.get("running")),
            },
        )

    bind_visible_transport._aify_plugin_patch = True  # type: ignore[attr-defined]
    methods["aify.session.bind_transport"] = bind_visible_transport
