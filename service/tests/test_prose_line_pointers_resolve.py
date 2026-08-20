"""A `file.py:1234` pointer in the docs an agent is TOLD to trust must land inside that file.

`test_prose_paths_resolve.py` already checks that a named file exists. This checks the other half of the
same claim: that the LINE exists too. The two failed apart, because a refactor that moves 20,000 lines
out of a file leaves the filename valid and every line number in it meaningless.

MEASURED BEFORE WRITING, 2026-08-20: 33 line-pointers in the primary-entry-point docs, 21 of them dead.
`DECISIONS.md` and `KNOWN_ISSUES.md` between them name `api_v2.py` at lines 281, 352, 1047, 2228, 4637,
8517, 8650, 9923, 10927, 12391 and 19418. That file is 53 lines long: v0.5 moved every route domain out
of it. `server.js:1857` and `app.js:2315` are the same story from the JS side.

WHY IT MATTERS HERE AND NOT EVERYWHERE. The repo has 508 such pointers and 307 are dead, but most sit in
dated records -- V0.2_PLAN.md, the findings ledgers, dashboard audits -- where a pointer into code as it
stood IS the record, and rewriting it would be falsifying history. The population gated here is the one
CLAUDE.md declares as its primary entry points, plus CLAUDE.md itself: the documents an agent is
instructed to read before its first change. A dead pointer there sends a reader somewhere real and
wrong, which prose_paths_resolve's own words call worse than no trail, because it reads as governance.

DERIVED FROM CLAUDE.md, not listed. The population is parsed out of the "Primary entry points" section,
so a doc promoted into that list comes under this gate the same day, and one demoted leaves it.

WHAT IT DOES NOT CHECK: whether the line says what the prose claims. `install.sh:1285` resolves and
points five lines above the function it names. Resolvability is the floor, not the ceiling.
"""

from __future__ import annotations

import re
import unittest
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Same roots prose_paths_resolve uses: prose abbreviates, and `api_v2.py` means the one under routers.
PREFIXES = ("", "service/", "mcp/", "mcp/stdio/", "service/routers/", "service/new_dashboard/")

#: `path.py:123`, with an optional closing backtick between the two.
POINTER_RE = re.compile(r"(?<![/\w.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|js|mjs|sh))`?:(\d+)\b")


@lru_cache(maxsize=None)
def resolve(ref: str) -> Path | None:
    for prefix in PREFIXES:
        candidate = REPO / (prefix + ref)
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=None)
def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def gated_docs() -> list[Path]:
    """CLAUDE.md, every .md it links from Primary entry points, and every always-loaded skill file.

    The skills are in the population for a stronger reason than the entry points are. A SKILL.md is not
    read on demand: it loads into every agent's context every session, so a dead pointer there is paid
    by every agent on every turn, and the reader who follows it is mid-task rather than orienting.

    They are found by walking `.claude/skills`, not listed. The `.agents` mirror is byte-identical and
    gated as such by test_skill_mirror_parity, so checking one checks both -- and fixing one without the
    other reddens that gate rather than passing quietly.
    """
    claude = REPO / "CLAUDE.md"
    text = claude.read_text(encoding="utf-8", errors="replace")
    section = text.split("## Primary entry points", 1)
    assert len(section) == 2, "CLAUDE.md no longer has a Primary entry points section"
    body = section[1].split("\n## ", 1)[0]
    docs = [claude]
    for match in re.finditer(r"\]\(([^)]+\.md)\)", body):
        candidate = REPO / match.group(1)
        if candidate.is_file():
            docs.append(candidate)
    docs.extend(sorted((REPO / ".claude" / "skills").rglob("*.md")))
    return docs


class ProseLinePointersResolve(unittest.TestCase):
    def test_the_gated_population_is_not_empty(self):
        """A gate over nothing reports green exactly like a gate over everything."""
        docs = gated_docs()
        self.assertGreaterEqual(len(docs), 5, f"only {len(docs)} docs gated; the parse probably broke")
        names = {d.name for d in docs}
        for expected in ("CLAUDE.md", "DECISIONS.md", "KNOWN_ISSUES.md"):
            self.assertIn(expected, names, f"{expected} dropped out of the gated set")

    def test_every_line_pointer_lands_inside_its_file(self):
        dead = []
        for doc in gated_docs():
            for match in POINTER_RE.finditer(doc.read_text(encoding="utf-8", errors="replace")):
                ref, line = match.group(1), int(match.group(2))
                path = resolve(ref)
                if path is None:
                    dead.append(f"{doc.name}: {ref}:{line} — no such file")
                elif line > line_count(path):
                    dead.append(f"{doc.name}: {ref}:{line} — {path.name} has {line_count(path)} lines")

        self.assertEqual(
            dead,
            [],
            "line pointers in primary-entry-point docs do not land in their file. A refactor moved the "
            "code; the pointer did not move with it. Prefer naming the file and the SYMBOL — a symbol "
            "survives a refactor and a line number does not:\n  " + "\n  ".join(dead),
        )


if __name__ == "__main__":
    unittest.main()
