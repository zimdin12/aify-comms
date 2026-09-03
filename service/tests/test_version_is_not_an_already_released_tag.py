"""VERSION must not claim to be a release that already shipped.

WHAT EVERY EXISTING VERSION GATE MISSES. `test_version_single_source.py` and
`version-consistency.test.js` prove the five declarations agree WITH EACH OTHER -- VERSION,
`mcp/stdio/version.js`, its `package.json` and `package-lock.json`, and `.claude-plugin/plugin.json`.
Five files can agree perfectly on a number that stopped being true the moment the tag was cut.

MEASURED 2026-09-03, and it is exactly the shape D4 filed: VERSION read `0.6.1`, the `v0.6.1` tag sat
at `b7d77fdf`, and HEAD was 24 commits past it. Every version gate was green. Earlier the same gap
let VERSION read `0.6.0` while HEAD was 407 commits past that tag, and the v0.6.1 bump was a
hand-correction rather than something a test demanded -- which means the next one would have been a
hand-correction too, remembered or not.

WHY IT IS NOT COSMETIC. The number reaches the service's `/health`, the root endpoint,
`/openapi.json`, Dashboard Next and every MCP handshake, and `BRIDGE_VERSION` carries it to the
control plane as `bridgeVersion`. An agent reading `0.6.1` from a live bridge cannot tell a genuine
v0.6.1 from a tree 24 commits past it, and that is the same class of question `aify-comms doctor`
exists to answer honestly.

WHAT IT DOES NOT DO. It does not check that VERSION is the NEXT version, or that it increases
monotonically, or that a tag exists at all. Development against an unreleased number is the ordinary
state and must stay silent. The only claim refused is the false one: "I am the release `vX`" from a
tree that is not the commit `vX` names.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    """Git output, or "" when git cannot answer. A tarball is not a failing repo."""
    try:
        done = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _declared_version() -> str:
    for line in (REPO_ROOT / "VERSION").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


class VersionIsNotAnAlreadyReleasedTagTests(unittest.TestCase):
    def test_the_instrument_can_see_this_repository_at_all(self) -> None:
        """POSITIVE CONTROL.

        Every assertion below is satisfied when git answers nothing: no tags means no released tag to
        collide with, so the gate would be loudest-silent exactly when it had stopped working. This
        names the two facts the real assertion depends on.
        """
        self.assertTrue(_declared_version(), "VERSION is empty or unreadable")
        self.assertTrue(_git("rev-parse", "HEAD"), "git could not name HEAD")
        self.assertTrue(_git("tag", "-l"), "this repository reports NO tags, so nothing can collide")

    def test_VERSION_DOES_NOT_NAME_A_TAG_THIS_TREE_IS_PAST(self) -> None:
        version = _declared_version()
        tag = f"v{version}"
        tagged = _git("rev-list", "-n", "1", tag)
        if not tagged:
            # The ordinary development state: the number has not been released yet. Silent on
            # purpose -- demanding that a tag exist would demand a release per commit.
            return
        head = _git("rev-parse", "HEAD")
        self.assertTrue(head, "git could not name HEAD")
        if head == tagged:
            return  # sitting exactly on the release: the claim is true
        behind = _git("rev-list", "--count", f"{tag}..HEAD")
        self.assertEqual(
            head,
            tagged,
            f"VERSION says {version}, but {tag} was cut at {tagged[:8]} and HEAD is {head[:8]}"
            f" -- {behind or 'several'} commit(s) past it. Every other version gate is green,"
            f" because they only check the five declarations agree with each other."
            f" Bump VERSION (and version.js, both package files, plugin.json) to the version being"
            f" WORKED ON, so a live bridge reporting {version} means the release and not a tree"
            f" that has moved on from it.",
        )

    def test_a_tag_that_does_not_exist_is_not_an_error(self) -> None:
        """The development state, asserted so nobody 'fixes' it into a release-per-commit rule.

        Driven through the same helper the real test uses rather than a fake, so a change to how tags
        are resolved cannot leave this passing against a different mechanism.
        """
        self.assertEqual(_git("rev-list", "-n", "1", "v0.0.0-never-released"), "")


if __name__ == "__main__":
    unittest.main()
