"""A setting whose LABEL promises a unit must not be converted as if it were another one.

B7'S THIRD QUESTION WAS DECLARED NOT MACHINE-CHECKABLE, and most of it is not: whether a field
called "Retention (days)" still bounds retention rather than something a refactor left it pointing
at needs a person. But one slice of it is decidable, and it is the slice where the failure actually
happens -- the UNIT. "Idle close after (min)" read as seconds is off by sixty, silently, and looks
exactly like a working setting to every gate that only asks whether the key is read.

TRACED BY HAND FIRST, 2026-09-09, all fourteen unit- and sentinel-bearing settings. Every one was
correct: `retention_days * 86400 * 1000`, `repeat_minutes * 60`, `f"-{stale_hours} hours"`,
`int(max_mb) * 1024 * 1024`, `f"-{minutes} minutes"`, `stale_minutes * 60`.
`reply_reminder_max_count` disables the cap at 0 ("0 = unlimited") and `_contract_reminder_is_full`
returns True at `full_every <= 1` ("0 = always full"). This gate is not repairing a defect; it keeps
that audit from having to be repeated, which is the only kind of audit that stays true.

IT WAS A REGEX AND THE REGEX WAS THE DEFECT. Three separate false-greens, each found by review
driving the real upload line rather than reading the patch:

  the key's own NAME satisfied the unit   `retention_days` contains "days", so the mention was the
                                          evidence; `* 86400` -> `* 60` passed cleanly
  a factor SET could not express a CHAIN  `* 1024 * 1024` and `* 1024` both reduce to {1024}, so
                                          megabytes read as KILOBYTES passed
  and the product still could not see     `/ 1024 / 1024` (wrong direction), `* 1024 * 1024 * 1000`
  operators, comments or operands         (a time chain granted to bytes), and a missing factor
                                          "restored" by `# conversion review: * 1024` -- all passed

SO IT PARSES NOW. Python's own AST gives what no regex could: comments are gone before the walk
begins, `Mult` and `Div` are distinguishable, and a factor can be attributed to the OPERAND it is
applied to instead of to the line it shares. Each repair above was a smaller version of the same
mistake, which is the argument for parsing rather than for a better pattern.

AND PARSING DID NOT FIX THE FOURTH, which is the part worth knowing before trusting the sentence
above. Review drove two mutually exclusive branches, each converting megabytes to KILOBYTES:

    if legacy:  b = m * 1024
    else:       b = m * 1024

Neither path is ever right. The walk collected [1024, 1024] across the FILE, multiplied them to
1,048,576, matched what MB promises, and passed -- the check's own arithmetic manufactured a
correct answer out of two wrong ones. THE UNIT OF A CONVERSION IS AN EXPRESSION, NOT A FILE: each
outermost multiplicative expression is one SITE and each site is judged alone. Driven both ways --
the per-site gate kills that mutant, and a gate with the split removed lets it through, so the
split is what does the work rather than something else that changed with it.

SCOPE: PYTHON READERS. That is where a setting is enforced -- `shared.py` holds the upload cap,
`maintenance.py` the retention window. A dashboard module that renders "over the 500 MB limit" is
display, converts a different value (`file.size`), and is not judged here. Saying so is the point:
an unscoped gate that silently skipped those files would look identical to this one.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "service" / "new_dashboard"

#: What each label's unit is worth in the base its readers convert TO, the SQLite modifier word that
#: says the same thing without arithmetic, and whether a further *1000 into MILLISECONDS is a
#: legitimate continuation.
#:
#: THE CHAIN IS PER-UNIT, and granting it to everything was a false-green: `retention_days * 86400 *
#: 1000` is days -> seconds -> ms and correct, while `max_mb * 1024 * 1024 * 1000` is bytes
#: multiplied by a thousand and is not a unit conversion at all. Bytes have no millisecond.
UNITS: dict[str, dict[str, object]] = {
    "days": {"base": 86400, "words": {"days"}, "ms_chain": True},
    "min": {"base": 60, "words": {"minutes"}, "ms_chain": True},
    "h": {"base": 3600, "words": {"hours"}, "ms_chain": True},
    "s": {"base": 1000, "words": {"seconds"}, "ms_chain": False},
    "MB": {"base": 1048576, "words": set(), "ms_chain": False},
}

#: Numbers that are unit conversions at all. Anything else applied to a carrier (7, 100, 2) is
#: ordinary arithmetic and is not read as one.
CONVERSION_FACTORS = {60, 1000, 1024, 3600, 86400, 1048576}

#: `(min)`, `(days)`, `(h)`, `(s)`, `(MB)` at the end of a label, which is how this project writes a
#: unit. A label with no such suffix promises no unit and is not judged.
LABEL_UNIT = re.compile(r"\((days|min|h|s|MB)\)\s*$")

#: A name's own unit, read off its suffix: `retention_ms` is milliseconds however it was derived, so
#: the walk stops there rather than judging correct millisecond arithmetic against a days label.
SUFFIX_UNIT = (
    ("_ms", "ms"), ("_millis", "ms"),
    ("_bytes", "bytes"), ("_kb", "kb"),
    ("_seconds", "s"), ("_secs", "s"), ("_sec", "s"),
    ("_minutes", "min"), ("_mins", "min"), ("_min", "min"),
    ("_hours", "h"), ("_hrs", "h"),
    ("_days", "days"),
    ("_mb", "MB"),
)


def _name_unit(name: str) -> str:
    for suffix, unit in SUFFIX_UNIT:
        if name.endswith(suffix):
            return unit
    return ""


def schema_rows() -> list[dict]:
    """key and label for every drawn setting, read out of the RUNNING module.

    Imported rather than regexed, for the reason its sibling gate states: a shape change becomes an
    error here instead of a silent empty set.
    """
    result = subprocess.run(
        ["node", "-e",
         "import('./settings-panel.mjs').then(m => {"
         "  const rows = [];"
         "  for (const group of (m.SETTINGS_SCHEMA || [])) {"
         "    for (const item of (group.items || [])) "
         "      rows.push({key: (item && item.key) || '', label: (item && item.label) || ''});"
         "  }"
         "  console.log(JSON.stringify(rows));"
         "});"],
        cwd=DASHBOARD, capture_output=True, text=True, shell=True,
    )
    payload = [line for line in result.stdout.splitlines() if line.startswith("[")]
    if not payload:
        raise AssertionError(
            "could not read SETTINGS_SCHEMA out of settings-panel.mjs, so this gate judged nothing: "
            f"{result.stdout[-300:]}{result.stderr[-300:]}"
        )
    return json.loads(payload[-1])


def python_readers(key: str) -> list[Path]:
    """Non-test Python files naming this setting. The enforcing readers live here."""
    out = subprocess.run(
        ["git", "grep", "-l", "-F", key, "--", "service", "mcp",
         ":!*/tests/*", ":!*test_*", ":!*/fixtures/*"],
        cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [ROOT / p for p in out.stdout.split("\n") if p.strip().endswith(".py")]


def _mentions_key(node: ast.AST, key: str, locals_: set[str]) -> bool:
    """Does this expression read the setting, directly or through a one-hop local?"""
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and child.value == key:
            return True
        if isinstance(child, ast.Name) and child.id in locals_:
            return True
    return False


def carrier_locals(tree: ast.AST, key: str, unit: str) -> set[str]:
    """Names the setting's value flows into WHILE STILL IN THE LABEL'S UNIT.

    One hop, and it stops at a name whose own suffix declares a different unit -- following
    `retention_days` into `retention_ms` would judge correct millisecond arithmetic against a days
    label, which an earlier version of this did.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.Name] = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        if not targets or node.value is None or not _mentions_key(node.value, key, set()):
            continue
        for target in targets:
            carried = _name_unit(target.id)
            if carried and carried != unit:
                continue
            names.add(target.id)
    return names


def conversions_applied(tree: ast.AST, key: str,
                        locals_: set[str]) -> list[list[tuple[type, int]]]:
    """One list of (operator, factor) per conversion SITE applied to the setting's value.

    THE OPERAND, NOT THE LINE. A factor counts only when the OTHER side of the BinOp reads the
    setting -- so `file.size / (1024 * 1024)` on a line that also names `max_mb` contributes
    nothing, and `# conversion review: * 1024` contributes nothing because comments do not survive
    parsing.
    """
    # ONE ENTRY PER CONVERSION SITE, because a conversion is an EXPRESSION and not a file.
    #
    # COLLAPSING SITES LET TWO WRONGS MAKE A RIGHT. Two mutually exclusive branches each doing
    # `m * 1024` -- kilobytes on either path, both wrong -- contributed [1024, 1024], multiplied
    # to 1,048,576, and matched what MB promises. Review drove it. Each site is judged alone now.
    sites: list[list[tuple[type, int]]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or id(node) in seen:
            continue
        chain = _chain_factors(node, key, locals_, seen)
        if chain:
            sites.append(chain)
    return sites


def _chain_factors(node: ast.AST, key: str, locals_: set[str],
                   seen: set[int]) -> list[tuple[type, int]]:
    """The factors applied to the setting within ONE outermost multiplicative expression.

    `ast.walk` yields the outermost BinOp of a chain first, so marking every BinOp inside it as
    consumed keeps `a * 60 * 1000` one site rather than two overlapping ones.
    """
    factors: list[tuple[type, int]] = []
    stack = [node]
    touches_key = False
    while stack:
        current = stack.pop()
        if isinstance(current, ast.BinOp) and isinstance(current.op, (ast.Mult, ast.Div)):
            seen.add(id(current))
            for value_side, number_side in ((current.left, current.right),
                                            (current.right, current.left)):
                if (isinstance(number_side, ast.Constant)
                        and isinstance(number_side.value, int)
                        and not isinstance(number_side.value, bool)
                        and number_side.value in CONVERSION_FACTORS):
                    factors.append((type(current.op), number_side.value))
                    stack.append(value_side)
                    break
            else:
                stack.extend([current.left, current.right])
        elif _mentions_key(current, key, locals_):
            touches_key = True
    return factors if (touches_key and factors) else []


def modifier_words(tree: ast.AST, key: str, locals_: set[str]) -> set[str]:
    """SQLite modifier words in an f-string that interpolates the setting: `f"-{hours} hours"`."""
    words: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        if not any(_mentions_key(part.value, key, locals_)
                   for part in node.values if isinstance(part, ast.FormattedValue)):
            continue
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                for word in ("days", "minutes", "hours", "seconds"):
                    if word in part.value:
                        words.add(word)
    return words


class ASettingLabelledWithAUnitIsReadInThatUnit(unittest.TestCase):
    def _united(self) -> list[tuple[str, str, str]]:
        rows = []
        for row in schema_rows():
            match = LABEL_UNIT.search(row["label"])
            if match and row["key"]:
                rows.append((row["key"], row["label"], match.group(1)))
        return rows

    def test_the_scan_found_its_subject(self) -> None:
        """The control. An empty schema or an empty unit set satisfies everything below."""
        self.assertGreaterEqual(len(schema_rows()), 30, "implausibly few settings drawn")
        self.assertGreaterEqual(
            len(self._united()), 8,
            "too few labels carry a unit, so this gate is judging almost nothing")

    def test_the_parser_reads_operands_not_lines(self) -> None:
        """The negative controls, each one a false-green review actually published."""
        def applied(source: str, key: str = "max_shared_size_mb", unit: str = "MB"):
            tree = ast.parse(source)
            return conversions_applied(tree, key, carrier_locals(tree, key, unit))

        seed = 'm = settings["max_shared_size_mb"]\n'

        # A comment cannot supply a factor: parsing discards it.
        self.assertEqual(applied(seed + 'b = m * 1024  # review: * 1024'),
                         [[(ast.Mult, 1024)]])
        # A factor applied to a DIFFERENT value on the same line contributes nothing.
        self.assertEqual(applied(seed + 'b = size / (1024 * 1024) if m else 0'), [])
        # And the direction is visible.
        self.assertEqual(applied(seed + 'b = m / 1024 / 1024'),
                         [[(ast.Div, 1024), (ast.Div, 1024)]])
        # And the real shape still reads as the correct chain -- ONE site, both factors, so a
        # legitimate `* 1024 * 1024` is not split into two wrong-looking halves.
        self.assertEqual(applied(seed + 'b = int(m) * 1024 * 1024'),
                         [[(ast.Mult, 1024), (ast.Mult, 1024)]])

        # TWO MUTUALLY EXCLUSIVE BRANCHES, each converting megabytes to KILOBYTES. Review found
        # this passing: the flat collector this replaced reported [1024, 1024], multiplied them
        # to 1,048,576, and matched what MB promises -- while neither branch is ever right. Two
        # sites, judged apart, is the whole repair, and a flat collector cannot even express it.
        self.assertEqual(
            applied(seed + 'if legacy:\n    b = m * 1024\nelse:\n    b = m * 1024'),
            [[(ast.Mult, 1024)], [(ast.Mult, 1024)]])

    def test_a_reader_never_converts_a_setting_into_the_wrong_unit(self) -> None:
        wrong: list[str] = []
        judged = 0
        for key, label, unit in self._united():
            spec = UNITS[unit]
            base = int(spec["base"])                                       # type: ignore[arg-type]
            allowed = {base, base * 1000} if spec["ms_chain"] else {base}
            allowed_words = set(spec["words"])                             # type: ignore[arg-type]

            for path in python_readers(key):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:                       # pragma: no cover - a broken file is not
                    continue                              # this gate's finding to report
                locals_ = carrier_locals(tree, key, unit)
                rel = path.relative_to(ROOT).as_posix()

                words = modifier_words(tree, key, locals_)
                if words and not (words & allowed_words):
                    judged += 1
                    wrong.append(f"{rel} hands `{key}` to SQLite as {sorted(words)} while its "
                                 f"label {label!r} promises {sorted(allowed_words) or unit}")

                for site in conversions_applied(tree, key, locals_):
                    judged += 1
                    # DIVISION IS THE WRONG DIRECTION. Every unit here converts to a SMALLER unit
                    # -- days to seconds, megabytes to bytes -- so the factors multiply. A division
                    # by the right number is the inverse and lands orders out.
                    divisions = sorted(f for op, f in site if op is ast.Div)
                    if divisions:
                        wrong.append(f"{rel} DIVIDES `{key}` by {divisions} while its label "
                                     f"{label!r} promises a conversion INTO {unit}, which "
                                     f"multiplies")
                        continue
                    product = 1
                    for _op, factor in site:
                        product *= factor
                    # EACH SITE ALONE. Summing across sites let two wrong branches make a right one.
                    if product not in allowed:
                        wrong.append(f"{rel} converts `{key}` by a product of {product} at one "
                                     f"site, while its label {label!r} promises {sorted(allowed)}")

        self.assertGreater(judged, 0, (
            "no Python reader applied any conversion to a unit-labelled setting, so this gate "
            "compared nothing -- the readers moved, or the scan is looking in the wrong place"))
        self.assertEqual(wrong, [], (
            "a setting is converted as if its label promised a different unit, which is off by a "
            "factor and invisible to every gate that only asks whether the key is read:\n  "
            + "\n  ".join(wrong)))


if __name__ == "__main__":
    unittest.main()
