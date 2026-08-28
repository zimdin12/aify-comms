"""A row opts IN to compact phone layout at the markup, and the rule it opts into really overrides.

WHY THERE IS A CLASS AT ALL. Below 414px `.button-row` becomes a grid, so every button in it goes
full-width and stacks. That is RIGHT for long labels and wrong for short ones, and CSS cannot tell them
apart -- whether two buttons fit beside each other is a property of their LABELS. So the decision is
made per row, by measurement, and carried in the markup as `mobile-compact`.

MEASURED at 390x780, forcing each candidate row to wrap and re-reading its box:

    .button-row.top-quick-jumps   Work/Spawn/Settings/Notify/Refresh
        481px -> 381px of a 780px screen, FIVE rows -> ONE. 62% chrome -> 49%. Opted in.
    #page-settings .section-head  "Save changes" + "Reset"
        92px -> 42px, TWO rows -> ONE, 115 + 59 of 390. Opted in.
    .diagnostics-maintenance      "Repair delivered reads" + "Repair handoffs"
        120px -> 92px, still TWO rows, second button forced to a full 362px. NOT opted in.

Saving 28px for a ragged two-line layout is not an improvement, and long labels are exactly the case
the original comment said should stack. The refusal is as deliberate as the two inclusions.

HOW THAT EVIDENCE WAS TAKEN, precisely, because it is not a render of what is committed: a CDP
measurement of the LIVE pre-fix DOM and CSS, with the candidate declarations injected as a later
`<style>` and the box re-read in the same call. It is experimental evidence for the proposed rule. The
container still serves the old stylesheet, so the committed CSS has never rendered anywhere.

WHY 390 AND NOT 414, since the breakpoint is 414 and edges are where these break. The query is
inclusive (`max-width: 414px`) and nothing else applies between 390 and 414, so fitting at 390 is the
conservative case: more width at 414 cannot make a row that already fits stop fitting. The measurement
proves the worst narrower case and the source proves the rule applies at the edge. (I did also read it
at 414x780 -- one row each, rightmost element 400 of 414, no overflow -- but that run is corroboration,
not the argument.)

THE SELECTOR THIS REPLACED WAS DEAD. `styles.css` carried `.attention-strip .button-row`, with a
comment about "three full-width stacked rows", and it matches NOTHING: the strip renders contract cards
whose actions are `.contract-actions`, and no renderer emits a `button-row` inside it. Checked on three
pages, collapsed and expanded. I inherited that comment as precedent and extended a rule that had
stopped applying, which is its own small lesson about reading CSS rather than querying it.

WHAT THIS FILE ASSERTS, and the earlier version got it wrong in both directions. It required only that
a selector have SOME phone declaration -- so losing `display: flex` while keeping an unrelated `color`
would have passed with the row still stacked -- and it forbade the refused row ANY phone declaration at
all, so an unrelated mobile margin fix would have failed with a misleading verdict about compactness.
Both are fixed below: the rule must actually override the grid, and the refusal is about the opt-in
only.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

DASH = Path(__file__).resolve().parent.parent / "new_dashboard"
CSS = (DASH / "styles.css").read_text(encoding="utf-8")
HTML = (DASH / "index.html").read_text(encoding="utf-8")

#: The breakpoint whose `.button-row { display: grid }` causes the stacking.
#:
#: IT IS 414, NOT 760, and the first version of this file had it wrong in its prose AND its constant.
#: Both apply on a 390px screen, so a browser probe measured the improvement correctly either way and
#: only the positive control below noticed -- it reported the stacking rule as absent from a block it
#: was never in.
_PHONE = "@media (max-width: 414px)"

#: The opt-in class. One owner, so there are no sibling selectors left to drift apart.
_COMPACT = ".button-row.mobile-compact"


def _phone_block() -> str:
    """Every phone media query's body, concatenated, balancing braces from each opening one.

    THE BREAKPOINT REPEATS -- seven times in this stylesheet -- and taking only the first occurrence
    judged one arbitrary block and reported the rules it wanted as absent.
    """
    bodies = []
    for start in [i for i in range(len(CSS)) if CSS.startswith(_PHONE, i)]:
        open_brace = CSS.index("{", start)
        depth = 0
        for i in range(open_brace, len(CSS)):
            if CSS[i] == "{":
                depth += 1
            elif CSS[i] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(CSS[open_brace + 1:i])
                    break
        else:
            raise AssertionError("a phone media query is unbalanced")
    if not bodies:
        raise AssertionError("no phone media query found")
    return chr(10).join(bodies)


BLOCK = _phone_block()


def _declarations_for(selector: str) -> list:
    """Every declaration block whose selector list names `selector`, inside the phone blocks."""
    out = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", BLOCK):
        if selector in rule.group(1):
            out.append(" ".join(rule.group(2).split()))
    return out


def _classes_of_button_rows() -> list:
    """The class attribute of every `button-row` container in index.html."""
    return re.findall(r'<div class="(button-row[^"]*)"', HTML)


class TheMobileCompactOptInIsRealAndMeasured(unittest.TestCase):
    def test_the_phone_block_was_actually_found(self):
        """POSITIVE CONTROL. A brace-balance returning an empty string would make every assertion below
        pass against nothing -- the failure this file is about, one level up."""
        self.assertIn(_PHONE, CSS, "the phone breakpoint has been renamed or removed")
        self.assertGreater(len(BLOCK), 500, "the phone media block came back suspiciously small")
        self.assertIn(".button-row", BLOCK, "the rule that causes the stacking is not in this block")

    def test_the_stacking_rule_is_still_what_makes_this_necessary(self):
        """The premise. If `.button-row` stops becoming a grid on phones, the opt-in is dead weight and
        this whole mechanism should be deleted rather than kept passing."""
        self.assertTrue(
            any(re.search(r"display:\s*grid", d) for d in _declarations_for(".button-row")),
            "no .button-row rule sets a grid on phones; the compact opt-in may no longer be needed",
        )

    def test_the_compact_rule_actually_OVERRIDES_the_grid(self):
        """The assertion the first version was missing entirely.

        It checked only that a selector had SOME declaration, which a stray `color` would satisfy while
        every opted-in row went on stacking. What matters is that the rule sets a non-grid display AND
        wraps -- without the wrap, a flex row of five buttons overflows instead of stacking, which is
        worse than the defect it replaces.
        """
        decls = _declarations_for(_COMPACT)
        self.assertTrue(decls, f"{_COMPACT} has no phone rule at all; every opted-in row still stacks")
        joined = " ".join(decls)
        display = re.search(r"display:\s*([\w-]+)", joined)
        self.assertIsNotNone(display, f"{_COMPACT} sets no display, so it cannot override the grid")
        self.assertNotEqual(display.group(1), "grid",
                            "the compact rule re-declares the grid it exists to undo")
        self.assertRegex(joined, r"flex-wrap:\s*wrap",
                         "without wrapping, a compact row overflows instead of stacking")

    def test_every_row_MEASURED_as_compactable_carries_the_class(self):
        """The markup half. These are the two whose buttons were measured to fit."""
        classes = _classes_of_button_rows()
        self.assertGreaterEqual(
            len(classes), 3, f"only {len(classes)} button-row containers found; the scan is not reading index.html")
        top = [c for c in classes if "top-quick-jumps" in c]
        self.assertTrue(top, "the header quick-jump row is gone from index.html")
        self.assertIn("mobile-compact", top[0],
                      "the header row lost its opt-in: 5 stacked buttons, 481px of a 780px screen")
        settings = [c for c in classes if c.strip() == "button-row mobile-compact"]
        self.assertTrue(settings,
                        "the Settings save/reset row lost its opt-in: 92px instead of 42px on a phone")

    def test_the_LONG_labelled_row_is_deliberately_left_OUT(self):
        """THE ONE THAT MEASURED BADLY, pinned so nobody completes the set without re-measuring.

        Narrowed from the first version, which forbade `.diagnostics-maintenance` ANY phone declaration
        at all -- so an unrelated mobile margin fix would have failed here with a verdict about
        compactness. Only the opt-in is refused.
        """
        classes = _classes_of_button_rows()
        diagnostics = [c for c in classes if "diagnostics-maintenance" in c]
        self.assertTrue(diagnostics, "the maintenance row is gone from index.html")
        self.assertNotIn(
            "mobile-compact", diagnostics[0],
            "`.diagnostics-maintenance` was opted in. That was measured and rejected: it saves 28px "
            "and still wraps to two lines with one button forced to a full 362px. If the labels got "
            "shorter, re-measure and update this test with the new numbers.",
        )

    def test_the_dead_attention_strip_selector_is_not_reintroduced(self):
        """It matched nothing and was removed. Bringing it back would look like coverage and be none:
        the strip renders contract cards, whose actions are `.contract-actions`."""
        self.assertNotIn(
            ".attention-strip .button-row", CSS,
            "a selector that matches no element in this app is back in the stylesheet",
        )


if __name__ == "__main__":
    unittest.main()
