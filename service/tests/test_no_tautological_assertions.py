"""An assertion comparing a thing to itself passes forever and proves nothing.

WHY THIS EXISTS, and it is not hypothetical. `test_name_validation` asserted the real invariant that
the control plane re-exports the OWNER's object rather than a second copy:

    self.assertIs(api_v2.validate_name, validate_name)

In v0.5.4 I ran a mechanical repoint that rewrote stale carrier consumers — `api_v2.X` to `X` — over
45 sites. It was right about 44 of them and wrong here, because this test's SUBJECT is the carrier's
binding. The rewrite produced:

    self.assertIs(validate_name, validate_name)

which is green forever. The suite stayed at 1441 passing and a forked validator became undetectable.
The reviewer found it by reading; nothing in the suite could.

The lesson is about mechanical edits, not about this test. Any sweep that rewrites references can
collapse a two-sided comparison into a one-sided one, and the result is invisible in a passing run
and easy to miss in a diff. So the shape is checked directly.

WHAT COUNTS: both arguments of an equality/identity assertion having the same AST. That catches
`assertIs(x, x)` and `assertEqual(f(a), f(a))` while ignoring the legitimate cases where two
different expressions happen to be equal — which is what these assertions are for.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: Assertions whose two arguments are compared against each other.
TWO_SIDED = {"assertIs", "assertIsNot", "assertEqual", "assertNotEqual", "assertGreater",
             "assertLess", "assertGreaterEqual", "assertLessEqual"}


def _tautologies():
    for path in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in TWO_SIDED or len(node.args) < 2:
                continue
            if ast.dump(node.args[0]) == ast.dump(node.args[1]):
                yield (path.name, node.lineno, node.func.attr, ast.unparse(node.args[0]))


class NoTautologicalAssertionsTests(unittest.TestCase):
    def test_no_assertion_compares_a_thing_to_itself(self):
        found = [f"{name}:{line}  {call}({expr}, {expr})" for name, line, call, expr in _tautologies()]
        self.assertEqual(
            found,
            [],
            "an assertion compares an expression to itself, so it can never fail:\n  "
            + "\n  ".join(found)
            + "\nIf a mechanical rewrite collapsed two sides into one, restore the other side; if the "
            "comparison was always pointless, delete it.",
        )

    def test_the_sweep_can_actually_see_a_tautology(self):
        """Without this the gate above passes by matching nothing, which is the failure it exists for."""
        tree = ast.parse("self.assertIs(thing, thing)\n")
        call = tree.body[0].value
        self.assertEqual(ast.dump(call.args[0]), ast.dump(call.args[1]),
                         "the AST comparison no longer detects identical arguments")
        self.assertIn(call.func.attr, TWO_SIDED, "assertIs must be in the checked set")


if __name__ == "__main__":
    unittest.main()
