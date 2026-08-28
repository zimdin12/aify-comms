"""Every theme's primary-button text passes WCAG AA against its own accent.

WHAT `--accent-contrast` IS. `styles.css:108` sets `button.primary { background: var(--accent); color:
var(--accent-contrast) }`, so the pair is literally the foreground and background of every primary
action in the app -- Save changes, Spawn, Start agent, Send. Each theme declares its own.

MEASURED, and one theme failed. Computing the WCAG 2.x contrast ratio for all eight declared pairs:
seven land between 7.7:1 and 10.8:1, and `crimson` was 4.02:1 -- below the 4.5:1 minimum for normal
text. It was also the ONLY theme putting a LIGHT foreground (`#fff7fa`) on its accent; the other seven
use a near-black tint of their own hue. That anomaly is the defect, and both came from the same commit
that authored the theming engine, so it was an inconsistency at birth rather than a considered
exception.

THE 4.5 THRESHOLD IS THE RIGHT ONE, checked rather than assumed. WCAG relaxes to 3:1 for "large text",
which means 18.66px at bold or 24px otherwise. Rendered `button.primary` reports 14px at weight 750 --
bold, but nowhere near 18.66px -- so the normal-text rule applies and 4.02 is a real failure rather
than a pedantic one.

FIXED BY CHANGING THE FOREGROUND, not the accent. `#160508` gives 4.68:1, leaves the theme's signature
colour untouched, and makes crimson consistent with the other seven. Darkening the accent instead
would have passed too, at the cost of changing the colour the theme is named for everywhere it is
used -- borders, chips, bars -- to solve a text problem.

WHY A GATE AND NOT A ONE-OFF FIX. A contrast ratio is arithmetic on two hex values. Nobody can eyeball
it, a new theme is a single line of CSS added in a hurry, and the failure is invisible to everyone who
does not use that theme. This is exactly the kind of thing that should never depend on someone
remembering.

The two sibling variables `--secondary-contrast` and `--tertiary-contrast` were removed rather than
gated: they were declared on all eight themes plus `:root` -- nine each -- and read by NOTHING. They
existed only to be the pairs this file would have checked.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

CSS = (Path(__file__).resolve().parent.parent / "new_dashboard" / "styles.css").read_text(encoding="utf-8")

#: WCAG AA for normal-size text. `button.primary` renders 14px/750, which is bold but not "large".
MINIMUM_RATIO = 4.5


def _luminance(colour: str) -> float:
    value = colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    a, b = _luminance(foreground), _luminance(background)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def _theme_pairs() -> dict:
    """{theme: (accent_contrast, accent)} for every declared theme."""
    pairs = {}
    for name, body in re.findall(r'body\[data-theme="(\w+)"\]\s*\{([^}]+)\}', CSS):
        values = dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})", body))
        if "accent" in values and "accent-contrast" in values:
            pairs[name] = (values["accent-contrast"], values["accent"])
    return pairs


PAIRS = _theme_pairs()


class EveryThemeAccentMeetsContrast(unittest.TestCase):
    def test_the_ratio_maths_is_right(self):
        """POSITIVE CONTROL. Black on white is 21:1 by definition; a formula that cannot produce that
        number cannot be trusted to judge #d34b64, and every assertion below would be arithmetic on a
        broken function."""
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=1)
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#ffffff"), 1.0, places=2)

    def test_it_would_FAIL_the_ratio_that_prompted_this(self):
        """NEGATIVE CONTROL. The exact pair that was in the stylesheet must read as failing -- a gate
        that cannot fail the case it was written for is decoration."""
        self.assertLess(contrast_ratio("#fff7fa", "#d34b64"), MINIMUM_RATIO)

    def test_the_themes_were_actually_parsed(self):
        """A regex that matched nothing would make the population empty and the sweep vacuous."""
        self.assertGreaterEqual(len(PAIRS), 8, f"only parsed {sorted(PAIRS)}")
        self.assertIn("crimson", PAIRS, "the theme that failed is no longer being checked")

    def test_the_pair_still_colours_primary_button_text(self):
        """The premise. If `--accent-contrast` stops being the primary button's colour, this file is
        measuring something that no longer matters and should be repointed, not kept green."""
        self.assertRegex(
            CSS,
            r"button\.primary\s*\{[^}]*background:\s*var\(--accent\)[^}]*color:\s*var\(--accent-contrast\)",
            "button.primary no longer pairs --accent with --accent-contrast",
        )

    def test_every_theme_passes_AA_for_normal_text(self):
        failures = {
            name: round(contrast_ratio(fg, bg), 2)
            for name, (fg, bg) in PAIRS.items()
            if contrast_ratio(fg, bg) < MINIMUM_RATIO
        }
        self.assertEqual(
            failures, {},
            f"these themes render primary-button text below WCAG AA ({MINIMUM_RATIO}:1): {failures}. "
            "button.primary is 14px at weight 750 -- bold, but not large enough for the 3:1 relaxation. "
            "Darken --accent-contrast rather than the accent: the accent is the colour the theme is "
            "named for and is used on borders, chips and bars where the text rule does not apply.",
        )


if __name__ == "__main__":
    unittest.main()
