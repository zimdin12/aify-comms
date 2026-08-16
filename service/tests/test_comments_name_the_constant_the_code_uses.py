"""Comments that point at a constant which is not there.

Written after fixing a real one: `_status_turn_signals` (`service/api_core/turn_state.py`) had an
inline comment saying "Stale (no refresh within TURN_BUSY_STALE_SECONDS) is treated as not-busy"
while the code four lines below ages against TURN_BUSY_BACKSTOP_SECONDS. The comment travelled with
the block when the function was extracted from `_compute_live_status_cache`, where the surrounding
prose was about the other twin. It sat four lines under a docstring warning those two bounds "are not
interchangeable", so it pointed the next reader at exactly the swap the suite rejects
(`test_status_is_pure_event_long_ceiling_not_short_window`, verified by mutation).

A DETECTOR FOR THAT DEFECT WAS BUILT AND THEN DELIBERATELY NOT SHIPPED. The rule tried was: flag a
function whose prose names an UPPER_SNAKE constant it does not use, when it DOES use one sharing the
first two name parts. It found exactly one hit repo-wide — and that hit was contaminated. The
function's DOCSTRING also names the short bound, on purpose, to contrast the two:

    that one uses `TURN_BUSY_STALE_SECONDS`, this one `TURN_BUSY_BACKSTOP_SECONDS`

which is the best documentation in the file and precisely what a reader needs. The detector cannot
tell a contrast from a misstatement, so it flags the good prose and the bad prose identically. The
real defect was that the comment ASSERTED a bound as the one in force; that is a claim about meaning,
not a pattern over identifiers. A gate whose true positive and false positive are the same shape is
worse than no gate — it trains people to delete the explanation.

WHAT IS KEPT is the half that survives contact with the repo: a comment naming a constant that exists
NOWHERE. Renamed or deleted, prose left behind. Measured at ZERO when this was written, so this pins
it rather than reporting it — `docs inherit intention, not outcome`, and nothing else in the suite
reads a comment.
"""

from __future__ import annotations

import ast
import io
import pathlib
import re
import tokenize
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv"}
#: Three or more underscore-separated parts. Two-part names pull in too many ordinary words
#: (IO_ERROR, NOT_SET) that are English as often as they are identifiers.
UPPER = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,})\b")


def _python_sources() -> list[tuple[str, str]]:
    out = []
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        out.append((rel.as_posix(), path.read_text(encoding="utf-8")))
    return out


def comments_of(src: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError):
        pass
    return out


def dangling_constants(sources: list[tuple[str, str]], existing: set[str]) -> list[str]:
    out: list[str] = []
    for rel, src in sources:
        if "tests" in rel.split("/"):
            continue
        for lineno, text in comments_of(src):
            for name in UPPER.findall(text):
                if name not in existing:
                    out.append(f"{rel}:{lineno} names {name}")
    return out


def _names_that_exist(sources: list[tuple[str, str]]) -> set[str]:
    """Deliberately generous: any identifier, attribute, alias, def, or UPPER token in a string.

    A hit therefore means the name is genuinely absent, not merely hard to find. Constants also live
    across the language boundary here (the bridge and the service share several), so JS/JSON/MD/SH
    are folded in — a Python comment naming a JS constant is correct, not dangling.
    """
    existing: set[str] = set()
    for _rel, src in sources:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                existing.add(n.id)
            elif isinstance(n, ast.Attribute):
                existing.add(n.attr)
            elif isinstance(n, ast.alias):
                existing.add((n.asname or n.name).split(".")[-1])
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                existing |= set(UPPER.findall(n.value))
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                existing.add(n.name)
    for pattern in ("*.js", "*.mjs", "*.json", "*.md", "*.sh"):
        for path in REPO.rglob(pattern):
            if PRUNE & set(path.relative_to(REPO).parts):
                continue
            try:
                existing |= set(UPPER.findall(path.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
    return existing


class CommentsNameConstantsThatExistTests(unittest.TestCase):
    def test_no_comment_names_a_constant_that_exists_nowhere(self):
        sources = _python_sources()
        dangling = dangling_constants(sources, _names_that_exist(sources))
        self.assertEqual(
            dangling, [],
            "these comments point at a constant that exists nowhere in the repo — renamed or "
            "deleted, with the prose left behind for the next reader to hunt for: "
            + "; ".join(dangling[:10]),
        )

    def test_the_detector_fires_on_a_name_that_is_absent(self):
        """A clean tree cannot tell a working scan from a broken one."""
        fixture = [("service/fake.py", "x = 1  # bounded by TOTALLY_ABSENT_CONSTANT_NAME\n")]
        self.assertEqual(
            dangling_constants(fixture, {"SOMETHING_ELSE_ENTIRELY"}),
            ["service/fake.py:1 names TOTALLY_ABSENT_CONSTANT_NAME"],
        )

    def test_the_detector_stays_quiet_when_the_name_exists(self):
        fixture = [("service/fake.py", "x = 1  # bounded by A_REAL_CONSTANT_NAME\n")]
        self.assertEqual(dangling_constants(fixture, {"A_REAL_CONSTANT_NAME"}), [])

    def test_test_files_are_out_of_scope(self):
        """Test prose routinely names constants from other trees and hypothetical ones."""
        fixture = [("service/tests/test_x.py", "x = 1  # about TOTALLY_ABSENT_CONSTANT_NAME\n")]
        self.assertEqual(dangling_constants(fixture, set()), [])
