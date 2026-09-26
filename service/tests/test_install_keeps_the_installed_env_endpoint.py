"""install.sh keeps the aify-env endpoint already installed, unless told another.

`install.sh --env-endpoint <url>` bakes the aify-env the doctor asks into the `aify-comms` launcher.
v0.6.22 read that value back on a reinstall; 0.7.0 deleted the read-back and fixed only redeploy.sh, so
running install.sh directly (which the doctor's own fix lines tell the operator to do) reset a custom
endpoint to 127.0.0.1:8802. The doctor then read a healthy aify-env as unreachable, and a red
spawn-delegation row invites starting a second aify-env, the action that reaps the fleet (v0.7.1 review,
B2/W06).

This runs the REAL install.sh in `--emit-wrappers` mode, which renders the launchers into a scratch
directory and exits before npm or any config change, with HOME and the registry pointed at scratch.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
TEMPLATES = REPO / "mcp" / "stdio" / "node_modules" / "aify-wrapper" / "wrappers"
CUSTOM = "http://127.0.0.1:9000"


def _posix(path) -> str:
    return str(path).replace("\\", "/")


class InstallKeepsTheInstalledEnvEndpointTests(unittest.TestCase):
    def setUp(self):
        self.bash = shutil.which("bash")
        if not self.bash:
            self.skipTest("bash not on PATH")
        if not TEMPLATES.is_dir():
            self.skipTest("aify-wrapper package not installed - run 'npm install' in mcp/stdio")
        self.tmp = Path(tempfile.mkdtemp(prefix="aify-env-endpoint-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "bin"
        self.out.mkdir()

    def _install(self, *extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_", "CLAUDE_MCP"))}
        env.update(HOME=_posix(self.tmp), USERPROFILE=_posix(self.tmp),
                   AIFY_SERVICE_REGISTRY=_posix(self.tmp / "services.json"))
        return subprocess.run(
            [self.bash, _posix(INSTALL_SH), "--client", "claude", "http://127.0.0.1:8800", *extra,
             "--emit-wrappers", _posix(self.out)],
            capture_output=True, text=True, env=env, timeout=300,
        )

    def _baked(self) -> str:
        for line in (self.out / "aify-comms").read_text(encoding="utf-8").splitlines():
            if line.startswith("export AIFY_ENV_ENDPOINT="):
                return line.split("=", 1)[1].strip('"')
        raise AssertionError("the launcher bakes no AIFY_ENV_ENDPOINT")

    def _seed_installed(self, endpoint: str) -> None:
        (self.out / "aify-comms").write_text(f'#!/bin/bash\nexport AIFY_ENV_ENDPOINT="{endpoint}"\n', encoding="utf-8")

    def test_a_reinstall_without_the_flag_keeps_the_installed_endpoint(self):
        self._seed_installed(CUSTOM)
        done = self._install()
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])
        self.assertEqual(self._baked(), CUSTOM)

    def test_control_the_flag_still_overrides_what_is_installed(self):
        self._seed_installed(CUSTOM)
        done = self._install("--env-endpoint", "http://127.0.0.1:9100")
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])
        self.assertEqual(self._baked(), "http://127.0.0.1:9100")

    def test_control_nothing_installed_takes_the_default(self):
        done = self._install()
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])
        self.assertEqual(self._baked(), "http://127.0.0.1:8802")

    def test_the_flag_without_a_url_is_refused_rather_than_ignored(self):
        done = self._install("--env-endpoint", "--with-hook")
        self.assertNotEqual(done.returncode, 0, "a bare --env-endpoint was silently ignored")
        self.assertIn("--env-endpoint", done.stderr)
