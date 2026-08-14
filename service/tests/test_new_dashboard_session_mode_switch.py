"""Plan 6 C4/C5/C6 (2026-05-26) — dashboard wiring for the resident<->managed
session-mode switch chip.

This is a string-match smoke test: there's no headless browser in the test
harness, so we verify the JS source contains the gated chip renderer, the
fetch call against PATCH /agents/{id}/session-mode, the settings load in
`refresh()`, and the click-handler selector.

STRING MATCHES ASSERT WHERE CODE LIVES, NOT WHAT IT DOES, and two of these have
now fired on pure relocations in v0.5.4 while the behaviour was unchanged — one
of them ("event.stopPropagation()" appearing anywhere in app.js) could never
have failed on the bug it was named for. As each body moves to a module, prefer
retiring the match in favour of a test that CALLS the code, and name the
replacement where the match used to be.

For dynamic behavior coverage, see test_agent_session_mode_switch.py
(server side) — together they assert the full operator flow.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "service" / "new_dashboard" / "app.js"
INDEX_HTML = ROOT / "service" / "new_dashboard" / "index.html"


class NewDashboardSessionModeSwitchTests(unittest.TestCase):
    def setUp(self):
        self.script = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    # test_state_seeds_settings_snapshot was RETIRED in v0.5.4 and replaced by
    # `service/new_dashboard/state.test.mjs`, which imports the object and asserts the property instead of
    # grepping app.js for the text "settings: {}".
    #
    # It was a location pin. It proved a line had been written somewhere in a 4,900-line file, and it broke
    # when `state` moved to its own module even though the seeding was byte-identical. The replacement can
    # fail on wrong VALUES, which this could not: `settings: {}` appearing inside a comment would have
    # satisfied it.

    def test_refresh_loads_settings_via_api(self):
        # 2026-06-18 resilient-poll refactor (267b88f): refresh() now batches all GETs through
        # Promise.allSettled (tolerant of a single failed endpoint) instead of per-call .catch.
        self.assertIn(
            "api('/settings')",
            self.script,
            "refresh() must GET /api/v1/settings (in the allSettled batch — tolerant of failure)",
        )
        self.assertIn(
            "state.settings = val(9)",
            self.script,
            "refresh() must persist the settings snapshot (allSettled slot 9) into state",
        )

    def test_render_mode_switch_chip_helper_exists_without_settings_gate(self):
        self.assertIn(
            "function renderModeSwitchChip(agent)",
            self.script,
            "Plan 6 C4: renderModeSwitchChip helper must be defined",
        )
        self.assertNotIn(
            "state.settings.manual_session_mode !== true",
            self.script,
            "Manual session switching must always be visible in Sessions/chat details; "
            "manual_session_mode must not gate the chip",
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

    def test_click_handler_delegates_mode_switch_clicks(self):
        """app.js still OWNS the delegation; what the handler then does is proven by calling it.

        This asserted `switchAgentSessionMode(agentId, targetMode)` appeared in app.js, and went red when
        that body moved to `service/new_dashboard/agent-click-handlers.mjs` in v0.5.4 — while the
        behaviour was unchanged. That is the failure mode of a location pin: it reports where a line
        LIVES, so it fails on a pure relocation and would pass just as happily on a handler that passed
        the two arguments in the wrong order.

        RETIRED BY `agent-click-handlers.test.mjs::switchModeFromChip SUPPRESSES the default and STOPS
        propagation before switching`, which CALLS the handler and asserts the agent id and target mode
        arrive in that order — the thing this could never check. What is left here is the half app.js
        genuinely still owns: the delegated listener must catch the chip's clicks and hand them on.
        """
        self.assertIn(
            "const modeSwitchButton = event.target.closest('[data-mode-switch]')",
            self.script,
            "Plan 6 C4: global click delegation must catch [data-mode-switch] clicks",
        )
        self.assertIn(
            "switchModeFromChip(modeSwitchButton, event, switchAgentSessionMode)",
            self.script,
            "Plan 6 C4: the delegated listener must hand the click to the extracted handler",
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
        self.assertIn(
            "Object.assign(existingAgent, body.agent)",
            self.script,
            "A successful switch must apply the returned mode/status immediately instead of waiting for polling",
        )
        self.assertIn(
            "renderSessionWorkspace()",
            self.script,
            "A successful switch must repaint the selected session immediately",
        )

    def test_chip_is_inserted_into_session_header_card_actions(self):
        # The Details panel chip lives inside the runtime-card .contract-actions
        # block in renderSessionConsole. Verify the renderer call site exists.
        self.assertIn(
            "${renderModeSwitchChip(agent)}",
            self.script,
            "Plan 6 C4: Details panel (session header card) must render the chip",
        )
        # Plan 6 C5: same renderer call also appears in the per-session row
        # body (renderSessionRail) so operators can flip a single session's
        # mode from the rail without opening Details first. Same data-attrs +
        # click handler — no new code path needed.
        # Counting renderModeSwitchChip call sites should be >= 2 (header card + session rail).
        self.assertGreaterEqual(
            self.script.count("renderModeSwitchChip(agent)"),
            2,
            "Plan 6 C5: Sessions rail must render the chip too (>= 2 call sites total — "
            "header card + per-session row)",
        )

    # ─── C6 — Settings UI toggle ───────────────────────────────────────────

    def test_settings_page_has_grouped_editor_with_manual_session_mode(self):
        # Phase 1.7 (2026-06-16): the C6 single-toggle stub became a grouped, schema-driven
        # settings editor. The page declares the form host + a Save button; the schema still
        # exposes manual_session_mode (now one knob among many).
        self.assertIn('id="settings-form"', self.html, "Settings page must declare the settings-form host")
        self.assertIn('id="settings-save"', self.html, "Settings page must declare a Save button")
        # The two script assertions here ("SETTINGS_SCHEMA" and "key: 'manual_session_mode'") were
        # RETIRED in v0.5.4: the schema moved to service/new_dashboard/settings-panel.mjs and is now
        # asserted by settings-panel.test.mjs, which READS the schema object rather than grepping for
        # its text -- it checks the key is present AND that its type is still a toggle, which the
        # string match could not. The HTML assertions above stay: they are a real cross-file contract
        # with index.html that the replacement does not cover.

    # test_render_settings_builds_from_state_and_schema was RETIRED in v0.5.4 and replaced by
    # service/new_dashboard/settings-panel.test.mjs, which CALLS renderSettings against a fake DOM and
    # asserts the rendered output: one tab and one panel per schema group, the Help tab, the active tab
    # marked, and the in-progress-edit guard behaving -- a focused INPUT must not be rebuilt out from
    # under the operator, while a focused TAB must still switch. That last distinction is a bug that
    # actually shipped (2026-06-29) and no source regex can tell the fixed version from the broken one.

    def test_save_handler_puts_settings(self):
        self.assertIn("async function saveSettings()", self.script, "saveSettings helper must be defined")
        self.assertIn("method: 'PUT'", self.script, "saveSettings must PUT /api/v1/settings")
        self.assertIn("data-setting-key", self.script, "saveSettings must collect values from the schema-rendered inputs")

    # RETIRED: test_click_handler_stops_propagation_so_chip_does_not_select_session.
    #
    # It asserted the string "event.stopPropagation()" appeared ANYWHERE in app.js — a 3,500-line file
    # with several handlers that legitimately call it. It could not tell which handler stopped
    # propagation, so it would have passed with the chip's call deleted as long as any other branch
    # still had one. It went red in v0.5.4 when the two bodies that did call it moved out.
    #
    # Now proven by tests that CALL the handlers and count the calls on the event they were given:
    #   agent-click-handlers.test.mjs :: "switchModeFromChip SUPPRESSES the default and STOPS
    #                                     propagation before switching"
    #   agent-click-handlers.test.mjs :: "toggleFavouriteRow STOPS PROPAGATION so the star does not
    #                                     also select the row"


if __name__ == "__main__":
    unittest.main()
