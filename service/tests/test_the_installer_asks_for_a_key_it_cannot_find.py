"""The installer asked for nothing when a host had no key, and every call then 401'd.

THE DEFECT, measured 2026-09-02. `install.sh` resolved the service key from the shell or `.env` and,
finding neither, carried on silently. A host was configured with no key at all; every advertisement
to the service was refused with 401; both sides reported healthy; and the operator lost a day to a
fleet that would not spawn, with no component anywhere naming a credential. An installer that
proceeds with a missing value is how that happens.

WHY ASKING IS NOT THE SAME AS GENERATING. `--generate` MINTS a key, which is right for a fresh host
with nobody to ask. Asking is right when a key already exists somewhere else -- another machine, a
password manager, a second service -- and only this host is missing it. Silence guessed neither, and
guessing wrong is worse than either: a generated key on a host that should have joined an existing
fleet 401s exactly as loudly as no key at all.

THESE TEST THE UNATTENDED BRANCH, which is the one nothing routine reaches. A prompt cannot be driven
here, but the property that matters when nobody is watching can be: with no terminal the installer
must ask nobody, print nothing, and exit 0 -- the same as the read-only path -- because CI, a service
manager and an agent all run it that way, and a script that hangs or errors there is worse than one
that says nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API_KEY_SH = REPO / "scripts" / "api-key.sh"
INSTALL_SH = REPO / "install.sh"


def bash() -> str | None:
    return shutil.which("bash")


@unittest.skipIf(bash() is None, "bash is required")
@unittest.skipUnless(API_KEY_SH.is_file(), "scripts/api-key.sh is missing")
class TheInstallerAsksTests(unittest.TestCase):
    def _run(self, *args, env_file_contents: str = "") -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(env_file_contents, encoding="utf-8")
            env = dict(os.environ)
            # The SHELL half of the resolver is sealed too: an ambient key on the machine running
            # these tests would make every assertion below pass for the wrong reason.
            env.pop("CLAUDE_MCP_API_KEY", None)
            env.pop("AIFY_API_KEY", None)
            env["AIFY_ENV_FILE"] = str(env_file)
            return subprocess.run(
                [bash(), str(API_KEY_SH), *args],
                env=env, capture_output=True, text=True, timeout=60,
                stdin=subprocess.DEVNULL,
            )

    def test_with_no_key_and_no_terminal_it_asks_nobody_and_says_nothing(self):
        """The unattended branch. A prompt written to a device that is not there prints
        '/dev/tty: No such device or address' on every run, which teaches an operator that errors
        there are normal -- and an installer that hangs waiting for an answer nobody can give is
        worse still."""
        result = self._run("--ask")
        self.assertEqual(result.stdout.strip(), "", "a key was produced from nowhere")
        self.assertEqual(result.stderr.strip(), "",
                         f"the unattended path printed: {result.stderr.strip()!r}")
        self.assertEqual(result.returncode, 0,
                         "no key and nobody to ask is not a failure; the caller decides what to do")

    def test_it_matches_the_read_only_path_exactly_when_there_is_nothing_to_ask(self):
        """CONTROL, and the reason the codes matter: `install.sh` calls this and treats a non-zero
        exit as a problem. If `--ask` diverged from the plain read, adding the ask would have turned
        every keyless install into a reported failure."""
        asked = self._run("--ask")
        plain = self._run()
        self.assertEqual((asked.returncode, asked.stdout.strip()),
                         (plain.returncode, plain.stdout.strip()))

    def test_a_key_already_present_is_returned_and_NOT_asked_about(self):
        """Re-running the installer is the update path. Asking again for a key the host already
        holds trains an operator to paste secrets nothing needed."""
        result = self._run("--ask", env_file_contents="API_KEY=a-key-that-is-long-enough-to-pass\n")
        self.assertEqual(result.stdout.strip(), "a-key-that-is-long-enough-to-pass")
        self.assertEqual(result.returncode, 0)

    def test_a_weak_key_is_reported_rather_than_refused_on_the_read_path(self):
        """Unchanged by the ask, and pinned while this file is being edited: a weak key already in
        use is the operator's running state -- the service is on it and every installed bridge holds
        it -- so aborting an ordinary install neither rotates it nor helps."""
        result = self._run("--ask", env_file_contents="API_KEY=short\n")
        self.assertEqual(result.stdout.strip(), "short")
        self.assertIn("WARNING", result.stderr)

    @unittest.skipUnless(INSTALL_SH.is_file(), "install.sh is missing")
    def test_the_installer_actually_calls_the_asking_path(self):
        """A helper proven alone leaves the call to it unproven, and this repo has shipped exactly
        that. `install.sh` cannot be executed here without a full install, so the call is read --
        which is weaker than running it, and is said so rather than dressed up."""
        text = INSTALL_SH.read_text(encoding="utf-8")
        self.assertIn("scripts/api-key.sh\" --ask", text,
                      "install.sh resolves the key without the ask, so a host with none is "
                      "configured silently -- the defect this file exists for")


if __name__ == "__main__":
    unittest.main()
