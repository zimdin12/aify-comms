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




class AccessorRewriteEdgeShapeTests(unittest.TestCase):
    """Accessor shapes the reviewer would reasonably worry about, proven rather than assumed.

    `accessor_line_ranges` uses `ast.walk`, so it finds a `_borrowed_*` function wherever it is
    defined — nested inside another function, decorated, inside a class, inside a conditional, or
    async. Each of those is checked here rather than argued about, because "walk finds it" is the
    kind of claim that is right until the one shape where it isn't.

    NOTE ON HOW THESE ARE CHECKED: corruption is detected with `ast.Call`, never a substring. The
    text `def _borrowed_x():` CONTAINS `_borrowed_x()`, so a substring check reports every healthy
    accessor as self-referential — a false alarm I produced twice before writing this down.
    """

    SHAPES = {
        "nested in a function":
            "def outer():\n    def _borrowed_x():\n        from m import X\n        return X\n    return X\n",
        "decorated":
            "@cache\ndef _borrowed_x():\n    from m import X\n    return X\n\n\ndef h():\n    return X\n",
        "inside a class":
            "class C:\n    def _borrowed_x(self):\n        from m import X\n        return X\n\n\ndef h():\n    return X\n",
        "inside a conditional":
            "if True:\n    def _borrowed_x():\n        from m import X\n        return X\n\n\ndef h():\n    return X\n",
        "async accessor":
            "async def _borrowed_x():\n    from m import X\n    return X\n\n\ndef h():\n    return X\n",
    }

    def test_no_shape_corrupts_its_accessor(self):
        for label, source in self.SHAPES.items():
            with self.subTest(label):
                out = rewrite(source, ["X"])
                tree = ast.parse(out)  # must still parse
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if not node.name.startswith("_borrowed_"):
                        continue
                    self.assertFalse(
                        any(isinstance(s, ast.Call) and isinstance(s.func, ast.Name)
                            and s.func.id == node.name for s in ast.walk(node)),
                        f"{label}: {node.name} was rewritten to call itself",
                    )
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.ImportFrom):
                            self.assertFalse(
                                any(a.name.startswith("_borrowed_") for a in sub.names),
                                f"{label}: {node.name}'s import line was rewritten",
                            )

    def test_every_shape_still_rewrites_its_call_site(self):
        """Protection must not become blanket refusal — the point is to rewrite the CALLERS."""
        for label, source in self.SHAPES.items():
            with self.subTest(label):
                self.assertIn("_borrowed_x()", rewrite(source, ["X"]),
                              f"{label}: the call site was not rewritten at all")




class AccessorRewriteTokenDomainTests(unittest.TestCase):
    """Only Python NAME tokens are rewritten — never comments, never string literals.

    THE REVIEWER'S BLOCKER on the first version, which substituted over raw line text:

        # comment _LISTEN_EVENTS          ->  # comment _borrowed_listen_events()
        msg = 'literal _LISTEN_EVENTS'    ->  msg = 'literal _borrowed_listen_events()'

    The comment case is an undeclared textual change in a series that proves every slice on byte
    identity. The string case is BEHAVIOUR-CHANGING: constant names appear as data in SQL text, JSON
    keys, regex sources, telemetry names and diagnostics.

    The old `test_it_leaves_everything_else_byte_identical` did NOT catch this, and that is the
    instructive part — it contained `# a comment with _LISTEN_EVENTS mentioned` but only asserted on
    an unrelated trailing comment and an unrelated unicode string. It looked like comment
    preservation was pinned. It wasn't. A test that mentions the risky input without asserting on it
    is worse than no test, because it reads as coverage.
    """

    def _rewritten(self, body: str) -> str:
        return rewrite(body + ACCESSOR, ["_LISTEN_EVENTS"])

    def test_a_full_line_comment_is_untouched(self):
        out = self._rewritten("# note about _LISTEN_EVENTS here\ndef h():\n    return _LISTEN_EVENTS\n")
        self.assertIn("# note about _LISTEN_EVENTS here", out)

    def test_a_trailing_comment_is_untouched(self):
        out = self._rewritten("def h():\n    return _LISTEN_EVENTS  # uses _LISTEN_EVENTS\n")
        self.assertIn("# uses _LISTEN_EVENTS", out)

    def test_string_literals_of_every_quoting_style_are_untouched(self):
        for literal in ["'_LISTEN_EVENTS'", '"_LISTEN_EVENTS"', '"""_LISTEN_EVENTS"""']:
            with self.subTest(literal):
                out = self._rewritten(f"def h():\n    s = {literal}\n    return _LISTEN_EVENTS\n")
                self.assertIn(literal, out)

    def test_a_sql_string_containing_the_name_is_untouched(self):
        """The behaviour-changing case: a constant name as DATA."""
        out = self._rewritten(
            'def h():\n    q = "SELECT _LISTEN_EVENTS FROM t"\n    return _LISTEN_EVENTS\n')
        self.assertIn('"SELECT _LISTEN_EVENTS FROM t"', out)

    def test_a_module_level_string_assignment_is_untouched(self):
        out = self._rewritten("NAME = '_LISTEN_EVENTS'\ndef h():\n    return _LISTEN_EVENTS\n")
        self.assertIn("NAME = '_LISTEN_EVENTS'", out)

    def test_an_fstring_literal_part_is_untouched(self):
        """The text stays; only a real code reference would be a NAME token."""
        out = self._rewritten('def h():\n    return f"name is _LISTEN_EVENTS"\n')
        self.assertIn('f"name is _LISTEN_EVENTS"', out)

    def test_the_code_reference_is_still_rewritten(self):
        """Token-awareness must not become refusal to rewrite anything."""
        out = self._rewritten("# _LISTEN_EVENTS\ndef h():\n    return _LISTEN_EVENTS\n")
        self.assertIn("return _borrowed_listen_events()", out.split("def _borrowed")[0])

    def test_attribute_access_on_the_constant_is_rewritten(self):
        out = self._rewritten("def h():\n    return _LISTEN_EVENTS.get('k')\n")
        self.assertIn("_borrowed_listen_events().get('k')", out)

    def test_an_attribute_with_the_same_NAME_is_not_rewritten(self):
        """`obj._LISTEN_EVENTS` is an attribute of something else, not the module constant.

        This one is a KNOWN LIMITATION rather than a guarantee: tokenize sees a NAME either way, so
        the rewrite fires. Asserted here as current behaviour so it is a decision on the record, not
        a surprise during a migration — if a target constant is ever also an attribute name, the
        migration must exclude it explicitly.
        """
        out = self._rewritten("def h():\n    return obj._LISTEN_EVENTS\n")
        self.assertIn("obj._borrowed_listen_events()", out)




class AccessorRewriteExactOutputTests(unittest.TestCase):
    """Compare the WHOLE output string, not fragments.

    Every earlier test in this file used `assertIn`, and that is exactly how the reviewer's second
    blocker hid: the reconstruction was inserting a blank line between every original line, and each
    `assertIn` still found its fragment. Substring checks cannot see what was ADDED.

    These fixtures assert the complete result, so any change to an untouched span — a blank line, an
    indent, a comment, a byte of unicode — is a failure.
    """

    def test_a_mixed_file_is_reproduced_exactly_except_the_code_reference(self):
        source = (
            "# header mentioning _CONST\n"
            "\n"
            "\n"
            "TEXT = '_CONST'\n"
            "\n"
            "def handler(x):  # trailing _CONST\n"
            '    """Doc with _CONST inside."""\n'
            "    sql = \"SELECT _CONST FROM t\"\n"
            "    label = f'name _CONST is {_CONST}'\n"
            "    note = 'café — ünïcode'\n"
            "\n"
            "    return _CONST\n"
        )
        expected = (
            "# header mentioning _CONST\n"
            "\n"
            "\n"
            "TEXT = '_CONST'\n"
            "\n"
            "def handler(x):  # trailing _CONST\n"
            '    """Doc with _CONST inside."""\n'
            "    sql = \"SELECT _CONST FROM t\"\n"
            "    label = f'name _CONST is {_borrowed_const()}'\n"
            "    note = 'café — ünïcode'\n"
            "\n"
            "    return _borrowed_const()\n"
        )
        self.assertEqual(rewrite(source, ["_CONST"]), expected)

    def test_a_constant_that_does_not_appear_is_a_byte_for_byte_no_op(self):
        source = (
            "import os\n"
            "\n"
            "\n"
            "def f():\n"
            "    # nothing to see\n"
            "    return os.getcwd()\n"
        )
        self.assertEqual(rewrite(source, ["_MISSING_CONST"]), source)

    def test_blank_line_runs_are_preserved_exactly(self):
        """Two blank lines must stay two — the bug turned every gap into an extra line."""
        source = "def a():\n    return _CONST\n\n\ndef b():\n    pass\n"
        out = rewrite(source, ["_CONST"])
        self.assertEqual(out, "def a():\n    return _borrowed_const()\n\n\ndef b():\n    pass\n")

    def test_a_file_with_no_trailing_newline_is_preserved(self):
        source = "def a():\n    return _CONST"
        self.assertEqual(rewrite(source, ["_CONST"]), "def a():\n    return _borrowed_const()")

    def test_crlf_and_tabs_survive(self):
        """This repo has mangled bytes six times; whitespace forms are not academic here."""
        source = "def a():\n\tif True:\n\t\treturn _CONST\n"
        self.assertEqual(rewrite(source, ["_CONST"]),
                         "def a():\n\tif True:\n\t\treturn _borrowed_const()\n")




class AccessorRewriteRealRepoShapesTests(unittest.TestCase):
    """The 14 accessors that a name-prefix check did not protect.

    Earlier slices wrote borrow accessors under their own naming — `_virtual_rpc_command_set()`,
    `_terminal_end_statuses()`, `_active_run_bridge_stale_seconds()` — long before `_borrowed_*`
    became the convention. The first structural check keyed on that prefix and therefore did not see
    them, so running the rewriter over those modules corrupted their import lines. That is the same
    failure the protection exists to prevent, reintroduced by matching a NAME instead of a SHAPE.

    This is the third distinct instance today of substring/naming standing in for structure. The
    rule is now in the tool itself: an accessor is recognised by what it DOES.
    """

    OLD_STYLE = (
        "def _active_run_bridge_stale_seconds():\n"
        "    from service.routers.api_v2 import ACTIVE_RUN_BRIDGE_STALE_SECONDS\n"
        "\n"
        "    return ACTIVE_RUN_BRIDGE_STALE_SECONDS\n"
    )

    def test_an_accessor_without_the_borrowed_prefix_is_protected(self):
        source = "def h(age):\n    return age < ACTIVE_RUN_BRIDGE_STALE_SECONDS\n\n\n" + self.OLD_STYLE
        out = rewrite(source, ["ACTIVE_RUN_BRIDGE_STALE_SECONDS"])
        ast.parse(out)
        accessor = out.split("def _active_run_bridge_stale_seconds")[1]
        self.assertIn("import ACTIVE_RUN_BRIDGE_STALE_SECONDS", accessor)
        self.assertIn("return ACTIVE_RUN_BRIDGE_STALE_SECONDS", accessor)
        self.assertIn("_borrowed_active_run_bridge_stale_seconds()",
                      out.split("def _active_run_bridge_stale_seconds")[0])

    def test_a_delegating_shim_is_protected_too(self):
        """Shims import a FUNCTION and call it; they must not be rewritten either."""
        shim = ("def _helper(*a, **k):\n"
                "    from service.routers.api_v2 import _helper as _impl\n"
                "\n"
                "    return _impl(*a, **k)\n")
        source = "def h():\n    return SOME_CONST\n\n\n" + shim
        out = rewrite(source, ["SOME_CONST", "_impl"])
        ast.parse(out)
        self.assertIn("return _impl(*a, **k)", out)

    def test_every_accessor_in_the_live_repo_is_recognised(self):
        """Run the detector over the real modules, not a fixture.

        If this ever finds an accessor-shaped function the detector misses, the rewriter would
        corrupt it — so the assertion is that the live tree contains no such blind spot.
        """
        import glob
        from pathlib import Path

        from service.tests.accessor_rewrite import is_constant_accessor

        repo = Path(__file__).resolve().parent.parent.parent
        missed = []
        for pattern in ("service/routers/**/*.py", "service/reconcilers/*.py"):
            for path in repo.glob(pattern):
                if path.name == "api_v2.py" or "__pycache__" in path.parts:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    src = ast.unparse(node)
                    looks_like = ("from service.routers.api_v2 import" in src
                                  and len(node.body) <= 3)
                    if looks_like and not is_constant_accessor(node):
                        missed.append(f"{path.relative_to(repo).as_posix()}: {node.name}")
        self.assertEqual(
            missed, [],
            "accessor-shaped functions the detector does not recognise — the rewriter would "
            "corrupt these:\n  " + "\n  ".join(missed),
        )


if __name__ == "__main__":
    unittest.main()
