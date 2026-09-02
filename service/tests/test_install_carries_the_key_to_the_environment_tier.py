"""A key already in `.env` never reached aify-env, and re-running the installer changed nothing.

THE DEFECT, measured 2026-09-02. `install.sh` resolved the service key on every run -- clients got
it, because `scripts/api-key.sh` reads `.env` -- but the hand-off to aify-env's credential store sat
inside `if [ "$WITH_API_KEY" = true ]`. That flag asks for a key to be GENERATED; it never meant
"this host has no key". So an operator who set `API_KEY` by hand and re-ran the installer got
configured clients and an aify-env with no credential at all.

WHAT THAT LOOKS LIKE FROM OUTSIDE, which is why it went unnoticed: every advertisement to
`/environments/heartbeat` is refused with 401, `advertising` stays false, the aify-comms bridge
correctly keeps describing the host, and aify-env reports healthy throughout. The operator sees
spawns refusing and no environment online, with nothing anywhere naming a credential. It is the same
shape `scripts/api-key.sh` exists to fix one layer up: a key in `.env` the installer would not
propagate, where the obvious remedy -- run the installer again -- makes no difference.

THIS RUNS THE INSTALLER, it does not read it. The existing install tests are static-text checks, and
a static check on this would have passed the whole time the defect existed: the carry line was
present and correct, in a branch that never ran. The only thing that distinguishes the two is
executing the path.

SANDBOXED. `HOME` and `AIFY_HOME` point at a temporary directory and `aify-env` is a stub on PATH, so
nothing here touches the operator's real configuration or credential store. The stub is what the
assertion reads: a credential carried is a credential COMMAND ISSUED, and the argv proves it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

#: What the real `claude` would write. Hashed before and after, so a leak fails loudly instead of
#: silently repointing the operator's MCP server at a directory this test is about to delete.
REAL_CLAUDE_CONFIG = Path.home() / ".claude.json"
REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"

#: The stub records argv here. A fixed path rather than one passed through the environment: the
#: carrier runs the command in its own context, and a log path that arrived as an exported variable
#: silently produced an empty write and a stub that looked like it had never been called.
STUB = """#!/usr/bin/env bash
echo "CALLED: $*" >> "{log}"
echo "aify-comms-testref.key"
"""

#: A `claude` that records its argv and does nothing else. Without it `install.sh` reaches the real
#: CLI, which resolves its config from USERPROFILE and writes the operator's machine.
CLAUDE_STUB = """#!/usr/bin/env bash
echo "CLAUDE: $*" >> "{log}"
exit 0
"""


def bash() -> str | None:
    return shutil.which("bash")


@unittest.skipIf(bash() is None, "bash is required to run install.sh")
@unittest.skipUnless(INSTALL_SH.is_file(), "install.sh is missing")
class InstallCarriesTheKeyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / "calls.log"
        binaries = self.root / "bin"
        binaries.mkdir()
        stub = binaries / "aify-env"
        # Forward slashes: this path is read by bash, and a Windows separator inside a shell
        # redirection is an escape sequence rather than a directory.
        stub.write_text(STUB.format(log=str(self.log).replace("\\", "/")), encoding="utf-8", newline="\n")
        stub.chmod(0o755)
        # A `claude` stub too. `install.sh` registers the MCP server with `claude mcp add`, and
        # the real CLI resolves its config from USERPROFILE -- so with only HOME redirected it
        # rewrote the operator's REAL ~/.claude.json to a path inside this temp dir, which
        # tearDown then deleted. Every later session found aify-comms pointing at nothing.
        claude_stub = binaries / "claude"
        claude_stub.write_text(CLAUDE_STUB.format(log=str(self.log).replace(chr(92), '/')),
                               encoding='utf-8', newline=chr(10))
        claude_stub.chmod(0o755)
        self.binaries = binaries
        #: The seal, recorded before anything runs. A seal that is not checked is one that has
        #: already broken once.
        self._config_before = (REAL_CLAUDE_CONFIG.read_bytes()
                               if REAL_CLAUDE_CONFIG.is_file() else None)

    def tearDown(self):
        self._tmp.cleanup()
        # Restore FIRST, then fail: a test that reports a leak and leaves the operator pointing
        # into a deleted temp directory has done the damage regardless.
        if self._config_before is not None and REAL_CLAUDE_CONFIG.is_file():
            if REAL_CLAUDE_CONFIG.read_bytes() != self._config_before:
                REAL_CLAUDE_CONFIG.write_bytes(self._config_before)
                raise AssertionError(
                    "install.sh wrote the REAL ~/.claude.json despite the sandbox. It has been "
                    "restored, but the stubs no longer cover every path it takes there."
                )

    def _run_installer(self, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        # POSIX-form PATH. A `C:/...` entry is silently ignored by this shell, so the stub is never
        # found and every probe falls through to the real binary -- which reads as "the installer did
        # not carry the key" no matter what the installer does.
        posix_bin = "/" + str(self.binaries).replace(":", "").replace("\\", "/")
        env["PATH"] = posix_bin + os.pathsep.replace(os.pathsep, ":") + env.get("PATH", "")
        env["HOME"] = str(self.root)
        # USERPROFILE too: on Windows the `claude` CLI reads its config path from that, not HOME.
        env["USERPROFILE"] = str(self.root)
        env["AIFY_HOME"] = str(self.root / ".aify-comms")
        return subprocess.run(
            [bash(), str(INSTALL_SH), "--client", "claude", "http://127.0.0.1:8800", *extra],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=900,
        )

    def calls(self) -> str:
        try:
            return self.log.read_text(encoding="utf-8")
        except OSError:
            return ""

    def test_the_stub_is_reachable(self):
        """CONTROL. Without this, an unreachable stub makes every assertion below vacuous -- and that
        is exactly what happened while this test was being written: a Windows-form PATH entry was
        ignored, the real binary answered, and the log stayed empty for the wrong reason."""
        result = subprocess.run(
            [bash(), "-c", "command -v aify-env"],
            env={**os.environ, "PATH": "/" + str(self.binaries).replace(":", "").replace("\\", "/")
                 + ":" + os.environ.get("PATH", "")},
            capture_output=True, text=True, timeout=60,
        )
        self.assertIn(str(self.binaries.name), result.stdout.replace("\\", "/"),
                      f"the stub is not what `aify-env` resolves to: {result.stdout.strip()!r}")

    def test_a_key_already_in_env_is_carried_without_asking_for_one(self):
        """THE DEFECT. No `--with-api-key`: the key is already on the host, and it must still reach
        the tier that needs it."""
        if not (REPO / ".env").is_file():
            self.skipTest(".env is absent, so there is no key for the installer to find")
        result = self._run_installer()
        self.assertEqual(result.returncode, 0, f"installer failed:\n{result.stdout[-2000:]}")
        self.assertIn(
            "credential set --service aify-comms --stdin", self.calls(),
            "the installer did not hand the key to aify-env, so its advertisements will 401 and no "
            "environment will come online -- with nothing naming a credential anywhere",
        )

    def test_it_also_reads_the_credential_back(self):
        """The carrier verifies through the public path rather than trusting its own write. A store
        that accepted the key and could not return it is the failure that leaves everything looking
        configured."""
        if not (REPO / ".env").is_file():
            self.skipTest(".env is absent")
        self._run_installer()
        self.assertIn("credential status --service aify-comms", self.calls())


if __name__ == "__main__":
    unittest.main()
