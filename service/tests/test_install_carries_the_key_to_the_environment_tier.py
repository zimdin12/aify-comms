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

IT RUNS THE REAL INSTALLER, SO TWO AT ONCE FAIL EACH OTHER. This is the only test here that executes
`install.sh`, and two concurrent installs race over the same stub log and the same global state. It
produced a false red twice on 2026-09-02, both times because a targeted `-k` run was started
alongside a full sweep. If this file fails in the full suite and PASSES on its own, that is what you
are looking at, not a defect.

SANDBOXED THREE WAYS, AND THE SANDBOX IS ASSERTED RATHER THAN ASSUMED. An earlier version set `HOME`
and `AIFY_HOME` only -- and `install.sh` registers the MCP server by invoking `claude mcp add`, whose
CLI resolves its own config path from `USERPROFILE` on Windows and ignored both. The installer duly
rewrote the operator's REAL `~/.claude.json` with a server path inside this test's temporary
directory, which `tearDown` then deleted; every later session found aify-comms pointing at nothing.

So: `claude` and `aify-env` are both stubs on PATH, so the real CLIs are never reached;
`HOME`, `USERPROFILE` and `AIFY_HOME` all point into the temp root; and the real config file is
hashed before and after, with a restore-and-fail if it moved. A seal that is not checked is a seal
that has already broken once.

The stubs are what the assertions read: a credential carried is a credential COMMAND ISSUED, and the
argv proves it.
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
    #: ONE INSTALLER RUN FOR THE WHOLE CLASS, because it is the same run every assertion reads.
    #: Executing `install.sh` costs about a minute, and this file ran it twice to ask two questions
    #: about one recorded behaviour -- which is the shape T1 exists to remove: cost without coverage.
    #: The state asserted below is a LOG OF WHAT HAPPENED, so sharing it across tests shares
    #: evidence rather than mutable state.
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.log = cls.root / "calls.log"
        binaries = cls.root / "bin"
        binaries.mkdir()
        log = str(cls.log).replace(chr(92), "/")
        # Forward slashes: these paths are read by bash, and a Windows separator inside a shell
        # redirection is an escape sequence rather than a directory.
        for name, body in (("aify-env", STUB), ("claude", CLAUDE_STUB)):
            stub = binaries / name
            stub.write_text(body.format(log=log), encoding="utf-8", newline=chr(10))
            stub.chmod(0o755)
        cls.binaries = binaries
        #: The seal, recorded before anything runs. A seal that is not checked is one that has
        #: already broken once.
        cls._config_before = (REAL_CLAUDE_CONFIG.read_bytes()
                              if REAL_CLAUDE_CONFIG.is_file() else None)
        cls.result = cls._install() if (REPO / ".env").is_file() else None

    @classmethod
    def tearDownClass(cls):
        # THE SEAL IS CHECKED BEFORE THE CLEANUP, and that ordering is load-bearing. It ran after
        # until 2026-09-02, so a cleanup that raised -- which it does on Windows whenever the
        # installer leaves a handle open in the temp tree -- skipped the check entirely. The one
        # safety assertion in this file was reachable only when nothing else went wrong.
        #
        # And the cleanup no longer raises: a temp directory this test cannot delete is housekeeping,
        # not a result. It errored the whole class in the full suite while passing in isolation,
        # which is the most expensive shape a test can have -- green alone, red together, and neither
        # about the thing under test.
        try:
            if cls._config_before is not None and REAL_CLAUDE_CONFIG.is_file():
                if REAL_CLAUDE_CONFIG.read_bytes() != cls._config_before:
                    REAL_CLAUDE_CONFIG.write_bytes(cls._config_before)
                    raise AssertionError(
                        "install.sh wrote the REAL ~/.claude.json despite the sandbox. It has been "
                        "restored, but the stubs no longer cover every path it takes there."
                    )
        finally:
            # Best-effort, and last. A leftover temp directory is swept by the runner's own per-run
            # root; a failure here must never turn a passing class red.
            try:
                cls._tmp.cleanup()
            except OSError:
                pass

    @classmethod
    def _install(cls, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        # POSIX-form PATH. A `C:/...` entry is silently ignored by this shell, so the stub is never
        # found and every probe falls through to the real binary -- which reads as "the installer did
        # not carry the key" no matter what the installer does.
        posix_bin = "/" + str(cls.binaries).replace(":", "").replace("\\", "/")
        env["PATH"] = posix_bin + os.pathsep.replace(os.pathsep, ":") + env.get("PATH", "")
        env["HOME"] = str(cls.root)
        # USERPROFILE too: on Windows the `claude` CLI reads its config path from that, not HOME.
        env["USERPROFILE"] = str(cls.root)
        env["AIFY_HOME"] = str(cls.root / ".aify-comms")
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
        self.assertIsNotNone(self.result, "the installer did not run, so this proves nothing")
        self.assertEqual(self.result.returncode, 0, f"installer failed: {self.result.stdout[-2000:]}")
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
        self.assertIn("credential status --service aify-comms", self.calls())


if __name__ == "__main__":
    unittest.main()
