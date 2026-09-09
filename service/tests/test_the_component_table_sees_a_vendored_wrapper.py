"""The installer's closing table said aify-wrapper was MISSING after a correct install.

Observed 2026-09-08: `scripts/components.sh` proves a component by a command on PATH, and for
aify-wrapper that command is `aify-wrapper-check`, which only a separate global npm install provides
and no installer runs. The launchers had been rendered from the pinned package under
mcp/stdio/node_modules -- the component was there, in the only place the bridge reads it from.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "components.sh"


def _sandbox(with_vendored: bool) -> Path:
    root = Path(tempfile.mkdtemp(prefix="components-"))
    (root / "scripts").mkdir()
    shutil.copy(SCRIPT, root / "scripts" / "components.sh")
    if with_vendored:
        pkg = root / "mcp" / "stdio" / "node_modules" / "aify-wrapper"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({"name": "aify-wrapper", "version": "9.9.9"}))
    return root


def _rows(root: Path) -> dict[str, tuple[str, str]]:
    env = {**os.environ, "PATH": "/usr/bin:/bin"}  # no aify-* commands on PATH
    # A RELATIVE PATH FROM `cwd`, because a Windows path handed to bash loses its separators:
    # `C:\Users\...\components.sh` reaches the shell as `C:UsersADMINI~1...` and exits 127 with
    # no rows at all, which surfaces as a KeyError on the row rather than as "the script did not
    # run". Every assertion below then fails for a reason that has nothing to do with the subject.
    done = subprocess.run(["bash", "scripts/components.sh"],
                          cwd=root, capture_output=True, text=True, env=env)
    out: dict[str, tuple[str, str]] = {}
    for line in done.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            out[parts[0]] = (parts[1], parts[2])
    # AN EMPTY TABLE IS THE REFUSAL, NOT A NON-ZERO EXIT. The script exits non-zero when any
    # component is MISSING, which is what this sandbox constructs on purpose -- guarding on the
    # exit status rejected a run that had produced exactly the right rows and reported that the
    # script had not run. No rows is the condition that actually went wrong.
    if not out:
        raise AssertionError(
            f"components.sh produced no rows (exit {done.returncode}), so this test judged "
            f"nothing: {done.stdout[-300:]}{done.stderr[-300:]}")
    return out


class VendoredWrapper(unittest.TestCase):
    def test_a_vendored_wrapper_counts_as_installed_with_its_version(self):
        rows = _rows(_sandbox(with_vendored=True))
        self.assertEqual(rows["aify-wrapper"], ("installed", "9.9.9"), rows)

    def test_no_wrapper_anywhere_is_still_reported_missing(self):
        rows = _rows(_sandbox(with_vendored=False))
        self.assertEqual(rows["aify-wrapper"][0], "missing", rows)


if __name__ == "__main__":
    unittest.main()
