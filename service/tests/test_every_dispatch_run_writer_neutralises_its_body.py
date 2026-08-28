r"""Every writer of `dispatch_runs.body` neutralises what it stores. Derived, not listed.

WHAT THIS REPLACES, AND WHY IT IS A DIFFERENT TEST. `test_read_receipt_injection_via_dispatch_body.py`
closed the receipt-forgery chain and pinned the fix with two `assertIn` calls against the text of
`dispatch_runs.py`. That is a location pin: it proves a line was written in one file. The property
that actually matters belongs to the COLUMN -- "no stored dispatch body carries a structural marker
unless the service wrote it" -- and a column is only as safe as its worst writer.

Measured 2026-08-28: `dispatch_runs.body` has FOUR writers, and ONE neutralised.

    service/api_core/dispatch_runs.py        fresh dispatch     neutralised  (the one pinned)
    service/api_core/dispatch_runs.py        steer contract     RAW
    service/api_core/console_input_queue.py  terminal coalesce  RAW
    service/api_core/session_mode_audit.py   audit anchor       service-authored, no foreign text

The two raw writers were in the same function and the neighbouring module. The pin could not see
them because it was looking at a line, not at a question.

WHY THE RAW ONES WERE REACHABLE, checked against the live database rather than argued:

  * `POST /contracts/hygiene/repair-read-receipts` -- a button on the Work loop page, "Repair
    delivered reads" -- selects `status IN ('claimed','running','completed','failed','cancelled')`
    with a non-empty `message_id`, and calls `_mark_dispatch_source_messages_read` on every row.
  * Steer rows land as `delivered` and 139 of them had reached `completed`; ALL 140 carried a
    non-empty `message_id`. Terminal-coalesce rows are inserted `running` outright.
  * `_dispatch_source_message_ids` reads ids out of a body that starts with the buffer header, and
    `_mark_dispatch_source_messages_read` inserts a receipt for each against ANY message with that id.
    Unread is the ABSENCE of a receipt, so a forged one makes a message vanish from `comms_listen`.

So a sender whose body began with the buffer header and carried `MessageId:` lines could suppress
another agent's messages -- through the steer path, or the terminal path, but not the one path the
gate watched.

NOTHING WAS EXPLOITED. Same live read, 2026-08-28: 574 rows have a body starting with the header and
every one is `start_if_possible` -- genuine merged buffers the service wrote itself. Zero steer or
terminal rows. The positive control for that query (2,382 bodies containing "Subject") and its
negative control (0 bodies containing a token nothing writes) both behaved, so the zero is a zero.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SERVICE = Path(__file__).resolve().parents[1]

#: The transformation that makes a body structurally inert. Named once; the classifier below accepts
#: an expression only because it reaches this, never because of where it lives.
NEUTRALISER = "_neutralise_buffer_markers"

_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+dispatch_runs\s*\((?P<columns>[^)]*)\)", re.IGNORECASE | re.DOTALL
)


def _product_sources() -> list[Path]:
    """Every non-test module under `service/`. Walked, never enumerated: a fifth writer added in a
    new file must be judged by this test on the day it lands, not on the day somebody remembers."""
    return sorted(
        path for path in SERVICE.rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    )


def _body_binding(call: ast.Call, sql: str):
    """The expression bound to the `body` column by this INSERT, or None if it binds none.

    Positional: the SQL names its columns in order and the parameter tuple fills them in that order,
    so the binding is the tuple element at `body`'s index. Reading the tuple by NAME is impossible --
    they are positional placeholders -- which is exactly why a wrong one is invisible on sight.
    """
    match = _INSERT_RE.search(sql)
    if not match:
        return None
    columns = [column.strip() for column in match.group("columns").split(",")]
    if "body" not in columns:
        return None
    index = columns.index("body")
    if len(call.args) < 2 or not isinstance(call.args[1], (ast.Tuple, ast.List)):
        return None
    elements = call.args[1].elts
    if index >= len(elements):
        return None
    return elements[index], len(columns), len(elements)


def _neutralised_names(tree: ast.AST) -> set[str]:
    """Names assigned from the neutraliser anywhere in the module."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        function = node.value.func
        if isinstance(function, ast.Name) and function.id == NEUTRALISER:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _verdict(expression: ast.AST, neutralised: set[str]) -> str:
    """`ok` if this expression cannot carry a foreign structural marker, else why not."""
    if (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Name)
        and expression.func.id == NEUTRALISER
    ):
        return "ok"
    if isinstance(expression, ast.Name):
        if expression.id in neutralised:
            return "ok"
        return f"binds `{expression.id}`, which is not assigned from {NEUTRALISER}(...)"
    if isinstance(expression, (ast.Constant, ast.JoinedStr)):
        # A literal or f-string the service composes itself. It cannot carry a marker the service did
        # not write, which is precisely the invariant -- see `session_mode_audit.py`.
        return "ok"
    return f"binds a {type(expression).__name__} this gate cannot vouch for"


def _writers():
    """Every INSERT into dispatch_runs in the service, with the expression it binds to `body`."""
    found = []
    for path in _product_sources():
        text = path.read_text(encoding="utf-8")
        if "INSERT INTO dispatch_runs" not in text:
            continue
        tree = ast.parse(text)
        neutralised = _neutralised_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            sql = node.args[0]
            if not isinstance(sql, ast.Constant) or not isinstance(sql.value, str):
                continue
            if "INSERT INTO dispatch_runs" not in sql.value:
                continue
            found.append((path, node.lineno, _body_binding(node, sql.value), neutralised))
    return found


class EveryWriterOfTheColumnHoldsTheInvariant(unittest.TestCase):
    def test_the_walk_finds_the_writers_that_exist(self):
        """POSITIVE CONTROL. A walk that finds nothing passes every assertion below, and this repo
        has produced that wrong zero more than once. Cross-checked against a plain text search, so
        the AST half cannot go quiet on its own."""
        by_text = {
            path for path in _product_sources()
            if "INSERT INTO dispatch_runs" in path.read_text(encoding="utf-8")
        }
        by_ast = {path for path, *_ in _writers()}
        self.assertTrue(by_text, "no module writes dispatch_runs at all -- the search is broken")
        self.assertEqual(
            by_ast, by_text,
            "the AST walk and a text search disagree about which modules insert into dispatch_runs. "
            "A statement built by string concatenation, or a params list this gate cannot read, is "
            "invisible to the walk and would pass unjudged.",
        )
        self.assertGreaterEqual(len(_writers()), 4, "fewer INSERT sites than the four measured")

    def test_every_writer_neutralises_the_body_it_binds(self):
        offenders = []
        for path, line, binding, neutralised in _writers():
            if binding is None:
                continue
            expression, columns, elements = binding
            self.assertEqual(
                columns, elements,
                f"{path.name}:{line} binds {elements} values to {columns} columns; the positional "
                "match this gate relies on is broken and so is the INSERT",
            )
            verdict = _verdict(expression, neutralised)
            if verdict != "ok":
                offenders.append(f"{path.relative_to(SERVICE.parent)}:{line} {verdict}")
        self.assertEqual(
            offenders, [],
            "a writer of dispatch_runs.body stores a sender's text unneutralised:\n  "
            + "\n  ".join(offenders)
            + "\nThe claim-time parser assumes no stored body carries a structural marker unless the "
            "service wrote it. That is a property of the column, so every writer must hold it -- see "
            "this file's header for what the two raw writers bought.",
        )

    def test_the_classifier_refuses_a_raw_binding(self):
        """NEGATIVE CONTROL. A gate that cannot say no cannot say yes. Both shapes are judged by the
        same `_verdict`, so a change that made everything pass would fail here."""
        tree = ast.parse("stored = _neutralise_buffer_markers(body)")
        neutralised = _neutralised_names(tree)
        self.assertEqual(neutralised, {"stored"})
        raw = ast.parse("x = body", mode="exec").body[0].value
        self.assertNotEqual(
            _verdict(raw, neutralised), "ok",
            "the classifier accepts a raw `body`, so it accepts the defect",
        )
        good = ast.parse("x = stored", mode="exec").body[0].value
        self.assertEqual(_verdict(good, neutralised), "ok")
        direct = ast.parse("x = _neutralise_buffer_markers(body)", mode="exec").body[0].value
        self.assertEqual(_verdict(direct, neutralised), "ok")

    def test_a_body_column_that_moves_is_still_found(self):
        """The index is read from the SQL, never assumed. A reordered column list must still resolve
        to the right tuple slot, or this gate would vouch for whatever sat where `body` used to."""
        source = (
            'db.execute("INSERT INTO dispatch_runs (id, body, status) VALUES (?,?,?)", (a, raw, c))'
        )
        tree = ast.parse(source)
        call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and node.args)
        expression, columns, elements = _body_binding(call, call.args[0].value)
        self.assertEqual((columns, elements), (3, 3))
        self.assertEqual(expression.id, "raw", "the gate read the wrong tuple slot for `body`")


if __name__ == "__main__":
    unittest.main()
