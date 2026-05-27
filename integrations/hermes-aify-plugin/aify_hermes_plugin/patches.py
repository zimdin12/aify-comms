from __future__ import annotations

import inspect
import os
import tempfile
import textwrap
from pathlib import Path
from types import ModuleType


def _same_path(left: object, right: object) -> bool:
    try:
        return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
            os.path.abspath(os.fspath(right))
        )
    except TypeError:
        return False


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

    existing = methods.get("aify.session.bind_transport")
    if existing is not None and getattr(existing, "_aify_plugin_patch", False):
        return
    if existing is not None:
        # A direct source patch from an older install is already present.
        return

    def bind_visible_transport(rid, params: dict) -> dict:  # type: ignore[no-untyped-def]
        target = str(params.get("session_id") or params.get("session_key") or "").strip()
        if not target:
            return module._err(rid, 4006, "session_id required")
        try:
            snapshot = list(module._sessions.items())
        except Exception as exc:
            return module._err(rid, 5000, f"could not enumerate active sessions: {exc}")

        found_sid = ""
        found_session = None
        for sid, session in snapshot:
            if sid == target or str(session.get("session_key") or "") == target:
                found_sid = sid
                found_session = session
                break
        if not found_sid or found_session is None:
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
            else:
                active_labels = [
                    f"{sid}:{session.get('session_key') or ''}"
                    for sid, session in active_candidates
                ]
                return module._err(
                    rid,
                    4010,
                    "visible session not found"
                    + (
                        f"; active sessions: {', '.join(active_labels)}"
                        if active_labels
                        else ""
                    ),
                )

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


def patch_hermes_cli_main(module: ModuleType) -> None:
    """Keep the wrapper-owned active-session file alive for visible binding."""

    original = getattr(module, "_launch_tui", None)
    if not callable(original) or getattr(original, "_aify_plugin_patch", False):
        return

    def launch_tui_with_active_file(*args, **kwargs):  # type: ignore[no-untyped-def]
        active_session_file = os.environ.get("HERMES_TUI_ACTIVE_SESSION_FILE", "").strip()
        if not active_session_file:
            return original(*args, **kwargs)

        target = Path(active_session_file)
        used_wrapper_file = False
        original_mkstemp = tempfile.mkstemp
        original_unlink = os.unlink

        def mkstemp_for_wrapper_file(*mk_args, **mk_kwargs):  # type: ignore[no-untyped-def]
            nonlocal used_wrapper_file
            prefix = mk_kwargs.get("prefix")
            suffix = mk_kwargs.get("suffix")
            if prefix is None and len(mk_args) >= 1:
                prefix = mk_args[0]
            if suffix is None and len(mk_args) >= 2:
                suffix = mk_args[1]
            if (
                not used_wrapper_file
                and prefix == "hermes-tui-active-session-"
                and suffix == ".json"
            ):
                target.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(target), os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
                used_wrapper_file = True
                return fd, str(target)
            return original_mkstemp(*mk_args, **mk_kwargs)

        def preserve_wrapper_file(path, *unlink_args, **unlink_kwargs):  # type: ignore[no-untyped-def]
            if used_wrapper_file and _same_path(path, target):
                return None
            return original_unlink(path, *unlink_args, **unlink_kwargs)

        tempfile.mkstemp = mkstemp_for_wrapper_file
        os.unlink = preserve_wrapper_file
        try:
            return original(*args, **kwargs)
        finally:
            tempfile.mkstemp = original_mkstemp
            os.unlink = original_unlink

    launch_tui_with_active_file._aify_plugin_patch = True  # type: ignore[attr-defined]
    setattr(module, "_launch_tui", launch_tui_with_active_file)


def _replace_function_source(module: ModuleType, name: str, transform) -> bool:  # type: ignore[no-untyped-def]
    fn = getattr(module, name, None)
    if not callable(fn):
        return False
    try:
        source = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return False
    new_source = transform(source)
    if new_source == source:
        return False
    filename = inspect.getsourcefile(fn) or f"<aify-hermes-plugin:{name}>"
    exec(compile(new_source, filename, "exec"), module.__dict__)
    patched = getattr(module, name, None)
    if callable(patched):
        patched._aify_plugin_patch = True  # type: ignore[attr-defined]
    return True


def patch_codex_runtime(module: ModuleType) -> None:
    """Patch Codex Responses stream null-output handling in memory."""

    def transform_stream(source: str) -> str:
        changed = source
        marker = "Responses stream hit SDK NoneType iterable bug"
        if marker not in changed:
            needle = """        except RuntimeError as exc:
            err_text = str(exc)
"""
            patch = """        except TypeError as exc:
            err_text = str(exc)
            if "NoneType" in err_text and "iterable" in err_text:
                logger.debug(
                    "Responses stream hit SDK NoneType iterable bug; falling back to create(stream=True). %s err=%s",
                    agent._client_log_context(),
                    err_text,
                )
                return agent._run_codex_create_stream_fallback(api_kwargs, client=active_client)
            raise
"""
            if needle in changed:
                changed = changed.replace(needle, patch + needle, 1)
        changed = changed.replace(
            """                if isinstance(_out, list) and not _out:
                    if collected_output_items:
                        final_response.output = list(collected_output_items)
""",
            """                if not isinstance(_out, list) or not _out:
                    if collected_output_items:
                        final_response.output = list(collected_output_items)
""",
        )
        return changed

    def transform_fallback(source: str) -> str:
        return source.replace(
            """                if isinstance(_out, list) and not _out:
                    if collected_output_items:
                        terminal_response.output = list(collected_output_items)
""",
            """                if not isinstance(_out, list) or not _out:
                    if collected_output_items:
                        terminal_response.output = list(collected_output_items)
""",
        )

    _replace_function_source(module, "run_codex_stream", transform_stream)
    _replace_function_source(
        module,
        "run_codex_create_stream_fallback",
        transform_fallback,
    )
