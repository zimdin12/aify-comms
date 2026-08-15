"""The `ensure_virtual_terminal` split, re-proved against the real code on every run.

Same shape as the other `*_split_is_inert` proofs here: proving the split once at refactor time proves
the commit, running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: the 62-line branch that re-points an existing virtual RPC terminal at the session
now asking for it. It is an EARLY EXIT — it ends in the response the handler returns — which
`service/tests/extract_method.py` refused outright until the call-site-shape rule landed in v0.5.4.
That is why this block sat in a 238-line handler with no way to prove moving it was inert, and why
this proof did not exist before.

THE INTERESTING PART IS WHERE IT LANDED. `api_core/console_terminal_rows.py` already holds
`_reuse_virtual_rpc_console_terminal`, which is the INVERSE operation: that one points a SESSION at
an existing terminal, this one points a TERMINAL at a new session. Same pair of rows, opposite
direction, different entry points. They are adjacent and NOT merged — collapsing them means deciding
which row is authoritative, which is a behaviour question and not refactor work.

WHAT THIS DOES NOT DO: it proves the extraction is inert. It says nothing about whether the handler
is correct — the console tests own that.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
# `ensure_virtual_terminal` moved to `agents/virtual_terminal.py` in v0.5.4 — provisioning a
# terminal is not using one, and `console.py` kept the read/input verbs. A round-trip proof
# names the module holding the CALLER, so a relocation must touch it — see the pin below.
CALLER = REPO / "service" / "routers" / "agents" / "virtual_terminal.py"
ROWS = REPO / "service" / "api_core" / "console_terminal_rows.py"

#: ONE tuple, read by every check below. Naming modules inline per check has gone blind five times in
#: this directory: a helper landing somewhere an inline list does not mention makes the round trip
#: inline NOTHING while the test keeps passing.
MODULES = (CALLER, ROWS)
FIXTURE = Path(__file__).resolve().parent / "data" / "ensure_virtual_terminal_before_split.py"

SOURCE_FUNCTION = "ensure_virtual_terminal"
EXTRACTIONS = ["_reanchor_existing_virtual_terminal"]

#: PER HELPER, not as a set — the set form asserted the owner list was exactly one module, which stops
#: meaning anything the moment a second extraction lands elsewhere.
OWNERS = {"_reanchor_existing_virtual_terminal": ROWS}


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class EnsureVirtualTerminalSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS)

    def test_the_source_function_is_still_where_this_proof_looks(self):
        """`CALLER` is a location pin, and a relocation is what breaks it.

        Added when `ensure_virtual_terminal` moved out of `console.py` in v0.5.4. The round trip
        already fails then — it cannot find the caller to inline into — but it fails as a
        gate-internal error about a missing definition, alongside two unrelated-looking failures in
        this file. This says the true thing in one line instead.
        """
        declared = {
            n.name for n in ast.parse(CALLER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(
            SOURCE_FUNCTION, declared,
            f"{SOURCE_FUNCTION} is not declared in {CALLER.name}. If it was relocated, repoint "
            "CALLER at its new module — this proof names the file holding the caller, so a move "
            "must touch it.",
        )

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash.

        That produced a round-trip failure pointing at an untouched block once already. The count is
        asked of the LIVE source rather than hardcoded — a sibling proof copied a `> 5` threshold from
        a neighbour and failed on capture because its function simply had fewer em dashes.
        """
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        live = CALLER.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        declared = {
            n.name for n in ast.parse(CALLER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(helper, declared, f"{helper} is back in virtual_terminal.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [
                path for path in MODULES
                if any(
                    isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == helper
                    for n in ast.parse(path.read_text(encoding="utf-8")).body
                )
            ]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(ROWS.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"console_terminal_rows.py imports upward from {node.module}",
                )

    def test_the_call_site_passes_the_two_names_that_are_ALSO_rebound(self):
        """`existing` and `session_row` are parameters AND assigned inside the helper.

        On the path where the terminal is already anchored to this session neither is re-read, so both
        must arrive from the caller. A free-name scan misses this — the names are written somewhere in
        the block, so they read as local — and it was the gate's live-in check that caught it. Pinned
        because dropping either parameter still parses, still passes the round trip, and raises
        UnboundLocalError only on the branch that skips the rebinding.
        """
        split = ast.parse(CALLER.read_text(encoding="utf-8"))
        call = next(
            node for node in ast.walk(split)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_reanchor_existing_virtual_terminal"
        )
        passed = [a.id for a in call.args if isinstance(a, ast.Name)]
        for name in ("existing", "session_row"):
            self.assertIn(name, passed, f"{name} must be passed in, not left to the helper to bind")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
