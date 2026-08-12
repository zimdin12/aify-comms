"""Tests for the accessor rewriter — above all, that it does not corrupt the accessors.

The two real defects this tool exists to prevent are both reproduced here as tests, because both
shipped: an accessor rewritten to return itself (RecursionError at runtime) and an accessor's own
import line rewritten (SyntaxError at import). Each is now a red test if the protection breaks.
"""

from __future__ import annotations

import ast
import unittest

from service.tests.accessor_rewrite import accessor_name, build_accessor, rewrite

ACCESSOR = build_accessor("_LISTEN_EVENTS")


class AccessorRewriteTests(unittest.TestCase):
    def test_it_rewrites_uses_outside_the_accessor(self):
        source = "def handler():\n    return _LISTEN_EVENTS.get('x')\n" + ACCESSOR
        out = rewrite(source, ["_LISTEN_EVENTS"])
        self.assertIn("_borrowed_listen_events().get('x')", out)

    def test_it_does_NOT_rewrite_the_accessor_return(self):
        """Defect one, shipped: the accessor returned a call to itself. RecursionError per call.

        Asserted against the ACCESSOR's own body, not the whole file. A blanket
        `assertNotIn("return _borrowed_listen_events()")` also matches the legitimate rewrite in
        `handler` -- which is the correct behaviour -- so the first version of this test failed on
        the tool working properly.
        """
        out = rewrite("def handler():\n    return _LISTEN_EVENTS\n" + ACCESSOR, ["_LISTEN_EVENTS"])
        accessor_body = out.split("def _borrowed_listen_events")[1]
        self.assertIn("    return _LISTEN_EVENTS\n", accessor_body)
        self.assertNotIn("return _borrowed_listen_events()", accessor_body)
        # and the caller SHOULD have been rewritten
        self.assertIn("return _borrowed_listen_events()", out.split("def _borrowed_listen_events")[0])

    def test_it_does_NOT_rewrite_the_accessor_import(self):
        """Defect two, shipped: `from ... import _borrowed_x()` — a SyntaxError."""
        out = rewrite("def handler():\n    return _LISTEN_EVENTS\n" + ACCESSOR, ["_LISTEN_EVENTS"])
        self.assertIn("from service.routers.api_v2 import _LISTEN_EVENTS", out)
        self.assertNotIn("import _borrowed_listen_events()", out)

    def test_the_result_always_parses(self):
        """Both shipped defects were detectable this cheaply."""
        out = rewrite("def handler():\n    return _LISTEN_EVENTS\n" + ACCESSOR, ["_LISTEN_EVENTS"])
        ast.parse(out)

    def test_no_accessor_ends_up_self_recursive(self):
        source = ("def a():\n    return _LISTEN_EVENTS\n"
                  + ACCESSOR + build_accessor("_OTHER_SET"))
        out = rewrite(source, ["_LISTEN_EVENTS", "_OTHER_SET"])
        tree = ast.parse(out)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_borrowed_"):
                with self.subTest(node.name):
                    self.assertFalse(
                        any(isinstance(s, ast.Call) and isinstance(s.func, ast.Name)
                            and s.func.id == node.name for s in ast.walk(node)),
                        f"{node.name} calls itself",
                    )

    def test_it_leaves_everything_else_byte_identical(self):
        """Line-based, not ast.unparse: this series proves every slice on byte identity."""
        source = ("# a comment with _LISTEN_EVENTS mentioned\n"
                  "def handler():\n"
                  "    x = 1  # trailing\n"
                  "    return _LISTEN_EVENTS\n"
                  "\n\n"
                  "def untouched():\n"
                  '    return "café — ünïcode"\n' + ACCESSOR)
        out = rewrite(source, ["_LISTEN_EVENTS"])
        self.assertIn("    x = 1  # trailing\n", out)
        self.assertIn('    return "café — ünïcode"\n', out)
        self.assertIn("\n\n\ndef untouched():", "\n" + out)

    def test_a_substring_name_is_not_rewritten(self):
        """`_LISTEN_EVENTS_EXTRA` must not become `_borrowed_listen_events()_EXTRA`."""
        out = rewrite("def h():\n    return _LISTEN_EVENTS_EXTRA\n" + ACCESSOR, ["_LISTEN_EVENTS"])
        self.assertIn("return _LISTEN_EVENTS_EXTRA", out)

    def test_no_constants_is_a_no_op(self):
        source = "def h():\n    return 1\n"
        self.assertEqual(rewrite(source, []), source)

    def test_accessor_name_matches_what_build_accessor_emits(self):
        """A mismatch here would generate an accessor nothing calls — silently."""
        self.assertIn(f"def {accessor_name('_FOO_BAR')}()", build_accessor("_FOO_BAR"))


if __name__ == "__main__":
    unittest.main()
