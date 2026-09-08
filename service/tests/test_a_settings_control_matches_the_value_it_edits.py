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
import re
from html.parser import HTMLParser
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

#: What each schema type must actually EMIT. The schema says a control is a number; the renderer
#: decides whether it draws `<input type="number">`, and review changed only the renderer while every
#: schema-based check stayed green. Two authorities, so both are read.
WIDGET_FOR_TYPE = {
    "toggle": "checkbox",
    "number": "number",
    "text": "text",
    "color": "color",
    "theme": "select",
    "select": "select",
    "csv": "text",
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


#: The tags the settings panel is known to emit, measured across every arm on 2026-09-08. Pinning
#: the VOCABULARY is what keeps this reader from silently mis-counting a container it does not model:
#: `<template>` puts its contents in a DocumentFragment, `<noscript>` and `<iframe>` have their own
#: rules, and a start tag looks identical inside all of them. Anything outside this set stops the
#: gate rather than being guessed at.
PANEL_TAGS = frozenset(
    {"b", "button", "code", "div", "input", "label", "option", "p", "section", "select", "span"}
)

#: Containers whose contents a browser does not connect. Enumerated deliberately, and the claim below
#: is bounded to them: `template` is the one review demonstrated, with 35 fields inside
#: `template.content` and ZERO connected. Anything else with container semantics is caught by
#: PANEL_TAGS instead of being modelled here.
INERT_CONTAINERS = frozenset({"template"})


class _FieldReader(HTMLParser):
    """Every settings field a browser would CONNECT, by real parsing rather than by pattern.

    WHY THE STANDARD LIBRARY IS ENOUGH HERE. `HTMLParser` puts `<script>` and `<style>` into CDATA
    mode and routes comments to `handle_comment`, so their contents never arrive as start tags. That
    is the container semantics three rounds of pattern matching lacked.

    IT IS NOT A DOM, THOUGH, and that is the bound on the claim. A start tag inside `<template>` is
    still a start tag here, while a browser puts it in a DocumentFragment where it is not an editable
    setting -- review demonstrated exactly that. So template contents are skipped explicitly, and the
    panel's tag vocabulary is pinned so any OTHER container arrives as a failure rather than as a
    miscount.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, dict] = {}
        self.duplicates: list[str] = []
        self.unexpected_tags: set[str] = set()
        self._select: str | None = None
        self._inert_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in INERT_CONTAINERS:
            self._inert_depth += 1
            return
        if tag not in PANEL_TAGS:
            self.unexpected_tags.add(tag)
        # A FIELD INSIDE AN INERT CONTAINER IS NOT A FIELD. The browser never connects it.
        if self._inert_depth:
            return
        attributes = dict(attrs)
        key = attributes.get("data-setting-key")
        if tag in ("input", "select") and key:
            if key in self.fields:
                self.duplicates.append(key)
            self.fields[key] = {
                "tag": tag,
                "widgetType": "select" if tag == "select" else attributes.get("type"),
                "declaredType": attributes.get("data-setting-type"),
                "value": attributes.get("value"),
                "checked": "checked" in attributes,
                "selected": None,
                "min": attributes.get("min"),
                "max": attributes.get("max"),
            }
            self._select = key if tag == "select" else None
            return
        if tag == "option" and self._select and "selected" in attributes:
            self.fields[self._select]["selected"] = attributes.get("value")

    def handle_endtag(self, tag):
        if tag in INERT_CONTAINERS:
            self._inert_depth = max(0, self._inert_depth - 1)
            return
        if tag == "select":
            self._select = None


def live_fields(html: str) -> tuple[dict[str, dict], list[str], set[str]]:
    """The fields a browser would CONNECT, duplicates, and any tag outside the panel's vocabulary."""
    reader = _FieldReader()
    reader.feed(html)
    reader.close()
    return reader.fields, reader.duplicates, reader.unexpected_tags


def displayed(control: dict, shown: dict) -> str | None:
    """What the panel is SHOWING for this control, normalised to compare with what it was given.

    Each widget carries its value somewhere different -- an attribute, a `checked` flag, a selected
    option -- so a single field cannot be read for all of them.
    """
    if shown is None:
        return None
    kind = control.get("type")
    if kind == "toggle":
        return "true" if shown.get("checked") else "false"
    if kind in ("select", "theme"):
        return shown.get("selected")
    return shown.get("value")


def as_displayed(control: dict, value) -> str | None:
    """The same value expressed the way its widget would show it, so the two can be compared."""
    kind = control.get("type")
    if kind == "toggle":
        return "true" if value else "false"
    if kind == "csv":
        return ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
    if value is None:
        return ""
    return str(value)


class SettingsControlsMatchTheirValues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node is not on PATH, so the real schema cannot be evaluated")
        cls.defaults = declared_settings()
        # THE DEFAULTS GO IN, so the probe can render the values the service actually ships with.
        # Only Python declares them, and only JavaScript can say what the panel does with them.
        done = subprocess.run(
            [node, str(PROBE), json.dumps(cls.defaults)],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if done.returncode != 0:
            raise AssertionError(f"the schema probe failed:\n{done.stderr[:2000]}")
        cls.probe = json.loads(done.stdout)
        cls.controls = cls.probe["controls"]
        # EVERY ARM, PARSED. Review hid the fields only at the shipped default, so a baseline-only
        # check passed while the arm the defaults comparison drew from held no live fields at all.
        cls.parsed = {}
        cls.duplicates = {}
        cls.unexpected = {}
        for name, html in cls.probe["arms"].items():
            fields, duplicates, unexpected = live_fields(html)
            cls.parsed[name] = fields
            if duplicates:
                cls.duplicates[name] = duplicates
            if unexpected:
                cls.unexpected[name] = sorted(unexpected)

    # ── what a parsed field shows, and how the arms compare ───────────────────────────────────

    def _shown(self, arm: str, key: str):
        field = self.parsed.get(arm, {}).get(key)
        if field is None:
            return None
        return (field["value"], field["checked"], field["selected"])

    def test_every_arm_rendered_the_full_set_of_LIVE_fields(self):
        """THE GRAMMAR CHECK, on every specimen and by a real parser.

        Three rounds of review defeated pattern-based extraction in turn -- an HTML comment, then
        CDATA, then an inert `<script type="text/plain">`. `html.parser` puts scripts and styles into
        CDATA mode and routes comments away, so a field inside any of them is not a start tag and
        never becomes a field. Nothing here enumerates what is forbidden.

        AND EVERY ARM, not just the baseline: review hid the fields ONLY at the shipped default, so a
        baseline-only check passed while the arm the defaults comparison drew from held none.
        """
        expected = len(self.controls)
        wrong = []
        for name, fields in sorted(self.parsed.items()):
            if len(fields) != expected:
                missing = sorted({c["key"] for c in self.controls} - set(fields))
                wrong.append(f"{name}: {len(fields)} live field(s), expected {expected}"
                             + (f"; missing {missing[:4]}" if missing else ""))
        self.assertEqual(wrong, [], "\n".join(["arms did not render every field as a live element:"] + wrong))
        self.assertEqual(self.duplicates, {}, f"a key was rendered more than once: {self.duplicates}")
        # THE VOCABULARY IS PINNED. A tag outside it may carry container semantics this reader does
        # not model -- `<template>` puts its contents in a fragment a browser never connects -- so it
        # stops the gate instead of being counted as though it were ordinary markup.
        self.assertEqual(
            self.unexpected, {},
            f"the panel emitted tags outside its known vocabulary: {self.unexpected}. If that is "
            "deliberate, decide what this gate should do about their container semantics first.",
        )

    def test_the_probe_actually_rendered_the_panel(self):
        """POSITIVE CONTROL. Every assertion is vacuous on an empty schema or a panel that threw, and
        a render that produced nothing looks exactly like one with no disagreements."""
        self.assertIsNone(self.probe["renderError"], "the settings panel threw while rendering")
        self.assertGreater(len(self.probe["arms"]["baseline"]), 1000, "the panel rendered almost nothing")
        self.assertGreater(len(self.controls), 30, "the evaluated schema holds almost no controls")
        self.assertGreater(len(self.defaults), 30, "DEFAULT_SETTINGS parsed as almost nothing")

    def test_no_setting_is_edited_by_two_controls(self):
        """A duplicated key gives one value two widgets. The parser reports it directly."""
        keys = [c["key"] for c in self.controls]
        schema_duplicates = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(schema_duplicates, [], f"the schema declares these twice: {schema_duplicates}")

    def test_every_control_agrees_with_the_value_it_edits(self):
        found = disagreements(self.defaults, self.controls)
        self.assertEqual(found, [], "\n".join(["controls disagree with their settings:"] + found))

    def test_every_control_redraws_when_its_own_setting_changes(self):
        """THE BINDING, read from the field that NAMES the setting."""
        inert = [c["key"] for c in self.controls
                 if self._shown(f"changed:{c['key']}", c["key"]) == self._shown("baseline", c["key"])]
        self.assertEqual(
            inert, [],
            f"these controls did not redraw when their own setting changed: {inert}. "
            "The panel is displaying something other than the value they name.",
        )

    def test_no_control_moves_when_a_DIFFERENT_setting_changes(self):
        """THE OTHER HALF. Review SWAPPED two bindings; each key still changed the panel, so a
        whole-panel comparison credited both while each showed the other's value."""
        spills = {}
        for control in self.controls:
            arm = f"changed:{control['key']}"
            moved = [c["key"] for c in self.controls
                     if c["key"] != control["key"]
                     and self._shown(arm, c["key"]) != self._shown("baseline", c["key"])]
            if moved:
                spills[control["key"]] = moved
        self.assertEqual(
            spills, {},
            f"changing one setting altered a field naming a DIFFERENT setting: {spills}",
        )

    def test_every_control_emits_the_widget_its_type_promises(self):
        """THE SCHEMA IS NOT THE RENDERER."""
        wrong = []
        for control in self.controls:
            field = self.parsed["baseline"].get(control["key"])
            if field is None:
                wrong.append(f"{control['key']}: nothing live carries this key")
                continue
            expected = WIDGET_FOR_TYPE.get(control.get("type") or "")
            if expected is None:
                wrong.append(f"{control['key']}: type {control.get('type')!r} has no expected widget")
            elif field["widgetType"] != expected:
                wrong.append(f"{control['key']}: declared {control.get('type')!r} but the panel emits "
                             f"{field['widgetType']!r}")
        self.assertEqual(wrong, [], "\n".join(["rendered widgets disagree with their types:"] + wrong))

    def test_every_control_displays_the_value_it_was_GIVEN(self):
        """FIDELITY, which movement cannot establish: a renderer showing `value + 1` moves the right
        field by the right amount and shows the wrong number."""
        wrong = []
        for control in self.controls:
            key = control["key"]
            want = as_displayed(control, self.probe["supplied"][key])
            got = displayed(control, self.parsed["baseline"].get(key))
            if got != want:
                wrong.append(f"{key}: given {want!r}, the panel shows {got!r}")
        self.assertEqual(wrong, [], "\n".join(["controls display something other than their value:"] + wrong))

    def test_every_control_displays_its_SHIPPED_DEFAULT_unaltered(self):
        """The same claim against the values an operator sees on first opening Settings."""
        wrong, inherited = [], []
        for control in self.controls:
            key = control["key"]
            if key not in self.defaults:
                continue
            want = as_displayed(control, self.defaults[key])
            got = displayed(control, self.parsed["defaults"].get(key))
            if control.get("type") == "color" and want == "":
                inherited.append(key)
                slot = {
                    "dashboard_primary_color": "accent",
                    "dashboard_secondary_color": "secondary",
                    "dashboard_tertiary_color": "tertiary",
                }.get(key)
                expected = (self.probe.get("inheritedPalette") or {}).get(slot)
                if not re.fullmatch(r"#[0-9a-f]{6}", got or ""):
                    wrong.append(f"{key}: inherits its colour but the panel shows {got!r}")
                elif expected and got != expected:
                    wrong.append(f"{key}: inherits from theme {self.probe.get('themeKey')!r}, which "
                                 f"defines {expected!r}, but the panel shows {got!r}")
                continue
            if got != want:
                wrong.append(f"{key}: ships {want!r}, the panel shows {got!r}")
        self.assertEqual(wrong, [], "\n".join(["defaults are not displayed as they are:"] + wrong))
        self.assertEqual(
            sorted(inherited),
            ["dashboard_primary_color", "dashboard_secondary_color", "dashboard_tertiary_color"],
            "the set of colours that inherit from the theme changed; re-decide the claim for them",
        )

    def test_the_bounds_the_panel_EMITS_admit_the_shipped_default(self):
        """THE RENDERED CONTRACT, not the schema's copy of it. A browser obeys the attribute."""
        wrong = []
        for control in self.controls:
            key = control["key"]
            if control.get("type") != "number" or key not in self.defaults:
                continue
            value = self.defaults[key]
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            field = self.parsed["defaults"].get(key) or {}
            for name in ("min", "max"):
                emitted = field.get(name)
                if emitted is None:
                    continue
                limit = float(emitted)
                if name == "min" and value < limit:
                    wrong.append(f"{key}: the panel emits min={emitted} but the default is {value}")
                if name == "max" and value > limit:
                    wrong.append(f"{key}: the panel emits max={emitted} but the default is {value}")
        self.assertEqual(wrong, [], "\n".join(["emitted bounds exclude their own defaults:"] + wrong))

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
