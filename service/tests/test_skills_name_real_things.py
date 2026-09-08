"""Every file a skill points at must exist.

The same gate `test_architecture_doc_names_real_things.py` puts on ARCHITECTURE.md, aimed at the
skills. The reason generalises and this repo has paid for it: prose rots, and a TROUBLESHOOTING doc
that sends the reader to a file which moved is worse than one that says nothing, because the reader
follows it and then distrusts everything around it.

Found on the first run: four files opened with "Split out of `dispatch-bridge.md`" and
`lifecycle.md` pointed a reader at the same name. The file is `dispatch-bridges.md`. That pointer had
been broken since the 2026-08-03 split and nothing noticed, because nothing was looking.

WHAT IS NOT CHECKED, and why the allowlist is small and named: a skill legitimately mentions files
that belong to somebody else's project (hermes's `web_server.py`, Claude Code's `settings.json`). A
name is exempt only when it is external, and each entry says whose it is — an unexplained exemption
is how a gate stops meaning anything.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / ".claude" / "skills"

# Repo-relative paths and bare filenames, backticked — an unquoted filename in prose is usually being
# discussed rather than pointed at.
#
# Illustrative paths are deliberately absent from this comment: `test_prose_paths_resolve.py` scans
# every Python docstring for names that do not resolve, and an example path invented to explain the
# pattern is exactly the thing it catches. It caught this file on the first run, which is the two
# gates covering each other rather than overlapping — that one reads `*.py` under service/, mcp/ and
# scripts/, this one reads `.claude/skills/**/*.md`, and neither sees the other's surface.
FILE_REF = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|js|mjs|sh|json|md))`")

# Files owned by other projects, or by the operator's machine rather than this repo.
EXTERNAL = {
    "web_server.py": "hermes's own dashboard server",
    "hooks.json": "Claude Code's per-user hook config",
    "settings.json": "Claude Code's per-user settings",
    "install-deps.js": "an example third-party plugin's dep installer",
}

# Directories a bare filename may live under. A skill writes `claude.js`, not the full path, and
# resolving it is this gate's job rather than the author's.
SEARCH_ROOTS = ("service", "mcp", "scripts", "docs", ".claude", "wrappers")


def _skill_files() -> list[Path]:
    return sorted(SKILLS.rglob("*.md"))


def _exists(ref: str) -> bool:
    if "/" in ref:
        # Try it as repo-relative first, then as a suffix of any real path: skills write
        # `adapters/claude.js` for a file that lives at `mcp/stdio/adapters/claude.js`.
        if (REPO / ref).exists():
            return True
        tail = ref.replace("/", "\\")
        for root in SEARCH_ROOTS:
            for p in (REPO / root).rglob("*"):
                if str(p).endswith(tail) or str(p).replace("\\", "/").endswith(ref):
                    return True
        return False
    for root in SEARCH_ROOTS:
        base = REPO / root
        if base.exists() and any(base.rglob(ref)):
            return True
    return (REPO / ref).exists()


def test_the_scan_reaches_the_skills():
    """Without this, a renamed directory makes every assertion below pass on an empty set."""
    files = _skill_files()
    assert len(files) >= 10, f"expected the skill corpus, found {len(files)}"
    assert any(f.name == "SKILL.md" for f in files)


def test_every_file_a_skill_points_at_exists():
    broken: dict[str, list[str]] = {}
    for path in _skill_files():
        text = path.read_text(encoding="utf-8")
        for ref in set(FILE_REF.findall(text)):
            if ref in EXTERNAL:
                continue
            if not _exists(ref):
                broken.setdefault(ref, []).append(path.relative_to(SKILLS).as_posix())

    assert not broken, "skills point at files that do not exist:\n" + "\n".join(
        f"  {ref}  cited in: {', '.join(sorted(where))}" for ref, where in sorted(broken.items())
    )


# A backticked `thing()`. Prose names a function to explain a mechanism, so what must be true is that
# the name EXISTS -- not that it lives anywhere in particular.
#
# THE FILE HALF ABOVE AND THIS ONE ANSWER THE SAME QUESTION about different tokens, which is why they
# live together. A second implementation elsewhere would agree with this one until one of them was
# fixed; that is this repo's standing reason for not writing the second.
SYMBOL_REF = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})\(\)`")

# Names that are somebody else's, each saying whose. The same rule as EXTERNAL above: an unexplained
# exemption is how a gate stops meaning anything.
EXTERNAL_SYMBOLS = {
    "fetch": "the platform builtin",
    "spawn": "node:child_process",
    "derive": "named as a concept in the status model, not as an export",
    "discover_mcp_tools": "hermes's own tool discovery",
}

SYMBOL_ROOTS = ("service", "mcp", "scripts")
SYMBOL_SKIP = {"tests", "fixtures", "__pycache__", "node_modules", ".git", ".pytest_cache"}


def _symbol_sources() -> list[str]:
    out: list[str] = []
    for root in SYMBOL_ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".js", ".mjs"):
                continue
            if any(part in SYMBOL_SKIP for part in path.parts):
                continue
            out.append(path.read_text(encoding="utf-8", errors="ignore"))
    return out


def test_every_function_a_skill_names_appears_in_source():
    """A skill naming a function nobody implements describes behaviour nobody has.

    THAT IS NOT HYPOTHETICAL HERE. Two paragraphs in `dispatch-bridges.md` described a `channel-enter`
    mechanism present in NO source file, and were deleted this version -- but only because somebody
    read them, which is not a mechanism. This is the same check the file half makes, on the other kind
    of pointer.

    TESTS DO NOT COUNT AS AN IMPLEMENTATION: a name appearing only in a test is a name nothing
    implements, which is exactly the condition being looked for.
    """
    sources = _symbol_sources()
    assert len(sources) > 200, f"the source walk found only {len(sources)} files; it is measuring nothing"

    missing: dict[str, list[str]] = {}
    for path in _skill_files():
        text = path.read_text(encoding="utf-8")
        for name in set(SYMBOL_REF.findall(text)):
            if name in EXTERNAL_SYMBOLS:
                continue
            if not any(name in body for body in sources):
                missing.setdefault(name, []).append(path.relative_to(SKILLS).as_posix())

    assert not missing, "skills name functions that appear in no source file:\n" + "\n".join(
        f"  {name}()  cited in: {', '.join(sorted(where))}" for name, where in sorted(missing.items())
    )


def test_the_symbol_scan_can_say_absent_and_present():
    """POSITIVE AND NEGATIVE CONTROL, in the same run. A probe that cannot return ABSENT cannot return
    PRESENT -- and the sweep above passes trivially if the source walk came back empty."""
    sources = _symbol_sources()
    assert any("rowResumeKey" in body for body in sources), "the walk cannot find a function that exists"
    assert not any("aFunctionNobodyDefines_xyzzy" in body for body in sources)


def test_every_external_symbol_exemption_is_still_referenced():
    """Same rule as the file exemptions: a name nobody mentions should leave the list."""
    mentioned: set[str] = set()
    for path in _skill_files():
        mentioned |= set(SYMBOL_REF.findall(path.read_text(encoding="utf-8")))
    unused = sorted(name for name in EXTERNAL_SYMBOLS if name not in mentioned)
    assert not unused, f"declared external and mentioned by no skill: {', '.join(unused)}"


def test_every_external_exemption_is_still_referenced():
    """An exemption for a name nobody mentions any more is dead policy pretending to be coverage."""
    mentioned: set[str] = set()
    for path in _skill_files():
        mentioned.update(FILE_REF.findall(path.read_text(encoding="utf-8")))
    unused = sorted(name for name in EXTERNAL if name not in mentioned)
    assert not unused, f"EXTERNAL names nothing cites any more: {unused}"
