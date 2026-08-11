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
    def test_a_clean_extraction_passes(self):
        # The helper's trailing `return total` IS an escape by the letter of the rule, so the
        # honest shape of a value-returning extraction is checked separately below. Here the block
        # is lifted verbatim with the assignment left in the caller.
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
        assert_extraction_preserves_behaviour(original, split, "_accumulate")

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
        split = '''
def handler(payload, db):
    name = payload["name"]
    rows = db.query(name)
    _accumulate(rows)
    label = f"{name}:{total}"
    return label


def _accumulate(rows):
    total = 0
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(ORIGINAL.replace("    return label", "    return label"), split, "_accumulate")
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


if __name__ == "__main__":
    unittest.main()
