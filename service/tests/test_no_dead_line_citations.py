"""A comment may not cite a line number that does not exist in the file it names.

Comments across this repo cite evidence by line — `api_v2.py:10105`, `server.js:1849` — and those
citations were true when written. `api_v2.py` was 20,545 lines at its peak; it is 53 now, so every
citation into it resolves to nothing. The claim around it may still be correct, but a reader who
follows the pointer lands in the wrong place and learns nothing, which is worse than no pointer:
a precise-looking reference is trusted more than a vague one.

This is the same failure as [docs inherit intention, not outcome] — prose written beside the work
describes the state at that moment, and nothing in the suite reads prose. The v0.5.x series moved
several hundred declarations, so any citation into a file that shrank is suspect by default.

SCOPE: `.py`, `.js` and `.mjs` under the repo, pruning the same directories as the other structural
gates, plus `fixtures/` and `data/` — a frozen fixture is a snapshot of an older tree and its
comments must NOT be rewritten to match today's line numbers.

WHAT IT DOES NOT DO: verify that a citation which is IN range points at anything relevant. That
needs judgement, not a gate. This catches only the citations that are provably dead, which is the
class that arose mechanically from files shrinking.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "tests", "fixtures", "data", "__pycache__", ".git", ".venv"}
SUFFIXES = {".py", ".js", ".mjs"}

# A filename, a separator (`:` or `~`), then a line number — the two citation forms this repo uses.
CITATION = re.compile(r"\b([\w./-]+\.(?:py|js|mjs))\s*[:~]\s*(\d{2,})\b")


def _sources() -> list[pathlib.Path]:
    return [
        path
        for path in sorted(REPO.rglob("*"))
        if path.suffix in SUFFIXES and not PRUNE & set(path.relative_to(REPO).parts)
    ]


def _resolve(cited: str) -> pathlib.Path | None:
    """Find the cited file. A bare basename resolves only if exactly one file in the repo matches —
    an ambiguous name is not a citation this gate can judge, so it is skipped rather than guessed."""
    direct = REPO / cited
    if direct.is_file():
        return direct
    matches = [p for p in _sources() if p.name == pathlib.PurePosixPath(cited).name]
    return matches[0] if len(matches) == 1 else None


def test_no_comment_cites_a_line_beyond_the_end_of_the_file_it_names():
    offenders: list[str] = []
    for path in _sources():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for cited, number in CITATION.findall(line):
                target = _resolve(cited)
                if target is None or target == path:
                    continue
                try:
                    length = len(target.read_text(encoding="utf-8").splitlines())
                except (UnicodeDecodeError, OSError):
                    continue
                if int(number) > length:
                    offenders.append(
                        f"{path.relative_to(REPO).as_posix()}:{lineno} cites {cited}:{number}, "
                        f"but {cited} is {length} lines"
                    )

    assert not offenders, (
        "dead line citation(s) — a comment points at a line that no longer exists.\n"
        "The claim may still be true; the pointer is not. Drop the line number and name the file "
        "that owns the behaviour NOW, keeping the original reference as history if it explains the "
        "finding.\n  " + "\n  ".join(offenders)
    )


def test_the_scan_covers_a_real_population():
    """An empty offender list must mean 'checked and clean', not 'checked nothing'."""
    sources = _sources()
    assert len(sources) > 250, f"only {len(sources)} source files found — the walk is broken"
    names = {path.name for path in sources}
    assert {"doctor.js", "claude-channel.js", "control_plane.py"} <= names
    assert not any("fixtures" in path.relative_to(REPO).parts for path in sources), (
        "a frozen fixture is a snapshot of an older tree — its comments must not be rewritten"
    )


def test_the_pattern_matches_what_it_claims_and_no_more():
    """Anti-vacuity, on the regex itself — a citation form it cannot see is a citation it cannot judge."""
    assert CITATION.findall("see api_v2.py:10105 for the shape") == [("api_v2.py", "10105")]
    assert CITATION.findall("(api_v2.py ~18935/19037)") == [("api_v2.py", "18935")]
    assert CITATION.findall("service/routers/api_v2.py:10496") == [("service/routers/api_v2.py", "10496")]
    assert CITATION.findall("server.js:1849 hits the guard") == [("server.js", "1849")]

    # Things that are NOT line citations must not be flagged.
    assert CITATION.findall("mcp/stdio/server.js is the bridge") == []
    assert CITATION.findall("bumped to 0.5.4") == []
    assert CITATION.findall("see file.py:7") == [], "a 1-digit line is too weak a signal to judge"
