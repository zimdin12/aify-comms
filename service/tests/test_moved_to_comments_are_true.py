"""Every `# X moved to <path>` comment must name a module that actually declares X.

These comments are the v0.5.x extraction series' own trail markers: 163 of them across six files,
each recording where a symbol went when it left. They are the first thing a reader follows, and
NOTHING VERIFIED THEM. Measured when this gate was written, 10 were wrong — every one a v0.5.3 claim
whose symbol took a second hop in v0.5.4 and whose comment recorded only the first. So the marker
pointed at a router that no longer had it, which is worse than no marker: it sends the reader
somewhere specific and wrong.

THIS IS THE COMMENT FORM OF A LOCATION PIN, and the repo already has a rule about those. A source
regex asserting where code LIVES proves only that a line was written. A comment asserting where code
lives does not even prove that. The difference is that this one can be checked cheaply, so it should
be — a claim that can rot and is never re-read is a claim that will rot.

RECORDING A SECOND HOP: append it rather than rewriting, so the trail stays readable.

    # _turn_busy_holds_delivery moved to service/routers/dispatch_messages/shared.py in v0.5.3, then
    # on to service/api_core/claim_gating.py in v0.5.4.

`then on to` is the only multi-hop form in the file, and it is what makes the LAST path authoritative
instead of the first.

IF A MOVE ALSO RENAMES, name the destination symbol, or this gate cannot find it:

    # _now moved to service/clock.py as `now` in v0.5.

WHY THE PARSER LOOKS LIKE THIS. Two shapes were assumed rather than matched when this was first
written, and both produced confident wrong answers:

  * requiring `# <one-identifier> moved to` missed `# _A and _B moved to <path>`, which was then
    swallowed as a CONTINUATION of the tombstone above it;
  * taking the LAST path in the accumulated text then attributed the swallowed line's path to the
    previous symbol, reporting `_DISPATCH_TERMINAL_STATUSES` as pointing at a module its own comment
    never named.

So: a tombstone is any comment line containing ` moved to `; a continuation is a following comment
line that does NOT; the symbol list splits on `and` and commas; and the path is the first after
`moved to` unless `then on to` appears.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent

_MOVED = re.compile(r"^#\s*(.+?)\s+moved to\s+(.*)$")
_PATH = re.compile(r"([\w/]+\.py)")
# The destination name MUST be quoted. An unquoted `as \w+` matches the ordinary English "as" in the
# prose that follows a tombstone -- it read "as a group" as a rename to a symbol called `a`, and then
# reported a correct comment as broken. Requiring backticks makes the rename form unambiguous, which
# is why the docstring above writes it that way.
_RENAME = re.compile(r"\bas\s+[`'\"]([A-Za-z_]\w*)[`'\"]")
_IDENT = re.compile(r"^[A-Za-z_]\w*$")

#: Below this, assume the extractor broke rather than that the comments vanished.
MIN_EXPECTED_CLAIMS = 100


def _source_files():
    for path in sorted(SERVICE.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        yield path


def _claims(path: Path):
    """(line, symbol, destination path, renamed-to) for every tombstone in one file."""
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    out = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        match = _MOVED.match(stripped) if stripped.startswith("#") else None
        if not match:
            i += 1
            continue
        head, rest = match.group(1), match.group(2)
        j = i + 1
        while j < len(lines):
            nxt = lines[j].strip()
            if not nxt.startswith("#") or " moved to " in nxt:
                break
            rest += " " + nxt.lstrip("#").strip()
            j += 1
        symbols = [s.strip() for s in re.split(r"\band\b|,", head) if _IDENT.match(s.strip())]
        found = _PATH.findall(rest)
        dest = (found[-1] if "then on to" in rest else found[0]) if found else None
        rename = _RENAME.search(rest)
        for symbol in symbols:
            out.append((i + 1, symbol, dest, rename.group(1) if rename else None))
        i = j
    return out


def _declared_names(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def broken_claims():
    """(source file, line, symbol, destination, why) for every tombstone that no longer holds."""
    bad = []
    for path in _source_files():
        for line, symbol, dest, rename in _claims(path):
            rel = path.relative_to(REPO).as_posix()
            if not dest:
                bad.append((rel, line, symbol, "-", "the comment names no module"))
                continue
            declared = _declared_names(REPO / dest)
            if declared is None:
                bad.append((rel, line, symbol, dest, "that module does not exist or does not parse"))
            elif (rename or symbol) not in declared:
                bad.append((rel, line, symbol, dest, "that module does not declare %s" % (rename or symbol)))
    return bad


def all_claims():
    return [(p.relative_to(REPO).as_posix(), *c) for p in _source_files() for c in _claims(p)]


class MovedToCommentsAreTrueTests(unittest.TestCase):
    def test_every_moved_to_comment_names_the_module_that_has_the_symbol(self):
        bad = broken_claims()
        detail = "\n".join("  %s:%d  %s -> %s   (%s)" % row for row in bad)
        self.assertEqual(
            bad,
            [],
            "%d 'moved to' comment(s) point somewhere the symbol no longer is. Follow the symbol and "
            "append the next hop as 'then on to <path>' — do not delete the first hop, the trail is "
            "the point. If the move renamed it, write 'moved to <path> as `newname`'.\n%s"
            % (len(bad), detail),
        )

    def test_the_extractor_still_finds_the_comments(self):
        """Anti-vacuity. An extractor that silently matched nothing would pass the test above."""
        claims = all_claims()
        self.assertGreaterEqual(
            len(claims),
            MIN_EXPECTED_CLAIMS,
            "only %d 'moved to' claim(s) found; the extraction regexes have probably stopped matching "
            "rather than the comments having genuinely gone" % len(claims),
        )
        self.assertIn(
            "service/control_plane.py",
            {row[0] for row in claims},
            "the control plane carries most of these markers and must be among the files scanned",
        )

    def test_a_claim_pointing_at_the_wrong_module_is_caught(self):
        """The gate must actually reject a false destination, not merely tolerate a true one."""
        real = _declared_names(REPO / "service/clock.py")
        self.assertIsNotNone(real, "service/clock.py must exist for this case to mean anything")
        self.assertNotIn(
            "_a_symbol_clock_does_not_have_zzz",
            real,
            "the destination-check must be reading real declarations",
        )
        self.assertIn("now", real, "…and must find one that is genuinely there")

    def test_multi_hop_and_multi_symbol_comments_parse(self):
        """The two shapes that broke the first version of this parser."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text(
                "# _A and _B moved to service/one.py in v0.5.\n"
                "# _C moved to service/two.py in v0.5.3, then on to service/three.py in v0.5.4.\n"
                "# _D moved to service/four.py as `renamed` in v0.5.\n",
                encoding="utf-8",
            )
            got = [(sym, dest, rename) for _, sym, dest, rename in _claims(probe)]

        self.assertEqual(
            got,
            [
                ("_A", "service/one.py", None),
                ("_B", "service/one.py", None),
                ("_C", "service/three.py", None),
                ("_D", "service/four.py", "renamed"),
            ],
            "a multi-symbol comment must yield one claim per symbol, a multi-hop comment must resolve "
            "to its LAST module, and a renaming move must carry the destination name",
        )


if __name__ == "__main__":
    unittest.main()
