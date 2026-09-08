"""A dashboard control must agree with the value it edits.

WHAT THIS ASKS THAT ITS SIBLING DOES NOT. `test_every_dashboard_setting_has_a_reader.py` answers
"is this setting still read" -- the stale-setting question. This answers the other half of the B7
audit: does the CONTROL the operator is given match the VALUE the reader consumes. A checkbox on a
number, a number field on a boolean, or a slider whose range excludes the shipped default are all
settings that LOOK correct from either side alone.

BOTH SIDES ARE PARSED, NOT GREPPED. The Python defaults come from an AST walk of `DEFAULT_SETTINGS`
and the controls from the panel's own row literals, so a renamed key fails loudly instead of matching
a substring somewhere else in the file.

FOUR FAILURES IT CATCHES, each one a thing that reads as healthy from one side:

  a control whose key nothing declares   the operator edits a value no reader will ever see
  a control type that disagrees          the label promises a checkbox for something read as a count
  a range that excludes the default      the panel cannot display the value the service ships with
  a declared type nothing can produce    a control type with no meaning on the Python side

A SETTING WITH NO CONTROL IS NOT A FAILURE. Internal tunables are deliberately not operator-facing,
and forcing every one onto a panel would be worse than leaving them alone. They are pinned by NAME
instead, so adding one is a decision somebody writes down rather than a silent default.
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETTINGS_PY = REPO / "service" / "api_core" / "settings.py"
PANEL_MJS = REPO / "service" / "new_dashboard" / "settings-panel.mjs"

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

#: Declared but deliberately NOT on the panel. Each is an internal tunable whose value is a
#: judgement about the service's own behaviour rather than an operator preference. Listed by name so
#: that hiding a NEW setting from the operator is a decision, not an omission.
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


ROW = re.compile(r"\{\s*key:\s*'([^']+)'([^}]*)\}")


def panel_rows() -> list[dict]:
    """Every control row the settings panel declares."""
    source = PANEL_MJS.read_text(encoding="utf-8")
    rows = []
    for match in ROW.finditer(source):
        rest = match.group(2)
        row = {"key": match.group(1)}
        for field in ("label", "type"):
            found = re.search(rf"{field}:\s*'([^']*)'", rest)
            if found:
                row[field] = found.group(1)
        for field in ("min", "max"):
            found = re.search(rf"{field}:\s*(-?\d+)", rest)
            if found:
                row[field] = int(found.group(1))
        rows.append(row)
    return rows


def disagreements(defaults: dict, rows: list[dict]) -> list[str]:
    """Every way a control and the value it edits can fail to describe the same thing.

    PURE, so the tests below can feed it a known-broken pair. A checker that has only ever seen the
    real files has never been shown to report anything.
    """
    found = []
    for row in rows:
        key = row["key"]
        label = row.get("label", key)
        if key not in defaults:
            found.append(f"control {label!r} edits {key!r}, which no setting declares")
            continue
        accepts = CONTROL_ACCEPTS.get(row.get("type", ""))
        if accepts is None:
            found.append(f"control {label!r} uses type {row.get('type')!r}, which has no meaning here")
            continue
        value = defaults[key]
        # bool is a subclass of int in Python, so a toggle and a number must be told apart exactly.
        if bool in accepts:
            if not isinstance(value, bool):
                found.append(f"control {label!r} is a toggle but {key!r} defaults to {value!r}")
        elif isinstance(value, bool):
            found.append(f"control {label!r} is a {row.get('type')} but {key!r} defaults to the boolean {value!r}")
        elif not isinstance(value, accepts):
            found.append(f"control {label!r} is a {row.get('type')} but {key!r} defaults to {value!r}")
        elif row.get("type") == "number":
            low, high = row.get("min"), row.get("max")
            if low is not None and value < low:
                found.append(f"control {label!r} has min {low} but {key!r} defaults to {value}")
            if high is not None and value > high:
                found.append(f"control {label!r} has max {high} but {key!r} defaults to {value}")
    return found


class SettingsControlsMatchTheirValues(unittest.TestCase):
    def setUp(self):
        self.defaults = declared_settings()
        self.rows = panel_rows()

    def test_the_readers_of_both_files_actually_found_something(self):
        """POSITIVE CONTROL. Both parsers return an empty collection if a file is renamed or its
        shape changes, and every assertion below passes vacuously on an empty one."""
        self.assertGreater(len(self.defaults), 30, "DEFAULT_SETTINGS parsed as almost nothing")
        self.assertGreater(len(self.rows), 30, "the settings panel parsed as almost no controls")

    def test_every_control_agrees_with_the_value_it_edits(self):
        found = disagreements(self.defaults, self.rows)
        self.assertEqual(found, [], "\n".join(["controls disagree with their settings:"] + found))

    def test_a_setting_hidden_from_the_operator_is_named(self):
        """Hiding a setting is allowed and is a decision. Hiding a NEW one silently is not."""
        shown = {row["key"] for row in self.rows}
        hidden = {key for key in self.defaults if key not in shown}
        self.assertEqual(
            hidden, NOT_OPERATOR_FACING,
            "a setting appeared on neither the panel nor the internal list; decide which it is",
        )

    def test_the_list_of_hidden_settings_holds_no_ghosts(self):
        """The other end of the same field: a name here that no longer exists is a rule about
        nothing, and it would keep passing for ever."""
        gone = NOT_OPERATOR_FACING - set(self.defaults)
        self.assertEqual(gone, set(), f"named as internal but no longer declared: {sorted(gone)}")

    # ── the checker can say PRESENT, proven on inputs it must reject ──────────────────────────

    def test_negative_control_a_control_for_a_setting_that_does_not_exist(self):
        found = disagreements({"real": 1}, [{"key": "ghost", "label": "Ghost", "type": "number"}])
        self.assertTrue(any("no setting declares" in f for f in found), found)

    def test_negative_control_a_toggle_on_a_number(self):
        found = disagreements({"count": 90}, [{"key": "count", "label": "Count", "type": "toggle"}])
        self.assertTrue(any("is a toggle" in f for f in found), found)

    def test_negative_control_a_number_field_on_a_boolean(self):
        found = disagreements({"flag": True}, [{"key": "flag", "label": "Flag", "type": "number"}])
        self.assertTrue(any("defaults to the boolean" in f for f in found), found)

    def test_negative_control_a_range_that_excludes_its_own_default(self):
        low = disagreements({"n": 5}, [{"key": "n", "label": "N", "type": "number", "min": 10}])
        high = disagreements({"n": 5000}, [{"key": "n", "label": "N", "type": "number", "max": 100}])
        self.assertTrue(any("has min" in f for f in low), low)
        self.assertTrue(any("has max" in f for f in high), high)

    def test_negative_control_a_control_type_nobody_declared(self):
        found = disagreements({"x": "y"}, [{"key": "x", "label": "X", "type": "wormhole"}])
        self.assertTrue(any("no meaning here" in f for f in found), found)

    def test_positive_control_a_matching_pair_reports_nothing(self):
        """Without this, a checker that flagged EVERYTHING would satisfy all five controls above."""
        self.assertEqual(disagreements(
            {"flag": True, "count": 90, "name": "x", "runtimes": ["a"]},
            [
                {"key": "flag", "label": "Flag", "type": "toggle"},
                {"key": "count", "label": "Count", "type": "number", "min": 1, "max": 600},
                {"key": "name", "label": "Name", "type": "text"},
                {"key": "runtimes", "label": "Runtimes", "type": "csv"},
            ],
        ), [])


if __name__ == "__main__":
    unittest.main()
