"""Two near-identical console-input loops, pinned as twins rather than merged.

`send_message` and `create_dispatch` each queue the terminal `input` control that delivers a message
into a console session, and in v0.5.4 each was extracted into `dispatch_messages/shared.py`:

    _queue_console_dispatch_inputs        the send path
    _queue_console_inputs_for_dispatch    the dispatch path

FIFTY-ONE OF THEIR FIFTY-THREE BODY LINES ARE CHARACTER-FOR-CHARACTER IDENTICAL. The two that differ
are declared in `SUBSTITUTIONS` below, and only one of them is cosmetic:

    a variable rename — `msg_id` in one, `message_id` in the other;
    a VALUE — `"source": "message_send"` versus `"source": "dispatch"`.

WHY THEY ARE NOT MERGED. The second difference is real: the terminal delivery contract records which
entry point produced it, so collapsing the two would either lose that distinction or thread it through
as a parameter. That is a behaviour-shaped change, and v0.5.x is the refactor line — an empty
behaviour changelog. Merging them is a reviewable decision, not something to smuggle into a slice that
claims byte-identical bodies.

WHAT THIS TEST IS FOR. A duplicated fix is the failure mode of duplicated code, and it is silent: the
copy that was not updated keeps working, wrongly, on whichever entry point it serves. So the pair is
pinned — the two bodies must stay identical MODULO exactly the declared substitutions. Change one
without the other and this fails, naming the line.

It also fails if the two ever become genuinely identical, which is deliberate: at that point the
second difference is gone, nothing stands in the way of merging them, and this file should be deleted
rather than left asserting a distinction that no longer exists.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
#: v0.5.4: the pair left `dispatch_messages/shared.py` for a leaf, and being ADJACENT is the point —
#: in the router they sat 200 lines apart, where a fix applied to one and not the other was
#: invisible. This pin follows them.
SHARED = REPO / "service" / "api_core" / "console_input_queue.py"

SEND_TWIN = "_queue_console_dispatch_inputs"
DISPATCH_TWIN = "_queue_console_inputs_for_dispatch"

#: (line as it appears in the SEND twin, line as it appears in the DISPATCH twin). Compared stripped,
#: so indentation differences between the two call sites do not register as divergence.
SUBSTITUTIONS = [
    (
        "recipient_message_id = source_message_ids.get(recipient_id, msg_id)",
        "recipient_message_id = source_message_ids.get(recipient_id, message_id)",
    ),
    (
        '"source": "message_send",',
        '"source": "dispatch",',
    ),
]


def _loop_body(function_name: str) -> list[str]:
    """The shared loop, stripped, from whichever twin is asked for.

    Sliced at the `for recipient_id, terminal in …` line because the send twin wraps its loop in a
    `if req.trigger:` guard and builds `source_message_ids` itself, while the dispatch twin receives
    that map already built. Those framing lines are NOT duplicated logic and comparing them would make
    this test fail on a difference nobody needs to fix.
    """
    source = SHARED.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function_name
    )
    lines = source.replace("\r\n", "\n").split("\n")[node.lineno - 1:node.end_lineno]
    start = next(i for i, line in enumerate(lines) if line.strip().startswith("for recipient_id, terminal in"))
    return [line.strip() for line in lines[start:] if line.strip()]


class ConsoleInputQueueingTwinsAgreeTests(unittest.TestCase):
    def test_both_twins_exist(self):
        """Without this the comparison below would raise StopIteration rather than fail informatively."""
        declared = {
            n.name for n in ast.parse(SHARED.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SEND_TWIN, declared)
        self.assertIn(DISPATCH_TWIN, declared)

    def test_the_loops_are_the_same_length(self):
        """A line added to one and not the other is the cheapest way for these to drift."""
        send, dispatch = _loop_body(SEND_TWIN), _loop_body(DISPATCH_TWIN)
        self.assertEqual(
            len(send), len(dispatch),
            f"{SEND_TWIN} has {len(send)} lines and {DISPATCH_TWIN} has {len(dispatch)}; "
            "one of them was edited and the other was not",
        )

    def test_the_loops_differ_ONLY_by_the_declared_substitutions(self):
        send, dispatch = _loop_body(SEND_TWIN), _loop_body(DISPATCH_TWIN)
        allowed = {(a, b) for a, b in SUBSTITUTIONS}
        undeclared = [
            (i, a, b) for i, (a, b) in enumerate(zip(send, dispatch))
            if a != b and (a, b) not in allowed
        ]
        self.assertEqual(
            undeclared, [],
            "the two console-input loops have diverged beyond their declared substitutions. A fix "
            "applied to one and not the other is silent — the un-updated copy keeps working, wrongly, "
            "on whichever entry point it serves:\n  "
            + "\n  ".join(f"line {i}:\n    send:     {a}\n    dispatch: {b}" for i, a, b in undeclared),
        )

    def test_every_declared_substitution_is_STILL_USED(self):
        """A stale entry would silently widen what counts as agreement.

        If a substitution is no longer present, either the difference was resolved — in which case it
        should be deleted here — or the line moved and the pair is being compared loosely without
        anyone noticing.
        """
        send, dispatch = _loop_body(SEND_TWIN), _loop_body(DISPATCH_TWIN)
        actual = {(a, b) for a, b in zip(send, dispatch) if a != b}
        for pair in SUBSTITUTIONS:
            self.assertIn(
                pair, actual,
                f"declared substitution {pair} no longer occurs; delete it or fix the comparison",
            )

    def test_the_twins_are_not_yet_identical(self):
        """The end state, asserted so it is noticed rather than passed through.

        If these ever become identical there is nothing left to justify two copies, and this file
        should be deleted along with one of them. Reaching that state should be a decision, not a
        thing that quietly happened.
        """
        self.assertNotEqual(
            _loop_body(SEND_TWIN), _loop_body(DISPATCH_TWIN),
            "the two loops are now identical — merge them and delete this test",
        )


if __name__ == "__main__":
    unittest.main()
