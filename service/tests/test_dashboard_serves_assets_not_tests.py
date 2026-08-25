"""The dashboard's asset mount publishes the dashboard, not the test tree beside it.

The modules the browser loads live in the same directory as their own tests and one very large
fixture, and `StaticFiles(directory=APP_DIR)` published all of it. Measured 2026-08-25 against the
running service:

    /assets/extraction-proof.test.mjs               200, 126,697 bytes
    /assets/fixtures/app.before-settings-fields.js  200, 280,178 bytes

88 test files (988 KB) and one fixture (273 KB) — 1,262 KB of test source, on a service that
docker-compose starts with `--host 0.0.0.0`. Not localhost-only, and the fixture is a whole
historical copy of app.js.

Nothing needed it. A traced cold load made 126 requests and not one was a test file, so refusing them
removes surface rather than changing behaviour.

DERIVED FROM THE DIRECTORY, not from a list of the 89 names. A list would go stale the moment a test
is added — silently, and in the direction that publishes more.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from service.new_dashboard_app import APP_DIR, AssetsOnly, app


class DashboardServesAssetsNotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    # ── the pure rule ──────────────────────────────────────────────────────────────────────────
    def test_real_assets_are_served(self):
        """The control. A rule that refused everything would satisfy every refusal test below."""
        for path in ("app.js", "styles.css", "refresh-status.mjs", "vendor/xterm.js"):
            self.assertTrue(AssetsOnly.is_asset(path), f"{path} would no longer be served")

    def test_tests_and_fixtures_are_refused(self):
        for path in (
            "refresh-status.test.mjs",
            "extraction-proof.test.mjs",
            "fixtures/app.before-settings-fields.js",
            "fixtures/nested/anything.js",
        ):
            self.assertFalse(AssetsOnly.is_asset(path), f"{path} is still published")

    def test_an_empty_or_odd_path_is_refused(self):
        """Fails closed: a path the rule cannot parse is not an asset."""
        for path in ("", "/", None, "fixtures"):
            self.assertFalse(AssetsOnly.is_asset(path))

    def test_backslashes_do_not_slip_past_the_directory_check(self):
        """A separator this rule did not normalise would leave the directory check looking at one long
        segment, which is how a deny rule quietly stops denying."""
        # BUILT WITH chr(92), not written as an escape. The first version of this line reached the
        # file as a single backslash, which Python reads as '\\a' -- a BELL character. The
        # path then contained no separator at all, the rule correctly called it one filename, and the
        # test failed against working code. Third escaping mishap of the session; the cure is to stop
        # writing backslashes through layers that each get a turn at them.
        separator = chr(92)
        self.assertFalse(AssetsOnly.is_asset(f"fixtures{separator}app.before-settings-fields.js"))
        self.assertFalse(AssetsOnly.is_asset(f"a{separator}b{separator}c.test.mjs"))

    # ── every file actually on disk ────────────────────────────────────────────────────────────
    def test_every_test_file_in_the_directory_is_refused(self):
        """Derived, so a test added tomorrow is covered without editing this file."""
        published = [
            p.name for p in APP_DIR.glob("*.test.mjs") if AssetsOnly.is_asset(p.name)
        ] + [
            p.name for p in APP_DIR.glob("*.test.js") if AssetsOnly.is_asset(p.name)
        ]
        self.assertEqual(published, [], f"still published: {', '.join(published[:5])}")

    def test_the_scan_found_the_test_files_at_all(self):
        """The other control: if the glob matched nothing, the assertion above proves nothing."""
        self.assertGreater(
            len(list(APP_DIR.glob("*.test.mjs"))), 50,
            "the test-file scan found almost nothing; this file would pass vacuously",
        )

    # ── and over real HTTP ─────────────────────────────────────────────────────────────────────
    def test_the_mount_refuses_them_in_practice(self):
        """The pure rule is only half of it — this proves the mount CALLS the rule."""
        self.assertEqual(self.client.get("/assets/extraction-proof.test.mjs").status_code, 404)
        self.assertEqual(
            self.client.get("/assets/fixtures/app.before-settings-fields.js").status_code, 404,
        )

    def test_the_mount_still_serves_the_dashboard(self):
        response = self.client.get("/assets/app.js")
        self.assertEqual(response.status_code, 200, "the dashboard stopped serving its own code")
        self.assertGreater(len(response.content), 10_000)

    def test_a_refused_file_that_exists_is_indistinguishable_from_one_that_does_not(self):
        """404 rather than 403: whether the file is there is itself the thing not being published."""
        real = self.client.get("/assets/extraction-proof.test.mjs")
        absent = self.client.get("/assets/no-such-file.test.mjs")
        self.assertEqual(real.status_code, absent.status_code)
        self.assertTrue((APP_DIR / "extraction-proof.test.mjs").exists(), "fixture assumption broke")


if __name__ == "__main__":
    unittest.main()
