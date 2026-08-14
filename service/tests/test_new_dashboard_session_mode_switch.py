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

    # RETIRED: test_render_mode_switch_chip_helper_exists_without_settings_gate, and
    # test_chip_emits_data_attributes_for_click_handler.
    #
    # Both grepped app.js — for the helper's `function` line, and for its `data-mode-switch` /
    # `data-target-mode` / label strings. They went red in v0.5.4 when `renderModeSwitchChip` moved to
    # `service/new_dashboard/session-rail.mjs`, with the chip unchanged.
    #
    # They also could not have caught the one mistake that matters here: a chip offering to switch an
    # agent to the mode it is ALREADY in would have satisfied every one of those matches, because the
    # template text is identical either way. The inversion is the feature.
    #
    # Now proven by tests that CALL it, in `session-rail.test.mjs`:
    #   "THE CHIP OFFERS THE OPPOSITE MODE, never the current one"
    #   "it carries the agent id the click handler reads"
    #   "the agent id is ESCAPED into both the attribute and the title"
    #   "an agent in NEITHER mode renders nothing at all"
    #   "IT IS NOT GATED ON A SETTING — the chip renders whatever manual_session_mode says"
    #
    # The last of those replaces the `assertNotIn` above, and replaces it with something stronger: the
    # function takes one parameter, so there is nowhere for a settings gate to live, and the chip is
    # asserted to render with `manual_session_mode: false` set.

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

    def test_chip_is_rendered_on_BOTH_surfaces_wherever_those_now_live(self):
        """Two call sites, and they no longer live in the same file.

        The Details-panel chip sits in `renderSessionConsole`, which moved to
        `service/new_dashboard/session-console.mjs` in v0.5.4; the per-session row chip is in
        `renderSessionRail`, which moved to `session-rail.mjs` earlier in the same series. This test
        counted both in app.js and went red on the second relocation while the UI was unchanged.

        The INVARIANT is that an operator can flip a session's mode from either surface — the rail
        without opening Details, and Details itself. So it is asserted per surface, by file, rather than
        as a total count in one file: a count would go green again the moment two chips landed in the
        same place, which is the one arrangement that does NOT satisfy the requirement.
        """
        dash = ROOT / "service" / "new_dashboard"
        surfaces = {
            "session-console.mjs": "Details panel (session header card)",
            "session-rail.mjs": "Sessions rail (per-session row)",
        }
        for filename, description in surfaces.items():
            path = dash / filename
            self.assertTrue(path.exists(), f"{filename} must exist — it holds the {description} chip")
            source = path.read_text(encoding="utf-8")
            self.assertIn(
                "renderModeSwitchChip(agent)",
                source,
                f"Plan 6 C4/C5: {description} must render the mode-switch chip ({filename})",
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
