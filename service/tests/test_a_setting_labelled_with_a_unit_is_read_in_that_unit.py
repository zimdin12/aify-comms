"""A setting whose LABEL promises a unit must not be converted as if it were another one.

B7'S THIRD QUESTION WAS DECLARED NOT MACHINE-CHECKABLE, and most of it is not: whether a field
called "Retention (days)" still bounds retention rather than something a refactor left it pointing
at needs a person. But one slice of it is decidable, and it is the slice where the failure actually
happens -- the UNIT. "Idle close after (min)" read as seconds is off by sixty, silently, and looks
exactly like a working setting to every gate that only asks whether the key is read.

TRACED BY HAND FIRST, 2026-09-09, all fourteen unit- and sentinel-bearing settings. Every one was
correct: `retention_days * 86400 * 1000`, `repeat_minutes * 60`, `f"-{stale_hours} hours"`,
`int(max_mb) * 1024 * 1024`, `f"-{minutes} minutes"`. `reply_reminder_max_count` disables the cap at
0 ("0 = unlimited") and `_contract_reminder_is_full` returns True at `full_every <= 1`
("0 = always full"). So this gate is not repairing a defect -- it is keeping a hand-audit from
having to be repeated, which is the only kind of audit that stays true.

WHAT IT ASSERTS, deliberately narrow. For every non-test line carrying this setting's value AND
performing a unit conversion, that conversion must include one the label promises. It does NOT
require a conversion to be present -- a line with none is silent here -- so its teeth are on the
WRONG-CONVERSION case: `retention_days * 60`, or a megabyte cap multiplied by 1024 once. That is the
mutation that actually bites, and precisely the one an "is this key read?" gate cannot see.

**IT WAS DECORATION WHEN FIRST WRITTEN, and only the mutation sweep said so.** Five real unit bugs
were applied to the production readers and FOUR SURVIVED. Two separate causes, both worth keeping:

  * THE KEY'S OWN NAME SATISFIED THE UNIT. `retention_days` contains the word `days`,
    `contract_stale_hours` contains `hours`, every `*_seconds` contains `seconds` -- so the mention
    itself supplied the evidence the check was looking for, and `* 86400` -> `* 60` passed cleanly.
    Carrier names are stripped from a line before its conversions are read: measure the conversion,
    never the name of the thing being converted.
  * THE CONVERSION IS USUALLY NOT ON THE KEY'S OWN LINE. `repeat_minutes * 60`,
    `int(max_mb) * 1024 * 1024` and `f"-{stale_hours} hours"` all name a LOCAL. The scan follows one
    hop into the name a setting is assigned to.

AND THE HOP THEN OVERREACHED, which the same sweep caught in its turn: following `retention_days`
into `retention_ms` judged `int(time.time() * 1000) - retention_ms`, correct millisecond arithmetic
on a value no longer in days. A carrier whose NAME declares a different unit has already been
converted, and is not followed.

REACH, STATED RATHER THAN IMPLIED: one hop, within one file. `worker_idle_close_minutes` is handed
to another module as `idle_close_minutes=` and scaled in the query there; that is outside this gate
and is not claimed to be covered.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "service" / "new_dashboard"

#: What each label's unit is worth in the base the code converts TO, and the SQLite modifier word
#: that says the same thing without arithmetic.
#:
#: THE PRODUCT, NOT A SET OF FACTORS. `* 1024 * 1024` and `* 1024` both reduce to the SET {1024},
#: so a megabyte cap read as KILOBYTES satisfied an allowed-set of {1024, 1048576} -- review
#: removed one `* 1024` from `shared.py` and this gate still reported 3 passed. Multiplying the
#: factors together distinguishes them, because 1024 != 1048576.
#:
#: DIVISIBILITY WAS CONSIDERED AND REJECTED: 3600 is divisible by 60, so a minutes value read as
#: hours would satisfy a "product divides cleanly" rule.
UNITS: dict[str, dict[str, object]] = {
    "days": {"base": 86400, "words": {"days"}},
    "min": {"base": 60, "words": {"minutes"}},
    "h": {"base": 3600, "words": {"hours"}},
    "s": {"base": 1000, "words": {"seconds"}},
    "MB": {"base": 1048576, "words": set()},
}

#: The only chaining this tree does is a further *1000 into milliseconds
#: (`retention_days * 86400 * 1000`), so a product is correct at the base or the base times 1000.
CHAIN = (1, 1000)

#: Numbers that are unit conversions at all. Anything else on the line (7, 100, 200) is ordinary
#: arithmetic and must not be multiplied into the product.
ALL_FACTORS = {60, 1000, 1024, 3600, 86400, 1048576}
ALL_WORDS = {w for spec in UNITS.values() for w in spec["words"]}              # type: ignore[index]

#: `(min)`, `(days)`, `(h)`, `(s)`, `(MB)` at the end of a label, which is how this project writes
#: a unit. A label with no such suffix promises no unit and is not judged.
LABEL_UNIT = re.compile(r"\((days|min|h|s|MB)\)\s*$")

#: A factor may sit behind an opening parenthesis -- `file.size / (1024 * 1024)` is one conversion
#: written in two halves, and a regex that missed the first read its product as 1024 and refused a
#: correct line. The paren is optional, never required.
FACTOR = re.compile(r"[*/]\s*\(?\s*(\d[\d_]*)")
WORD = re.compile(r"['\"]\s*-?\{?[^'\"}]*\}?\s*(days|minutes|hours|seconds)\b")


def _schema_rows() -> list[dict]:
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


def _mentions(name: str) -> list[tuple[str, int, str]]:
    """Non-test, non-fixture lines naming `name`, across the service and the bridges."""
    out = subprocess.run(
        ["git", "grep", "-n", name, "--", "service", "mcp",
         ":!*/tests/*", ":!*test_*", ":!*.test.*", ":!*/fixtures/*"],
        cwd=ROOT, capture_output=True, text=True)
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3:
            rows.append((parts[0], int(parts[1]), parts[2]))
    return rows


#: `name = ... <key> ...` or `name=... <key> ...`, which is how a setting reaches a local before it
#: is converted. ONE hop is enough for every reader in this tree today and is where this stops:
#: a transitive walk would need a real parser, and a gate that half-follows is worse than one whose
#: reach is stated.
ASSIGN = re.compile(r"^\s*(?:[\w.\[\]\"']+\s*=\s*)?(\w+)\s*=\s*(?![=])")
KWARG = re.compile(r"\b(\w+)\s*=\s*[^=]")


#: A name's own unit, read off its suffix. `retention_ms` is milliseconds however it was derived.
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


def _carriers(key: str, unit: str) -> dict[str, set[str]]:
    """Per file, the names this setting's value flows into WHILE STILL IN THE LABEL'S UNIT.

    WITHOUT THE HOP THE GATE WAS DECORATION, and the mutation sweep proved it: four of five real
    unit bugs survived, because the conversion happens on a line naming a LOCAL
    (`repeat_minutes * 60`, `int(max_mb) * 1024 * 1024`, `f"-{stale_hours} hours"`) while the scan
    only looked at lines naming the setting.

    AND THE HOP HAS TO STOP AT A CONVERSION, which the first version did not: following
    `retention_days` into `retention_ms` then judged `int(time.time() * 1000) - retention_ms`, a
    line doing correct millisecond arithmetic on a value that is no longer in days. A carrier whose
    NAME declares a different unit has already been converted and its later arithmetic is not this
    setting's unit handling.

    REACH, STATED RATHER THAN IMPLIED: one hop, within one file. `worker_idle_close_minutes` is
    handed to another module as `idle_close_minutes=` and scaled there, which is beyond this and is
    not claimed.
    """
    per_file: dict[str, set[str]] = {}
    for path, _number, text in _mentions(key):
        names = per_file.setdefault(path, {key})
        head = text.split("#", 1)[0]
        candidates = set()
        match = ASSIGN.match(head)
        if match:
            candidates.add(match.group(1))
        candidates |= {kw.group(1) for kw in KWARG.finditer(head)}
        for name in candidates:
            if name == key:
                continue
            carried = _name_unit(name)
            if carried and carried != unit:
                continue          # already converted; its arithmetic is not this label's business
            names.add(name)
    return per_file


def _lines_naming(path: str, names: set[str]) -> list[tuple[int, str]]:
    """Every line in ONE file naming any of these carriers, read once rather than per name."""
    body = (ROOT / path).read_text(encoding="utf-8", errors="replace").splitlines()
    return [(number, text) for number, text in enumerate(body, 1)
            if any(name in text for name in names)]


def _product(text: str, carriers: set[str] = frozenset()) -> int:
    """The factors on one line MULTIPLIED, which is what a conversion actually is.

    Returns 1 when the line converts nothing, so a caller can tell "no conversion here" from
    "a conversion, and it is wrong".
    """
    stripped = text
    for name in sorted(carriers, key=len, reverse=True):
        stripped = stripped.replace(name, "_")
    product = 1
    for match in FACTOR.finditer(stripped):
        value = int(match.group(1).replace("_", ""))
        if value in ALL_FACTORS:
            product *= value
    return product


def _conversions(text: str, carriers: set[str] = frozenset()) -> set:
    """The unit conversions on one line: recognised factors and SQLite modifier words.

    THE CARRIER NAMES ARE REMOVED FIRST, and that is not tidiness. `retention_days` CONTAINS the
    word `days`, `contract_stale_hours` contains `hours`, every `*_seconds` contains `seconds` --
    so the identifier satisfied the unit requirement all by itself and the check could never fail.
    The mutation that exposed it (`* 86400` -> `* 60`) passed cleanly. Measure the conversion, never
    the name of the thing being converted.
    """
    stripped = text
    for name in sorted(carriers, key=len, reverse=True):
        stripped = stripped.replace(name, "_")
    found = {int(m.group(1).replace("_", "")) for m in FACTOR.finditer(stripped)}
    found &= ALL_FACTORS
    found |= {m.group(1) for m in WORD.finditer(stripped)}
    return found


class ASettingLabelledWithAUnitIsReadInThatUnit(unittest.TestCase):
    def test_the_scan_found_its_subject(self) -> None:
        """The control. An empty schema or an empty unit set satisfies everything below."""
        rows = _schema_rows()
        self.assertGreaterEqual(len(rows), 30, f"implausibly few settings drawn: {len(rows)}")
        united = [r for r in rows if LABEL_UNIT.search(r["label"])]
        self.assertGreaterEqual(
            len(united), 8,
            f"only {len(united)} labels carry a unit, so this gate is judging almost nothing")

    def test_the_scan_can_say_no(self) -> None:
        """The negative control, both halves: a wrong factor must be SEEN, and plain arithmetic
        must NOT be mistaken for a unit conversion."""
        self.assertEqual(_conversions("x = y * 60"), {60})
        self.assertEqual(_conversions('params.append(f"-{n} hours")'), {"hours"})
        self.assertEqual(_conversions("limit = count * 7"), set(),
                         "a factor that is not a unit conversion was read as one")
        # THE CARRIER'S OWN NAME MUST NOT SATISFY THE UNIT. `retention_days` contains `days`, and
        # until this was stripped every unit key satisfied its own check -- a mutation from
        # `* 86400` to `* 60` passed cleanly, which the mutation sweep is what caught.
        self.assertEqual(
            _conversions('int(settings["retention_days"] * 60)', {"retention_days"}), {60},
            "the key's own name was read as a unit conversion, so the check cannot fail")
        self.assertIn(
            "hours", _conversions('params.append(f"-{stale_hours} hours")', set()),
            "the positive control for a modifier word stopped matching")

    def test_a_reader_never_converts_a_setting_into_the_wrong_unit(self) -> None:
        wrong: list[str] = []
        judged = 0
        for row in _schema_rows():
            match = LABEL_UNIT.search(row["label"])
            if not match or not row["key"]:
                continue
            unit = match.group(1)
            base = int(UNITS[unit]["base"])                                    # type: ignore[arg-type]
            allowed_products = {base * step for step in CHAIN}
            allowed_words = set(UNITS[unit]["words"])                          # type: ignore[arg-type]
            for path, names in _carriers(row["key"], unit).items():
                for number, text in _lines_naming(path, names):
                    seen = _conversions(text, names)
                    if not seen:
                        continue
                    judged += 1
                    product = _product(text, names)
                    words = seen & ALL_WORDS
                    # AT LEAST ONE CORRECT CONVERSION, rather than NO other ones -- because a CHAIN
                    # is legitimate and the first version of this gate failed on one:
                    # `retention_days * 86400 * 1000` carries the right days->seconds factor and
                    # then a seconds->ms factor, and forbidding every unlisted factor called that a
                    # defect. A line converting ONLY by the wrong factor still fails, which is the
                    # case with teeth.
                    if words and not (words & allowed_words):
                        wrong.append(
                            f"{path}:{number} hands `{row['key']}` to SQLite as "
                            f"{sorted(words)} while its label {row['label']!r} promises "
                            f"{sorted(allowed_words) or unit}  --  {text.strip()[:80]}")
                    elif product != 1 and product not in allowed_products:
                        wrong.append(
                            f"{path}:{number} converts `{row['key']}` by a product of {product} "
                            f"while its label {row['label']!r} promises "
                            f"{sorted(allowed_products)}  --  {text.strip()[:80]}")

        self.assertGreater(judged, 0, (
            "no line mentioning a unit-labelled setting performed any conversion, so this gate "
            "compared nothing -- the readers moved, or the scan is looking in the wrong place"))
        self.assertEqual(wrong, [], (
            "a setting is converted as if its label promised a different unit, which is off by a "
            "factor and invisible to every gate that only asks whether the key is read:\n  "
            + "\n  ".join(wrong)))


if __name__ == "__main__":
    unittest.main()
