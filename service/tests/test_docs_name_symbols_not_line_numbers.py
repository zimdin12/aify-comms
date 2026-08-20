"""The docs an agent is TOLD to trust name files and SYMBOLS, never line numbers.

A line number is a fact about a file at one moment. This repo moves thousands of lines at a time -- v0.5
emptied `api_v2.py` from 20,545 lines to 53, and v0.6 Phase 2 took `install.sh` from 4,371 to 2,934 when
the wrapper bodies moved to `wrappers/` -- so every pointer past the edit shifts silently. Nothing goes
red, because a line number cannot be wrong, only stale.

MEASURED, 2026-08-20, in two passes. First: 21 of 33 pointers in these docs named a line PAST THE END of
its file, `api_v2.py:12391` among them. Those were repaired. Then the survivors were checked against
what they claimed, and that is the pass that produced this rule:

    install.sh:203  claimed `install_claude_wrapper`   - the function is at 476
    install.sh:319  claimed codex app-server ports     - the line prunes dangling symlinks
    install.sh:539  claimed a pi-session-state probe   - the line detects the Hermes install root
    server.js:895   claimed an auto-register call site - the line is a `// moved to ...` tombstone

Five of the six checkable survivors pointed somewhere real and wrong, which prose_paths_resolve's own
words call worse than no trail: it reads as governance. Only one was still correct. A gate that merely
required them to RESOLVE would have passed every one of those five, because a wrong line inside a
2,934-line file resolves perfectly.

So the rule is not "resolve" but "do not use". A symbol survives the refactor that moves it; a line
number is the thing that did not survive the last one, and grep finds a symbol in less time than it
takes to notice a pointer has drifted.

DERIVED, not listed: the population is CLAUDE.md plus every .md it links from its Primary entry points
section, plus every file under `.claude/skills`. A doc promoted into that list is covered the same day.
The skills are there for a stronger reason than the entry points: a SKILL.md loads into every agent's
context every session, so a bad pointer there is paid by every agent on every turn.

SYMBOL NAMES WERE CHECKED TOO, AND ARE NOT GATED, so nobody repeats the search. Of 546 backticked
identifiers in these docs, 16 exist nowhere in this repo's source -- and 15 of those are correct:
`tty_do_resize` is the Linux kernel's, `delete_empty_sessions` is hermes', `approval_mode` and
`allowed_models` are config keys, `poolStale` appears in a "Fix =" sentence describing work not yet
done, and `test_session_identity_sticky` names a test inside a historical red-baseline record. A gate
here would be 15 false alarms and one finding, and a gate that cries wolf gets switched off. The one
real candidate is `statusBucketForPresence`, presented as ours and absent under every variant name;
locating a successor needs more archaeology than one backticked name in a dated entry is worth, and
inventing a replacement would be worse than leaving it.

Historical records are deliberately OUT. The repo holds ~490 such pointers and most sit in dated
plans, ledgers and audits, where a pointer into code as it stood IS the record and rewriting it would
falsify history.
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
NEWLINE = chr(10)

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

    def test_no_doc_an_agent_must_trust_cites_a_line_number(self):
        found = []
        for doc in gated_docs():
            for match in POINTER_RE.finditer(doc.read_text(encoding="utf-8", errors="replace")):
                found.append(f"{doc.name}: {match.group(0)}")

        self.assertEqual(
            found,
            [],
            "these docs cite line numbers, and line numbers rot silently — a refactor moves the code "
            "and the pointer keeps resolving to something unrelated. Name the file and the SYMBOL "
            "instead; grep finds it, and it survives the next move:"
            + NEWLINE + "  " + (NEWLINE + "  ").join(found),
        )


if __name__ == "__main__":
    unittest.main()
