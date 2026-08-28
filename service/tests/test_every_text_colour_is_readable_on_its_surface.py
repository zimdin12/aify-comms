"""Every text colour taken from a palette token is readable on the surfaces it can sit on.

THE OTHER HALF OF THE CONTRAST SURFACE. `theme-contrast.test.mjs` gates the colours the runtime
DERIVES from an accent -- and that is where every failure of this session lived: five of eight themes
below AA on primary buttons, 820 of 5832 accents failing on hover, an accent-text token judged against
a constant instead of the accent-mixed surfaces it is drawn on. All of them computed by a brightness
threshold standing in for a contrast one.

This file covers the colours a PERSON chose: `--green`, `--red`, `--amber`, `--muted`, `--text` and the
rest, used directly as `color:` in 141 rules. Measured 2026-08-28 against the three page surfaces they
can inherit, the worst is `--red` at 5.27:1 on `--panel-2`. Every one passes.

THAT CONTRAST IS THE POINT. Hand-picked colours were fine and every derived one was not, which is the
argument for gating the derivation rather than the palette -- and the argument for gating the palette
too, because the next token will be added in a hurry by someone who cannot eyeball a ratio.

WHAT IT DELIBERATELY DOES NOT JUDGE. A rule that sets its OWN background is measured against that
background by whatever owns it, not against the page. `--accent-contrast` is the clearest case: it is
by definition the colour drawn on `--accent`, and judging it against `--bg` reports 1.03:1 for a pair
that never occurs. The first version of this sweep did exactly that and produced one confident false
failure -- the same error as certifying a proxy, in the opposite direction. 70 of the 211 rules own
their background and are excluded here; `theme-contrast.test.mjs` owns the accent ones.

Composited tints are also out of scope: `.status-chip.ok` sits on `rgba(84,197,139,.08)` over the
panel, which this cannot resolve from the stylesheet alone. Measured by hand on the same day -- the
four chips run 4.73 to 7.74 against both panels -- and left uncovered rather than approximated, because
a gate that guesses at a background is the thing this file exists to argue against.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

CSS = (Path(__file__).resolve().parent.parent / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

#: WCAG AA for normal text. The dashboard's body size is 14px and its smallest is 11px, so nothing here
#: qualifies for the 3:1 large-text relaxation.
MINIMUM_RATIO = 4.5

#: The page surfaces a rule can inherit when it does not set its own background.
SURFACES = {"--bg": "#0a0b0c", "--panel": "#15191b", "--panel-2": "#1d2325"}


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


ROOT_COLOURS = dict(re.findall(r"^\s*--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", CSS, re.M))


def _rules():
    """(token, value, selector) for every rule that sets a text colour and INHERITS its background."""
    inheriting, own_background = [], 0
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        selector, body = match.group(1).strip(), match.group(2)
        colour = re.search(r"(?<!-)color:\s*var\(--([\w-]+)", body)
        if not colour or colour.group(1) not in ROOT_COLOURS:
            continue
        if re.search(r"(?<!-)background(-color)?:", body):
            own_background += 1
            continue
        inheriting.append((colour.group(1), ROOT_COLOURS[colour.group(1)],
                           selector.split("\n")[-1].strip()[:60]))
    return inheriting, own_background


INHERITING, OWN_BACKGROUND = _rules()


class EveryTextColourIsReadableOnItsSurface(unittest.TestCase):
    def test_the_ratio_maths_is_right(self):
        """POSITIVE CONTROL. Black on white is 21:1 by definition; without it every assertion below is
        arithmetic on an unverified function."""
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=1)

    def test_the_stylesheet_was_actually_parsed(self):
        """A regex matching nothing makes the population empty and the sweep vacuous -- the failure this
        whole session kept finding in my own instruments."""
        self.assertGreater(len(ROOT_COLOURS), 15, f"only {len(ROOT_COLOURS)} palette tokens parsed")
        self.assertGreater(len(INHERITING), 50, f"only {len(INHERITING)} inheriting rules found")
        self.assertIn("red", ROOT_COLOURS, "the palette no longer defines --red")

    def test_rules_that_own_their_BACKGROUND_are_excluded(self):
        """The exclusion must do work, and must exclude the right thing.

        Without it this sweep reports `--accent-contrast` at 1.03:1 against `--bg` -- a confident
        failure for a pair that never occurs, since that token is by definition drawn on `--accent`.
        Judging a colour against a surface it never touches is the same error as certifying a proxy,
        pointed the other way.
        """
        self.assertGreater(OWN_BACKGROUND, 10, "no rule was excluded, so the refinement is inert")
        self.assertNotIn(
            "accent-contrast", {token for token, _, _ in INHERITING},
            "accent-contrast is being judged against page surfaces it is never drawn on",
        )

    def test_it_would_FAIL_a_colour_that_is_unreadable(self):
        """NEGATIVE CONTROL. A sweep that cannot fail proves nothing about the ones it passes."""
        self.assertLess(contrast_ratio("#2a2f31", SURFACES["--panel"]), MINIMUM_RATIO)

    def test_every_inherited_text_colour_meets_AA(self):
        failures = {}
        for token, value, selector in INHERITING:
            worst_surface, worst_value = min(
                SURFACES.items(), key=lambda kv: contrast_ratio(value, kv[1]))
            ratio = contrast_ratio(value, worst_value)
            if ratio < MINIMUM_RATIO:
                failures[token] = (value, round(ratio, 2), worst_surface, selector)
        self.assertEqual(
            failures, {},
            f"these palette colours are unreadable on a surface they can inherit: {failures}. "
            "Darken the surface or lighten the token -- and check theme-contrast.test.mjs, which owns "
            "the accent-derived colours rather than these hand-picked ones.",
        )


if __name__ == "__main__":
    unittest.main()
