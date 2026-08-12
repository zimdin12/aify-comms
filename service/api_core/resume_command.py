"""How to resume or take over a session: the command, sourced from the runtime adapter.

v0.5.4 layer 0. Extracted from `service/routers/agents/shared.py` because
`service/api_core/registration_gates.py` needs it — the duplicate-owner gate's 409 tells the operator
how to resume after taking over, and an api_core leaf may not import upward from a router. So this had
to move before that gate's decision logic could leave the 684-line `register_agent` at all. The
bottom-up rule is not bureaucracy here: it is the only order in which either step is possible.

ITS OWN MODULE rather than joining `api_core/runtime.py`, which owns `_normalize_runtime` and is its
only module-level dependency. That file's docstring promises it "imports the vocabulary leaf and the
standard library, nothing else, so it cannot join an import cycle" — and this function reaches
`service.runtimes` for an adapter. The import is function-scope, so the cycle claim would survive, but
the sentence would not, and a docstring that quietly stops being true is worse than a second small
module.

WHY THE ADAPTER IMPORT IS DEFERRED: `service.runtimes` imports adapters that reach back into service
modules. Hoisting it to module scope is what would create the cycle the runtime leaf avoids. It stays
inside the function.

BEST-EFFORT BY CONTRACT: returns "" rather than raising. Its callers are an error path and a
mode-switch response; a resume hint that raises would replace an actionable 409 with a 500.
"""

from __future__ import annotations

from typing import Any

from service.api_core.runtime import _normalize_runtime


def _resume_command_for(runtime: Any, session_handle: Any, agent_id: Any = "") -> str:
    """Takeover/resume command for a session, sourced from the runtime adapter.

    Used by the mode-switch response (managed -> resident takeover) and the
    mutual-exclusion collision guard's actionable error. For hermes the resume
    target is the per-agent daemon session `aify-<agentId>` when no concrete
    handle is pinned; everything else resumes by the pinned handle. Best-effort:
    returns "" if the adapter has no resume command (never raises).
    """
    handle = str(session_handle or "").strip()
    normalized = _normalize_runtime(runtime)
    if not handle and normalized == "hermes" and agent_id:
        handle = f"aify-{agent_id}"
    if not handle:
        return ""
    try:
        from service.runtimes import adapter_for
        # Pass the agent id: the wrapper needs `--aify-agent` to export AIFY_AGENT_ID,
        # without which the resumed session's turn detector and turn hooks all silently
        # no-op and its status latches (the general-manager incident). A resume command
        # that omits it is a command that breaks the agent it resumes.
        return adapter_for(normalized).resume_command(handle, str(agent_id or "").strip()) or ""
    except Exception:
        return ""
