"""Tests for the extract-method verifier — including that it REJECTS.

A verifier is worth exactly nothing until it has been shown to fail on bad input. `extract_method`
is about to be the sole evidence that splitting a 684-line `register_agent` did not change
behaviour, so every rejection path here is a load-bearing test, not a formality.

The most important case in this file is `test_refuses_a_block_containing_return`: that is the input
the round-trip proof CANNOT judge, and the gate must refuse it rather than pass it.
"""

from __future__ import annotations

import unittest

from service.tests.extract_method import assert_extraction_preserves_behaviour, escapes

ORIGINAL = '''
def handler(payload, db):
    name = payload["name"]
    rows = db.query(name)
    total = 0
    for row in rows:
        total += row.weight
    label = f"{name}:{total}"
    return label
'''

GOOD_SPLIT = '''
def handler(payload, db):
    name = payload["name"]
    rows = db.query(name)
    total = _sum_weights(rows)
    label = f"{name}:{total}"
    return label


def _sum_weights(rows):
    """Extracted."""
    total = 0
    for row in rows:
        total += row.weight
    return total
'''


class ExtractMethodGateTests(unittest.TestCase):
    def test_refuses_a_split_that_strands_a_caller_local(self):
        """THE DEFECT THE REVIEWER FOUND IN THIS FILE, and the most important test here.

        This exact split was previously asserted to PASS. It is broken: `_accumulate` binds `total`,
        and the caller reads `total` on the next line -- which after the split is a helper local, so
        the caller raises NameError. Inline-back closes on it happily, because the round trip
        reconstructs the ORIGINAL (correct by definition) rather than exercising the SPLIT.

        A structural proof cannot see this class at all, which is why live-outs are computed
        directly instead of being inferred from the round trip.
        """
        original = '''
def handler(payload, db):
    name = payload["name"]
    rows = db.query(name)
    total = 0
    for row in rows:
        total += row.weight
    label = f"{name}:{total}"
    return label
'''
        split = '''
def handler(payload, db):
    name = payload["name"]
    rows = db.query(name)
    _accumulate(rows)
    label = f"{name}:{total}"
    return label


def _accumulate(rows):
    total = 0
    for row in rows:
        total += row.weight
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_accumulate")
        self.assertIn("REFUSED", str(caught.exception))
        self.assertIn("hand it back", str(caught.exception))

    def test_a_void_extraction_with_no_live_outs_passes(self):
        """The honest VOID shape: the block binds nothing the caller goes on to read."""
        original = '''
def handler(db):
    db.begin()
    db.write_one()
    db.write_two()
    db.commit()
'''
        split = '''
def handler(db):
    db.begin()
    _writes(db)
    db.commit()


def _writes(db):
    db.write_one()
    db.write_two()
'''
        assert_extraction_preserves_behaviour(original, split, "_writes")

    def test_the_value_returning_shape_passes(self):
        """The COMMON shape: helper ends `return total`, caller does `total = _sum_weights(rows)`.

        Refusing this would refuse nearly every real extraction, so inline-back models it exactly —
        the trailing return is rewritten back into the caller's assignment before comparing.
        """
        assert_extraction_preserves_behaviour(ORIGINAL, GOOD_SPLIT, "_sum_weights")

    def test_refuses_a_MID_BLOCK_return(self):
        """The blind spot, refused rather than passed.

        Inline-back reproduces the original perfectly here, so the round trip would PASS — but the
        early `return` now exits the helper instead of the handler, so the handler continues where
        it previously stopped. That is a real behaviour change this proof cannot see, which is
        precisely why it must be refused instead of blessed.
        """
        original = '''
def handler(payload, db):
    name = payload["name"]
    if not name:
        return None
    rows = db.query(name)
    return rows
'''
        split = '''
def handler(payload, db):
    name = payload["name"]
    _guard(name)
    rows = db.query(name)
    return rows


def _guard(name):
    if not name:
        return None
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_guard")
        self.assertIn("REFUSED", str(caught.exception))
        self.assertIn("not a single trailing", str(caught.exception))

    def test_a_dropped_statement_fails(self):
        """No live-outs here, so the round trip is what must catch it."""
        original = '''
def handler(db):
    db.begin()
    db.write_one()
    db.write_two()
    db.commit()
'''
        split = '''
def handler(db):
    db.begin()
    _writes(db)
    db.commit()


def _writes(db):
    db.write_one()
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_writes")
        self.assertIn("did not reproduce the original", str(caught.exception))

    def test_a_reordered_statement_fails(self):
        original = '''
def handler(db):
    a = db.one()
    b = db.two()
    c = a + b
    print(c)
'''
        split = '''
def handler(db):
    a = db.one()
    _work(db, a)
    print(c)


def _work(db, a):
    c = a + b
    b = db.two()
'''
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(original, split, "_work")

    def test_a_renamed_variable_fails(self):
        original = '''
def handler(db):
    total = db.one()
    print(total)
'''
        split = '''
def handler(db):
    _work(db)
    print(total)


def _work(db):
    amount = db.one()
'''
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(original, split, "_work")

    def test_a_return_inside_a_nested_def_is_not_an_escape(self):
        """Walking blindly would reject safe extractions."""
        import ast

        block = ast.parse('''
def outer():
    def inner():
        return 1
    values = [inner()]
''').body[0].body
        self.assertEqual(escapes(block), [])

    def test_break_and_yield_are_escapes(self):
        import ast

        self.assertIn("Break", escapes(ast.parse("for i in x:\n    break\n").body))
        self.assertIn("Yield", escapes(ast.parse("y = (yield 1)\n").body))

    def test_two_call_sites_are_rejected_as_undefined(self):
        original = '''
def handler():
    a = 1
    b = 2
'''
        split = '''
def handler():
    _w()
    _w()


def _w():
    a = 1
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("exactly one call", str(caught.exception))




class ExtractMethodPreconditionTests(unittest.TestCase):
    """The reject classes the reviewer listed. Each one PASSES inline-back while changing
    behaviour, which is exactly why refusing them up front is the only honest option."""

    def _refuse(self, original, split, helper, needle):
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, helper)
        self.assertIn("REFUSED", str(caught.exception))
        self.assertIn(needle, str(caught.exception))

    def test_refuses_global(self):
        self._refuse(
            "def f():\n    global g\n    g = 1\n",
            "def f():\n    _w()\n\n\ndef _w():\n    global g\n    g = 1\n",
            "_w", "global")

    def test_refuses_nonlocal(self):
        self._refuse(
            "def f():\n    x = 1\n    nonlocal_marker = x\n",
            "def f():\n    x = 1\n    _w()\n\n\ndef _w():\n    nonlocal x\n    x = 2\n",
            "_w", "nonlocal")

    def test_refuses_del(self):
        self._refuse(
            "def f():\n    x = 1\n    del x\n",
            "def f():\n    x = 1\n    _w(x)\n\n\ndef _w(x):\n    del x\n",
            "_w", "del")

    def test_refuses_a_nested_closure(self):
        self._refuse(
            "def f():\n    a = 1\n    cb = lambda: a\n",
            "def f():\n    a = 1\n    cb = _w(a)\n\n\ndef _w(a):\n    cb = lambda: a\n    return cb\n",
            "_w", "capture")

    def test_refuses_frame_introspection(self):
        self._refuse(
            "def f():\n    snapshot = locals()\n",
            "def f():\n    snapshot = _w()\n\n\ndef _w():\n    snapshot = locals()\n    return snapshot\n",
            "_w", "frame-sensitive")

    def test_refuses_await_extracted_out_of_a_sync_function(self):
        self._refuse(
            "def f(db):\n    rows = 1\n",
            "def f(db):\n    rows = _w(db)\n\n\nasync def _w(db):\n    rows = await db.q()\n    return rows\n",
            "_w", "is sync")

    def test_refuses_await_in_a_non_async_helper(self):
        self._refuse(
            "async def f(db):\n    rows = await db.q()\n",
            "async def f(db):\n    rows = _w(db)\n\n\ndef _w(db):\n    rows = await db.q()\n    return rows\n",
            "_w", "not `async def`")

    def test_allows_a_clean_async_extraction(self):
        assert_extraction_preserves_behaviour(
            "async def f(db):\n    rows = await db.q()\n    return rows\n",
            "async def f(db):\n    rows = await _w(db)\n    return rows\n\n\n"
            "async def _w(db):\n    rows = await db.q()\n    return rows\n",
            "_w")


if __name__ == "__main__":
    unittest.main()
