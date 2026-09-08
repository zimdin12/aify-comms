"""What the operator can change, and what the service declares, must agree in both directions.

TWO GATES ALREADY ASK WHETHER A SETTING IS READ -- `test_every_setting_has_a_reader.py` over the 43
declared, `test_every_dashboard_setting_has_a_reader.py` over the 35 the dashboard draws. Neither
asks whether those two POPULATIONS agree, and that is a different question with two failure modes:

  SHOWN BUT NOT DECLARED -- a control writes a key nothing defaults or validates. The operator
  changes it, it saves, and it is absent from every default the service falls back to. Currently
  ZERO, which is what makes it worth pinning: a set that is empty today is the one a new field slips
  into unnoticed.

  DECLARED BUT NOT SHOWN -- a knob no operator can reach. Six of these exist and all six are
  deliberate: internal reconciler timings with readers and a stated rationale at their declaration.
  That is a fine design; what is not fine is a NEW operator-facing setting quietly joining them
  because somebody added a default and forgot the control.

WHY A LIST RATHER THAN A COUNT. A count goes green again the moment somebody adds one setting and
removes another, which is exactly the change that should be loudest. The names are pinned, and
adding one is a decision that shows up as an edit to this file in the same commit.

WHAT THIS GATE IS, EXACTLY, and what owns the rest. It compares the schema's DECLARED keys with the
service's defaults. It does NOT call the renderer, so it cannot see a field the schema declares and
the renderer omits, nor a control that emits a different key than its schema row names. Review
demonstrated all three of those surviving it.

  the rendered control and the value it edits  ->  test_a_settings_control_matches_the_value_it_edits.py
  whether a setting is read at all             ->  test_every_setting_has_a_reader.py (43 declared)
                                                   test_every_dashboard_setting_has_a_reader.py (35 shown)
  whether the two populations agree            ->  this file

A second renderer walk here would be two implementations of one question, which agree until one is
fixed -- the shape this project keeps finding in its own gates.

AND IT DOES NOT ASK WHETHER A READER DOES WHAT THE LABEL SAYS. That is the B7 pass's third question,
it needs a person, and all four gates say so about themselves.

TRACED FROM CODE 2026-09-08 for the v0.6.3 end-of-version pass, which is the operator's own question:
"do they still make sense, are they stale, do they still work (trace from code)".
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from service.api_core.settings import DEFAULT_SETTINGS  # noqa: E402

DASHBOARD = REPO / "service" / "new_dashboard"

#: Declared with a default and a reader, but deliberately NOT on the settings page: internal timings
#: the reconcilers key on, each with its rationale written at its declaration in `settings.py`.
#:
#: ADDING A NAME HERE IS A DECISION, not a repair. A new operator-facing setting that has no control
#: is the defect this file exists to catch.
INTENTIONALLY_NOT_ON_THE_PAGE = {
    "active_managed_run_wall_ceiling_minutes",
    "agent_offline_revalidate_seconds",
    "managed_reply_capture_fallback",
    "orphaned_dispatch_run_retention_hours",
    "queued_run_backstop_seconds",
    "stranded_reply_fail_minutes",
    # These two are additionally read by NOTHING, on purpose -- the behaviour moved into the bridge,
    # which decides from its own environment at start-up. `test_every_setting_has_a_reader.py` owns
    # that fact and states the reason; they appear here only because not being on the page follows
    # from it.
    "console_auto_confirm_claude_compaction",
    "console_auto_confirm_claude_dev_channels",
}


def _keys_the_dashboard_draws() -> set[str]:
    """Read out of the RUNNING module, not parsed out of it.

    A regex over the source would go stale the next time the schema's shape changes -- which this
    project has been bitten by before, in the gate that grepped `app.js` for `SETTINGS_SCHEMA.map`.
    Importing it means a shape change is an import error here rather than a silent empty set.
    """
    result = subprocess.run(
        ["node", "-e",
         # EVERY ROW, keyless ones included. Filtering on `item.key` here is what let a keyless row
         # be silently skipped: the Python side then had nothing to notice, because every comparison
         # below is a set operation and a set cannot miss a member it was never given.
         "import('./settings-panel.mjs').then(m => {"
         "  const keys = [];"
         "  for (const group of (m.SETTINGS_SCHEMA || [])) {"
         "    for (const item of (group.items || [])) keys.push((item && item.key) || '');"
         "  }"
         "  console.log(JSON.stringify(keys));"
         "});"],
        cwd=DASHBOARD, capture_output=True, text=True, shell=True,
    )
    payload = [line for line in result.stdout.splitlines() if line.startswith("[")]
    if not payload:
        raise AssertionError(
            "could not read SETTINGS_SCHEMA out of settings-panel.mjs, so this gate judged nothing: "
            f"{result.stdout[-400:]}{result.stderr[-400:]}"
        )
    keys = json.loads(payload[-1])
    # A ROW WITH NO KEY WAS SILENTLY SKIPPED, so appending one added a control this gate could not
    # see -- and every comparison below is a set operation, which cannot notice a missing member.
    blank = [index for index, key in enumerate(keys) if not str(key or "").strip()]
    if blank:
        raise AssertionError(
            f"{len(blank)} schema row(s) declare no key (at positions {blank}), so the settings page "
            f"draws a control this gate cannot compare against anything"
        )
    return set(keys)


class TheSettingsSurfaceAgreesWithTheDeclarationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shown = _keys_the_dashboard_draws()
        self.declared = set(DEFAULT_SETTINGS)

    def test_the_dashboard_reads_a_non_empty_schema(self):
        """The positive control. Every assertion below is satisfied by an empty set."""
        self.assertGreater(len(self.shown), 20,
                           f"only {len(self.shown)} settings were read from the schema, so the "
                           f"comparisons below are against almost nothing")
        self.assertGreater(len(self.declared), 20)

    def test_every_control_writes_a_key_the_service_declares(self):
        # A control whose key has no default is one the operator can set and the service will never
        # fall back to. Empty today, which is why it is pinned.
        undeclared = sorted(self.shown - self.declared)
        self.assertEqual(undeclared, [],
                         f"the settings page writes {undeclared}, which `DEFAULT_SETTINGS` does not "
                         f"declare -- the operator can set a value nothing defaults or validates")

    def test_every_declared_setting_is_reachable_or_deliberately_not(self):
        unreachable = sorted(self.declared - self.shown - INTENTIONALLY_NOT_ON_THE_PAGE)
        self.assertEqual(unreachable, [],
                         f"{unreachable} have a default but no control, and are not in this file's "
                         f"decided list -- either give them a control or add them with the reason")

    def test_the_decided_list_names_only_real_settings(self):
        # A name that stops being a setting must not sit here forever pretending to be governed --
        # the same rot the oversized-file allowlist is written to avoid.
        stale = sorted(INTENTIONALLY_NOT_ON_THE_PAGE - self.declared)
        self.assertEqual(stale, [],
                         f"{stale} are excused from the settings page but are no longer declared at "
                         f"all, so this exclusion is governing nothing")

    def test_the_decided_list_does_not_excuse_a_setting_that_IS_shown(self):
        # The opposite rot: a knob that gained a control while still being listed as deliberately
        # hidden. The list would then be describing a state that no longer exists.
        contradicted = sorted(INTENTIONALLY_NOT_ON_THE_PAGE & self.shown)
        self.assertEqual(contradicted, [],
                         f"{contradicted} are listed as deliberately absent from the settings page "
                         f"and the dashboard draws a control for them")


if __name__ == "__main__":
    unittest.main()
