r"""The list of decisions that need the operator says how many there are. It must be right.

`docs/V0_7_WEAK_POINTS.md` opens with a ranked list, and its own prose explains why: the file is over
two thousand lines and a decision buried on line 900 is a decision nobody makes. The count at the top
is the first thing a reader takes from it.

THAT COUNT HAS BEEN WRONG TWICE, in both directions, and the entry says so itself. It stood at eight
for a full day of rounds while six more decisions were written below it. Then on 2026-08-29 a re-walk
found TWO of the nineteen already shipped -- `GET /terminals` exists, and its own docstring opens
"THIS DID NOT EXIST"; `active_count()` has a caller and `/health` reports `sockets`, which answered 6
when it was checked. An operator reading that list was being sent at two things already done.

Neither direction is detectable by reading the list. Both are arithmetic.

WHAT THIS CANNOT DO: decide whether an item is closed. That is a judgement, and it is why closed
entries are MARKED rather than deleted -- numbering that other entries and commit messages refer to
keeps resolving, and the marking is what makes the arithmetic possible at all.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "V0_7_WEAK_POINTS.md"
SECTION = "## What actually needs you, ranked"

#: Spelled-out numbers, because the prose spells them. A literal map rather than a derivation:
#: there is nothing to derive it FROM, and the alternative -- requiring a digit -- would be this
#: test dictating the document's prose style.
WORDS = {
    "TEN": 10, "ELEVEN": 11, "TWELVE": 12, "THIRTEEN": 13, "FOURTEEN": 14, "FIFTEEN": 15,
    "SIXTEEN": 16, "SEVENTEEN": 17, "EIGHTEEN": 18, "NINETEEN": 19, "TWENTY": 20,
    "TWENTY-ONE": 21, "TWENTY-TWO": 22, "TWENTY-THREE": 23, "TWENTY-FOUR": 24, "TWENTY-FIVE": 25,
}

ITEM = re.compile(r"^(\d+)\. \*\*(.*)$", re.MULTILINE)
#: The convention for a retired entry: the bold lead names it, so a closure is visible at the top of
#: the item rather than buried in its body.
CLOSED = re.compile(r"^\d+\. \*\*(?:CLOSED|FIXED)\b", re.MULTILINE)


def ranked_section(text: str | None = None) -> str:
    body = text if text is not None else DOC.read_text(encoding="utf-8")
    start = body.index(SECTION) + len(SECTION)
    rest = body[start:]
    end = rest.index("\n## ")
    return rest[:end]


def stated_open_count(section: str) -> int:
    match = re.search(r"^([A-Z][A-Z-]+) genuine decisions", section.strip(), re.MULTILINE)
    assert match, "the ranked list no longer states how many decisions it holds"
    word = match.group(1)
    assert word in WORDS, f"unmapped number word {word!r}; add it to WORDS"
    return WORDS[word]


def item_numbers(section: str) -> list[int]:
    return [int(m.group(1)) for m in ITEM.finditer(section)]


def closed_count(section: str) -> int:
    return len(CLOSED.findall(section))


class TheRankedDecisionListCountsItselfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.section = ranked_section()

    def test_the_parser_finds_a_real_list(self):
        """Anti-vacuity: every assertion below is arithmetic over what this finds, and a parser that
        found nothing would make all of them true."""
        numbers = item_numbers(self.section)
        self.assertGreaterEqual(len(numbers), 10, f"only {len(numbers)} ranked items parsed")
        self.assertEqual(list(range(1, len(numbers) + 1)), numbers,
                         "the ranked items are not numbered 1..N without gaps or repeats")

    def test_the_stated_count_is_the_items_that_are_still_open(self):
        items = len(item_numbers(self.section))
        closed = closed_count(self.section)
        self.assertEqual(
            items - closed, stated_open_count(self.section),
            f"the list holds {items} entries of which {closed} are marked closed, so it should say "
            f"{items - closed} genuine decisions",
        )

    def test_a_closed_entry_says_so_in_its_bold_lead(self):
        """Closed entries stay numbered and stay in place, so the only thing separating them from
        live ones is the marking. If that stops being visible at the top of the item, the arithmetic
        above still balances and a reader is still misled."""
        self.assertGreaterEqual(closed_count(self.section), 1,
                                "no entry is marked closed; the CLOSED convention may have been lost")

    def test_the_parser_sees_a_planted_item_and_a_planted_closure(self):
        planted = (
            SECTION + "\n\n"
            "THIRTEEN genuine decisions; the rest is recorded judgement.\n\n"
            "1. **A live thing.** Prose.\n"
            "2. **CLOSED 2026-01-01 by verification.** Prose.\n"
            "3. **FIXED somewhere.** Prose.\n"
            "\n## Next section\n"
        )
        section = ranked_section(planted)
        self.assertEqual([1, 2, 3], item_numbers(section))
        self.assertEqual(2, closed_count(section))
        self.assertEqual(13, stated_open_count(section))
        # And the arithmetic that would fail on it: 3 items, 2 closed, 1 open, says 13.
        self.assertNotEqual(len(item_numbers(section)) - closed_count(section),
                            stated_open_count(section))

    def test_an_unmapped_number_word_is_refused_rather_than_ignored(self):
        """A word this map does not know must stop the test, not read as zero -- a silent zero would
        make the count assertion pass for any list."""
        planted = SECTION + "\n\nFORTY genuine decisions.\n\n1. **X.** y.\n\n## Next\n"
        with self.assertRaises(AssertionError):
            stated_open_count(ranked_section(planted))


if __name__ == "__main__":
    unittest.main()
