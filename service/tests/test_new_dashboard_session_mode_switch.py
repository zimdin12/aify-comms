"""Plan 6 C4/C5/C6 (2026-05-26) — dashboard wiring for the resident<->managed
session-mode switch chip.

This is a string-match smoke test: there's no headless browser in the test
harness, so we verify the JS source contains the gated chip renderer, the
fetch call against PATCH /agents/{id}/session-mode, the settings load in
`refresh()`, and the click-handler selector.

For dynamic behavior coverage, see test_agent_session_mode_switch.py
(server side) — together they assert the full operator flow.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "service" / "new_dashboard" / "app.js"


class NewDashboardSessionModeSwitchTests(unittest.TestCase):
    def setUp(self):
        self.script = APP_JS.read_text(encoding="utf-8")

    def test_state_seeds_settings_snapshot(self):
        self.assertIn(
            "settings: {}",
            self.script,
            "Plan 6 C3/C4: state.settings must be seeded so renderModeSwitchChip "
            "can read manual_session_mode before the first refresh completes",
        )

    def test_refresh_loads_settings_via_api(self):
        self.assertIn(
            "api('/settings').catch",
            self.script,
            "Plan 6 C3/C4: refresh() must GET /api/v1/settings (tolerant of failure)",
        )
        self.assertIn(
            "state.settings = settings",
            self.script,
            "Plan 6 C3/C4: refresh() must persist the settings snapshot into state",
        )

    def test_render_mode_switch_chip_helper_exists_and_gates_on_setting(self):
        self.assertIn(
            "function renderModeSwitchChip(agent)",
            self.script,
            "Plan 6 C4: renderModeSwitchChip helper must be defined",
        )
        self.assertIn(
            "state.settings.manual_session_mode !== true",
            self.script,
            "Plan 6 C4: renderModeSwitchChip must short-circuit when "
            "manual_session_mode is not true (visibility gate)",
        )

    def test_chip_emits_data_attributes_for_click_handler(self):
        self.assertIn(
            'data-mode-switch="${esc(agent.id)}"',
            self.script,
            "Plan 6 C4: chip must carry data-mode-switch=<agentId> so the "
            "click handler can identify the target agent",
        )
        self.assertIn(
            'data-target-mode="${target}"',
            self.script,
            "Plan 6 C4: chip must carry data-target-mode=<resident|managed>",
        )
        # The text content swaps between "Switch to resident" / "Switch to managed".
        self.assertIn(
            "Switch to ${target}",
            self.script,
            "Plan 6 C4: chip label must describe the target mode",
        )

    def test_click_handler_calls_switch_agent_session_mode(self):
        self.assertIn(
            "const modeSwitchButton = event.target.closest('[data-mode-switch]')",
            self.script,
            "Plan 6 C4: global click delegation must catch [data-mode-switch] clicks",
        )
        self.assertIn(
            "switchAgentSessionMode(agentId, targetMode)",
            self.script,
            "Plan 6 C4: click handler must invoke switchAgentSessionMode",
        )

    def test_switch_agent_session_mode_fetches_patch_endpoint(self):
        self.assertIn(
            "async function switchAgentSessionMode(agentId, targetMode",
            self.script,
            "Plan 6 C4: switchAgentSessionMode helper must be defined",
        )
        self.assertIn(
            "/agents/${encodeURIComponent(agentId)}/session-mode",
            self.script,
            "Plan 6 C4: helper must hit PATCH /api/v1/agents/{id}/session-mode",
        )
        self.assertIn(
            "method: 'PATCH'",
            self.script,
            "Plan 6 C4: PATCH method required",
        )

    def test_chip_is_inserted_into_session_header_card_actions(self):
        # The Details panel chip lives inside the runtime-card .contract-actions
        # block in renderSessionConsole. Verify the renderer call site exists.
        self.assertIn(
            "${renderModeSwitchChip(agent)}",
            self.script,
            "Plan 6 C4: Details panel (session header card) must render the chip",
        )


if __name__ == "__main__":
    unittest.main()
