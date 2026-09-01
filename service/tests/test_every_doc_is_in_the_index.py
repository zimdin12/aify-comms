"""A document nobody indexed is invisible in exactly the way the index was written to fix.

`docs/README.md` separates live reference from finished-work records, because until it existed
`V0.3_SPEC.md` and `ARCHITECTURE.md` sat side by side with equal weight. Measured 2026-09-01: only 23
of 70 documents were reachable from the files a newcomer opens, and the three most recent, most
substantial design documents in the repo -- `ENVIRONMENT_ADVERTISEMENT`, `SERVICE_ADAPTER_CONTRACT`,
`HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER` -- were NOT among them, while `V0.2_SPEC` ("what shipped,
and the ledger behind it") was. Live design invisible, history signposted.

AN INDEX ROTS THE MOMENT IT IS WRITTEN, and silently: the next document lands, nobody adds a line, and
the file still reads complete. So the population comes from the DIRECTORY and the assertion is that
every entry appears -- the same shape as `every-role-button-is-keyboard-operable`, which derives its
population from the markup rather than a list somebody has to remember to update.

IT DOES NOT JUDGE THE CLASSIFICATION, and could not: whether a document is live reference or a
finished record is a reading, not a fact, and the index says so itself for the handful where its three
signals disagree. This asserts only that a reader is TOLD SOMETHING about every file. Being placed in
the wrong section is a disagreement somebody can have; being absent is a file nobody can find.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
INDEX = DOCS / "README.md"


def indexed_text() -> str:
    return INDEX.read_text(encoding="utf-8")


def documents() -> list[str]:
    """Every markdown file directly in `docs/`, excluding the index itself.

    NOT recursive: `docs/superpowers/plans/` and `docs/superpowers/specs/` are dated working documents,
    one per piece of work, and are records by construction. The index says so rather than listing
    them, and a gate over them would fail on every plan anybody writes.
    """
    return sorted(p.name for p in DOCS.glob("*.md") if p.name != "README.md")


class EveryDocIsInTheIndexTests(unittest.TestCase):
    def test_there_are_documents_and_an_index_to_check(self):
        """POSITIVE CONTROL. An empty directory or a missing index would make the gate vacuous."""
        self.assertTrue(INDEX.is_file(), f"{INDEX} is missing; the index this gate protects is gone")
        self.assertGreater(len(documents()), 40, "the scan found almost no documents")
        self.assertGreater(len(indexed_text()), 2000, "the index is too short to be describing 70 files")

    def test_the_check_can_report_an_absence(self):
        """NEGATIVE CONTROL on the matcher: a name that is definitely not there must be reported."""
        self.assertNotIn("ZZZ_NOT_A_REAL_DOCUMENT", indexed_text())

    def test_every_document_is_named_in_the_index(self):
        text = indexed_text()
        # Matched WITHOUT the extension, because the index names most files bare and links a few.
        # A first version of this check matched `NAME.md` only and reported 51 of 70 as missing --
        # a false alarm from the instrument, not a finding.
        missing = [name for name in documents() if name[:-3] not in text]
        self.assertEqual(
            missing, [],
            "these documents are not named in docs/README.md:\n  " + "\n  ".join(missing)
            + "\nAdd each under `Live reference` if it describes how something works NOW, or under "
            "`Finished work` if it records a decision already made. If you cannot tell, put it under "
            "`Unclassified` with the reason -- that is a real answer and guessing is not.",
        )


if __name__ == "__main__":
    unittest.main()
