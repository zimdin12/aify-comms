"""The `runtime_state` dict: reading a session handle out of it, and writing one into it.

v0.5.4 layer 0. Three functions that were in three places — `_runtime_state_with_handle` in the control
plane (reached from the agents package and spawn_requests through IDENTICAL borrow shims),
`_runtime_handle_from_state` and `_runtime_state_replacing_handle` in the agents package — while being
one subject and one call chain: `_runtime_state_replacing_handle` calls `_runtime_state_with_handle`.

They moved together because moving only the one that blocked a caller would have left a family split
across a layer boundary, which is the arrangement that produced the shims in the first place.

WHY THE HANDLE KEY DIFFERS BY RUNTIME: codex addresses a conversation by `threadId`, everything else by
`sessionId`. That is a wire-format fact about each runtime's resume call, not a preference, so the
asymmetry is deliberate and a "tidy-up" that unified the key would break codex resume.

A LEAF. Imports `_normalize_runtime` (api_core/runtime.py) and `_json_loads_or`
(api_core/serialization.py) and nothing else, so it cannot join an import cycle and — the rule that
matters — it does not import the control plane. The control plane is now a caller.
"""

from __future__ import annotations

from typing import Any

from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _json_loads_or


def _runtime_state_with_handle(runtime: Any, runtime_state: Any, session_handle: str) -> dict[str, Any]:
    state = runtime_state if isinstance(runtime_state, dict) else _json_loads_or(runtime_state, {})
    result = dict(state or {})
    handle = str(session_handle or "").strip()
    if not handle:
        return result
    if _normalize_runtime(runtime) == "codex":
        result["threadId"] = handle
    else:
        result["sessionId"] = handle
    return result


def _runtime_handle_from_state(runtime: Any, runtime_state: Any) -> str:
    state = runtime_state if isinstance(runtime_state, dict) else _json_loads_or(runtime_state, {})
    normalized = _normalize_runtime(runtime)
    if normalized == "codex":
        return str(state.get("threadId") or state.get("sessionId") or "").strip()
    if normalized == "pi":
        return str(state.get("sessionId") or state.get("threadId") or state.get("sessionFile") or "").strip()
    if normalized == "hermes":
        return str(state.get("sessionId") or state.get("threadId") or state.get("sessionKey") or "").strip()
    return str(state.get("sessionId") or state.get("threadId") or "").strip()


def _runtime_state_replacing_handle(runtime: Any, runtime_state: Any, session_handle: str) -> dict[str, Any]:
    state = runtime_state if isinstance(runtime_state, dict) else _json_loads_or(runtime_state, {})
    result = dict(state or {})
    result.pop("sessionId", None)
    result.pop("threadId", None)
    return _runtime_state_with_handle(runtime, result, session_handle)
