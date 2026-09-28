"""install.sh writes the Windows user PATH only for this profile's own ~/.local/bin (2026-09-28).

A run with a temporary HOME registered that home's .local\\bin in the machine's user PATH. 84 dead
Temp\\tmp*\\.local\\bin entries and three sandbox hermes bins took it to 9,018 characters, and
`claude-aify` in a new terminal then reported `claude` not found. `scripts/add-to-user-path.sh` now
writes only for the directory `cygpath -F 40` names as this profile's.

`powershell.exe` is replaced by an exported shell function that records its call, so no case here can
touch the real PATH, including the one that is meant to reach the writer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "add-to-user-path.sh"
BASH = shutil.which("bash")
STUB = 'powershell.exe() { printf "%s\\n" "$AIFY_SHIM_DIR" >> "$STUB_LOG"; }; export -f powershell.exe; exec bash "$@"'


def profile_bin() -> str:
    profile = subprocess.run(["cygpath", "-F", "40"], capture_output=True, text=True, check=True).stdout.strip()
    return f"{profile}/.local/bin"


@unittest.skipUnless(BASH and shutil.which("cygpath"), "the user PATH exists only on Windows (Git Bash)")
class TheUserPathTakesOnlyThisProfilesBin(unittest.TestCase):
    def run_helper(self, directory: str, **env_extra: str) -> tuple[list[str], str]:
        with tempfile.TemporaryDirectory() as scratch:
            log = Path(scratch) / "powershell-calls.txt"
            env = {**os.environ, "STUB_LOG": str(log), **env_extra}
            done = subprocess.run(
                [BASH, "-c", STUB, "stub", str(HELPER), directory],
                env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
            return calls, done.stderr

    def test_a_temporary_home_never_reaches_the_writer(self):
        with tempfile.TemporaryDirectory() as home:
            calls, stderr = self.run_helper(f"{home}/.local/bin", HOME=home, USERPROFILE=home)
        self.assertEqual(calls, [], "a temporary home's bin reached the PATH writer")
        self.assertIn("left alone", stderr)

    def test_a_path_that_cannot_be_read_never_reaches_the_writer(self):
        """Review of 7f638a65: two failed conversions printed nothing, compared equal, and passed."""
        broken_cygpath = 'cygpath() { if [ "$1" = "-F" ]; then echo /c/Users/someone; else return 1; fi; }; export -f cygpath; '
        with tempfile.TemporaryDirectory() as scratch:
            log = Path(scratch) / "powershell-calls.txt"
            done = subprocess.run(
                [BASH, "-c", broken_cygpath + STUB, "stub", str(HELPER), "C:/Temp/evil/.local/bin"],
                env={**os.environ, "STUB_LOG": str(log)},
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertFalse(log.exists(), "an unreadable path reached the PATH writer")
            self.assertIn("left alone", done.stderr)

    def test_CONTROL_this_profiles_bin_reaches_the_writer(self):
        """Without this, a helper that never writes at all would pass the case above."""
        calls, _ = self.run_helper(profile_bin())
        self.assertEqual(len(calls), 1, "this profile's own bin must still be added")
        self.assertTrue(calls[0].lower().endswith("\\.local\\bin"), calls)

    def test_CONTROL_the_stub_is_what_answered(self):
        """The real powershell.exe would write the real PATH; prove the stub shadows it."""
        with tempfile.TemporaryDirectory() as scratch:
            log = Path(scratch) / "calls.txt"
            done = subprocess.run(
                [BASH, "-c", STUB, "stub", "-c", "powershell.exe probe"],
                env={**os.environ, "STUB_LOG": str(log), "AIFY_SHIM_DIR": "probe"},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["probe"])


if __name__ == "__main__":
    unittest.main()
