"""The multi-output extraction dialect: `a, b, c = _helper(...)`. Probes before production use.

WHY THIS DIALECT EXISTS. The status derivation's decision block has THREE live-outs
(`effective_status`, `reason`, `awaiting_reply`). The gate's VALUE form handled exactly one, so it
refused the extraction — correctly, since it could not verify the result. A verifier that can only
express single-output splits would force either a worse split of that block or no gate at all.

WHY IT IS ITS OWN SLICE. The reviewer's standing rule on dialect changes: "A dialect expansion would
be its own verifier-change slice with false-pass probes first, not a convenience tweak inside a
production extraction." The narrow dialect was chosen because false passes are dangerous; widening
it is a change to the thing doing the proving, so it gets probed before anything relies on it.

WHAT THE PROBES ARE FOR. Every test below that asserts a REFUSAL is guarding a shape where
inline-back would otherwise compare something meaningless and pass. The transposition case is the
sharpest: `a, b = _h()` returning `(b, a)` has the same names, the same arity and the same types, and
silently swaps two values. Nothing about it looks wrong. It must fail, and it does.
"""

from __future__ import annotations

import unittest

from service.tests.extract_method import assert_extraction_preserves_behaviour

ORIGINAL = '''
def compute(rows, flag):
    total = 0
    label = ""
    for row in rows:
        total += row
    if flag:
        label = "on"
    return total, label
'''

GOOD_SPLIT = '''
def compute(rows, flag):
    total, label = _summarize(rows, flag)
    return total, label


def _summarize(rows, flag):
    total = 0
    label = ""
    for row in rows:
        total += row
    if flag:
        label = "on"
    return total, label
'''


class MultiOutputDialectTests(unittest.TestCase):
    def test_a_correct_multi_output_split_is_accepted(self):
        """The shape the dialect was added for: same names, same order, single trailing return."""
        assert_extraction_preserves_behaviour(ORIGINAL, GOOD_SPLIT, "_summarize")

    def test_transposed_outputs_are_REFUSED(self):
        """THE dangerous case. Same names, same arity, same types — values silently swapped.

        `total, label = _summarize(...)` against `return label, total`. Nothing in the signature or
        the call site looks wrong; only the order differs. Inline-back reconstructs
        `total, label = label, total`, which does not match the original, so the round trip fails.
        If this ever passes, the dialect is unsafe and must be withdrawn.
        """
        # `rindex` targets the HELPER's trailing return, not the caller's — the caller keeps
        # `return total, label` so the only difference between this and GOOD_SPLIT is the order the
        # helper hands its two values back.
        transposed = GOOD_SPLIT[: GOOD_SPLIT.rindex("return total, label")] + "return label, total\n"
        self.assertIn("return total, label", transposed, "the caller's return must be untouched")
        with self.assertRaises(AssertionError) as caught:
            assert_extraction_preserves_behaviour(ORIGINAL, transposed, "_summarize")
        # It must fail because the ROUND TRIP did not close, not because the fixture is malformed.
        self.assertIn("did not reproduce the original", str(caught.exception))

    def test_arity_mismatch_is_REFUSED(self):
        """Unpacking two names from a helper that returns three cannot be verified element-wise."""
        wrong = GOOD_SPLIT.replace("return total, label", "return total, label, flag")
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(ORIGINAL, wrong, "_summarize")

    def test_a_non_tuple_return_under_tuple_unpacking_is_REFUSED(self):
        """`a, b = _h()` where the helper returns a LIST: element identity is unverifiable."""
        wrong = GOOD_SPLIT.replace("return total, label", "return [total, label]")
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(ORIGINAL, wrong, "_summarize")

    def test_a_non_name_unpack_target_is_REFUSED(self):
        """`obj.x, b = _h()` — the round trip cannot compare an attribute target to a returned name."""
        wrong = GOOD_SPLIT.replace("    total, label = _summarize(rows, flag)",
                                   "    holder.total, label = _summarize(rows, flag)")
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(ORIGINAL, wrong, "_summarize")

    def test_a_returned_non_name_element_is_REFUSED(self):
        """`return total, label.strip()` — the second element is an expression, not an identity."""
        wrong = GOOD_SPLIT.replace("return total, label", "return total, label.strip()")
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(ORIGINAL, wrong, "_summarize")

    def test_a_dropped_statement_still_fails_under_the_new_dialect(self):
        """The dialect must not weaken the ORIGINAL guarantee: a lost line still breaks the round trip."""
        wrong = GOOD_SPLIT.replace('    if flag:\n        label = "on"\n', "")
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(ORIGINAL, wrong, "_summarize")

    def test_a_changed_body_still_fails_under_the_new_dialect(self):
        wrong = GOOD_SPLIT.replace("total += row", "total += row * 2")
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(ORIGINAL, wrong, "_summarize")

    def test_single_output_behaviour_is_unchanged(self):
        """The widening must not alter the existing one-name VALUE form."""
        original = "def f(rows):\n    total = 0\n    for r in rows:\n        total += r\n    return total\n"
        split = (
            "def f(rows):\n    total = _sum(rows)\n    return total\n\n\n"
            "def _sum(rows):\n    total = 0\n    for r in rows:\n        total += r\n    return total\n"
        )
        assert_extraction_preserves_behaviour(original, split, "_sum")

    def test_an_async_multi_output_helper_must_still_be_awaited(self):
        """The await-shape check predates this dialect and must keep applying to it.

        An `async def` helper whose call is not awaited returns a coroutine; unpacking it would raise,
        but inline-back reconstructs the original perfectly and is blind to the difference.
        """
        original = "async def f(rows):\n    a = 1\n    b = 2\n    return a, b\n"
        unawaited = (
            "async def f(rows):\n    a, b = _pair(rows)\n    return a, b\n\n\n"
            "async def _pair(rows):\n    a = 1\n    b = 2\n    return a, b\n"
        )
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(original, unawaited, "_pair")

    def test_live_in_checking_still_applies_to_multi_output(self):
        """A helper reading a caller local it was never handed still raises NameError at runtime."""
        original = "def f(rows, scale):\n    a = 0\n    b = 0\n    for r in rows:\n        a += r * scale\n    return a, b\n"
        missing_arg = (
            "def f(rows, scale):\n    a, b = _pair(rows)\n    return a, b\n\n\n"
            "def _pair(rows):\n    a = 0\n    b = 0\n    for r in rows:\n        a += r * scale\n    return a, b\n"
        )
        with self.assertRaises(AssertionError):
            assert_extraction_preserves_behaviour(original, missing_arg, "_pair")


if __name__ == "__main__":
    unittest.main()
