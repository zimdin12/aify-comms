"""The pre-JS CSS fallback agrees with the value `applyTheme` writes over it.

THIS FILE USED TO CLAIM MORE THAN IT COULD. It parsed the eight `body[data-theme=...]` blocks and
declared the themes accessible. Those declarations are a FALLBACK: `theme.js`'s `applyTheme()` calls
`derivePaletteVars()` and sets every variable as an INLINE body style, which beats any stylesheet rule.
So the value this file audited was overwritten milliseconds into every boot, and seven passing static
pairs certified an artifact nobody ever sees. Review found it by executing the producer; no amount of
reading this file would have.

Worse, the runtime was failing where the static values passed -- five of eight themes below AA,
including the default at 2.03:1. That is now gated where the decision is made, in
`service/new_dashboard/theme-contrast.test.mjs`, against `derivePaletteVars` itself and against
arbitrary operator accents, which have no fixed population to enumerate.

WHAT IS LEFT FOR THIS FILE, and it is a real job rather than a consolation one: the fallback is what
paints between first paint and `applyTheme`, so if it disagrees with the runtime value the page flashes
one foreground and repaints with another. Agreement is the only property the CSS can be held to, and it
is the only one asserted here.
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

#: What `derivePaletteVars` returns for every shipped accent. Kept as a constant so a change to the
#: producer fails HERE too, rather than only in the JS gate.
RUNTIME_FOREGROUND = "#06110f"


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

    def test_the_fallback_AGREES_with_what_the_runtime_writes(self):
        """One value, two places. A mismatch is a visible flash, not a theory: the CSS paints first and
        `applyTheme` repaints, so opposite foregrounds would show as the button text inverting on load.

        The runtime derives `#06110f` for all eight shipped accents -- every one is light enough to
        carry dark text -- so the fallback is that value. If a future accent is dark enough to need the
        near-white foreground, this test fails and names the theme, which is the moment to change both.
        """
        disagreeing = {
            name: fg for name, (fg, _bg) in PAIRS.items() if fg.lower() != RUNTIME_FOREGROUND
        }
        self.assertEqual(
            disagreeing, {},
            f"these CSS fallbacks differ from the value theme.js derives ({RUNTIME_FOREGROUND}): "
            f"{disagreeing}. The page paints the fallback and then repaints the derived value, so a "
            "disagreement is a visible flash of the wrong foreground. Change both together, and check "
            "theme-contrast.test.mjs, which owns the readability half.",
        )

    def test_the_agreed_value_is_itself_readable_on_every_accent(self):
        """ANTI-VACUITY. Agreement alone would be satisfied by both sides being equally wrong -- this is
        the same arithmetic the JS gate applies, kept here so the fallback cannot agree its way into an
        unreadable pair."""
        failures = {
            name: round(contrast_ratio(fg, bg), 2)
            for name, (fg, bg) in PAIRS.items()
            if contrast_ratio(fg, bg) < MINIMUM_RATIO
        }
        self.assertEqual(failures, {}, f"the agreed fallback is unreadable on: {failures}")


if __name__ == "__main__":
    unittest.main()
