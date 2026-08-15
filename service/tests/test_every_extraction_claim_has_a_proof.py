"""A docstring that says "Extracted from `f`" must have a test that inlines it back into `f`.

WRITTEN BECAUSE I SHIPPED ONE WITHOUT. `_build_online_agent_board` said it came out of
`get_analytics_pulse`, and no proof named that function — the analytics proof covers `get_analytics`,
a different handler in the same file. All three suites were green, the undefined-name sweep was
clean, and none of that is the round trip the receipt asks for. The claim was simply unbacked, and
nothing could have told me except looking.

WHAT COUNTS AS A PROOF, and there are three shapes because extractions arrive in three shapes:

  * `EXTRACTIONS = [...]` — the helper is inlined back and the result AST-compared to a frozen
    pre-split fixture. This is the real proof and the common case.
  * `RELOCATED_ACCESSOR` / `RELOCATED_WITH_THE_BLOCK` — a name that TRAVELLED with an extraction
    rather than being one. It is pinned by the same proof under its own constant, because inline-back
    cannot express "this moved too".
  * A whole-function move out of a module (`Extracted from `service/routers/api_v2.py``) is verified
    by AST and byte identity against `git show HEAD:` at the time of the move, not by inline-back.
    Those say a PATH, not a function name, and are deliberately out of scope here.

THE PATTERN IS NARROW ON PURPOSE. It matches only a backticked bare identifier. The first version
matched any word after "Extracted from" and captured `service` out of every
"Extracted from `service/routers/api_v2.py`" — 46 reported violations, 44 of them that. A gate that
cries wolf on the repo's own conventions gets switched off.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TESTS = REPO / "service" / "tests"

#: A METHOD extraction: "Extracted from `some_function`". Not a path — see the module docstring.
EXTRACTED = re.compile(r"Extracted from\s+`([A-Za-z_][A-Za-z0-9_]*)`", re.I)
#: Constants a proof uses to pin a name that travelled WITH an extraction.
TRAVELLED = ("RELOCATED_ACCESSOR", "RELOCATED_WITH_THE_BLOCK", "RELOCATED")


def _product_sources():
    for base in ("service", "mcp"):
        root = REPO / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            parts = path.parts
            if "__pycache__" in parts or "tests" in parts or "node_modules" in parts:
                continue
            yield path


def proven_names() -> set[str]:
    """Every helper name a test pins — in an EXTRACTIONS list or as a travelled-name constant."""
    out: set[str] = set()
    for path in sorted(TESTS.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - another gate's failure
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if not (targets & {"EXTRACTIONS", *TRAVELLED}):
                continue
            for element in ast.walk(node.value):
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    out.add(element.value)
    return out


def claims() -> list[tuple[str, str, str]]:
    """(file, helper, source function) for every method-extraction claim in the product tree."""
    out = []
    for path in _product_sources():
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        module_doc = ast.get_docstring(tree) or ""
        rel = path.relative_to(REPO).as_posix()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            match = EXTRACTED.search((ast.get_docstring(node) or "") + "\n" + module_doc)
            if match:
                out.append((rel, node.name, match.group(1)))
    return out


class EveryExtractionClaimHasAProofTests(unittest.TestCase):
    def test_every_helper_claiming_an_extraction_is_pinned_by_a_proof(self):
        proven = proven_names()
        unproven = [row for row in claims() if row[1] not in proven]
        self.assertEqual(
            [], unproven,
            "these helpers say they were extracted from a function, and no test inlines them back "
            "into it. Green suites are not that proof — capture the pre-split fixture and add the "
            "helper to an EXTRACTIONS list:\n  "
            + "\n  ".join(f"{f}: {h}() <- {src}()" for f, h, src in unproven),
        )

    def test_the_scan_finds_the_claims_and_the_proofs(self):
        """Anti-vacuity in both halves: a broken pattern would report no claims and pass forever."""
        self.assertGreater(len(claims()), 15, "expected many extraction claims in the tree")
        self.assertGreater(len(proven_names()), 40, "expected many pinned helper names")

    def test_a_path_style_claim_is_NOT_treated_as_a_method_extraction(self):
        """`Extracted from \\`service/routers/api_v2.py\\`` is a whole-function move, proved elsewhere.

        Matching those produced 46 violations where there were 2. The distinction is load-bearing:
        a module move is verified by AST+byte identity at the time, and demanding an inline-back
        proof for it would be asking for a round trip that cannot be written.
        """
        self.assertIsNone(EXTRACTED.search("Extracted from `service/routers/api_v2.py` in v0.5."))
        self.assertEqual(
            "get_analytics_pulse",
            EXTRACTED.search("Extracted from `get_analytics_pulse` in v0.5.4.").group(1),
        )

    def test_a_travelled_name_counts_as_pinned(self):
        """Two real helpers are pinned this way, and neither is an extraction in its own right.

        `_terminal_end_statuses_ordered` and `_apply_pending_resident_takeover_if_ready` moved WITH
        the blocks that use them. Inline-back cannot express "this came too", so their proofs name
        them under their own constants — which is a real pin, not an exemption.
        """
        proven = proven_names()
        for name in ("_terminal_end_statuses_ordered", "_apply_pending_resident_takeover_if_ready"):
            self.assertIn(name, proven)


if __name__ == "__main__":
    unittest.main()
