"""A comment that states a constant's VALUE must state the value the constant has.

Sibling to `test_comments_name_the_constant_the_code_uses.py`, and deliberately a different signal.
That file records a detector it built and refused to ship: "prose names an UPPER_SNAKE constant it
does not use" cannot tell a deliberate contrast from a misstatement, so its true positive and its
false positive have the same shape, and a gate like that trains people to delete explanations.

A stated NUMBER is not that. `CONSOLE_WORKING_LEASE_SECONDS=20` in a comment is a factual claim with
exactly one correct answer, and when the constant moves the claim is simply false. There is no reading
under which the prose meant something else.

WHY IT MATTERS HERE RATHER THAN AS A TIDINESS RULE. These particular comments are load-bearing for
reasoning about the status path: the four sites below state the two windows that decide whether a
managed agent reads `working` or `online`, and they are written precisely so the next person does not
have to open the Python module to see whether a cadence fits inside a lease. On 2026-08-25 I read the
lease value out of `terminal-runtime.js`'s comment and used it to argue about a live status flap. Had
it been stale, the argument would have been wrong and nothing would have said so.

PINNED AT ZERO, not reported. Every stated value agrees with its constant today, which is what makes
this a gate rather than a finding — the same choice, for the same reason, as the sibling file's
"measured at ZERO when this was written".

Deliberately narrow. It only looks at comment lines, only at `NAME = number` / `NAME is number` /
`NAME currently number`, and only for constants whose real value is a plain integer literal. A
constant declared as an expression (`TURN_BUSY_BACKSTOP_SECONDS = 30 * 60`) is skipped rather than
guessed at, and "NAME was 5" is not matched at all, because a comment recording history is not making
a claim about the present.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

#: Where a constant may be DECLARED, per language.
PY_ROOTS = ("service",)
JS_ROOTS = ("mcp/stdio", "service/new_dashboard")

#: Where prose may CITE one. Tests are excluded: a test naming a value is asserting it, not caching it.
PROSE_ROOTS = ("service", "mcp/stdio")

_PY_CONST = re.compile(r"^([A-Z][A-Z0-9_]{5,})\s*(?::\s*[\w\[\], ]+)?=\s*([0-9][0-9_]*)\s*(?:#.*)?$", re.M)
_JS_CONST = re.compile(r"^(?:export\s+)?const\s+([A-Z][A-Z0-9_]{5,})\s*=\s*([0-9][0-9_]*)\s*;", re.M)
_CLAIM = re.compile(r"([A-Z][A-Z0-9_]{5,})\s*(?:=|is|,\s*currently|\s+currently)\s*([0-9][0-9_]*)")


def _sources(roots, suffixes):
    for root in roots:
        for path in (REPO / root).rglob("*"):
            text = str(path).replace("\\", "/")
            if path.suffix not in suffixes:
                continue
            if any(skip in text for skip in ("node_modules", "__pycache__", "fixtures")):
                continue
            yield path


def _declared_values() -> dict[str, int]:
    values: dict[str, int] = {}
    for path in _sources(PY_ROOTS, {".py"}):
        for match in _PY_CONST.finditer(path.read_text(encoding="utf-8", errors="replace")):
            values.setdefault(match.group(1), int(match.group(2).replace("_", "")))
    for path in _sources(JS_ROOTS, {".js", ".mjs"}):
        for match in _JS_CONST.finditer(path.read_text(encoding="utf-8", errors="replace")):
            values.setdefault(match.group(1), int(match.group(2).replace("_", "")))
    return values


def _claims() -> list[tuple[str, int, str, int, int]]:
    """(file, line, constant, stated, actual) for every comment that states a known constant's value."""
    values = _declared_values()
    found = []
    for path in _sources(PROSE_ROOTS, {".py", ".js", ".mjs"}):
        rel = str(path.relative_to(REPO)).replace("\\", "/")
        if "/tests/" in f"/{rel}":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            stripped = line.strip()
            if not stripped.startswith(("#", "//", "*")):
                continue
            for match in _CLAIM.finditer(line):
                name = match.group(1)
                if name in values:
                    found.append((rel, number, name, int(match.group(2).replace("_", "")), values[name]))
    return found


def test_the_scanner_finds_the_constants_it_needs() -> None:
    """Positive control on the declaration half. Every assertion below is driven by this map, and an
    empty one would make the whole file pass while checking nothing."""
    values = _declared_values()
    assert len(values) >= 50, f"only {len(values)} constants found; the declaration regexes have drifted"
    # Asserted as FOUND and numeric, never as a specific number. Pinning the value here would mean
    # a legitimate change to the lease failed this control as well as the stale-comment test below,
    # and the second failure would say nothing the first did not -- a control that also asserts the
    # subject stops being a control.
    for name in ("CONSOLE_WORKING_LEASE_SECONDS", "TURN_BUSY_STALE_SECONDS"):
        assert isinstance(values.get(name), int), f"{name} was not picked up as an integer constant"


def test_the_scanner_finds_the_prose_it_checks() -> None:
    """Positive control on the prose half, and the one that matters most: a comment regex that quietly
    stopped matching would report zero stale claims for the best possible reason and the worst."""
    claims = _claims()
    assert claims, "no comment states a constant value any more — the prose regex has stopped matching"
    cited = {name for _, _, name, _, _ in claims}
    assert "CONSOLE_WORKING_LEASE_SECONDS" in cited or "TURN_BUSY_STALE_SECONDS" in cited, (
        f"the known status-window comments are no longer found; cited={sorted(cited)}"
    )


def test_no_comment_states_a_value_the_constant_does_not_have() -> None:
    stale = [
        f"{rel}:{line} says {name} is {stated}, but it is {actual}"
        for rel, line, name, stated, actual in _claims()
        if stated != actual
    ]
    assert not stale, "prose is caching a value that has moved:\n  " + "\n  ".join(stale)
