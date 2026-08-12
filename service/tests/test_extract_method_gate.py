"""Tests for the extract-method verifier — including that it REJECTS.

A verifier is worth exactly nothing until it has been shown to fail on bad input. `extract_method`
is about to be the sole evidence that splitting a 684-line `register_agent` did not change
behaviour, so every rejection path here is a load-bearing test, not a formality.

The most important case in this file is `test_refuses_a_block_containing_return`: that is the input
the round-trip proof CANNOT judge, and the gate must refuse it rather than pass it.
"""

from __future__ import annotations

import unittest

from service.tests.extract_method import (
    assert_extraction_preserves_behaviour,
    assert_extractions_preserve_behaviour,
    escapes,
)

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




class ExtractMethodRegionTests(unittest.TestCase):
    """with/try boundary rules — which turned out to need NO new machinery.

    The reviewer listed these as blocking. Probing them first, rather than building rules on
    assumption, showed inline-back already decides every one of them correctly: hoisting a call OUT
    of a `with` or `try` region changes the reconstructed tree, so the round trip fails on exactly
    the dangerous cases and passes on the safe ones.

    What the probe DID find was a false rejection: a call nested inside a `with` was resolved to the
    enclosing `with` statement, so the inliner replaced the whole block. That made the commonest safe
    shape look like a behaviour change. `_find_call_site` is depth-aware now, and
    `test_call_that_stays_inside_a_with_is_allowed` is the regression pin for it.
    """

    WITH_ORIGINAL = '''
def f(conn):
    with conn:
        a()
        b()
'''

    def test_call_hoisted_OUT_of_a_with_is_refused(self):
        """The context manager is no longer active across the work. Real behaviour change."""
        split = '''
def f(conn):
    _w()
    with conn:
        pass


def _w():
    a()
    b()
'''
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(self.WITH_ORIGINAL, split, "_w")

    def test_call_that_stays_inside_a_with_is_allowed(self):
        """The common, safe shape — and the one a depth-blind inliner wrongly rejected."""
        split = '''
def f(conn):
    with conn:
        _w()


def _w():
    a()
    b()
'''
        assert_extraction_preserves_behaviour(self.WITH_ORIGINAL, split, "_w")

    def test_moving_the_whole_with_statement_is_allowed(self):
        original = '''
def f(conn):
    before()
    with conn:
        a()
'''
        split = '''
def f(conn):
    before()
    _w(conn)


def _w(conn):
    with conn:
        a()
'''
        assert_extraction_preserves_behaviour(original, split, "_w")

    def test_call_hoisted_OUT_of_a_try_is_refused(self):
        """Exceptions would no longer reach the handler that used to catch them."""
        original = '''
def f():
    try:
        risky()
    except ValueError:
        handle()
'''
        split = '''
def f():
    _w()
    try:
        pass
    except ValueError:
        handle()


def _w():
    risky()
'''
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(original, split, "_w")

    def test_call_that_stays_inside_the_try_region_is_allowed(self):
        original = '''
def f():
    try:
        risky()
        more()
    except ValueError:
        handle()
'''
        split = '''
def f():
    try:
        _w()
    except ValueError:
        handle()


def _w():
    risky()
    more()
'''
        assert_extraction_preserves_behaviour(original, split, "_w")

    def test_live_outs_are_checked_in_the_nested_block_not_the_function_top_level(self):
        """A stranded local inside a `with` must still be caught."""
        original = '''
def f(conn):
    with conn:
        total = compute()
        use(total)
'''
        split = '''
def f(conn):
    with conn:
        _w()
        use(total)


def _w():
    total = compute()
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("hand it back", str(caught.exception))




class ExtractMethodLiveOutOrderingTests(unittest.TestCase):
    """Liveness is positional. The reviewer found the set-based version hid real violations."""

    def test_a_later_rebind_does_not_excuse_an_earlier_read(self):
        """The reviewer's exact counterexample.

        The set-based check subtracted every name assigned anywhere after the call, so the trailing
        `total = 0` made it ignore the broken `use(total)` above it.
        """
        original = '''
def f():
    total = compute()
    use(total)
    total = 0
'''
        split = '''
def f():
    _w()
    use(total)
    total = 0


def _w():
    total = compute()
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("hand it back", str(caught.exception))

    def test_a_rebind_BEFORE_any_read_is_genuinely_safe(self):
        """The other direction must still pass, or the rule is just a blanket refusal."""
        original = '''
def f():
    total = compute()
    total = 0
    use(total)
'''
        split = '''
def f():
    _w()
    total = 0
    use(total)


def _w():
    total = compute()
'''
        assert_extraction_preserves_behaviour(original, split, "_w")

    def test_a_store_inside_a_nested_def_does_not_count_as_a_rebind(self):
        """A nested def binds its OWN scope; it cannot rebind the caller's local."""
        original = '''
def f():
    total = compute()

    def inner():
        total = 99
        return total
    use(total)
'''
        split = '''
def f():
    _w()

    def inner():
        total = 99
        return total
    use(total)


def _w():
    total = compute()
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("hand it back", str(caught.exception))




class ExtractMethodAugAssignTests(unittest.TestCase):
    """`+=` reads before it writes, but the AST marks the target Store-only.

    The reviewer found this by RUNNING the gate against the shape, not by reading it, and reported
    `augassign PASSED_UNSAFELY`. It is the same live-out class as the first two, wearing Python's
    compound-assignment form.
    """

    def test_augassign_after_the_call_is_a_read(self):
        original = '''
def f():
    total = compute()
    total += 1
'''
        split = '''
def f():
    _w()
    total += 1


def _w():
    total = compute()
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("hand it back", str(caught.exception))

    def test_augassign_through_a_subscript_reads_the_base(self):
        original = '''
def f(i):
    totals = compute()
    totals[i] += 1
'''
        split = '''
def f(i):
    _w()
    totals[i] += 1


def _w():
    totals = compute()
'''
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(original, split, "_w")

    def test_augassign_through_an_attribute_reads_the_base(self):
        original = '''
def f():
    acc = compute()
    acc.total += 1
'''
        split = '''
def f():
    _w()
    acc.total += 1


def _w():
    acc = compute()
'''
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(original, split, "_w")

    def test_augassign_on_an_unrelated_name_is_still_fine(self):
        """The rule must not become a blanket refusal of any `+=` after a call."""
        original = '''
def f(counter):
    scratch = compute()
    use(scratch)
    counter += 1
'''
        split = '''
def f(counter):
    scratch = _w()
    use(scratch)
    counter += 1


def _w():
    scratch = compute()
    return scratch
'''
        assert_extraction_preserves_behaviour(original, split, "_w")




class ExtractMethodLiveInTests(unittest.TestCase):
    """The dual of live-outs: caller locals the helper READS but was never handed.

    The reviewer found this by running it, reporting `missing_live_in PASSED_UNSAFELY`. Inline-back
    closes perfectly — splicing the body back reproduces the original, where the name IS in scope —
    while the split raises NameError. Fourth and final case of the proof examining the reconstructed
    original instead of the split.
    """

    def test_a_caller_local_the_helper_reads_but_was_not_passed_is_refused(self):
        original = '''
def f():
    x = compute()
    y = x + 1
    return y
'''
        split = '''
def f():
    x = compute()
    y = _w()
    return y


def _w():
    y = x + 1
    return y
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("never passed", str(caught.exception))

    def test_the_same_extraction_passes_once_the_value_is_handed_over(self):
        original = '''
def f():
    x = compute()
    y = x + 1
    return y
'''
        split = '''
def f():
    x = compute()
    y = _w(x)
    return y


def _w(x):
    y = x + 1
    return y
'''
        assert_extraction_preserves_behaviour(original, split, "_w")

    def test_module_level_names_are_not_live_ins(self):
        """Globals, imports and sibling defs are in scope wherever the helper lands."""
        original = '''
def f():
    y = HELPER_CONST + 1
    return y
'''
        split = '''
HELPER_CONST = 5


def f():
    y = _w()
    return y


def _w():
    y = HELPER_CONST + 1
    return y
'''
        assert_extraction_preserves_behaviour(original, split, "_w")

    def test_builtins_are_not_live_ins(self):
        original = '''
def f(items):
    n = len(items)
    return n
'''
        split = '''
def f(items):
    n = _w(items)
    return n


def _w(items):
    n = len(items)
    return n
'''
        assert_extraction_preserves_behaviour(original, split, "_w")

    def test_a_name_the_helper_binds_itself_first_is_not_a_live_in(self):
        """Order matters here too: bound before read inside the helper means it needs nothing."""
        original = '''
def f():
    t = 0
    t = t + 1
    return t
'''
        split = '''
def f():
    t = _w()
    return t


def _w():
    t = 0
    t = t + 1
    return t
'''
        assert_extraction_preserves_behaviour(original, split, "_w")

    def test_an_augmented_read_of_an_unpassed_local_is_also_a_live_in(self):
        """`+=` reads, so it needs the value handed over just like a plain load."""
        original = '''
def f():
    total = 0
    total += 1
    return total
'''
        split = '''
def f():
    total = 0
    _w()
    return total


def _w():
    total += 1
'''
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(original, split, "_w")




class ExtractMethodCallSignatureTests(unittest.TestCase):
    """Does the CALL actually supply what the helper requires?

    The fifth false PASS. Knowing a name is a PARAMETER told the live-in check the helper had it,
    while saying nothing about whether the call hands it over. Both shapes below raise TypeError
    before the helper body ever runs, and inline-back cannot see that because splicing the body
    ignores the calling convention entirely.
    """

    ORIGINAL = '''
def f():
    x = compute()
    y = x + 1
    return y
'''

    def _split(self, call, params="x"):
        return f'''
def f():
    x = compute()
    y = {call}
    return y


def _w({params}):
    y = x + 1
    return y
'''

    def test_missing_required_parameter_is_refused(self):
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w()"), "_w")
        self.assertIn("never supplied", str(caught.exception))

    def test_a_wrong_keyword_name_is_refused(self):
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(z=x)"), "_w")
        self.assertIn("not a parameter", str(caught.exception))

    def test_an_extra_keyword_is_refused(self):
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(x, extra=1)"), "_w")
        self.assertIn("not a parameter", str(caught.exception))

    def test_a_correct_positional_argument_passes(self):
        assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(x)"), "_w")

    def test_a_correct_keyword_argument_passes(self):
        assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(x=x)"), "_w")

    def test_a_zero_parameter_helper_still_passes(self):
        """The rule must not become a blanket refusal of helpers that need nothing."""
        assert_extraction_preserves_behaviour(
            'def f():\n    t = 0\n    return t\n',
            'def f():\n    t = _w()\n    return t\n\n\ndef _w():\n    t = 0\n    return t\n',
            "_w")

    def test_the_same_name_supplied_twice_is_refused(self):
        original = '''
def f():
    x = compute()
    y = x + 1
    return y
'''
        split = '''
def f():
    x = compute()
    y = _w(x, x=x)
    return y


def _w(x):
    y = x + 1
    return y
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("both positionally and by keyword", str(caught.exception))

    def test_shapes_outside_the_dialect_are_refused_rather_than_guessed(self):
        """Defaults, *args/**kwargs and kw-only params are all expressible and all add ways to be
        subtly wrong. A mechanically-generated extraction needs none of them, so they are refused."""
        original = '''
def f():
    x = compute()
    y = x + 1
    return y
'''
        for params in ["x=1", "*args", "**kwargs", "*, x"]:
            split = f'''
def f():
    x = compute()
    y = _w(x)
    return y


def _w({params}):
    y = x + 1
    return y
'''
            with self.subTest(params=params):
                with self.assertRaises(AssertionError) as caught:
                    assert_extraction_preserves_behaviour(original, split, "_w")
                self.assertIn("dialect", str(caught.exception))




class ExtractMethodSameNameBindingTests(unittest.TestCase):
    """Supplying the RIGHT parameter with the WRONG caller value.

    The sixth false PASS, and the most dangerous of the six, because it is legal Python: there is no
    TypeError to catch. `_w(y)` where the parameter is `x` type-checks fine, inline-back splices
    `z = x + 1` and reconstructs the original exactly, and the split quietly computes with a
    different value. Silent behaviour drift is the worst thing a structural gate can miss.

    inline_back does NOT substitute arguments -- it splices the body as written -- so the only
    handoff it models correctly is same-name. Everything else is refused rather than guessed at.
    """

    ORIGINAL = '''
def f():
    x = compute_x()
    y = compute_y()
    z = x + 1
    return z
'''

    def _split(self, call):
        return f'''
def f():
    x = compute_x()
    y = compute_y()
    z = {call}
    return z


def _w(x):
    z = x + 1
    return z
'''

    def test_a_wrong_positional_value_is_refused(self):
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(y)"), "_w")
        self.assertIn("same-name", str(caught.exception))

    def test_a_wrong_keyword_value_is_refused(self):
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(x=y)"), "_w")
        self.assertIn("same-name", str(caught.exception))

    def test_an_expression_argument_is_refused(self):
        """`_w(x + 1)` would need real substitution to verify; refused instead of guessed."""
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(x + 1)"), "_w")

    def test_an_attribute_argument_is_refused(self):
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(o.x)"), "_w")

    def test_the_correct_same_name_positional_passes(self):
        assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(x)"), "_w")

    def test_the_correct_same_name_keyword_passes(self):
        assert_extraction_preserves_behaviour(self.ORIGINAL, self._split("_w(x=x)"), "_w")

    def test_a_zero_parameter_helper_is_unaffected(self):
        assert_extraction_preserves_behaviour(
            'def f():\n    t = 0\n    return t\n',
            'def f():\n    t = _w()\n    return t\n\n\ndef _w():\n    t = 0\n    return t\n',
            "_w")




class ExtractMethodAwaitShapeTests(unittest.TestCase):
    """An `async def` helper whose call is not awaited returns a COROUTINE.

    Seventh false PASS. The body never runs at all, and inline-back reconstructs the original
    perfectly because splicing a body says nothing about how the call is invoked. Independent of
    whether the helper body contains `await`, which is why the earlier async precondition missed it.
    """

    ASYNC_ORIGINAL = '''
async def f():
    x = compute()
    y = x + 1
    return y
'''

    def test_an_async_helper_that_is_not_awaited_is_refused(self):
        split = '''
async def f():
    x = compute()
    y = _w(x)
    return y


async def _w(x):
    y = x + 1
    return y
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(self.ASYNC_ORIGINAL, split, "_w")
        self.assertIn("coroutine", str(caught.exception))

    def test_an_async_helper_in_a_sync_caller_is_refused(self):
        original = '''
def f():
    x = compute()
    y = x + 1
    return y
'''
        split = '''
def f():
    x = compute()
    y = _w(x)
    return y


async def _w(x):
    y = x + 1
    return y
'''
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(original, split, "_w")

    def test_an_async_helper_awaited_in_an_async_caller_passes(self):
        split = '''
async def f():
    x = compute()
    y = await _w(x)
    return y


async def _w(x):
    y = x + 1
    return y
'''
        assert_extraction_preserves_behaviour(self.ASYNC_ORIGINAL, split, "_w")

    def test_awaiting_a_sync_helper_is_refused(self):
        split = '''
async def f():
    x = compute()
    y = await _w(x)
    return y


def _w(x):
    y = x + 1
    return y
'''
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(self.ASYNC_ORIGINAL, split, "_w")
        self.assertIn("awaits a helper that is not", str(caught.exception))

    def test_a_genuine_async_extraction_with_real_await_still_passes(self):
        """The rule must not refuse the shape the whole gate exists to allow."""
        assert_extraction_preserves_behaviour(
            'async def f(db):\n    rows = await db.q()\n    return rows\n',
            'async def f(db):\n    rows = await _w(db)\n    return rows\n\n\n'
            'async def _w(db):\n    rows = await db.q()\n    return rows\n',
            "_w")

    def test_a_plain_sync_extraction_is_unaffected(self):
        assert_extraction_preserves_behaviour(
            'def f():\n    t = 0\n    return t\n',
            'def f():\n    t = _w()\n    return t\n\n\ndef _w():\n    t = 0\n    return t\n',
            "_w")


class ExtractMethodBindingOrderTests(unittest.TestCase):
    """A name bound and read inside the SAME statement is not a live-in — but order still matters.

    THE EIGHTH HOLE, found by running the gate against the first real extraction rather than by
    reading it. `live_in_violations` advanced its `bound` set only AFTER each top-level statement,
    so an extracted `for` loop reported its own loop variable and its own body-assigned locals as
    "caller locals never passed". Every real extraction contains a loop, so the usable dialect was
    close to empty.

    The obvious fix — subtract everything a statement assigns — would have been wrong in the other
    direction, and `test_iterable_read_before_the_loop_rebinds_it_is_still_caught` is the case that
    proves it: there the name really is read before it is bound, and the split really does raise
    NameError. So these pin BOTH directions. A gate that stops refusing is worth less than no gate.
    """

    def test_loop_variable_and_body_locals_are_not_live_ins(self):
        """The shape that exposed the hole, kept faithful to it.

        The CALLER must also bind `i` and `start` — in `get_analytics` it does, because the hourly,
        daily and monthly loops all use the same variable names. Without that the names are not
        caller locals at all and the check is clean either way, so a simpler fixture would have
        passed against the BROKEN implementation too and pinned nothing. I wrote that simpler
        fixture first and it proved exactly nothing; this one fails on the old code with
        `['i', 'start']` and passes on the fixed code.
        """
        original = (
            "def f(n):\n"
            "    first = []\n"
            "    for i in range(n):\n"
            "        start = i * 2\n"
            "        first.append(start)\n"
            "    second = []\n"
            "    for i in range(n):\n"
            "        start = i\n"
            "        second.append(start)\n"
            "    return first, second\n"
        )
        split = (
            "def f(n):\n"
            "    first = _w(n)\n"
            "    second = []\n"
            "    for i in range(n):\n"
            "        start = i\n"
            "        second.append(start)\n"
            "    return first, second\n"
            "\n"
            "\n"
            "def _w(n):\n"
            "    first = []\n"
            "    for i in range(n):\n"
            "        start = i * 2\n"
            "        first.append(start)\n"
            "    return first\n"
        )
        assert_extraction_preserves_behaviour(original, split, "_w")

    def test_iterable_read_before_the_loop_rebinds_it_is_still_caught(self):
        """`for x in items: items = []` READS items first. Still a live-in. Still refused."""
        original = (
            "def f():\n"
            "    items = [1]\n"
            "    total = 0\n"
            "    for x in items:\n"
            "        items = []\n"
            "        total += x\n"
            "    return total\n"
        )
        split = (
            "def f():\n"
            "    items = [1]\n"
            "    total = _w()\n"
            "    return total\n"
            "\n"
            "\n"
            "def _w():\n"
            "    total = 0\n"
            "    for x in items:\n"
            "        items = []\n"
            "        total += x\n"
            "    return total\n"
        )
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("items", str(caught.exception))

    def test_assignment_value_is_read_before_its_target_binds(self):
        """`n = n + 1` reads a caller `n` the helper was never given."""
        original = "def f():\n    n = 1\n    n = n + 1\n    return n\n"
        split = (
            "def f():\n"
            "    n = 1\n"
            "    n = _w()\n"
            "    return n\n"
            "\n"
            "\n"
            "def _w():\n"
            "    n = n + 1\n"
            "    return n\n"
        )
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("`n`", str(caught.exception))

    def test_a_name_bound_in_only_one_branch_is_not_treated_as_bound(self):
        """Binding under `if` does not make the name bound on the path that skipped it."""
        original = (
            "def f(flag):\n"
            "    seen = 0\n"
            "    if flag:\n"
            "        seen = 1\n"
            "    total = seen\n"
            "    return total\n"
        )
        split = (
            "def f(flag):\n"
            "    seen = 0\n"
            "    total = _w(flag)\n"
            "    return total\n"
            "\n"
            "\n"
            "def _w(flag):\n"
            "    if flag:\n"
            "        seen = 1\n"
            "    total = seen\n"
            "    return total\n"
        )
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original, split, "_w")
        self.assertIn("`seen`", str(caught.exception))

    def test_with_target_binds_before_its_body(self):
        original = (
            "def f(path):\n"
            "    data = None\n"
            "    with open(path) as fh:\n"
            "        data = fh.read()\n"
            "    return data\n"
        )
        split = (
            "def f(path):\n"
            "    data = _w(path)\n"
            "    return data\n"
            "\n"
            "\n"
            "def _w(path):\n"
            "    data = None\n"
            "    with open(path) as fh:\n"
            "        data = fh.read()\n"
            "    return data\n"
        )
        assert_extraction_preserves_behaviour(original, split, "_w")


class ExtractMethodDecoratedFunctionTests(unittest.TestCase):
    """Route handlers are decorated, and the natural way to slice one drops its decorators.

    `ast.get_source_segment(src, node)` returns the text from the `def` line, so a caller obtaining
    `original_src` the obvious way hands over a function with an EMPTY decorator list while the
    split module still has them. Comparing whole nodes then failed with "extraction is NOT
    behaviour-preserving" — the most alarming message this module can produce — for a split that
    was correct. The first real extraction from `get_analytics` hit it immediately.

    Decorators are compared on their own now, and only when the caller supplied them, so the two
    failures cannot be mistaken for each other.
    """

    ORIGINAL_NO_DECORATOR = (
        "async def handler(n):\n"
        "    out = []\n"
        "    for i in range(n):\n"
        "        out.append(i)\n"
        "    return out\n"
    )
    SPLIT_WITH_DECORATOR = (
        '@router.get("/thing")\n'
        "async def handler(n):\n"
        "    out = await _w(n)\n"
        "    return out\n"
        "\n"
        "\n"
        "async def _w(n):\n"
        "    out = []\n"
        "    for i in range(n):\n"
        "        out.append(i)\n"
        "    return out\n"
    )

    def test_a_decoratorless_original_still_verifies_against_a_decorated_split(self):
        assert_extraction_preserves_behaviour(
            self.ORIGINAL_NO_DECORATOR, self.SPLIT_WITH_DECORATOR, "_w")

    def test_a_changed_decorator_is_still_refused_when_the_caller_supplies_them(self):
        """Skipping decorators must not mean ignoring them. A changed route is a changed API."""
        original_with_decorator = '@router.get("/thing")\n' + self.ORIGINAL_NO_DECORATOR
        moved_route = self.SPLIT_WITH_DECORATOR.replace('"/thing"', '"/something-else"')
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(original_with_decorator, moved_route, "_w")
        self.assertIn("decorators changed", str(caught.exception))


class ExtractMultipleBlocksTests(unittest.TestCase):
    """Several blocks out of one function, proved by inlining them ALL back.

    The single-extraction gate refuses the second of two splits — correctly but uselessly — because
    the split function still calls the OTHER new helper, which appears nowhere in the original. That
    is not a defect in the split, it is the proof modelling one extraction.

    The alternative was a chain of pre-split fixtures, one per extraction. It works and it rots:
    each is another copy of a function still being edited, and a stale one proves the wrong thing
    while staying green. Inlining all of them back against the TRUE original is one comparison that
    keeps working however many blocks come out.
    """

    ORIGINAL = (
        "def f(n):\n"
        "    a = []\n"
        "    for i in range(n):\n"
        "        a.append(i)\n"
        "    b = []\n"
        "    for i in range(n):\n"
        "        b.append(i * 2)\n"
        "    return a, b\n"
    )
    SPLIT = (
        "def f(n):\n"
        "    a = _first(n)\n"
        "    b = _second(n)\n"
        "    return a, b\n"
        "\n"
        "\n"
        "def _first(n):\n"
        "    a = []\n"
        "    for i in range(n):\n"
        "        a.append(i)\n"
        "    return a\n"
        "\n"
        "\n"
        "def _second(n):\n"
        "    b = []\n"
        "    for i in range(n):\n"
        "        b.append(i * 2)\n"
        "    return b\n"
    )

    def test_two_extractions_inline_back_together(self):
        assert_extractions_preserve_behaviour(self.ORIGINAL, self.SPLIT, ["_first", "_second"])

    def test_the_single_extraction_gate_cannot_do_this_alone(self):
        """Pinning WHY the multi version exists, so nobody deletes it as redundant."""
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(self.ORIGINAL, self.SPLIT, "_first")

    def test_a_broken_one_among_several_is_still_caught(self):
        """The multi version must not pass by averaging. One bad block fails the whole proof."""
        broken = self.SPLIT.replace("b.append(i * 2)", "b.append(i * 3)")
        with self.assertRaises(AssertionError) as caught:
            assert_extractions_preserve_behaviour(self.ORIGINAL, broken, ["_first", "_second"])
        self.assertIn("NOT behaviour-preserving", str(caught.exception))

    def test_a_missing_helper_is_named(self):
        with self.assertRaises(AssertionError) as caught:
            assert_extractions_preserve_behaviour(self.ORIGINAL, self.SPLIT, ["_first", "_nope"])
        self.assertIn("_nope", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
