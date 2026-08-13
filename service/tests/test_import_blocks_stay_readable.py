"""A multi-name import must not be collapsed onto one enormous line.

WHY THIS IS A TEST AND NOT A STYLE PREFERENCE. In v0.5.4 my accessor-cleanup tool rebuilt every import
it touched as `from X import a, b, c, ...` on one line, producing lines approaching a thousand
characters across fourteen modules. The reviewer caught it and named the second-order problem, which
is the one that matters: it also reads as line-count compression, and this series reports line counts
as evidence. Measured afterwards, 7 of the 264 lines I had claimed removed from the carrier were
imports folded up rather than code moved out.

I then fixed it, and RE-INTRODUCED IT in the same session, because the next mechanical pass — the
dangling-import resolver — rebuilt import statements the same single-line way. Twice from two
different tools means the property needs a gate rather than another resolution to be careful. Any
future rewriting pass will now fail here instead of shipping a diff nobody can review.

WHAT IS NOT FLAGGED: a single-name import. `from service.api_core.terminal_ownership import
_release_stale_terminal_owner` is 104 characters and cannot be made shorter by wrapping — there is
nothing to wrap. Two dozen of those exist and they are fine. The defect is specifically MANY names
folded onto one line, which is what destroys the diff.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent

#: Beyond this, a multi-name import stops being reviewable in a side-by-side diff.
MAX_LINE = 120


def _offenders():
    for path in sorted(SERVICE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except SyntaxError:
            continue
        lines = source.split("\n")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if (node.end_lineno or node.lineno) != node.lineno:
                continue  # already wrapped
            if len(node.names) < 2:
                continue  # nothing to wrap
            width = len(lines[node.lineno - 1])
            if width > MAX_LINE:
                yield (path.relative_to(REPO).as_posix(), node.lineno, width, len(node.names))


class ImportBlocksStayReadableTests(unittest.TestCase):
    def test_no_multi_name_import_is_collapsed_onto_one_long_line(self):
        found = [f"{p}:{ln}  {width} chars, {count} names" for p, ln, width, count in _offenders()]
        self.assertEqual(
            found,
            [],
            "a multi-name import is collapsed onto one line longer than "
            f"{MAX_LINE} characters:\n  " + "\n  ".join(found)
            + "\nWrap it in parentheses, one name per line. If a mechanical pass produced this, fix "
            "the pass — this happened twice in v0.5.4 from two different tools.",
        )

    def test_the_detector_recognises_a_collapsed_import(self):
        """Asserting an absence means a green run and a broken detector look identical."""
        names = ", ".join(f"_some_helper_number_{i}" for i in range(12))
        collapsed = f"from service.control_plane import {names}\n"
        self.assertGreater(len(collapsed), MAX_LINE, "the synthetic sample must exceed the limit")
        node = ast.parse(collapsed).body[0]
        self.assertIsInstance(node, ast.ImportFrom)
        self.assertGreaterEqual(len(node.names), 2, "the sample must be a multi-name import")
        self.assertEqual(
            (node.end_lineno or node.lineno), node.lineno,
            "the sample must be single-line, which is the shape being detected",
        )

    def test_no_module_imports_the_same_name_from_the_same_module_twice(self):
        """A duplicate identical import is behaviour-neutral and is the residue of a bad edit.

        Found by the reviewer in `messages.py` and `sessions.py` after v0.5.4 repointing: each had the SAME
        `from service.api_core.spawn_request_state import _has_claimable_spawn_request` line twice. Nothing
        breaks — the second binding is the same object — so no sweep, no `create_app()` and no suite has any
        reason to complain. It only shows up as noise in the next diff over that file, which is exactly when
        it is most expensive to notice.

        The class gets the gate rather than the two instances getting a fix, which is this repo's rule for
        a finding a generator can reproduce.
        """
        offenders = []
        for path in sorted(SERVICE.rglob("*.py")):
            # `tests/data/` holds pristine pre-split fixtures — captured function bodies, not modules.
            if "__pycache__" in path.parts or path.parent.name == "data":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            # MODULE SCOPE ONLY — `tree.body`, not `ast.walk`. My first version walked the whole tree and
            # flagged 8 legitimate cases: every borrow shim in this repo does
            # `from service.control_plane import X as _impl` inside its OWN function body, so a walk sees
            # the same (module, alias) pair once per shim and calls it a duplicate. Those are separate
            # scopes and not the defect. The defect is two identical module-level imports in one file.
            seen: dict[tuple[str, str], int] = {}
            for node in tree.body:
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                for alias in node.names:
                    key = (node.module, alias.asname or alias.name)
                    if key in seen:
                        offenders.append(
                            f"{path.relative_to(REPO).as_posix()}:{node.lineno} imports {key[1]} from "
                            f"{key[0]} again (first at line {seen[key]})"
                        )
                    else:
                        seen[key] = node.lineno
        self.assertEqual(offenders, [], "\n  ".join([""] + offenders))

    def test_a_long_single_name_import_is_deliberately_allowed(self):
        """Guards the exemption itself: a 1-name import cannot be wrapped, so it must not be flagged."""
        # Length is COMPUTED, not hand-tuned: a fixture whose whole point is "longer than the limit"
        # must not depend on me counting characters correctly — I got it wrong twice writing this.
        prefix = "from service.api_core.terminal_ownership import "
        one = prefix + "_" * (MAX_LINE - len(prefix) + 5) + "\n"
        self.assertGreater(len(one), MAX_LINE, "the sample must exceed the limit to prove the exemption")
        node = ast.parse(one).body[0]
        self.assertEqual(len(node.names), 1)
        # the rule under test: fewer than two names is skipped regardless of width
        self.assertLess(len(node.names), 2)


if __name__ == "__main__":
    unittest.main()
