"""Resolving an environment's workspace: which root a requested path belongs to, and how to
normalise it for that environment's path style.

Three functions, 33 lines, and they are here because they are the FIRST of two blockers under the
spawn/pty group — the 394-line `_coldstart_spawn_request_for_dispatch` /
`_ensure_managed_pty_for_dispatch` pair cannot move while its workspace resolution stays in the
carrier. Bottom-up means the small thing moves first even though the big thing is the goal.

`_workspace_root_for` raises `HTTPException(400)` when a requested workspace falls outside the roots
the environment advertises. A leaf raising an HTTP status is deliberate and already the established
pattern here (`api_core/routing.py`, `api_core/validation.py`): translating it into a custom
exception and re-raising at the route would change which response the client gets, and this series
does not change behaviour.

PATH STYLE IS NOT COSMETIC. `_normalize_workspace_for_environment` leaves backslashes alone for a
Windows environment and converts them everywhere else, because a Codex thread created with a
backslash cwd fails `thread/resume` later — the failure lands far from the cause, which is why the
normalisation is centralised in one place rather than done at each call site.

DB ACCESS: none. These are pure path functions over an environment dict.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from service.api_core.capabilities import _environment_uses_windows_paths


def _normalize_workspace_for_environment(environment: dict[str, Any], workspace: str) -> str:
    value = str(workspace or "").strip()
    if not value:
        return ""
    if _environment_uses_windows_paths(environment):
        return value
    return value.replace("\\", "/")


def _workspace_root_for(environment: dict[str, Any], workspace: str) -> str:
    workspace_value = _normalize_workspace_for_environment(environment, workspace)
    roots = [str(root or "").strip() for root in (environment.get("cwdRoots") or []) if str(root or "").strip()]
    if not workspace_value or not roots:
        return roots[0] if roots else ""
    # CASE-INSENSITIVE ON WINDOWS ONLY. Backslashes were already folded here; case was not, so
    # `c:/Docker/proj` was refused against the advertised root `C:/Docker` as "outside the roots" —
    # a path that is literally inside it, because Windows filesystems are case-insensitive and the
    # drive letter's case differs freely between sources (`process.cwd()` vs an operator-typed path).
    # POSIX must stay case-SENSITIVE: there `/srv/Repo` and `/srv/repo` are genuinely two
    # directories, and folding case would admit a workspace outside the advertised root.
    fold = _environment_uses_windows_paths(environment)
    normalized_workspace = workspace_value.replace("\\", "/").rstrip("/")
    workspace_key = normalized_workspace.lower() if fold else normalized_workspace
    for root in roots:
        normalized_root = root.replace("\\", "/").rstrip("/")
        root_key = normalized_root.lower() if fold else normalized_root
        if workspace_key == root_key or workspace_key.startswith(root_key + "/"):
            return root
    raise HTTPException(400, f'Workspace "{workspace_value}" is outside the roots advertised by environment "{environment.get("id")}"')


def _workspace_for_environment(environment: dict[str, Any], requested_workspace: Optional[str], fallback_workspace: Optional[str] = "") -> tuple[str, str]:
    roots = [str(root or "").strip() for root in (environment.get("cwdRoots") or []) if str(root or "").strip()]
    workspace = _normalize_workspace_for_environment(environment, requested_workspace or fallback_workspace or "")
    if not workspace:
        workspace = roots[0] if roots else ""
    try:
        workspace_root = _workspace_root_for(environment, workspace)
    except HTTPException:
        if requested_workspace:
            raise
        workspace = _normalize_workspace_for_environment(environment, roots[0] if roots else "")
        workspace_root = _workspace_root_for(environment, workspace)
    if not workspace and workspace_root:
        workspace = workspace_root
    return workspace, workspace_root
