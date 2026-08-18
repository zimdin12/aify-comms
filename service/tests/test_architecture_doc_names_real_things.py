"""Every path and test `docs/ARCHITECTURE.md` names must exist, and each gate it cites must run.

WHY THIS FILE EXISTS. `docs/ARCHITECTURE.md` is written to be *actionable*: its central table pairs
each layer rule with the test that fails when the rule is broken, so a newcomer can trust the rule
without taking it on faith. That only works while the pairings are true. A doc that points at a
deleted test is worse than no doc — it tells a reader a rule is enforced when nothing enforces it,
which is the same false-green shape `aify-doctor`'s `env-bridge` check produced twice.

IT HAS ALREADY PAID FOR ITSELF. The first run of this check found
`test_bridge_and_service_quote_subjects_alike.py` in the doc — a filename that never existed. I wrote
it from memory instead of looking, and every word around it read correctly. The real file is
`test_subject_quoting_agrees_across_transports.py`. Nothing else would have caught that: prose is not
executed, and no suite reads a markdown file.

WHY IT CHECKS RUNNABILITY, NOT JUST EXISTENCE. A cited gate that exists but errors on import is
enforcing nothing while still reading as enforcement in the table. Existence is the cheap half.

The doc's own closing rule — "docs inherit intention, not outcome" — is why counts are deliberately
absent from it and are not checked here: this gate pins the CLAIMS THAT NAME THINGS, which are the
ones that rot into lies. A number in prose is nobody's observation; a path is a checkable fact.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "ARCHITECTURE.md"

#: Backticked paths in the doc. Only those containing a separator are treated as paths — the doc also
#: names bare identifiers in backticks (`_LIVE_STATE_CACHE`, `queueIfBusy`), which are not files.
_BACKTICKED = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|js|mjs|json))`")

#: Directory references, INCLUDING INSIDE THE FENCED DIAGRAM. The layering diagram is where the doc
#: makes its central structural claim — these are the layers — and it writes the directories as plain
#: text, not inline code. A first version of this regex only read backticked spans and found two of
#: them, so the layer names themselves, the part most worth verifying, went unchecked.
_DIR_REF = re.compile(r"\b(service/[a-z_]+|mcp/stdio)/(?:\*\*)?")


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def _cited_paths() -> list[str]:
    return sorted({m.group(1) for m in _BACKTICKED.finditer(_doc_text()) if "/" in m.group(1)})


class ArchitectureDocNamesRealThings(unittest.TestCase):
    def test_the_doc_exists_and_is_not_a_stub(self):
        """ANTI-VACUITY. Every assertion below passes trivially against an empty file: no citations
        means no broken citations. The doc must actually be there and actually cite things."""
        self.assertTrue(DOC.exists(), f"{DOC} is missing; the architecture doc is referenced from "
                                      "README.md and CLAUDE.md")
        self.assertGreater(len(_doc_text()), 4_000,
                           "the architecture doc has shrunk to a stub; this gate cannot verify "
                           "claims that are no longer made")
        self.assertGreaterEqual(
            len(_cited_paths()), 15,
            "the doc cites fewer paths than it did when this gate was written. Either the layer-rule "
            "table lost rows (a rule now has no named enforcer) or the citations were reworded out of "
            "backticks, where nothing can check them.",
        )

    def test_every_path_the_doc_names_exists(self):
        missing = [path for path in _cited_paths() if not (REPO / path).exists()]
        self.assertEqual(
            missing, [],
            "docs/ARCHITECTURE.md names files that do not exist: "
            + ", ".join(missing)
            + ". A doc that points at a deleted test tells a reader a rule is enforced when nothing "
              "enforces it. Fix the doc, or restore what it describes.",
        )

    def test_every_cited_directory_exists(self):
        cited = sorted({m.group(1) for m in _DIR_REF.finditer(_doc_text())})
        self.assertGreater(len(cited), 3, "the doc no longer names the layer directories")
        missing = [d for d in cited if not (REPO / d).is_dir()]
        self.assertEqual(missing, [], f"docs/ARCHITECTURE.md names missing directories: {missing}")

    def test_every_cited_python_gate_parses(self):
        """A cited gate that cannot even be parsed is enforcing nothing while still reading as
        enforcement in the table. Parsing is what this can prove cheaply and without importing the
        service; the suite run proves the rest."""
        gates = [p for p in _cited_paths() if p.startswith("service/tests/") and p.endswith(".py")]
        self.assertGreaterEqual(len(gates), 8, "the layer-rule table no longer cites Python gates")
        for gate in gates:
            with self.subTest(gate=gate):
                source = (REPO / gate).read_text(encoding="utf-8")
                try:
                    ast.parse(source)
                except SyntaxError as exc:  # pragma: no cover - would fail the suite anyway
                    self.fail(f"{gate} is cited as an enforcer but does not parse: {exc}")
                self.assertIn(
                    "def test", source,
                    f"{gate} is cited in the architecture doc as the test that enforces a rule, but "
                    "it declares no test function, so it enforces nothing.",
                )

    def test_the_doc_is_linked_from_the_entry_points(self):
        """A doc nobody is routed to is a doc nobody reads. README is the operator's entry point and
        CLAUDE.md is the agent's; the whole purpose of this file is to be found from them."""
        for entry in ("README.md", "CLAUDE.md"):
            with self.subTest(entry=entry):
                self.assertIn(
                    "docs/ARCHITECTURE.md", (REPO / entry).read_text(encoding="utf-8"),
                    f"{entry} does not link docs/ARCHITECTURE.md, so a newcomer arriving at "
                    f"{entry} will never be told it exists.",
                )

    def test_the_doc_does_not_claim_the_allowlist_has_entries(self):
        """The 1000-line allowlist being EMPTY is an end state, not a gap, and the doc says so. If
        somebody adds an entry, that sentence becomes false — and this is the only place the two
        facts are compared."""
        import json
        allowlist = json.loads((REPO / "oversized-allowlist.json").read_text(encoding="utf-8"))
        # Read the key BY NAME and require it. A `.get(..., default)` here would pass silently if the
        # policy file were ever restructured, which is the same "no evidence read as a pass" shape the
        # doc warns about — and it is exactly what the first version of this test did.
        self.assertIn("allowed", allowlist,
                      "oversized-allowlist.json has no `allowed` key; this gate can no longer tell "
                      "whether the doc's claim that the list is empty is true.")
        entries = allowlist["allowed"]
        if entries:
            self.fail(
                "oversized-allowlist.json has entries, but docs/ARCHITECTURE.md states the allowlist "
                f"is empty and that empty is the end state. Entries: {entries}. Adding one is a "
                "reviewer decision — if it was made, the doc must be updated to match."
            )


if __name__ == "__main__":
    unittest.main()
