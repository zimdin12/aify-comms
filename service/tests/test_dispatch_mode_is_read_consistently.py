"""Ten readers of `dispatch_mode`, six normalising and four not — on the same column, same request.

`dispatch_mode` decides delivery semantics for a run: `message_only` means deliver it as a message
and do NOT start a turn; `require_start` means a failure to start is a FAILURE rather than a quiet
cancel. It arrives from the request body — `models.py` types it a bare `str` with no validator — and
went into the column exactly as the caller spelled it.

The readers then disagreed with each other. Six ask
`str(row["dispatch_mode"] or "").strip().lower()`; four compared it raw, and the sharpest of those is
`claim_run_selection.py`:

    if run["dispatch_mode"] == "message_only":
        UPDATE dispatch_runs SET status = 'cancelled' ...   # and skip the run

A mode spelled `Message_Only` is recognised by the six and not by the four, so the SAME row means two
different things inside one request path: the delivery side treats it as message-only while the claim
side does not recognise it and lets the run proceed — starting a turn on an agent the sender asked
not to start. The other three raw sites downgrade a `require_start` failure to a `cancelled`, which is
what the sender is told.

AND THE SIBLING FIELD THREE LINES ABOVE IS NORMALISED:

    run_execution_mode = (run["execution_mode"] or "managed").strip().lower()
    ...
    if run["dispatch_mode"] == "message_only":

That is the same shape as the `launch_mode` fix: the field beside a normalised field was raw.

FIXED AT BOTH ENDS. `_create_dispatch_runs` normalises once on the way in — it is the only place a
caller-supplied mode enters the column, and every other caller passes a server literal that the fold
leaves alone — and the four raw readers now match their six siblings, which is what protects rows
written before this change.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv", "tests"}


def _raw_dispatch_mode_comparisons() -> list[tuple[str, int, str]]:
    """Comparisons of a `dispatch_mode` subscript against a string literal, WITHOUT normalisation.

    AST rather than grep: `str(row["dispatch_mode"] or "").strip().lower() == "terminal"` and
    `row["dispatch_mode"] == "terminal"` differ only in the call chain wrapped around the subscript,
    which a text scan would have to guess at.
    """
    found = []
    for path in sorted((REPO / "service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                continue
            if not isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                continue
            left = node.left
            # The bare subscript form, with no `.lower()`/`.strip()` wrapper around it.
            if not (isinstance(left, ast.Subscript)
                    and isinstance(left.slice, ast.Constant)
                    and left.slice.value == "dispatch_mode"):
                continue
            found.append((rel.as_posix(), node.lineno, ast.unparse(node)[:90]))
    return found


class DispatchModeReadConsistencyTests(unittest.TestCase):
    def test_no_reader_compares_dispatch_mode_raw(self):
        """THE ONE THAT MATTERS. A raw reader disagrees with the six that normalise."""
        offenders = _raw_dispatch_mode_comparisons()
        self.assertEqual(
            offenders, [],
            "these compare a raw `dispatch_mode`; six other readers normalise first, so the same row "
            "would mean two different things in one request path. Wrap it: "
            'str(row["dispatch_mode"] or "").strip().lower()',
        )

    def test_the_scanner_finds_the_shape_it_is_looking_for(self):
        """Anti-vacuity. An AST walk that matched nothing would pass the test above on any repo."""
        fixture = ast.parse('if run["dispatch_mode"] == "message_only":\n    pass\n')
        compares = [n for n in ast.walk(fixture) if isinstance(n, ast.Compare)]
        self.assertEqual(len(compares), 1)
        left = compares[0].left
        self.assertTrue(
            isinstance(left, ast.Subscript) and left.slice.value == "dispatch_mode",
            "the detector's own shape test must match the form the defect had",
        )
        # …and the normalised form must NOT match, or the gate would demand a rewrite of the six
        # readers that were always correct.
        normalised = ast.parse(
            'if str(row["dispatch_mode"] or "").strip().lower() == "terminal":\n    pass\n')
        left_ok = [n for n in ast.walk(normalised) if isinstance(n, ast.Compare)][0].left
        self.assertNotIsInstance(left_ok, ast.Subscript)

    def test_the_normalising_readers_are_still_there(self):
        """The other half of the population, so "no raw readers" cannot be satisfied by deleting them.

        Counted rather than named: the point is that the column IS read, in numbers, and every one of
        those reads folds case.
        """
        normalising = 0
        for path in sorted((REPO / "service").rglob("*.py")):
            rel = path.relative_to(REPO)
            if PRUNE & set(rel.parts):
                continue
            source = path.read_text(encoding="utf-8")
            normalising += source.count('["dispatch_mode"] or "").strip().lower()')
        self.assertGreaterEqual(
            normalising, 6, f"only {normalising} normalising reads found; the column is barely read",
        )

    def test_the_write_path_normalises_once(self):
        """`_create_dispatch_runs` is the only place a caller-supplied mode enters the column."""
        source = (REPO / "service/api_core/dispatch_runs.py").read_text(encoding="utf-8")
        self.assertIn('dispatch_mode = str(dispatch_mode or "").strip().lower()', source)

    def test_a_server_literal_is_unchanged_by_the_fold(self):
        """Every non-request caller passes a lowercase literal, so the fold must be a no-op for them.

        Read from the call sites rather than assumed: if one of these ever became mixed-case the fold
        would silently rewrite it, and that should be a decision.
        """
        literals = set()
        for path in sorted((REPO / "service").rglob("*.py")):
            rel = path.relative_to(REPO)
            if PRUNE & set(rel.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.keyword) or node.arg != "dispatch_mode":
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    literals.add(node.value.value)
        self.assertTrue(literals, "no server-literal dispatch_mode call sites found at all")
        for literal in sorted(literals):
            with self.subTest(literal=literal):
                self.assertEqual(literal.strip().lower(), literal, "the fold must be a no-op here")
