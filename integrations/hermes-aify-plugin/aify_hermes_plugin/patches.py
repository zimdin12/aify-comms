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


def patch_hermes_cli_web_server(module: ModuleType) -> None:
    """Expose the dashboard gateway URL to MCP children spawned by the gateway."""

    port = str(os.environ.get("AIFY_HERMES_PORT") or "").strip()
    token = str(getattr(module, "_SESSION_TOKEN", "") or "").strip()
    if not port or not token:
        return

    host = str(os.environ.get("AIFY_HERMES_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    gateway_url = f"ws://{host}:{port}/api/ws?token={token}"
    # Always prefer the gateway owned by this dashboard process. Operators can
    # resume Hermes from shells that still carry an older AIFY_HERMES_GATEWAY_URL;
    # preserving that inherited value makes the MCP child register hermes-live
    # against a dead port while its bridge heartbeat remains fresh.
    os.environ["AIFY_HERMES_GATEWAY_URL"] = gateway_url
    os.environ["HERMES_TUI_GATEWAY_URL"] = gateway_url
    os.environ["AIFY_HERMES_GATEWAY_TOKEN"] = token
    os.environ["AIFY_HERMES_GATEWAY_TOKEN_ENV"] = "AIFY_HERMES_GATEWAY_TOKEN"


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
