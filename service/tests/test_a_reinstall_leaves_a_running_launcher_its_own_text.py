"""A reinstall REPLACES each launcher file; it never rewrites one in place.

bash reads a script while it runs it, and a `*-aify` launcher is still running for as long as its runtime
is: when the runtime exits, the launcher reads on from its byte offset. `render_wrapper_template` wrote
claude-aify, codex-aify and pi-aify in place (`printf > "$target"`), so a reinstall under a live agent
handed each running launcher the middle of a different file on its way out. Measured 2026-09-15: seven
claude-aify launchers were running while a reinstall would have added nine lines near the top of each.
Only the hermes writer staged its file and renamed it.

What this proves is the mechanism a running reader depends on: after a second render, the path names a
NEW file, so the old one is still the file every running launcher holds. The control shows the same
identity check reports a file rewritten in place as the SAME file, so a pass is not an instrument that
always says "new".
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

#: Each client, and the launcher files its render writes.
LAUNCHERS = {
    "claude": ["claude-aify"],
    "codex": ["codex-aify"],
    "hermes": ["hermes-aify"],
    "pi": ["pi-aify", "omp-aify"],
}


def _posix(path) -> str:
    return str(path).replace("\\", "/")


class AReinstallReplacesEveryLauncher(unittest.TestCase):
    def setUp(self):
        self.bash = shutil.which("bash")
        if not self.bash:
            self.skipTest("bash not on PATH")
        if not TEMPLATES.is_dir():
            self.skipTest("aify-wrapper package not installed - run 'npm install' in mcp/stdio")
        self.tmp = Path(tempfile.mkdtemp(prefix="aify-reinstall-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _render(self, client: str, endpoint: str) -> None:
        env = {**os.environ, "AIFY_SERVICE_REGISTRY": _posix(self.tmp / "services.json")}
        result = subprocess.run(
            [self.bash, _posix(INSTALL_SH), "--client", client, endpoint, "--emit-wrappers", _posix(self.tmp / client)],
            capture_output=True, text=True, env=env, timeout=300,
        )
        self.assertEqual(result.returncode, 0, f"{client} render failed: {result.stdout[-1500:]}{result.stderr[-1500:]}")

    def _bash_reader(self, path: Path) -> subprocess.Popen:
        """bash holding `path` open at the end of its first line, until it is sent a line on stdin."""
        script = 'exec 3<"$1"; IFS= read -r _ <&3; echo held; IFS= read -r _; cat <&3'
        reader = subprocess.Popen(
            [self.bash, "-c", script, "reader", _posix(path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        self.assertEqual(reader.stdout.readline(), b"held\n", f"control: the reader opened {path.name}")
        return reader

    def test_a_second_render_puts_a_new_file_at_every_launcher_path(self):
        for client, names in LAUNCHERS.items():
            with self.subTest(client=client):
                self._render(client, "http://127.0.0.2:1")
                paths = [self.tmp / client / name for name in names]
                before = {path.name: (path.stat().st_ino, path.read_text(encoding="utf-8")) for path in paths}
                # A running launcher, as bash holds it: one line read, then a wait (the runtime), then the
                # rest read on from that offset. A Python handle is not one: on Windows it refuses the
                # rename that MSYS bash's own handle allows.
                readers = [self._bash_reader(path) for path in paths]
                try:
                    self._render(client, "http://127.0.0.2:2")
                    for path, reader in zip(paths, readers):
                        old_ino, old_text = before[path.name]
                        self.assertNotEqual(path.stat().st_ino, old_ino, f"{path.name} was rewritten in place")
                        self.assertIn("127.0.0.2:2", path.read_text(encoding="utf-8"), f"control: {path.name} was re-rendered")
                        rest, _ = reader.communicate(b"go\n", timeout=60)
                        self.assertEqual(rest.decode("utf-8"), old_text.split("\n", 1)[1], f"a running reader of {path.name} read on into the new text")
                        self.assertTrue(os.access(path, os.X_OK) or os.name == "nt", f"{path.name} lost its execute bit")
                finally:
                    for reader in readers:
                        if reader.poll() is None:
                            reader.kill()
                leftovers = [p.name for p in (self.tmp / client).iterdir() if ".render." in p.name or ".tmp." in p.name]
                self.assertEqual(leftovers, [], "a staged file was left beside the launchers")

    def test_control_a_file_rewritten_in_place_keeps_its_identity(self):
        path = self.tmp / "in-place"
        path.write_text("one\n", encoding="utf-8")
        before = path.stat().st_ino
        with open(path, "r+", encoding="utf-8") as handle:
            handle.write("two\n")
        self.assertEqual(path.stat().st_ino, before)
        self.assertNotEqual(before, 0, "this filesystem reports no file identity, so the check above proves nothing")


if __name__ == "__main__":
    unittest.main()
