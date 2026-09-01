"""An empty tracked file is almost always a shell accident, and one of these SHIPPED.

SPLIT-L1, external review round 7. Two 0-byte files named `how` were tracked at the repo root and
at `mcp/stdio/how`. Both arrived in the SAME commit (`21f2b351`), the one whose subject is about
`--mcp-transport <stdio|sse> how the launcher reaches MCP` -- a redirect that captured a word from
the command line, run twice from two directories.

THE SECOND ONE SHIPPED. The Dockerfile does `COPY mcp/ ./mcp/`, so `mcp/stdio/how` went into the
container image, and `install.sh` copies `mcp/stdio` into `~/.aify-comms` on every client, so it went
to every install too. Nothing referenced either file -- verified by widening the search past the two
obvious callers and confirming the method could still find a file that IS referenced.

Harmless in itself. Worth a gate because nothing noticed for as long as they existed, and the next
one will be created the same way: a word left on the end of a command.

DERIVED FROM WHAT GIT TRACKS, not from a walk of the filesystem. An untracked scratch file is nobody's
business; the question is only what the repository carries and therefore ships.

THE EXEMPTIONS ARE NAMED, NOT PATTERNED. `__init__.py` is legitimately empty -- it is how Python marks
a package, and four of them are load-bearing here. `.gitkeep` is the conventional way to track an
otherwise-empty directory. Both are files whose emptiness IS their content. Anything else that is
empty is a file somebody meant to write something into.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Files whose emptiness is deliberate and meaningful.
EMPTY_ON_PURPOSE = {"__init__.py", ".gitkeep", ".gitignore", ".npmignore"}


def tracked_files() -> list[Path]:
    """Every path git tracks, as absolute paths."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return [REPO / name for name in out.split("\0") if name]


def tracked_empty_files() -> list[str]:
    """Repo-relative paths of tracked files with no content, exemptions removed."""
    empty = []
    for path in tracked_files():
        # A tracked path can be absent from a working tree (sparse checkout, a submodule), and that
        # is not the same as empty. Missing is not a finding here.
        if not path.is_file():
            continue
        if path.name in EMPTY_ON_PURPOSE:
            continue
        if path.stat().st_size == 0:
            empty.append(path.relative_to(REPO).as_posix())
    return sorted(empty)


class NoAccidentalEmptyFilesTests(unittest.TestCase):
    def test_the_scan_sees_a_real_repository(self):
        """POSITIVE CONTROL. A scan that listed nothing would make the gate below vacuous."""
        tracked = tracked_files()
        self.assertGreater(len(tracked), 500, f"git ls-files returned {len(tracked)} paths")
        names = {p.name for p in tracked}
        self.assertIn("install.sh", names, "the scan is not looking at this repository")

    def test_the_exemptions_are_real_files_that_are_really_empty(self):
        """POSITIVE CONTROL on the exemption list, which is the only way to hide a finding here.

        If `__init__.py` were exempted and no empty one existed, the exemption would be dead weight
        that a reader would trust anyway. It is live: four of them are tracked and empty.
        """
        exempted_and_empty = [
            p.relative_to(REPO).as_posix()
            for p in tracked_files()
            if p.is_file() and p.name in EMPTY_ON_PURPOSE and p.stat().st_size == 0
        ]
        self.assertTrue(
            exempted_and_empty,
            "nothing empty is exempted, so the exemption list is untested and may hide anything",
        )

    def test_no_tracked_file_is_accidentally_empty(self):
        empty = tracked_empty_files()
        self.assertEqual(
            empty, [],
            "these tracked files have no content:\n  " + "\n  ".join(empty)
            + "\nAn empty tracked file is nearly always a redirect that captured a word from a "
            "command line. If one is deliberate, name it in EMPTY_ON_PURPOSE with the reason; if it "
            "is not, delete it. Note that anything under `mcp/` ships to the container AND to every "
            "client install.",
        )


if __name__ == "__main__":
    unittest.main()
