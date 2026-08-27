"""Both quick-jump rows get the compact phone treatment, or neither does.

THE DEFECT WAS A TWIN LEFT BEHIND. `styles.css` turns `.button-row` into a grid below 760px, which
makes every button in it full-width and stacks them. Someone noticed what that does to a phone and
fixed it -- for `.attention-strip .button-row` only, with a comment saying why:

    The attention-strip's quick-jump buttons are short — wrap them in a row instead of three
    full-width stacked rows, so the strip header stays compact on a phone.

The HEADER's own row, `.button-row.top-quick-jumps`, holds Work / Spawn / Settings / Notify / Refresh
and has exactly the same problem. It was not included, and nothing said the two belonged together.

MEASURED in a 390x780 viewport before changing anything: five buttons, each 362px wide, stacked at
50px intervals, putting the first content 481px down a 780px screen -- 62% of the first screen is
chrome. With the same treatment applied: 381px, 49%, ONE row, and no horizontal overflow. 100px of a
phone screen, for a selector.

WHY THIS TEST AND NOT A SCREENSHOT. The failure mode is divergence, not appearance: one row gets a
compact rule and its twin quietly does not, which is how it happened the first time. So this asserts
they are governed TOGETHER -- if a future change gives one of them a different display, wrap or flex
treatment inside the phone breakpoint, this fails and names the pair. It deliberately does not pin the
VALUES, because how compact they are is a design choice and only their agreement is the invariant.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

CSS = Path(__file__).resolve().parent.parent / "new_dashboard" / "styles.css"
TEXT = CSS.read_text(encoding="utf-8")

#: The breakpoint whose `.button-row { display: grid }` causes the stacking.
#:
#: IT IS 414, NOT 760, and I had it wrong first. Both apply on a 390px screen, so a browser probe
#: measured the improvement correctly either way and only this file's positive control noticed --
#: it reported the stacking rule as absent from a block it was never in.
_PHONE = "@media (max-width: 414px)"


def _phone_block() -> str:
    """Every phone media query's body, concatenated, by balancing braces from each opening one.

    THE BREAKPOINT REPEATS, and taking only the first occurrence is the mistake this file was written
    to be about. `styles.css` opens the same media query several times, so a version of this helper
    that stopped at `TEXT.index(...)` judged one arbitrary block and reported the rules it wanted as
    absent. Both that and the wrong breakpoint were caught by the positive control below rather than
    by reading, which is the only reason neither is still here.
    """
    bodies = []
    for start in [m for m in range(len(TEXT)) if TEXT.startswith(_PHONE, m)]:
        open_brace = TEXT.index("{", start)
        depth = 0
        for i in range(open_brace, len(TEXT)):
            if TEXT[i] == "{":
                depth += 1
            elif TEXT[i] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(TEXT[open_brace + 1:i])
                    break
        else:
            raise AssertionError("a phone media query is unbalanced")
    if not bodies:
        raise AssertionError("no phone media query found")
    return chr(10).join(bodies)


BLOCK = _phone_block()


def _declarations_for(selector: str) -> list:
    """Every declaration block whose selector list mentions `selector`, inside the phone block."""
    out = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", BLOCK):
        if selector in rule.group(1):
            out.append(" ".join(rule.group(2).split()))
    return out


class TheMobileQuickJumpsStayCompactTogether(unittest.TestCase):
    def test_the_phone_block_was_actually_found(self):
        """POSITIVE CONTROL. A brace-balance that returned an empty string would make every assertion
        below pass against nothing -- the failure this whole file is about, one level up."""
        self.assertIn(_PHONE, TEXT, "the phone breakpoint has been renamed or removed")
        self.assertGreater(len(BLOCK), 500, "the phone media block came back suspiciously small")
        self.assertIn(".button-row", BLOCK, "the rule that causes the stacking is not in this block")

    def test_the_stacking_rule_is_still_what_makes_this_necessary(self):
        """The premise. If `.button-row` stops becoming a grid on phones, both overrides are dead
        weight and this test should be deleted rather than kept passing."""
        self.assertTrue(
            any("grid" in d for d in _declarations_for(".button-row")),
            "no .button-row rule sets a grid on phones; the compact overrides may no longer be needed",
        )

    def test_BOTH_rows_are_named_by_the_compact_rule(self):
        attention = _declarations_for(".attention-strip .button-row")
        header = _declarations_for(".button-row.top-quick-jumps")
        self.assertTrue(attention, "the attention strip lost its compact treatment")
        self.assertTrue(
            header,
            "`.button-row.top-quick-jumps` has no phone rule. It holds Work/Spawn/Settings/Notify/"
            "Refresh, and without this they stack full-width: measured 481px of a 780px screen before "
            "any content. Give it the same treatment as `.attention-strip .button-row`.",
        )

    def test_they_are_governed_the_SAME_way(self):
        """The real invariant, and the one whose absence caused this. Divergence is the defect; the
        particular values are a design choice."""
        self.assertEqual(
            sorted(_declarations_for(".attention-strip .button-row")),
            sorted(_declarations_for(".button-row.top-quick-jumps")),
            "the two quick-jump rows are styled differently on phones again; that divergence is how "
            "the header row was left stacking in the first place",
        )


if __name__ == "__main__":
    unittest.main()
