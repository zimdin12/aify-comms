import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import service.main as main_module
from service.config import ServiceConfig


class DashboardRedirectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = ServiceConfig(
            data_dir=self.tmp.name,
            config_dir=self.tmp.name,
            api_key="",
            host="127.0.0.1",
            port=8800,
            mcp_enabled=False,
        )
        self.original_get_config = main_module.get_config
        self.addCleanup(setattr, main_module, "get_config", self.original_get_config)
        main_module.get_config = lambda: cfg
        self.client = TestClient(main_module.create_app())

    def test_legacy_dashboard_entry_points_redirect_to_new_dashboard(self):
        with patch.dict(os.environ, {"AIFY_DASHBOARD_URL": "http://dashboard.example:8801/"}):
            for path in ("/", "/api/v1/dashboard", "/api/v1/dashboard/dispatches"):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 307, path)
                self.assertEqual(response.headers["location"], "http://dashboard.example:8801/", path)

    def test_redirect_defaults_to_request_host_on_new_dashboard_port(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIFY_DASHBOARD_URL", None)
            response = self.client.get(
                "/api/v1/dashboard",
                headers={"host": "aify.internal:8800"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "http://aify.internal:8801/")


if __name__ == "__main__":
    unittest.main()
