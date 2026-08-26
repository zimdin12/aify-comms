"""The two supersession guards must stay the same guard.

`/turn-start` and `/turn-end` each refuse a post from a bridge that has been superseded, and the two
blocks are byte-identical. Nothing keeps them that way. This is the agreement test rather than a
refactor, deliberately: the block ends in an early `return`, so it does not fit either shape the
extract-method gate can prove, and a hot status path is a poor place to take an unprovable refactor
for a tidiness gain.

WHY DUPLICATION IS THE RISK HERE SPECIFICALLY. This repo has already paid for a hand-copied guard:
four call sites typed a quoted subject by hand instead of calling the quoter that neutralises escapes,
and a quote in a subject freed an imperative into an agent's context. A guard copied is a guard that
drifts, and the direction of drift matters -- `/turn-end` refusing a stale clear while `/turn-start`
accepts a stale set is exactly the asymmetry that produced the one-way ratchet toward `working` this
pair was written to end.

The comparison normalises whitespace only. A change to either block's LOGIC fails this; reindenting
one of them does not.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

SOURCE = Path(__file__).resolve().parent.parent / "routers" / "agents" / "turn_boundaries.py"

#: The guard, from its first line to the refusal it returns. Anchored on both ends so a match cannot
#: run past the block into whatever follows it.
GUARD = re.compile(
    r"try:\n\s+_body = await request\.json\(\).*?ignored\": \"superseded_bridge\"\}",
    re.S,
)


def _blocks() -> list[str]:
    return GUARD.findall(SOURCE.read_text(encoding="utf-8"))


class SupersessionGuardsAgreeTests(unittest.TestCase):
    def test_the_scanner_finds_both_guards(self) -> None:
        """Positive control. The assertion below compares a list to itself, and an empty list is
        equal to an empty list -- so a broken pattern would report agreement having read nothing."""
        blocks = _blocks()
        self.assertEqual(
            len(blocks), 2,
            f"expected the guard in both turn-start and turn-end, found {len(blocks)}. If one was "
            "deliberately removed, this test is the record that says the pair existed.",
        )

    def test_the_scanner_can_say_no(self) -> None:
        """Negative control. A pattern that matched anything would satisfy the count above."""
        self.assertEqual(
            GUARD.findall("def unrelated():\n    return {'ok': True}\n"), [],
            "the guard pattern matched source that contains no guard",
        )

    def test_both_guards_are_the_same_guard(self) -> None:
        blocks = _blocks()
        # Checked here as well as in the control, because unpacking a one-element list raises a
        # ValueError and the reader gets a stack trace where a sentence would do.
        self.assertEqual(
            len(blocks), 2,
            f"found {len(blocks)} supersession guard(s), not 2 -- one of turn-start or turn-end no "
            "longer refuses a superseded bridge at all",
        )
        first, second = blocks
        self.assertEqual(
            re.sub(r"\s+", " ", first).strip(),
            re.sub(r"\s+", " ", second).strip(),
            "turn-start and turn-end no longer refuse a superseded bridge the same way. Whichever "
            "one was changed, the other now disagrees with it -- and a stale bridge that is refused "
            "on one end and accepted on the other can only push an agent one way.",
        )

    def test_each_guard_reads_the_posted_bridge_id(self) -> None:
        """The field the whole guard turns on. Both blocks must key on what the DETECTOR sends, not
        on the stored attribution -- the harness hook posts no body and must stay authoritative."""
        for block in _blocks():
            self.assertIn('.get("bridgeId")', block)
            self.assertIn("AND agent_id = ?", block)


if __name__ == "__main__":
    unittest.main()
