"""A workspace inside an advertised root was refused as "outside the roots", on Windows.

`_workspace_root_for` checks a requested workspace against the `cwdRoots` its environment advertises,
and refuses with 400 when it is outside. The comparison folded BACKSLASHES but not CASE:

    root      C:/Docker
    workspace c:/Docker/proj      -> "outside the roots advertised by environment"

Windows filesystems are case-insensitive, so that path is literally inside the root. Drive-letter
case differs freely between sources — `process.cwd()` reports one form, an operator-typed or
config-file path another — so this needed no unusual input, and the message told the operator the
workspace was somewhere it was not.

POSIX MUST NOT BE FOLDED, which is why the fix is gated on the environment rather than applied
everywhere: `/srv/Repo` and `/srv/repo` are two different directories on Linux, and folding case
there would admit a workspace genuinely outside the advertised root — turning a wrong refusal into a
wrong ADMISSION, which is the worse direction for a boundary check.

This refusal was one of 41 operator-facing 4xx messages in the service that no test had ever
exercised (97 total). It is the fourth defect this session of the shape "a message names a cause the
code did not establish".
"""

from __future__ import annotations

import unittest

from fastapi import HTTPException

from service.api_core.workspace import _workspace_root_for

WINDOWS_ENV = {"id": "env-win", "machineId": "win32:box", "cwdRoots": ["C:/Docker"]}
POSIX_ENV = {"id": "env-linux", "machineId": "linux:box", "cwdRoots": ["/srv/Repo"]}


def _refusal(environment, workspace) -> str:
    try:
        _workspace_root_for(environment, workspace)
    except HTTPException as exc:
        assert exc.status_code == 400, exc.status_code
        return str(exc.detail)
    raise AssertionError(f"expected {workspace!r} to be refused")


class WorkspaceRootMatchingTests(unittest.TestCase):
    # ── Windows: case must not decide whether a path is inside its root ──────────────────────

    def test_windows_accepts_every_case_form_of_the_same_path(self):
        for workspace in (
            "C:/Docker/proj",     # exactly as advertised
            "c:/Docker/proj",     # lowercase drive letter — the common one
            "C:/docker/proj",     # lowercase directory
            "c:/DOCKER/proj",     # both
            "C:\\Docker\\proj",   # backslashes, which already worked
        ):
            with self.subTest(workspace=workspace):
                self.assertEqual(
                    _workspace_root_for(WINDOWS_ENV, workspace), "C:/Docker",
                    "a path inside the advertised root was refused because its case differed",
                )

    def test_windows_returns_the_root_as_advertised_not_as_requested(self):
        """The caller uses this to key spawn/console state, so it must be the environment's own
        spelling — otherwise two case forms of one root become two keys."""
        self.assertEqual(_workspace_root_for(WINDOWS_ENV, "c:/docker/proj"), "C:/Docker")

    def test_windows_still_refuses_a_workspace_that_is_genuinely_outside(self):
        detail = _refusal(WINDOWS_ENV, "C:/Other/proj")
        self.assertIn("outside the roots advertised", detail)
        self.assertIn("env-win", detail, "the operator must know WHICH environment refused")

    def test_a_sibling_directory_sharing_a_prefix_is_not_inside_the_root(self):
        """`C:/DockerOther` starts with `C:/Docker` as a STRING but is a different directory. The
        separator in the boundary check is what stops that, and folding case must not lose it."""
        for workspace in ("C:/DockerOther/proj", "c:/dockerother/proj"):
            with self.subTest(workspace=workspace):
                # `_refusal` itself fails if the path is ACCEPTED, which is the property under test.
                self.assertIn("outside the roots advertised", _refusal(WINDOWS_ENV, workspace))

    # ── POSIX: case is meaning, and must keep deciding ──────────────────────────────────────

    def test_posix_keeps_case_sensitivity(self):
        self.assertEqual(_workspace_root_for(POSIX_ENV, "/srv/Repo/p"), "/srv/Repo")
        detail = _refusal(POSIX_ENV, "/srv/repo/p")
        self.assertIn("outside the roots advertised", detail)

    # ── the degenerate inputs the caller actually passes ────────────────────────────────────

    def test_no_roots_advertised_yields_no_root_rather_than_a_refusal(self):
        """`_workspace_for_environment` calls this while resolving a default, so an environment that
        advertises nothing must not raise — it has no root to be outside of."""
        self.assertEqual(_workspace_root_for({"id": "e", "cwdRoots": []}, "C:/x"), "")

    def test_an_empty_workspace_falls_back_to_the_first_root(self):
        self.assertEqual(_workspace_root_for(WINDOWS_ENV, ""), "C:/Docker")

    def test_a_trailing_separator_does_not_change_the_answer(self):
        self.assertEqual(_workspace_root_for(WINDOWS_ENV, "C:/Docker/"), "C:/Docker")
        self.assertEqual(
            _workspace_root_for({"id": "e", "machineId": "win32:b", "cwdRoots": ["C:/Docker/"]},
                                "C:/Docker/proj"),
            "C:/Docker/",
        )
