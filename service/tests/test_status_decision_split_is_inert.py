"""Structural invariants for the status-decision split. THE INLINE-BACK PROOF HAS BEEN RETIRED.

WHAT THIS FILE USED TO DO. Between 87e953fa and 537a4bb7 it proved, on every run, that
`_decide_effective_status` was a byte-identical lift out of `_compute_live_status_cache`: inline the
helper back over its call and the result reproduced the pristine 551-line original exactly.

WHY IT NO LONGER CAN. The facts-object reshape replaced twenty positional parameters with a frozen
`StatusFacts`, so the body now reads `facts.turn_busy` where it read `turn_busy`. That is not a
byte-identical move and inline-back CANNOT prove it — the extract-method gate refuses it outright, with
the correct reason: the call passes a constructed object rather than a same-name variable, and
inline-back splices bodies without substituting arguments, so it cannot see a value swap. The gate is
right to refuse; the proof was never applicable to a reshape.

THE PROOF IS NOT REPLACED BY NOTHING. What guards the reshape is
`service/tests/test_status_decision_branches.py` — 36 direct branch tests over twelve assignment sites,
including precedence ordering and the hot-path query boundary, every one of them mutation-verified —
plus the seven status-matrix suites. That net was written FIRST, before the reshape, precisely so this
retirement would not leave a gap. The reviewer's sequence required it in that order for this reason.

WHAT REMAINS HERE are the structural invariants, which survive the reshape and are still worth
enforcing: the helper is not back in the carrier, exactly one module declares it, the leaf imports
nothing upward, and the caller unpacks in the order the helper returns. The pristine fixture is kept as
the record of what the function looked like before any of this, and is still asserted to be tracked and
un-mangled.

IF A FUTURE CHANGE MAKES THE DECISION BYTE-IDENTICALLY EXTRACTABLE AGAIN, restoring the round trip is a
one-line change and the fixture is still here to compare against.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent
#: WHERE `_compute_live_status_cache` LIVES, which is no longer the control plane. It moved to
#: `service/api_core/status_inputs.py` in v0.5.4, joining `_gather_status_inputs` — the two status
#: paths, legacy and engine, whose byproduct-parity promise is easier to keep in one module than
#: across two. This constant is still named CARRIER because that is the role it plays here: the file
#: the decision helper was lifted OUT of, whatever that file is called today.
#:
#: This is the cost of a location pin. Nothing about the split changed, but three assertions here
#: failed the moment the code moved, because they assert where code LIVES rather than what it does.
#: They are kept because "the helper has not crept back into its caller" is genuinely structural and
#: has no behavioural equivalent — but the pin has to be re-aimed by hand on every move, and a reader
#: who sees it red will reach for the wrong explanation first.
CARRIER = REPO / "service" / "api_core" / "status_inputs.py"
DECISION = REPO / "service" / "api_core" / "status_decision.py"

#: ONE tuple, read by every check that needs the pair. The alternative — each check naming its own
#: modules — has gone blind five times elsewhere in this directory when a helper landed somewhere the
#: inline list did not mention. Converted here for consistency rather than in response to a failure,
#: since this proof only ever spanned two files.
MODULES = (CARRIER, DECISION)
FIXTURE = Path(__file__).resolve().parent / "data" / "compute_live_status_cache_before_split.py"

SOURCE_FUNCTION = "_compute_live_status_cache"
EXTRACTIONS = ["_decide_effective_status"]


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class StatusDecisionSplitIsInertTests(unittest.TestCase):
    def test_the_inline_back_proof_is_retired_and_says_so(self):
        """A retired proof must be visibly retired, not silently deleted.

        This asserts the CAUSE rather than leaving a gap where a test used to be: the decision now
        takes a constructed `StatusFacts`, which is exactly the shape inline-back cannot verify. If
        someone later restores a same-name parameter passing style, this test starts failing and the
        round trip above it should be reinstated.
        """
        decision_src = DECISION.read_text(encoding="utf-8")
        self.assertIn("facts: StatusFacts", decision_src,
                      "the reshape is what retired the round trip; if it is gone, restore the proof")
        carrier_src = CARRIER.read_text(encoding="utf-8")
        self.assertIn("StatusFacts(", carrier_src, "the caller must construct the facts object")

    def test_the_characterization_net_that_replaced_the_proof_exists(self):
        """The retirement is only defensible while its successor is present and non-trivial."""
        net = REPO / "service" / "tests" / "test_status_decision_branches.py"
        self.assertTrue(net.exists(), "the branch characterization net is missing")
        text = net.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("def test_"), 30,
                                "the net that justifies retiring the round trip has shrunk")
        self.assertIn("_managed_console_is_booting", text, "the hot-path query boundary must stay covered")

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertGreater(text.count("—"), 5, "fixture looks locale-mangled, not utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")

    def test_the_helper_is_not_still_inline(self):
        declared = {
            n.name for n in ast.parse(CARRIER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(helper, declared, f"{helper} is back in the carrier; this proof is vacuous")

    def test_exactly_one_module_declares_the_helper(self):
        owners = [
            path for path in (CARRIER, DECISION)
            if any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in EXTRACTIONS
                for n in ast.parse(path.read_text(encoding="utf-8")).body
            )
        ]
        self.assertEqual([DECISION], owners)

    def test_the_decision_leaf_does_not_import_upward(self):
        for node in ast.walk(ast.parse(DECISION.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"status_decision.py imports upward from {node.module}",
                )

    def test_the_three_outputs_are_returned_in_the_order_the_caller_unpacks(self):
        """The multi-output dialect's one real risk, asserted here as well as in the verifier probes.

        A transposed return has the same names, arity and types and silently swaps values. The
        inline-back round trip used to catch it; that proof is retired (see the module docstring), so
        this assertion is now the ONLY structural guard on the return/unpack ordering and matters more
        than it did when it was written as a convenience restatement.
        """
        decision = next(
            n for n in ast.parse(DECISION.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        returned = decision.body[-1]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual(
            [e.id for e in returned.value.elts],
            ["effective_status", "reason", "awaiting_reply"],
        )
        call = next(
            n for n in ast.walk(ast.parse(CARRIER.read_text(encoding="utf-8")))
            if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Await)
            and getattr(n.value.value.func, "id", "") == EXTRACTIONS[0]
        )
        self.assertEqual(
            [e.id for e in call.targets[0].elts],
            ["effective_status", "reason", "awaiting_reply"],
            "the caller must unpack in the same order the helper returns",
        )

    def test_every_facts_field_is_wired_to_the_identically_named_local(self):
        """THE reshape's own risk, and the reason it needed its own assertion.

        Packing twenty values into `StatusFacts(...)` at the call site introduces a failure the old
        positional call could not have: a field wired to the WRONG local. `session_status=terminal_status`
        is valid Python, same type, no error — and it silently changes what the fleet reports.

        The construction is keyword-form and every value must be a bare Name equal to its keyword. That
        is checkable, so it is checked rather than trusted to review.
        """
        call = next(
            n for n in ast.walk(ast.parse(CARRIER.read_text(encoding="utf-8")))
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "StatusFacts"
        )
        self.assertEqual([], call.args, "facts must be passed by KEYWORD, never positionally")
        mismatched = [
            f"{kw.arg}={ast.unparse(kw.value)}"
            for kw in call.keywords
            if not (isinstance(kw.value, ast.Name) and kw.value.id == kw.arg)
        ]
        self.assertEqual(mismatched, [], "a facts field is wired to a differently-named local")
        self.assertEqual(20, len(call.keywords), "every fact must be supplied explicitly")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
