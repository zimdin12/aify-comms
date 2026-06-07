"""GET /version — stamped build identity + cached behind-count.

The health router is mounted with NO prefix (service/main.py:
`app.include_router(health.router)`), so the served path is `/version`
(NOT `/api/v1/version`). `/version` is on the unauth skip_paths allowlist.

The behind-count comes from a module-level cached `_check_update()` that hits
the GitHub compare API. The comparer is INJECTABLE so the test never touches
the network; a network failure must yield `update.behind_by = null` and must
NEVER raise / 500.
"""

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.routers import health
from service.config import get_config


class VersionEndpointTests(unittest.TestCase):
    def setUp(self):
        # Fresh app per test; reset the module cache so injected comparers
        # don't bleed across tests.
        health._reset_update_cache()
        # The update check short-circuits when build_sha is "unknown" (can't
        # compare an unknown sha). Pin a known sha so the injected comparer is
        # actually consulted; restore in tearDown.
        self._cfg = get_config()
        self._saved_sha = self._cfg.build_sha
        self._cfg.build_sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        self.app = FastAPI()
        self.app.include_router(health.router)
        self.client = TestClient(self.app)

    def tearDown(self):
        health._reset_update_cache()
        self._cfg.build_sha = self._saved_sha

    def test_version_fields_present(self):
        # Inject a comparer that reports 3 commits behind.
        def fake_compare(sha):
            return {"behind_by": 3, "ahead_by": 0, "status": "behind"}

        health.set_update_comparer(fake_compare)
        resp = self.client.get("/version")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        for key in ("name", "version", "sha", "sha_short", "branch", "built_at"):
            self.assertIn(key, data)
        self.assertIn("update", data)
        self.assertEqual(data["update"]["behind_by"], 3)
        self.assertEqual(data["update"]["status"], "behind")

    def test_network_failure_yields_null_behind_by(self):
        # A comparer that raises (offline / 403 rate-limited / 404 unknown sha)
        # must NOT propagate; the endpoint returns behind_by=null, never 500.
        def boom(sha):
            raise RuntimeError("network down")

        health.set_update_comparer(boom)
        resp = self.client.get("/version")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIsNone(data["update"]["behind_by"])

    def test_update_block_present_with_default_comparer(self):
        # With no injected comparer the default path still must not raise.
        # We force the network function to fail to keep the test offline-safe.
        def boom(sha):
            raise RuntimeError("offline")

        health.set_update_comparer(boom)
        resp = self.client.get("/version")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("update", resp.json())


if __name__ == "__main__":
    unittest.main()
