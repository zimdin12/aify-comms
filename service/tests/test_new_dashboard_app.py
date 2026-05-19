import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from service.new_dashboard_app import app


ROOT = Path(__file__).resolve().parents[2]


class NewDashboardAppTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_serves_preview_shell_and_assets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("AIFY Comms Dashboard Next", response.text)
        self.assertIn("/assets/app.js", response.text)
        self.assertIn("/assets/styles.css", response.text)

        script = self.client.get("/assets/app.js")
        self.assertEqual(script.status_code, 200, script.text)
        self.assertIn("resolveApiOrigin", script.text)

        styles = self.client.get("/assets/styles.css")
        self.assertEqual(styles.status_code, 200, styles.text)
        self.assertIn(".inspector", styles.text)

    def test_api_origin_is_configurable_without_hardcoded_localhost(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text()
        script = (ROOT / "service" / "new_dashboard" / "app.js").read_text()

        self.assertRegex(html, r'data-default-api-port="8800"')
        self.assertIn("localStorage.getItem('aify.next.apiOrigin')", script)
        self.assertNotIn("http://localhost:8800", html + script)
        self.assertNotIn("//localhost:8800", html + script)

    def test_compose_keeps_legacy_dashboard_and_adds_8801_preview(self):
        compose_path = ROOT / "docker-compose.yml"
        if not compose_path.exists():
            self.skipTest("docker-compose.yml is not copied into the service image")
        compose = compose_path.read_text()

        self.assertRegex(compose, r'\$\{SERVICE_PORT:-8800\}:8800')
        self.assertRegex(compose, r'\$\{NEW_DASHBOARD_PORT:-8801\}:8801')
        self.assertIn("service.new_dashboard_app:app", compose)

    def test_first_slice_has_attention_inspector_and_no_destructive_actions(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text()
        script = (ROOT / "service" / "new_dashboard" / "app.js").read_text()

        self.assertIn("Needs Attention", html)
        self.assertIn('id="inspector"', html)
        self.assertIn("lastReminderAt", script)
        self.assertIsNone(re.search(r"/contracts/[^'\"]+/(close|cancel|delete)", script))


if __name__ == "__main__":
    unittest.main()
