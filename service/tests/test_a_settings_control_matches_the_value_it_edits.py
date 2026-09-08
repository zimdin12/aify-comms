"""A dashboard control must agree with the value it edits.

WHAT THIS ASKS THAT ITS SIBLING DOES NOT. `test_every_dashboard_setting_has_a_reader.py` answers
"is this setting still read" -- the stale-setting question. This answers the other half of the B7
audit: does the CONTROL the operator is given match the VALUE the reader consumes, and is it wired to
that value at all.

THE CONTROLS COME FROM JAVASCRIPT RUNNING, NOT FROM A REGULAR EXPRESSION, and that is the whole
correction in this file's history. The first version extracted the control list with a regex, and
review demonstrated SEVEN source changes the entire gate stayed green through:

    a row commented out            still counted -- 35 parsed, 34 real
    a row written with " quotes    invisible -- 36 real, 35 parsed
    a key declared twice           read as one control, so a duplicate id was undetectable
    min: 90.5                      read as 90, and 90.5 excludes the shipped default of 90
    min: 45 * 3                    read as 45 while the browser computes 135
    the renderer rewired           retention_days drawn from rotation_enabled, every name still matching
    an option removed              a select whose shipped default is no longer in its own list

Every one of those is a question about what JavaScript DOES. `settings_schema_probe.mjs` imports the
panel and evaluates it, so comments, quoting, arithmetic and duplicates are handled by the parser
that actually ships, and the binding is measured by RE-RENDERING: change one setting on its own and a
field wired to it must redraw. A sentinel string cannot answer that -- it is not representable in a
checkbox, a select or a number input, which is why 15 of the 35 controls could never have been judged
that way.

WHAT THE NEGATIVE CONTROLS BELOW DO AND DO NOT PROVE, stated because review was right to raise it:
they feed `disagreements()` known-bad pairs, so they prove the COMPARISON can report each defect
class. They do not prove the extractor -- nothing written in Python could, which is why the extractor
is no longer written in Python.

A SETTING WITH NO CONTROL IS NOT A FAILURE. Internal tunables are deliberately not operator-facing.
They are pinned by NAME, in both directions, so hiding a NEW one is a decision somebody writes down.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETTINGS_PY = REPO / "service" / "api_core" / "settings.py"
PROBE = REPO / "service" / "tests" / "settings_schema_probe.mjs"

#: Which Python types each control can legitimately edit. A control type absent here fails, because
#: an unrecognised control is one nobody has decided the meaning of.
CONTROL_ACCEPTS = {
    "toggle": (bool,),
    "number": (int,),
    "text": (str,),
    "color": (str,),
    "theme": (str,),
    "select": (str,),
    "csv": (list,),
}

#: Declared but deliberately NOT on the panel. Each is an internal tunable whose value is a judgement
#: about the service's own behaviour rather than an operator preference.
NOT_OPERATOR_FACING = {
    "agent_offline_revalidate_seconds",
    "stranded_reply_fail_minutes",
    "active_managed_run_wall_ceiling_minutes",
    "queued_run_backstop_seconds",
    "orphaned_dispatch_run_retention_hours",
    "console_auto_confirm_claude_dev_channels",
    "console_auto_confirm_claude_compaction",
    "managed_reply_capture_fallback",
}


def declared_settings() -> dict:
    """`DEFAULT_SETTINGS`, by AST rather than by regex."""
    tree = ast.parse(SETTINGS_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", "") == "DEFAULT_SETTINGS" for target in node.targets
        ):
            return {
                key.value: ast.literal_eval(value)
                for key, value in zip(node.value.keys, node.value.values)
            }
    raise AssertionError("DEFAULT_SETTINGS is not an assignment in settings.py any more")


def disagreements(defaults: dict, controls: list[dict]) -> list[str]:
    """Every way a control and the value it edits can fail to describe the same thing.

    PURE, so the tests below can feed it a known-broken pair. A comparison that has only ever seen
    the real files has never been shown to report anything.
    """
    found = []
    for control in controls:
        key = control["key"]
        label = control.get("label") or key
        if key not in defaults:
            found.append(f"control {label!r} edits {key!r}, which no setting declares")
            continue
        accepts = CONTROL_ACCEPTS.get(control.get("type") or "")
        if accepts is None:
            found.append(f"control {label!r} uses type {control.get('type')!r}, which has no meaning here")
            continue
        value = defaults[key]
        # bool is a subclass of int in Python, so a toggle and a number must be told apart exactly.
        if bool in accepts:
            if not isinstance(value, bool):
                found.append(f"control {label!r} is a toggle but {key!r} defaults to {value!r}")
                continue
        elif isinstance(value, bool):
            found.append(f"control {label!r} is a {control.get('type')} but {key!r} defaults to the boolean {value!r}")
            continue
        elif not isinstance(value, accepts):
            found.append(f"control {label!r} is a {control.get('type')} but {key!r} defaults to {value!r}")
            continue

        low, high = control.get("min"), control.get("max")
        if isinstance(value, int) and not isinstance(value, bool):
            if isinstance(low, (int, float)) and value < low:
                found.append(f"control {label!r} has min {low} but {key!r} defaults to {value}")
            if isinstance(high, (int, float)) and value > high:
                found.append(f"control {label!r} has max {high} but {key!r} defaults to {value}")
        options = control.get("options")
        if options is not None and value not in options:
            found.append(
                f"control {label!r} offers {options!r} but {key!r} defaults to {value!r}, "
                "which the operator cannot select"
            )
    return found


class SettingsControlsMatchTheirValues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node is not on PATH, so the real schema cannot be evaluated")
        done = subprocess.run(
            [node, str(PROBE)], cwd=str(REPO), capture_output=True, text=True,
        )
        if done.returncode != 0:
            raise AssertionError(f"the schema probe failed:\n{done.stderr[:2000]}")
        cls.probe = json.loads(done.stdout)
        cls.defaults = declared_settings()
        cls.controls = cls.probe["controls"]

    def test_the_probe_actually_rendered_the_panel(self):
        """POSITIVE CONTROL. Every assertion below is vacuous on an empty schema or a panel that
        threw, and a render that produced nothing looks exactly like one with no disagreements."""
        self.assertIsNone(self.probe["renderError"], "the settings panel threw while rendering")
        self.assertGreater(self.probe["renderedLength"], 1000, "the panel rendered almost nothing")
        self.assertGreater(len(self.controls), 30, "the evaluated schema holds almost no controls")
        self.assertGreater(len(self.defaults), 30, "DEFAULT_SETTINGS parsed as almost nothing")

    def test_no_setting_is_edited_by_two_controls(self):
        """A duplicated key gives one value two widgets, and the regex this gate used to rely on
        collapsed them into one row where they were undetectable."""
        keys = [c["key"] for c in self.controls]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(duplicates, [], f"these settings have more than one control: {duplicates}")

    def test_every_control_agrees_with_the_value_it_edits(self):
        found = disagreements(self.defaults, self.controls)
        self.assertEqual(found, [], "\n".join(["controls disagree with their settings:"] + found))

    def test_every_control_redraws_when_its_own_setting_changes(self):
        """THE BINDING, measured rather than inferred from matching names.

        Review rewired one field to read another setting and every name-based check stayed green.
        Changing one setting on its own must change the rendered panel; a field that does not
        respond is not showing the value its label claims.
        """
        inert = sorted(k for k, responds in self.probe["respondsToItsOwnValue"].items() if not responds)
        self.assertEqual(
            inert, [],
            f"these controls did not redraw when their own setting changed: {inert}. "
            "The panel is displaying something other than the value they name.",
        )

    def test_a_setting_hidden_from_the_operator_is_named(self):
        shown = {c["key"] for c in self.controls}
        hidden = {key for key in self.defaults if key not in shown}
        self.assertEqual(
            hidden, NOT_OPERATOR_FACING,
            "a setting appeared on neither the panel nor the internal list; decide which it is",
        )

    def test_the_list_of_hidden_settings_holds_no_ghosts(self):
        gone = NOT_OPERATOR_FACING - set(self.defaults)
        self.assertEqual(gone, set(), f"named as internal but no longer declared: {sorted(gone)}")

    # ── the comparison can say PRESENT, proven on inputs it must reject ───────────────────────
    #
    # THESE PROVE THE COMPARISON, NOT THE EXTRACTOR, and review was right to insist on the
    # distinction. The extractor is `settings_schema_probe.mjs` evaluating the real module, which is
    # the only thing that can answer a question about JavaScript semantics.

    def test_negative_control_a_control_for_a_setting_that_does_not_exist(self):
        found = disagreements({"real": 1}, [{"key": "ghost", "label": "Ghost", "type": "number"}])
        self.assertTrue(any("no setting declares" in f for f in found), found)

    def test_negative_control_a_toggle_on_a_number(self):
        found = disagreements({"count": 90}, [{"key": "count", "label": "Count", "type": "toggle"}])
        self.assertTrue(any("is a toggle" in f for f in found), found)

    def test_negative_control_a_number_field_on_a_boolean(self):
        found = disagreements({"flag": True}, [{"key": "flag", "label": "Flag", "type": "number"}])
        self.assertTrue(any("defaults to the boolean" in f for f in found), found)

    def test_negative_control_a_fractional_bound_that_excludes_its_own_default(self):
        """`min: 90.5` truncated to 90 under the old regex and passed. A float must compare as one."""
        found = disagreements({"n": 90}, [{"key": "n", "label": "N", "type": "number", "min": 90.5}])
        self.assertTrue(any("has min 90.5" in f for f in found), found)

    def test_negative_control_a_computed_bound_that_excludes_its_own_default(self):
        """`min: 45 * 3` read as 45 under the old regex while the browser computes 135."""
        found = disagreements({"n": 90}, [{"key": "n", "label": "N", "type": "number", "min": 135}])
        self.assertTrue(any("has min 135" in f for f in found), found)

    def test_negative_control_a_select_whose_default_is_not_on_offer(self):
        found = disagreements(
            {"effort": "high"},
            [{"key": "effort", "label": "Effort", "type": "select", "options": ["low", "medium"]}],
        )
        self.assertTrue(any("cannot select" in f for f in found), found)

    def test_negative_control_a_control_type_nobody_declared(self):
        found = disagreements({"x": "y"}, [{"key": "x", "label": "X", "type": "wormhole"}])
        self.assertTrue(any("no meaning here" in f for f in found), found)

    def test_positive_control_a_matching_pair_reports_nothing(self):
        """Without this, a comparison that flagged EVERYTHING would satisfy every control above."""
        self.assertEqual(disagreements(
            {"flag": True, "count": 90, "name": "x", "runtimes": ["a"], "effort": "high"},
            [
                {"key": "flag", "label": "Flag", "type": "toggle"},
                {"key": "count", "label": "Count", "type": "number", "min": 1, "max": 600},
                {"key": "name", "label": "Name", "type": "text"},
                {"key": "runtimes", "label": "Runtimes", "type": "csv"},
                {"key": "effort", "label": "Effort", "type": "select", "options": ["low", "high"]},
            ],
        ), [])


if __name__ == "__main__":
    unittest.main()
