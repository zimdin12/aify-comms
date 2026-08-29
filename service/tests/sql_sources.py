r"""Read the SQL a module actually issues, f-strings included.

WHY THIS EXISTS. On 2026-08-29 three source-scanning gates in this suite disagreed with reality for
the same reason: an f-string is not an `ast.Constant`, and on Python 3.14 it is not a
`tokenize.STRING` either. A scanner keyed on either sees an f-string's literal PIECES -- split at
every interpolation -- or nothing at all.

That produced two false greens in one afternoon. `test_a_live_terminal_query_excludes_synthetic_rows`
reported "0 of 0 live-terminal queries", and `test_terminal_sql_compares_terminal_statuses` reported
that `recovering` was no longer used by any filter, which was false: twelve filters still used it and
only the text had moved. Both said so through their own positive controls, which is the only reason
either was noticed.

MEASURED, so the size of the blind spot is on the record: this reader finds 854 SQL statements
written as string literals in `service/`, and 123 of them carry an interpolation. Better than one in
eight, invisible to a scan keyed on `ast.Constant`.

WHAT THIS DOES NOT DO. It renders an interpolation as `{name}` rather than resolving it, except for
the status fragments a caller asks it to resolve. Resolving arbitrary expressions would mean
evaluating them, and a scanner that imports product modules to read a query is a different kind of
hazard from the one it fixes.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterator

#: A keyword at the START of a line, not anywhere in the text. The first version of this predicate
#: asked whether the text CONTAINED "UPDATE ", so `f"Buffered update from {sender}: {subject}"` was
#: classified as SQL and its two interpolations reported as values spliced into a statement. Every
#: alarming name in that first census came from prose.
SQL_STATEMENT = re.compile(
    r"^\s*(SELECT|INSERT\s+INTO|INSERT\s+OR|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|CREATE\s+INDEX|"
    r"WITH|PRAGMA)\b",
    re.IGNORECASE | re.MULTILINE,
)

SKIP_DIRS = {"node_modules", "tests", "fixtures", "__pycache__", ".git", ".pytest_cache",
             ".venv", "venv", "data", "new_dashboard"}


#: A statement keyword at the start of a line is necessary and NOT sufficient: "select a runtime to
#: continue" opens with one. A second clause keyword has to appear as well, which every real
#: statement in this tree has and ordinary prose does not.
SQL_CLAUSE = re.compile(r"\b(FROM|INTO|SET|VALUES|WHERE|TABLE|INDEX|JOIN)\b", re.IGNORECASE)


def looks_like_sql(text: str) -> bool:
    """True when a statement keyword opens a line AND a clause keyword follows somewhere.

    A BARE PROJECTION IS OUT OF SCOPE, deliberately. "SELECT 1" is valid SQL and so is
    "select a runtime to continue" as far as this predicate can tell; nothing in this tree issues
    the former, so the boundary costs nothing and keeps prose out. PRAGMA is exempt from the clause
    rule because "PRAGMA table_info(agents)" has no clause and is unmistakably a statement.
    """
    if not SQL_STATEMENT.search(text):
        return False
    if re.match(r"^\s*PRAGMA\b", text, re.IGNORECASE):
        return True
    return bool(SQL_CLAUSE.search(text))


def literal_text(node: ast.AST, resolve: dict[str, str] | None = None) -> str:
    """The text of a string literal, joining an f-string's pieces around its interpolations.

    `resolve` maps a NAME to the text it stands for -- used for the status fragments in
    `api_core/terminal_status.py`, so a filter that interpolates one is still read as naming its
    members. Anything unresolved renders as `{name}`, which keeps the statement readable and makes
    the interpolation visible rather than silently dropping it.
    """
    resolve = resolve or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        rendered = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                rendered.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                names = [n.id for n in ast.walk(part.value) if isinstance(n, ast.Name)]
                resolved = next((resolve[n] for n in names if n in resolve), None)
                rendered.append(resolved if resolved is not None
                                else "{" + (names[0] if names else "?") + "}")
        return "".join(rendered)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts, stack = [], [node]
        while stack:
            current = stack.pop()
            if isinstance(current, ast.BinOp) and isinstance(current.op, ast.Add):
                stack.extend([current.right, current.left])
            else:
                parts.append(literal_text(current, resolve))
        return "".join(parts)
    return ""


def sql_literals(root: Path, resolve: dict[str, str] | None = None
                 ) -> Iterator[tuple[Path, int, str]]:
    """(path, line, text) for every SQL statement literal under `root`, f-strings included."""
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - a file that does not parse has no queries to read
            continue
        # A JoinedStr'"'"'s literal PIECES are Constant nodes too, and yielding them alongside the whole
        # f-string double-counts every interpolated query -- and yields "UPDATE " on its own, which
        # reads as a statement with no clause. Collect the f-strings first and skip their children.
        inside_fstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for part in node.values:
                    if isinstance(part, ast.Constant):
                        inside_fstrings.add(id(part))
        seen: set[tuple[int, str]] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Constant, ast.JoinedStr)):
                continue
            if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                continue
            if id(node) in inside_fstrings:
                continue
            text = literal_text(node, resolve)
            if not looks_like_sql(text):
                continue
            key = (node.lineno, text[:120])
            if key in seen:
                continue
            seen.add(key)
            yield path, node.lineno, text


def status_fragment_resolutions() -> dict[str, str]:
    """The rendered status fragments, read from the module that owns them rather than re-listed."""
    from service.api_core import terminal_status

    return {
        name: getattr(terminal_status, name)
        for name in dir(terminal_status)
        # `_SQL`, not `_STATUS_SQL`: the narrower suffix misses `TERMINAL_LIVE_FILTER_SQL`, which
        # is the fragment ELEVEN of the twelve filters use. An earlier copy of this resolution had
        # that bug and its gate passed anyway -- one remaining filter used the stoppable fragment,
        # so the literal it looks for still appeared once and the ledger check was satisfied by a
        # thirteenth of its population.
        if name.endswith("_SQL") and isinstance(getattr(terminal_status, name), str)
    }
