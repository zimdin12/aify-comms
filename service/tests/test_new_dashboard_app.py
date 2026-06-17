import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from service.new_dashboard_app import app


ROOT = Path(__file__).resolve().parents[2]
_DASH = ROOT / "service" / "new_dashboard"


def _dashboard_js() -> str:
    """Combined Dashboard Next client JS. Phase 0.1 (DASHBOARD_REBUILD_PLAN) split app.js
    into ES modules (util.js / status.js / console-chooser.js + the app.js orchestrator), so
    source-presence assertions must scan all of them, not just app.js."""
    parts = []
    for name in ("app.js", "util.js", "status.js", "console-chooser.js"):
        path = _DASH / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


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
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()

        self.assertRegex(html, r'data-default-api-port="8800"')
        self.assertIn("localStorage.getItem('aify.next.apiOrigin')", script)
        self.assertNotIn("http://localhost:8800", html + script)
        self.assertNotIn("//localhost:8800", html + script)

    def test_compose_keeps_legacy_dashboard_and_adds_8801_preview(self):
        compose_path = ROOT / "docker-compose.yml"
        if not compose_path.exists():
            self.skipTest("docker-compose.yml is not copied into the service image")
        compose = compose_path.read_text(encoding="utf-8")

        self.assertRegex(compose, r'\$\{SERVICE_PORT:-8800\}:8800')
        self.assertRegex(compose, r'\$\{NEW_DASHBOARD_PORT:-8801\}:8801')
        self.assertIn("service.new_dashboard_app:app", compose)

    def test_first_slice_has_attention_inspector_and_no_destructive_actions(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()

        self.assertIn("Needs Attention", html)
        self.assertIn('id="inspector"', html)
        self.assertIn("lastReminderAt", script)
        self.assertIn('id="run-status-filter"', html)
        self.assertIn("loadRunsForStatus", script)
        self.assertIn("dispatch_runs_by_status", script)
        self.assertIsNone(re.search(r"/contracts/[^'\"]+/(close|cancel|delete)", script))

    def test_parity_foundations_are_declared_and_reused(self):
        script = _dashboard_js()

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
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()

        # WS-J: messaging consolidated into the Chat composer (the Sessions composer was a
        # duplicate and was removed). Type/priority/queue + paste-to-share live in chat now.
        self.assertIn('id="chat-expects-reply"', html)
        self.assertIn('id="chat-queue"', html)
        self.assertIn("sendMessageWithTimeout", script)
        self.assertIn("uploadPastedImage", script)
        self.assertIn("document.addEventListener('paste'", script)
        self.assertIn("/shared", script)
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
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 414px)", styles)
        self.assertRegex(styles, r"button,\s*input,\s*select,\s*textarea\s*\{[^}]*min-height:\s*44px")
        self.assertIn(".contract-actions { grid-template-columns: 1fr; }", styles)
        self.assertIn(".run-actions { grid-template-columns: 1fr; }", styles)

    def test_polish_slice_has_collapsible_sidebar_and_real_drawer_inspector(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

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
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('class="status-dot', script)
        self.assertIn(".status-dot", styles)
        self.assertIn("data-tone=", script)
        self.assertIn(".band { padding:", styles)
        self.assertIn(".scroll-region", styles)
        self.assertIn("max-height:", styles)
        self.assertRegex(styles, r"\.metric\[data-tone=\"warn\"\]")

    def test_session_centric_ia_has_session_workspace_and_diagnostics(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('data-page="sessions"', html)
        self.assertIn('data-page="environments"', html)
        self.assertIn('data-page="diagnostics"', html)
        self.assertIn('data-page="settings"', html)
        # Phase 1.1 (2026-06-16): chat-first landing added a Chat page (the new default).
        self.assertIn('data-page="chat"', html)
        self.assertIn('data-page="files"', html)
        self.assertIn('id="attention-strip"', html)
        self.assertIn('id="session-rail"', html)
        self.assertIn('id="session-view"', html)
        self.assertIn('id="session-bulk-toolbar"', html)
        # WS-J: Sessions is terminal-first (Console default) + a read-only Activity tab; the
        # duplicate message composer was removed (messaging lives in Chat).
        self.assertIn('id="session-console-panel"', html)
        self.assertIn('id="session-activity"', html)
        self.assertIn('data-session-tab="console"', html)
        self.assertIn('data-session-tab="activity"', html)
        self.assertEqual(html.count('id="composer"'), 0)
        self.assertEqual(html.count('id="contract-list"'), 1)
        self.assertEqual(html.count('id="run-list"'), 1)
        self.assertIn("function renderSessionWorkspace", script)
        self.assertIn("function groupedSessionsByEnvironment", script)
        self.assertIn("/messages/recent?limit=80", script)
        self.assertIn("function renderSessionBulkToolbar", script)
        self.assertIn("function selectedSessionIds", script)
        self.assertIn("function requestBulkSessionControl", script)
        self.assertIn("state.selectedSessionId", script)
        self.assertIn("state.selectedSessionTab", script)
        self.assertIn("data-session-select", script)
        self.assertIn("data-session-checkbox", script)
        self.assertIn("data-bulk-session-action", script)
        self.assertIn("session-runtime-badge", script)
        self.assertNotIn("runtime === 'claude'", script)
        self.assertNotIn('runtime === "claude"', script)
        self.assertNotIn("runtime === 'pi'", script)
        self.assertNotIn('runtime === "pi"', script)
        self.assertIn(".session-shell", styles)
        self.assertIn(".session-rail", styles)
        self.assertIn(".session-bulk-toolbar", styles)
        self.assertRegex(styles, r"@media \(max-width: 414px\)[\s\S]*\.session-shell\s*\{[^}]*grid-template-columns:\s*1fr")

    def test_universal_run_inspector_contract(self):
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("inspector: { kind: '', runId: '', source: '', run: null, events: [], hasMore: false, loadingMore: false", script)
        self.assertIn("runInspector: () => Boolean", script)
        self.assertIn("runInspector: { enabled: false, assertion: flowAssertions.runInspector }", script)
        self.assertIn("function openInspector(request)", script)
        self.assertIn("function openRunInspector", script)
        self.assertIn("function renderRunInspector", script)
        self.assertIn("function loadRunEvents", script)
        self.assertIn("RUN_INSPECTOR_EVENT_LIMIT = 50", script)
        self.assertIn("/dispatch/runs/${encodeURIComponent(runId)}/events", script)
        self.assertIn("params.set('limit', String(Math.min(limit, RUN_INSPECTOR_EVENT_LIMIT)))", script)
        self.assertIn("source: 'chat'", script)
        self.assertIn("data-run-chip", script)
        self.assertIn("data-run-inspector", script)
        self.assertIn("renderStatusChip(run.status", script)
        self.assertIn("function runInspectorCapabilities", script)
        for action in ["steer", "interrupt", "queue-after", "retry", "close", "open-console"]:
            self.assertIn(f'data-run-control="{action}"', script)
        self.assertIn("/dispatch/runs/${encodeURIComponent(run.id)}/control", script)
        self.assertIn("/messages/send", script)
        self.assertIn("patchRun(run.id", script)
        self.assertIsNone(re.search(r"/contracts/[^'\"]+/(close|cancel|delete)", script))
        self.assertIn("state.selectedSessionTab = 'console'", script)
        self.assertNotIn("run-inspector-console", script)
        self.assertRegex(styles, r"@media \(max-width: 414px\)[\s\S]*\.run-inspector-sheet")
        self.assertRegex(styles, r"\.run-inspector-controls\s*\{[^}]*position:\s*sticky;[^}]*bottom:\s*0")

    def test_status_why_and_activity_feed_are_gated_and_reuse_status_resolver(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="activity-feed"', html)
        self.assertIn('id="status-why-popover"', html)
        self.assertIn("statusWhy: () => Boolean", script)
        self.assertIn("activityFeed: () => Boolean", script)
        self.assertIn("statusWhy: { enabled: false, assertion: flowAssertions.statusWhy }", script)
        self.assertIn("activityFeed: { enabled: false, assertion: flowAssertions.activityFeed }", script)
        self.assertIn("function statusWhyContext", script)
        self.assertIn("function openStatusWhy", script)
        self.assertIn("function renderActivityFeed", script)
        self.assertIn("data-status-why=", script)
        self.assertIn("status-why-trigger", script)
        self.assertIn("renderStatusChip(status, statusWhyContext('session'", script)
        self.assertIn("renderStatusChip(run.status, statusWhyContext('run'", script)
        self.assertIn("renderStatusChip(contract.overdue ? 'failed' : contract.state || contract.status, statusWhyContext('contract'", script)
        self.assertNotRegex(script, r"class=\\\"status-chip \\$\\{[^}]*\\.status")
        self.assertIn(".status-why-popover", styles)
        self.assertIn(".activity-feed", styles)
        self.assertRegex(styles, r"@media \(max-width: 414px\)[\s\S]*\.status-why-popover")

    def test_mobile_shell_has_bottom_tab_bar_for_primary_destinations(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="mobile-tabbar"', html)
        self.assertIn('class="mobile-tabbar"', html)
        for page in ["sessions", "environments", "diagnostics", "settings"]:
            self.assertIn(f'data-mobile-page="{page}"', html)
            self.assertIn(f'data-page="{page}"', html)
        self.assertIn("document.querySelectorAll('.mobile-tabbar [data-page]')", script)
        self.assertIn("el.dataset.page === page", script)
        self.assertIn(".mobile-tabbar", styles)
        self.assertIn("display: none;", styles)
        self.assertRegex(styles, r"@media \(max-width: 414px\)[\s\S]*\.mobile-tabbar\s*\{[^}]*position:\s*fixed")
        self.assertRegex(styles, r"@media \(max-width: 414px\)[\s\S]*\.workspace\s*\{[^}]*padding-bottom:\s*calc\(\d+px \+ env\(safe-area-inset-bottom\)\)")
        self.assertRegex(styles, r"@media \(max-width: 414px\)[\s\S]*\.nav nav\s*\{[^}]*display:\s*none")

    def test_diagnostics_destination_has_summary_and_bulk_selection(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="diagnostics-summary"', html)
        self.assertIn('id="diagnostics-bulk-toolbar"', html)
        self.assertIn("selectedDiagnosticIds: new Set()", script)
        self.assertIn("diagnostics: () => Boolean", script)
        self.assertIn("diagnostics: { enabled: false, assertion: flowAssertions.diagnostics }", script)
        self.assertIn("function renderDiagnosticsSummary", script)
        self.assertIn("function renderDiagnosticsBulkToolbar", script)
        self.assertIn("function selectedDiagnostics", script)
        self.assertIn("function requestBulkDiagnosticAction", script)
        self.assertIn("data-diagnostic-select", script)
        self.assertIn("data-diagnostic-action", script)
        self.assertIn("closeWorkContract(item.id, false, false)", script)
        self.assertIn("remindWorkContract(item.id, false)", script)
        self.assertIn("patchRun(item.id", script)
        self.assertIsNone(re.search(r"/contracts/[^'\"]+/(close|cancel|delete)", script))
        self.assertIn(".diagnostics-summary", styles)
        self.assertIn(".diagnostics-bulk-toolbar", styles)
        self.assertRegex(styles, r"@media \(max-width: 414px\)[\s\S]*\.diagnostics-summary\s*\{[^}]*grid-template-columns:\s*1fr")

    def test_environments_destination_has_spawn_form_and_rich_cards(self):
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="environment-summary"', html)
        self.assertIn('id="environment-spawn-form"', html)
        for field in ["environment", "runtime", "agent-id", "role", "workspace", "prompt"]:
            self.assertIn(f'id="env-spawn-{field}"', html)
        self.assertIn("environments: () => Boolean", script)
        self.assertIn("environments: { enabled: false, assertion: flowAssertions.environments }", script)
        self.assertIn("function renderEnvironmentSummary", script)
        self.assertIn("function renderEnvironmentSpawnOptions", script)
        self.assertIn("function createSpawnRequest", script)
        self.assertIn("/spawn-requests", script)
        self.assertIn("createdBy: 'dashboard'", script)
        self.assertIn("mode: 'managed-warm'", script)
        self.assertIn("Spawn starts a fresh session", html)
        self.assertIn("env-runtime-pill", script)
        self.assertIn("env-root-list", script)
        self.assertIn("data-env-spawn", script)
        self.assertIn(".environment-summary", styles)
        self.assertIn(".environment-spawn", styles)
        self.assertIn(".env-runtime-pill", styles)
        self.assertRegex(styles, r"@media \(max-width: 414px\)[\s\S]*\.environment-spawn-grid\s*\{[^}]*grid-template-columns:\s*1fr")

    def test_attention_strip_does_not_show_diagnostics_bulk_checkboxes(self):
        script = _dashboard_js()

        self.assertIn("function contractCard(contract, { selectable = true } = {})", script)
        self.assertIn("items.map((contract) => contractCard(contract, { selectable: false }))", script)
        self.assertIn("contracts.map(contractCard)", script)

    def test_console_toolbar_and_robustness(self):
        # WS-D: the pseudo-terminal console gained a toolbar (copy/refresh/stop/start), WebGL,
        # seq-dedup/resync, a blocked-input guard, and an await-input pill.
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()

        self.assertIn("addon-webgl.js", html, "WebGL addon must be vendored + scripted")
        self.assertIn("WebglAddon", script, "WebGL renderer must be wired with a fallback")
        self.assertIn("data-console-action", script, "console toolbar actions must exist")
        for action in ("copyActiveConsole", "resyncActiveConsole", "stopConsoleTerminal", "startConsoleForSession"):
            self.assertIn(action, script, f"{action} must exist")
        self.assertIn("consoleAwaitingInputHint", script, "await-input detector must exist")
        self.assertIn("canInput === false", script, "blocked-input guard must exist")
        self.assertIn("seq > entry.lastSeq + 1", script, "seq gap-resync must exist")
        self.assertIn("ResizeObserver", script, "console must re-fit on resize")

    def test_global_analytics_page_exists(self):
        # WS-C: the global Analytics page (traffic chart, stat cards, health grid, run-status
        # mix, range selector) must exist — the old /analytics surface the new build dropped.
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="page-analytics"', html, "Analytics page section must exist")
        self.assertIn('data-page="analytics"', html, "Analytics nav item must exist")
        self.assertIn('id="analytics-traffic"', html)
        self.assertIn('id="analytics-range"', html)
        self.assertIn("function loadAnalytics()", script, "analytics loader must exist")
        self.assertIn("/analytics?range=", script, "must fetch the ranged analytics endpoint")
        self.assertIn("trafficChartHtml", script)
        self.assertIn("data-analytics-range", script, "range selector must be wired")
        self.assertIn(".chart-svg", styles, "traffic chart CSS must exist")
        self.assertIn(".health-grid", styles)

    def test_appearance_theming_and_settings_parity(self):
        # WS-A/WS-B: the Appearance group (8-theme picker + custom palette + title) must exist,
        # and the previously-missing settings keys must be in the schema.
        script = _dashboard_js()
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        # Theme engine
        self.assertIn("appearance: true", script, "Appearance settings group must exist")
        self.assertIn("type: 'theme'", script, "theme picker control type must exist")
        self.assertIn("type: 'color'", script, "color picker control type must exist")
        self.assertIn("data-theme-choice", script, "theme preview tiles must be clickable")
        self.assertIn("applyCachedTheme()", script, "cached theme must paint at startup")
        # Settings is tabbed with aligned field rows + real toggle switches (not tiny checkboxes).
        self.assertIn("settings-tab", script, "settings must be tabbed")
        self.assertIn("settings-field", script, "settings must use aligned field rows")
        self.assertIn('class="switch"', script, "toggles must be real switches")
        self.assertIn("data-settings-tab", script, "settings tabs must be wired")
        for theme in ("forest", "ember", "ocean", "indigo"):
            self.assertIn(f'body[data-theme="{theme}"]', styles, f"{theme} preset CSS must exist")

        # Settings parity — keys that were missing from the new schema
        for key in (
            "dashboard_theme", "dashboard_primary_color", "auto_confirm_session_id",
            "managed_via_wrapper", "contract_stale_hours", "agent_liveness_seconds",
            "active_run_stale_minutes", "active_managed_run_stale_minutes",
        ):
            self.assertIn(f"key: '{key}'", script, f"settings schema must expose {key}")
        self.assertIn("'xhigh'", script, "effort options must include xhigh")

    def test_help_reference_lives_on_settings_page_as_links_not_essay(self):
        # Slice 7 "Help" half: canonical-doc links + compact quick-start + endpoint
        # reference, NOT a third copy of the 8800 status essay. Static HTML on the
        # Settings page (plan pairs "Settings + Help").
        html = (ROOT / "service" / "new_dashboard" / "index.html").read_text(encoding="utf-8")
        styles = (ROOT / "service" / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("help-band", html)
        self.assertIn("Help &amp; Reference", html)
        # Links out to the canonical repo docs rather than duplicating them.
        self.assertIn("github.com/zimdin12/aify-comms/blob/main/README.md", html)
        self.assertIn("github.com/zimdin12/aify-comms/blob/main/KNOWN_ISSUES.md", html)
        # Compact quick-start + endpoint reference present.
        self.assertIn("install.sh --client claude", html)
        self.assertIn("Endpoint reference", html)
        self.assertIn(".help-endpoint-grid", styles)


if __name__ == "__main__":
    unittest.main()
