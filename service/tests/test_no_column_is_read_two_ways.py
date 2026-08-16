"""A column whose readers disagree about case is a defect that hides as a style difference.

Three slices in a row found the same shape by hand, so this is the census that finds the next one.

    launch_mode     `none` is the STOP marker; four readers compared it raw while the row could
                    hold `"None"` -- literally `str(None)` -- so a stopped agent read as not
                    stopped and cold-started on the next send.
    dispatch_mode   ten readers, six folding and four not. `Message_Only` was message-only to the
                    DELIVERY path and unrecognised by the CLAIM path: one row, two meanings, one
                    request, and a turn started on an agent that asked for message-only.

The tell in both cases was not the raw read on its own -- it was the SPLIT. When some readers of a
column fold case and others do not, the column has two meanings, and which one applies depends on
which code path reaches the row first. A uniformly raw column is merely fragile; a split one is
already inconsistent with itself.

WHAT THIS SCANS: every comparison of `something["col"]` against lowercase string literals in
`service/`, classified by whether a case-folding call wraps the subscript -- `.lower()`,
`.casefold()`, or one of the `_normalize_*` helpers, which are the repo's named folders. AST rather
than text, because `str(row["x"] or "").strip().lower() == "y"` and `row["x"] == "y"` differ only in
the call chain around the subscript.

THE LEDGER BELOW IS THE POLICY. A split is allowed only with a reason, and the reason has to be
something a reader can check -- "the write path validates it" is checkable; "it looks fine" is not.
Anything not listed fails, which is what makes the next `launch_mode` a red test instead of a lucky
scan.
"""

from __future__ import annotations

import ast
import collections
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv", "tests"}

FOLDERS = ("lower", "casefold", "upper")

#: Columns read both ways, and why each is not the defect above. Every entry names a guarantee that
#: can be checked in the code, not an impression.
ALLOWED_SPLITS = {
    # `agents.status` is folded at BOTH write paths (`agent_registration_writes.py` and the PATCH in
    # `routers/agents/attributes.py`) and `spawn_requests.status` is validated against an allowlist
    # at its PATCH before storage, so no row can hold a mixed-case status. The raw readers are
    # dashboard counters over those columns.
    "status": "folded at every write path; spawn status also allowlisted at the PATCH",
    # `row["state"]` here is a key of a dict this same function just built with
    # `_contract_row_to_dict`, not a stored column. There is no external writer to disagree with.
    "state": "an internally computed dict key, not a stored column",
    # `*_controls.action`: environment controls are `.strip().lower()`ed and allowlisted in
    # `control_environment` before storage; terminal control actions come from eight server-side
    # literals via `_append_terminal_control`. No caller-supplied spelling reaches either column.
    "action": "server-written or allowlisted at the write; never caller-spelled",
}


def _sources() -> list[tuple[str, str]]:
    out = []
    for path in sorted((REPO / "service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        out.append((rel.as_posix(), path.read_text(encoding="utf-8")))
    return out


def _column_and_fold(node) -> tuple[str | None, bool]:
    """For a comparison's left side: which column it reads, and whether case is folded first."""
    folded = False
    current = node
    for _ in range(8):
        if isinstance(current, ast.Call):
            func = current.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in FOLDERS or name.startswith("_normalize") or name.startswith("normalize"):
                folded = True
            if isinstance(func, ast.Attribute):
                current = func.value
                continue
            if current.args:
                current = current.args[0]
                continue
            return None, folded
        if isinstance(current, ast.BoolOp) and current.values:
            current = current.values[0]          # `row["x"] or "default"`
            continue
        if isinstance(current, ast.IfExp):
            current = current.body               # `row["x"] if "x" in row.keys() else ""`
            continue
        if isinstance(current, ast.Subscript):
            key = current.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return key.value, folded
            return None, folded
        return None, folded
    return None, folded


def _reader_populations(sources) -> dict[str, dict[str, list[tuple[str, int]]]]:
    found: dict[str, dict[str, list]] = collections.defaultdict(
        lambda: {"folded": [], "raw": []}
    )
    for rel, src in sources:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                continue
            if not isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                continue
            right = node.comparators[0]
            if isinstance(right, (ast.Set, ast.List, ast.Tuple)):
                values = [e.value for e in right.elts
                          if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if len(values) != len(right.elts):
                    continue
            elif isinstance(right, ast.Constant) and isinstance(right.value, str):
                values = [right.value]
            else:
                continue
            # Only literals that are already lowercase — a comparison against `"Mixed"` is not
            # making the assumption this gate is about.
            if not values or not all(v and v.isascii() and v == v.lower() for v in values):
                continue
            column, folded = _column_and_fold(node.left)
            if not column:
                continue
            found[column]["folded" if folded else "raw"].append((rel, node.lineno))
    return found


class NoColumnIsReadTwoWaysTests(unittest.TestCase):
    def test_no_undeclared_column_is_read_both_ways(self):
        """THE ONE THAT MATTERS. A split column means two meanings for one row."""
        populations = _reader_populations(_sources())
        splits = {
            column: sites for column, sites in sorted(populations.items())
            if sites["folded"] and sites["raw"] and column not in ALLOWED_SPLITS
        }
        self.assertEqual(
            {column: sites["raw"] for column, sites in splits.items()}, {},
            "these columns are compared case-folded in some places and raw in others, so the same "
            "stored value means different things depending on which path reads it. Fold at the raw "
            "sites, or declare the split in ALLOWED_SPLITS with the write-side guarantee that makes "
            "it safe.",
        )

    def test_every_declared_split_is_still_a_split(self):
        """The ledger shrinks honestly. An entry for a column that no longer splits is a stale
        exemption, and the next real split under that name would inherit it."""
        populations = _reader_populations(_sources())
        for column, reason in sorted(ALLOWED_SPLITS.items()):
            with self.subTest(column=column):
                sites = populations.get(column, {"folded": [], "raw": []})
                self.assertTrue(
                    sites["folded"] and sites["raw"],
                    f"`{column}` no longer has both reader kinds — delete its entry ({reason})",
                )

    def test_the_scan_is_not_silently_matching_nothing(self):
        populations = _reader_populations(_sources())
        self.assertGreater(len(populations), 10, "almost no column comparisons parsed")
        folded_total = sum(len(s["folded"]) for s in populations.values())
        self.assertGreater(folded_total, 20, "no folded readers found — the classifier is broken")

    def test_the_classifier_reads_the_wrapper_not_the_text(self):
        """Both shapes, and the two wrappers this repo actually writes around a column read."""
        def classify(expr: str):
            node = ast.parse(expr).body[0].value
            return _column_and_fold(node.left)

        self.assertEqual(classify('row["mode"] == "x"'), ("mode", False))
        self.assertEqual(classify('str(row["mode"] or "").strip().lower() == "x"'), ("mode", True))
        self.assertEqual(classify('_normalize_runtime(row["mode"]) == "x"'), ("mode", True))
        self.assertEqual(classify('(row["mode"] or "detached") == "x"'), ("mode", False))
        self.assertEqual(
            classify('(row["mode"] if "mode" in row.keys() else "") == "x"'), ("mode", False),
            "the conditional-subscript form the repo uses for optional columns",
        )
        self.assertEqual(classify('mode == "x"'), (None, False), "a plain name reads no column")

    def test_the_two_columns_this_gate_was_written_for_are_no_longer_split(self):
        """`launch_mode` and `dispatch_mode` were the findings; they must not reappear as splits."""
        populations = _reader_populations(_sources())
        for column in ("launch_mode", "dispatch_mode"):
            with self.subTest(column=column):
                sites = populations.get(column, {"folded": [], "raw": []})
                self.assertEqual(
                    sites["raw"], [],
                    f"`{column}` has a raw reader again: {sites['raw']}",
                )
                self.assertTrue(sites["folded"], f"`{column}` is not read at all any more")
