"""Repository inputs required by the production Docker build."""

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RepositoryBuildContractTests(unittest.TestCase):
    def test_installer_help_is_side_effect_free(self):
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("`omp --mode rpc`", result.stdout)

    def test_stdio_lockfile_required_by_dockerfile_is_versioned_input(self):
        lockfile = ROOT / "mcp" / "stdio" / "package-lock.json"
        self.assertTrue(
            lockfile.is_file(),
            "Dockerfile COPY requires mcp/stdio/package-lock.json in a clean checkout",
        )
        ignored_lines = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertNotIn("mcp/stdio/package-lock.json", ignored_lines)

        package = json.loads((lockfile.parent / "package.json").read_text(encoding="utf-8"))
        locked = json.loads(lockfile.read_text(encoding="utf-8"))
        self.assertEqual(locked["name"], package["name"])
        self.assertEqual(locked["version"], package["version"])
        self.assertEqual(locked["packages"][""]["dependencies"], package["dependencies"])

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY mcp/stdio/package.json mcp/stdio/package-lock.json", dockerfile)
        self.assertIn("RUN cd mcp/stdio && npm ci", dockerfile)

        if (ROOT / ".git").exists():
            repository = subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                check=False,
            )
            if repository.returncode == 0:
                tracked = subprocess.run(
                    ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "mcp/stdio/package-lock.json"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(tracked.returncode, 0, tracked.stderr)


if __name__ == "__main__":
    unittest.main()
