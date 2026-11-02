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

import posixpath
import re
from typing import Any, Optional

from fastapi import HTTPException

from service.api_core.capabilities import _environment_uses_windows_paths


def _lexical_key(value: str) -> str:
    """Collapse `.` and `..` segments, WITHOUT touching the filesystem.

    `_workspace_root_for` compared raw strings, so `/srv/repo/../../etc` started with `/srv/repo`
    and was admitted as being inside that root — by the check whose refusal message promises the
    workspace is "outside the roots advertised by environment". `C:/Docker/../Windows/System32`
    passed the same way. The advertised roots are the whole containment boundary for a spawn's cwd,
    and both sides of it (this and the bridge's `workspaceWithinRoots`) were pure prefix tests, so
    there was no second guard behind the first.

    LEXICAL, NOT RESOLVED, and the distinction is load-bearing: this service runs in a container and
    the workspace is a path on the HOST, so `realpath` would resolve against the wrong filesystem and
    answer confidently about a directory that is not the one being asked about. That means SYMLINKS
    are not followed — a link inside a root pointing out of it still passes. Saying so is better than
    a guarantee this cannot make; closing that needs the check to move to the side that owns the
    filesystem.

    `posixpath` on purpose, for Windows paths too: backslashes are folded to `/` before this runs, and
    a drive letter behaves as an ordinary leading segment (`C:/Docker/..` -> `C:`), which is what the
    comparison wants.
    """
    text = str(value or "")
    if not text:
        return text
    return posixpath.normpath(text)


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
    # `..` IS COLLAPSED BEFORE COMPARING — see `_lexical_key`. Note the order: normalise separators,
    # then collapse, then strip a trailing separator. `normpath` already drops trailing separators
    # except on the bare root `/`, which the strip turns into "" so that a `/` root keeps matching
    # every absolute path through the `startswith(root_key + "/")` arm below. That match-all is
    # existing behaviour and the bridge advertises `/` by default, so it must survive this change.
    normalized_workspace = _lexical_key(workspace_value.replace("\\", "/")).rstrip("/")
    workspace_key = normalized_workspace.lower() if fold else normalized_workspace
    for root in roots:
        normalized_root = _lexical_key(root.replace("\\", "/")).rstrip("/")
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


def workspace_within_roots(workspace: str, roots, *, windows: bool) -> bool:
    """PURE. aify-env's containment rule (its `workspaceWithinRoots`), the same law here: is
    `workspace` one of `roots` or below one? Case-folded on Windows only; the separator stops `/home/bo` admitting
    `/home/bob`. Deliberately NOT `_workspace_root_for`, which also resolves `..`: this predicts what aify-env will
    refuse, so it must judge exactly as aify-env does, whatever spawn-time validation does."""
    def normalise(value) -> str:
        slashed = str(value or "").replace("\\", "/")
        text = slashed.rstrip("/") or ("/" if slashed.startswith("/") else "")
        return text.lower() if windows else text
    target = normalise(workspace)
    if not target:
        return False
    # A '.' or '..' segment is refused, never resolved, as aify-env 0.9.11 does: textually D:/Games/../Elsewhere reads
    # as inside D:/Games while the process would start in D:/Elsewhere.
    if any(segment in (".", "..") for segment in target.split("/")):
        return False
    for root in roots or []:
        base = normalise(root)
        if base and (target == base or target.startswith("/" if base == "/" else base + "/")):
            return True
    return False


def launch_host(row, metadata: dict[str, Any]) -> dict[str, Any]:
    """The host facts `outside_launch_roots` judges by, built ONE way for every caller: what the host reported, never the
    service's own cwdRoots, whose drive-shaped entries would flip a Linux host to Windows matching (review of 20b7a1de).
    `row` is an environments row (or a mapping with its column names)."""
    return {"id": row["id"], "os": row["os"], "kind": row["kind"], "machineId": row["machine_id"], "metadata": metadata}


# THE ADD HINT IS aify-env's RULE, TWINNED (launch-roots.mjs parseLaunchRoot, definition-checks.mjs addHintFor,
# aify-env 0.9.11), held equal specimen for specimen by test_the_add_hint_is_aify_envs_specimen_for_specimen. The card
# and the misconfigured note both say what that host's own CLI would: the command, or why there is none.

#: What JavaScript's String.prototype.trim removes (WhiteSpace and LineTerminator); Python's strip() also removes
#: \x1c-\x1f, which trim keeps, so the parser twin cannot use it.
_JS_TRIM = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
#: JavaScript's `.` stops at these, so the drive pattern's tail must too.
_WINDOWS_DRIVE = re.compile("([a-zA-Z]):(/[^\n\r\u2028\u2029]*)?")
#: What one supported shell (bash, PowerShell, cmd.exe through npm's %* shim) would still rewrite inside double quotes.
_UNQUOTABLE = re.compile('["`$%!\x00-\x1f\x7f]')


def parse_launch_root(text, *, windows: bool) -> dict[str, str]:
    """PURE. aify-env's parseLaunchRoot: `{"root": stored spelling}` or `{"refused": its reason}`. Refused, never
    resolved: relative, UNC/device, or a '.'/'..' segment. A bare drive is its root; Windows roots get forward slashes
    and an uppercase drive."""
    typed = text.strip(_JS_TRIM) if isinstance(text, str) else ""
    if not typed:
        return {"refused": "no folder given"}
    if windows:
        slashed = typed.replace("\\", "/")
        if slashed.startswith("//"):
            return {"refused": f"{typed}: UNC and device paths are not supported yet"}
        drive = _WINDOWS_DRIVE.fullmatch(slashed)
        if not drive:
            return {"refused": f"{typed}: not an absolute folder with a drive letter (D:\\Games)"}  # example path
        root = f"{drive.group(1).upper()}:{drive.group(2) or '/'}"
    else:
        if not typed.startswith("/"):
            return {"refused": f"{typed}: not an absolute folder"}
        root = typed
    root = re.sub("/{2,}", "/", root)
    if any(segment in (".", "..") for segment in root.split("/")):
        return {"refused": f"{typed}: names . or ..; give the folder itself"}
    top = re.fullmatch("[A-Z]:/", root) if windows else root == "/"
    return {"root": root if top else root.rstrip("/")}


def add_hint(folder, *, windows: bool) -> dict[str, Any]:
    """PURE. aify-env's addHintFor: `{"command"}`, `{"unquotable": True}` for a valid root no single double-quoted
    spelling carries through every supported shell, or `{"refused"}`. Only the hint is restricted, never the root."""
    text = str(folder if folder is not None else "")
    read = parse_launch_root(text, windows=windows)
    if "refused" in read:
        return {"refused": read["refused"]}
    if _UNQUOTABLE.search(text) or (not windows and "\\" in text):
        return {"unquotable": True}
    return {"command": f'aify-env roots add "{read["root"]}"'}


def add_fix(folder: str, host: dict[str, Any]) -> tuple[str, str]:
    """`(command, sentence)` for making `folder` a launch root on `host` (a `launch_host` value): the command ("" when
    there is none) and the one sentence the note and the card both show."""
    hint = add_hint(folder, windows=_environment_uses_windows_paths(host))
    if "command" in hint:
        return hint["command"], (f"on that host, add it as a launch root: `{hint['command']}` (aify-env 0.9.11 or "
                                 "newer), or a folder above it to cover more")
    if hint.get("unquotable"):
        return "", ("on that host, add it, or a folder above it, as a launch root with `aify-env roots add` (aify-env "
                    "0.9.11 or newer), quoted for your shell: its name holds a character this hint does not quote for "
                    "every shell")
    return "", f"`aify-env roots add` refuses it ({hint['refused']}); move it under a launch root"


def outside_launch_roots(workspace: str, environment: dict[str, Any]) -> bool:
    """Whether `environment`'s aify-env reported the folders it starts work in (`metadata.launchRoots`, from its own
    heartbeat) and `workspace` lies outside all of them. No report is no evidence, so False (0.9.2 status fix C). Not
    `advertisedCwdRoots`: that is whatever cwdRoots a caller last sent, which aify-env never sends."""
    roots = (environment.get("metadata") or {}).get("launchRoots") or []
    return bool(roots) and not workspace_within_roots(workspace, roots, windows=_environment_uses_windows_paths(environment))
