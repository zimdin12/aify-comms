"""Every rule that sets a text colour from a palette token is CLASSIFIED, and most are measured.

WHAT THE FIRST VERSION CLAIMED AND DID NOT DO. It said "every text colour", judged 141 rules, excluded
70 as "owns a background", and called the contrast surface complete. The exclusion was LEXICAL: any
`background:` spelling at all. Thirteen of those excluded rules set `transparent` or `none`, which owns
nothing -- they inherit the ancestor surface and were exactly this file's subject. `button.ghost`,
`.nav-item`, `.settings-tab` and the message-action buttons were all dropped from a sweep that then
declared itself total. 141 + 70 was a classification presented as a closure.

WHAT IT DOES NOW. All 211 candidate rules land in a named class, and an unrecognised background syntax
FAILS rather than being quietly skipped:

    INHERITED        154  measured against the three fixed page surfaces
    OWNS_OPAQUE       42  measured against the background its own rule sets
    COMPOSITE         13  typed UNCOVERED -- rgba()/color-mix() over an underlay this cannot resolve
    UNRESOLVED         2  typed UNCOVERED -- background is `--accent-soft`/`--accent-softer`, derived
                          at runtime from the operator's accent and so not a value in this file

196 of 211 are measured and every one passes; the worst is `--red` at 5.27:1 on `--panel-2`. The other
15 are not silently absent, they are named, and the counts are pinned by an equality relation so a
parser that quietly stops seeing rules fails here instead of reporting a clean sweep of a smaller
population.

THE UNCOVERED 15 ARE NOT DELEGATED TO A PROMISE. Saying "some other owner measures them" does not
create that owner. The composites were measured BY HAND on 2026-08-28 -- the four status chips run 4.73
to 7.74 over both panels -- and hand measurement is evidence, not a gate, so it is recorded as a fact
with a date rather than as coverage.

WHY THIS MATTERS BEYOND TIDINESS: every contrast failure found this session was in a colour a machine
DERIVED, and every hand-picked one passed. The derived side is gated in
`service/new_dashboard/theme-contrast.test.mjs`. This is the other half, and it is worth having exact
rather than approximately right.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

CSS = (Path(__file__).resolve().parent.parent / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

#: WCAG AA for normal text. The dashboard's body size is 14px and its smallest is 11px.
MINIMUM_RATIO = 4.5

#: Surfaces an inheriting rule can sit on.
SURFACES = {"--bg": "#0a0b0c", "--panel": "#15191b", "--panel-2": "#1d2325"}

ROOT = dict(re.findall(r"^\s*--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", CSS, re.M))


def _luminance(colour: str) -> float:
    value = colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    x, y = _luminance(a), _luminance(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def _classify():
    """Every candidate rule, in exactly one bucket. An unknown background syntax is its own class."""
    buckets = {"INHERITED": [], "OWNS_OPAQUE": [], "COMPOSITE": [], "UNRESOLVED": [], "UNKNOWN": []}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        selector, body = match.group(1).strip(), match.group(2)
        colour = re.search(r"(?<!-)color:\s*var\(--([\w-]+)", body)
        if not colour or colour.group(1) not in ROOT:
            continue
        token, foreground = colour.group(1), ROOT[colour.group(1)]
        where = selector.split("\n")[-1].strip()[:60]
        background = re.search(r"(?<!-)background(?:-color)?:\s*([^;]+)", body)
        value = background.group(1).strip() if background else None

        # `transparent`/`none` OWNS NOTHING. Treating them as owned is the defect this file records.
        if value is None or value in ("transparent", "none"):
            buckets["INHERITED"].append((where, token, foreground))
        elif "rgba" in value or "color-mix" in value:
            buckets["COMPOSITE"].append((where, token, value[:40]))
        elif value.startswith("var(--"):
            bg_token = re.match(r"var\(--([\w-]+)", value).group(1)
            if bg_token in ROOT:
                buckets["OWNS_OPAQUE"].append((where, token, foreground, ROOT[bg_token]))
            else:
                buckets["UNRESOLVED"].append((where, token, bg_token))
        elif value.startswith("#"):
            buckets["OWNS_OPAQUE"].append((where, token, foreground, value.split()[0]))
        else:
            buckets["UNKNOWN"].append((where, token, value[:40]))
    return buckets


BUCKETS = _classify()
TOTAL = sum(len(v) for v in BUCKETS.values())


class TextColourContrastIsClassifiedAndMeasured(unittest.TestCase):
    def test_the_ratio_maths_is_right(self):
        """POSITIVE CONTROL. Black on white is 21:1 by definition."""
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=1)

    def test_it_would_FAIL_an_unreadable_colour(self):
        """NEGATIVE CONTROL. A sweep that cannot fail proves nothing about what it passes."""
        self.assertLess(contrast_ratio("#2a2f31", SURFACES["--panel"]), MINIMUM_RATIO)

    def test_the_classes_ACCOUNT_for_every_candidate(self):
        """The equality relation, so a parser that stops seeing rules fails here rather than reporting
        a clean sweep of a smaller population. The first version asserted only `> 50` included and
        `> 10` excluded, which would have stayed green through substantial loss."""
        self.assertEqual(TOTAL, 211, f"the candidate population moved: {[(k, len(v)) for k, v in BUCKETS.items()]}")
        self.assertEqual(len(BUCKETS["INHERITED"]), 154)
        self.assertEqual(len(BUCKETS["OWNS_OPAQUE"]), 42)
        self.assertEqual(len(BUCKETS["COMPOSITE"]), 13)
        self.assertEqual(len(BUCKETS["UNRESOLVED"]), 2)
        self.assertEqual(
            len(BUCKETS["INHERITED"]) + len(BUCKETS["OWNS_OPAQUE"])
            + len(BUCKETS["COMPOSITE"]) + len(BUCKETS["UNRESOLVED"]),
            TOTAL, "a rule fell outside every class",
        )

    def test_an_UNKNOWN_background_syntax_fails(self):
        """A class that cannot be judged must not be silently skipped -- that is how `transparent` came
        to be filed as 'owns a background' and dropped from a sweep claiming to be total."""
        self.assertEqual(BUCKETS["UNKNOWN"], [], f"unrecognised background syntax: {BUCKETS['UNKNOWN']}")

    def test_TRANSPARENT_rules_are_inherited_not_excluded(self):
        """The specific defect. `button.ghost` and `.settings-tab` set `background: transparent`, own
        nothing, and were excluded from a gate that then called itself complete."""
        inherited = {where for where, _, _ in BUCKETS["INHERITED"]}
        self.assertTrue(
            any(w.startswith("button.ghost") for w in inherited),
            "button.ghost is not being judged; a transparent background is being read as an owned one",
        )

    def test_every_INHERITED_colour_is_readable_on_the_page_surfaces(self):
        failures = {
            where: (token, round(min(contrast_ratio(fg, s) for s in SURFACES.values()), 2))
            for where, token, fg in BUCKETS["INHERITED"]
            if min(contrast_ratio(fg, s) for s in SURFACES.values()) < MINIMUM_RATIO
        }
        self.assertEqual(failures, {}, f"unreadable on a surface they inherit: {failures}")

    def test_every_OWNS_OPAQUE_pair_is_readable_against_its_own_background(self):
        """Measured against the token the rule itself sets, which is why these are not judged against
        the page: `--accent-contrast` on `--bg` is 1.03:1 for a pair that never occurs."""
        failures = {
            where: (token, round(contrast_ratio(fg, bg), 2))
            for where, token, fg, bg in BUCKETS["OWNS_OPAQUE"]
            if contrast_ratio(fg, bg) < MINIMUM_RATIO
        }
        self.assertEqual(failures, {}, f"unreadable against their own background: {failures}")

    def test_the_UNCOVERED_classes_are_named_rather_than_absent(self):
        """Typed absence. These cannot be resolved from the stylesheet -- a composite needs its
        underlay and an accent-derived token needs the runtime palette -- so they are declared, not
        quietly dropped and not delegated to an owner that does not exist."""
        self.assertTrue(BUCKETS["COMPOSITE"], "the composite class vanished; the classifier changed shape")
        self.assertTrue(
            any(tok in {"accent-soft", "accent-softer"} for _, _, tok in BUCKETS["UNRESOLVED"]),
            "the accent-derived backgrounds are no longer being reported as unresolved",
        )


if __name__ == "__main__":
    unittest.main()
