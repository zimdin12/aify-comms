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
        self.assertIn('id="run-status-filter"', html)
        self.assertIn("loadRunsForStatus", script)
        self.assertIn("dispatch_runs_by_status", script)
        self.assertIsNone(re.search(r"/contracts/[^'\"]+/(close|cancel|delete)", script))

    def test_parity_foundations_are_declared_and_reused(self):
        script = (ROOT / "service" / "new_dashboard" / "app.js").read_text()

        self.assertIn("const flowGates", script)
        self.assertIn("function resolveStatus", script)
        self.assertIn("const STATUS_KINDS", script)
        self.assertIn("function renderStatusChip", script)
        self.assertIn("function connectRealtimeSocket", script)
        self.assertIn("function applyRealtimeEvent", script)
        self.assertIn("terminalOwners", script)
        self.assertIn("data.agentId !== owner", script)
        self.assertNotRegex(script, r"class=\\\"status-chip \\$\\{[^}]*\\.status")

    def test_next_slice_exposes_safe_parity_controls(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text()
        script = (ROOT / "service" / "new_dashboard" / "app.js").read_text()

        self.assertIn('id="composer-type"', html)
        self.assertIn('id="composer-queue"', html)
        self.assertIn("sendMessageWithTimeout", script)
        self.assertIn("/dispatch/runs/${encodeURIComponent(runId)}", script)
        self.assertIn("/dispatch/runs/${encodeURIComponent(runId)}/control", script)
        self.assertIn("closeWorkContract", script)
        self.assertIn("remindWorkContract", script)
        self.assertIn("Closed from Work Loop by dashboard operator.", script)
        self.assertIn('data-page-jump="environments"', html)
        self.assertIn('data-page-jump="settings"', html)
        self.assertIn("openClassic", script)
        self.assertIn("requestSessionControl", script)
        self.assertIn('data-session-control="restart"', script)
        self.assertIn('data-session-control="stop"', script)
        self.assertIn("/sessions/${encodeURIComponent(sessionId)}/control", script)

    def test_mobile_contract_has_touch_and_single_column_controls(self):
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text()

        self.assertIn("@media (max-width: 414px)", styles)
        self.assertRegex(styles, r"button,\s*input,\s*select,\s*textarea\s*\{[^}]*min-height:\s*44px")
        self.assertIn(".contract-actions { grid-template-columns: 1fr; }", styles)
        self.assertIn(".run-actions { grid-template-columns: 1fr; }", styles)

    def test_polish_slice_has_collapsible_sidebar_and_real_drawer_inspector(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text()
        script = (ROOT / "service" / "new_dashboard" / "app.js").read_text()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text()

        self.assertIn('id="toggle-nav"', html)
        self.assertIn("function setNavCollapsed", script)
        self.assertIn("aify.next.navCollapsed", script)
        self.assertIn(".app-shell.nav-collapsed", styles)
        self.assertIn("matchMedia('(max-width: 760px)')", script)
        self.assertIn(".app-shell.nav-collapsed .nav nav", styles)
        self.assertIn(".inspector.open", styles)
        self.assertRegex(styles, r"\.inspector\s*\{[^}]*position:\s*fixed")
        self.assertRegex(styles, r"\.inspector\s*\{[^}]*transform:\s*translateX\(100%\)")
        self.assertIn("closeInspector", script)
        self.assertIn("openInspector", script)

    def test_polish_slice_makes_status_and_cockpit_visually_dense(self):
        script = (ROOT / "service" / "new_dashboard" / "app.js").read_text()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text()

        self.assertIn('class="status-dot', script)
        self.assertIn(".status-dot", styles)
        self.assertIn("data-tone=", script)
        self.assertIn(".band.compact", styles)
        self.assertIn(".scroll-region", styles)
        self.assertIn("max-height:", styles)
        self.assertRegex(styles, r"\.metric\[data-tone=\"warn\"\]")


if __name__ == "__main__":
    unittest.main()
