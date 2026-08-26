"""The terminal grid's bounds are declared once and imported, never re-typed.

THE RULE IS THE REPO'S OWN: "Derive allowed values, never list them. A list you must remember to
update is a defect with a delay on it."

WHAT WENT WRONG, and it is an ordering accident rather than carelessness. `TERMINAL_MAX_COLS` and its
three siblings were declared at the BOTTOM of `terminal_snapshot.py`, below the two functions that
clamp with the same numbers -- so `infer_source_width` and `render_snapshot` wrote `500`, `200`, `20`
and `5` as literals, because at their line the names did not exist yet. A fourth copy then appeared in
`terminal_snapshot_view.py`. Four places to change, one of which anybody would think to look at.

The comment on those constants already records what a disagreement costs: resize clamped to 2000x1000
while the live screen clamped to 500x200, so a console wider than 500 columns rendered at the WRONG
WIDTH -- the woven-rows garbling the server-rendered snapshot exists to prevent.

WHAT IS DELIBERATELY NOT A BOUND, so this test does not demand it be replaced: the probe width's floor
of 80 in `infer_source_width` (a generous replay width, not the terminal minimum), and the `or 400`,
`or 80`, `or 24`, `or 100`, `or 28` DEFAULTS, which differ between call sites on purpose.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]

#: The values, and the constant that owns each. Read from the module rather than typed here -- a test
#: that hardcodes 500 to check nothing hardcodes 500 is the defect wearing a different hat.
from service.terminal_snapshot import (  # noqa: E402
    TERMINAL_MAX_COLS,
    TERMINAL_MAX_ROWS,
    TERMINAL_MIN_COLS,
    TERMINAL_MIN_ROWS,
)

OWNERS = {
    TERMINAL_MAX_COLS: "TERMINAL_MAX_COLS",
    TERMINAL_MAX_ROWS: "TERMINAL_MAX_ROWS",
    TERMINAL_MIN_COLS: "TERMINAL_MIN_COLS",
    TERMINAL_MIN_ROWS: "TERMINAL_MIN_ROWS",
}

#: Files that clamp a terminal grid. Narrow on purpose: `500` is an ordinary number and appears all
#: over the service for unrelated reasons (a size limit in MB, a query limit). The claim is about the
#: modules that size a SCREEN.
GRID_FILES = [
    "service/terminal_snapshot.py",
    "service/api_core/terminal_snapshot_view.py",
]

#: A literal that is a bound's value but is NOT a bound, with the reason. Each entry is a decision.
ALLOWED = {
    # `infer_source_width` replays into a deliberately generous probe screen; 80 is that probe's own
    # floor, not the terminal's minimum width. They are equal to nothing and mean different things.
    ("service/terminal_snapshot.py", 80),
}


def _clamp_calls(path: Path) -> int:
    """How many `max(...)`/`min(...)` calls this file makes.

    The population the scan reads, kept separate from what it ASSERTS about. Counting literals for
    anti-vacuity was wrong in a way worth recording: the fix removes literals, so the count fell to
    one and the check failed on a correct file. The parser is proven by finding the CALLS.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("max", "min")
    )


def _clamp_literals(path: Path) -> list[tuple[int, int]]:
    """Numeric literals inside a `max(...)`/`min(...)` call in this file, with line numbers.

    Only clamp calls, because a bound's VALUE appearing as a default (`int(rows or 120)`) is not a
    re-typed bound -- it is a different quantity that happens to be a number.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in ("max", "min"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, int) and not isinstance(arg.value, bool):
                found.append((node.lineno, arg.value))
    return found


class TerminalGridBoundsHaveOneHomeTests(unittest.TestCase):
    def test_the_constants_are_declared_before_their_first_use(self):
        """The ordering accident that caused this. A constant below the code that needs it cannot be
        used by that code, and the literal it leaves behind looks deliberate."""
        text = (SERVICE / "terminal_snapshot.py").read_text(encoding="utf-8")
        declared_at = text.index("TERMINAL_MAX_COLS = ")
        first_use = min(
            (text.index(name) for name in OWNERS.values() if text.index(name) > 0),
            default=declared_at,
        )
        self.assertLessEqual(
            declared_at, first_use,
            "the grid bounds are declared after something already references them",
        )

    def test_the_scan_reads_a_real_population(self):
        """Anti-vacuity: an AST that matched no clamp calls would report a clean service.

        Counted as CALLS, not literals. Literals are what the fix removes, so counting them made this
        check fail on a correctly-fixed file -- an anti-vacuity guard that fires on success is worse
        than none, because the obvious repair is to lower the number until it stops.
        """
        total = sum(_clamp_calls(SERVICE.parent / f) for f in GRID_FILES)
        self.assertGreater(total, 5, f"only {total} clamp calls found across {GRID_FILES}")

    def test_the_scan_can_say_PRESENT(self):
        """Both controls in the same run as the zero they defend, on a fixture rather than by
        mutating a real file."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text(f"x = max(20, min(cols, {TERMINAL_MAX_COLS}))\n", encoding="utf-8")
            values = [v for _, v in _clamp_literals(probe)]
            self.assertIn(TERMINAL_MAX_COLS, values)
            probe.write_text("x = max(a, min(cols, b))\n", encoding="utf-8")
            self.assertEqual(_clamp_literals(probe), [])

    def test_no_grid_file_re_types_a_bound_it_could_import(self):
        offenders = []
        for rel in GRID_FILES:
            for lineno, value in _clamp_literals(SERVICE.parent / rel):
                if value not in OWNERS:
                    continue
                if (rel, value) in ALLOWED:
                    continue
                offenders.append(f"{rel}:{lineno} clamps with {value}, which is {OWNERS[value]}")
        self.assertEqual(offenders, [], (
            "these clamp a terminal grid with a literal that a constant already owns:\n  "
            + "\n  ".join(offenders)
            + "\n\nImport the constant. The comment on those constants records what a disagreement "
            "cost last time: resize clamped to 2000x1000 while the live screen clamped to 500x200, "
            "so a console wider than 500 columns rendered at the wrong width."
        ))

    def test_the_allowance_list_stays_small_and_still_applies(self):
        """An allowance that no longer matches is an unchecked exemption, and a growing list is the
        rule being negotiated away one literal at a time."""
        self.assertLessEqual(len(ALLOWED), 2, f"{len(ALLOWED)} literals are exempt; each was a decision")
        for rel, value in ALLOWED:
            values = [v for _, v in _clamp_literals(SERVICE.parent / rel)]
            self.assertIn(value, values, f"ALLOWED names {rel}:{value}, which no longer appears there")

    def test_the_bounds_still_clamp_exactly_as_the_literals_did(self):
        """BEHAVIOUR-IDENTICAL, asserted rather than assumed. Replacing a literal with a constant of
        the same value is only safe while the value IS the same, and this is the test that notices if
        somebody later 'tidies' one of the constants."""
        from service.terminal_snapshot import _clamp_grid
        self.assertEqual((TERMINAL_MIN_COLS, TERMINAL_MIN_ROWS), _clamp_grid(1, 1))
        self.assertEqual((TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS), _clamp_grid(10_000, 10_000))
        self.assertEqual((20, 5), _clamp_grid(1, 1), "the min bounds changed value")
        self.assertEqual((500, 200), _clamp_grid(10_000, 10_000), "the max bounds changed value")

    def test_the_snapshot_view_takes_the_WIDER_of_viewer_and_inferred(self):
        """The property the rewritten clamp must keep, and the reason it matters.

        `infer_source_width` returns the furthest column any cell reached, so a narrow captured TAIL
        infers a narrow width -- measured against the live database, three of twelve terminals inferred
        **18**, below TERMINAL_MIN_COLS. Rendering at 18 would shred a full-screen TUI. The call site
        takes `max(viewer, inferred)` and then floors, so a small inference can never narrow the
        console; only a LARGER inference widens it.
        """
        source = (SERVICE / "api_core" / "terminal_snapshot_view.py").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"max\(\s*TERMINAL_MIN_COLS\s*,\s*min\(\s*max\(\s*int\(cols\)\s*,\s*int\(src_w or 0\)\s*\)",
            "the snapshot view no longer takes the wider of viewer and inferred width",
        )


if __name__ == "__main__":
    unittest.main()
