"""The release version is declared ONCE, and drift fails the suite.

Before 2026-08-03 four components each carried their own literal and none tracked a
release: the service reported 0.1.0 (a stale SERVICE_VERSION in .env), config.py's own
default said 4.0.0, api_v2's root endpoint hardcoded 4.0.0, the dashboard hardcoded
0.1.0 — while the project shipped v0.1, v0.1.1 and v0.1.2. Nothing was wired to a
release, so no bump could have propagated.

The one source is the repo-root VERSION file. scripts/stamp.sh bakes it into
service/_build_stamp.json (the container has no repo root — the same reason the sha is
stamped) and config.py reads it from there.
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "service"


def _canonical_version() -> str:
    for line in (REPO_ROOT / "VERSION").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise AssertionError("VERSION contains no usable line")


class VersionSingleSourceTests(unittest.TestCase):
    def test_version_file_is_a_version(self):
        self.assertRegex(_canonical_version(), r"^\d+\.\d+\.\d+([-+].+)?$")

    def test_no_python_source_hardcodes_a_release_version(self):
        # The actual regression guard. A new endpoint or app declaring its own
        # `version="1.2.3"` is invisible in review and is exactly how this drifted.
        literal = re.compile(r'"version"\s*:\s*"\d+\.\d+\.\d+"|version\s*=\s*"\d+\.\d+\.\d+"')
        offenders = []
        for path in [
            SERVICE_DIR / "config.py",
            SERVICE_DIR / "main.py",
            SERVICE_DIR / "new_dashboard_app.py",
            SERVICE_DIR / "routers" / "api_v2.py",
        ]:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if literal.search(line) and "0.0.0-dev" not in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "hardcoded release version(s); read it from get_config().version instead:\n"
            + "\n".join(offenders),
        )

    def test_stamp_script_writes_the_version_field(self):
        # config.py can only report the real version if stamp.sh actually bakes it in.
        stamp_sh = (REPO_ROOT / "scripts" / "stamp.sh").read_text(encoding="utf-8")
        self.assertIn('"version":"$version"', stamp_sh)
        self.assertIn("VERSION", stamp_sh)

    def test_config_prefers_the_stamped_version_over_its_fallback(self):
        from service.config import ServiceConfig

        self.assertEqual(
            ServiceConfig().version, "0.0.0-dev",
            "the un-stamped fallback must stay obviously unreal — a plausible-looking "
            "default is how 4.0.0 survived three releases unnoticed",
        )

    def test_the_built_stamp_if_present_agrees_with_the_version_file(self):
        # Present in a built checkout; absent in a fresh clone, which is not a failure.
        stamp_path = SERVICE_DIR / "_build_stamp.json"
        if not stamp_path.exists():
            self.skipTest("no _build_stamp.json — run scripts/stamp.sh")
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        if "version" not in stamp:
            self.skipTest("stamp predates the version field")
        self.assertEqual(stamp["version"], _canonical_version())


if __name__ == "__main__":
    unittest.main()
