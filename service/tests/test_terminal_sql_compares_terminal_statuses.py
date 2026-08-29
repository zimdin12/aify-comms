"""SQL that filters `terminal_sessions.status` compares it against terminal statuses.

A status literal in a `WHERE ... IN (...)` is a claim about what the column can hold. When the column
cannot hold it, the clause is inert -- it costs nothing at runtime and misleads every reader, because
the SQL says "we handle this state" while the write path says the state cannot exist.

MEASURED 2026-08-26 by parsing every SQL statement in `service/` that filters a status column, mapping
each comparison to its table through the FROM/JOIN aliases: 11 clauses compare
`terminal_sessions.status` against `recovering`, and `recovering` is not in
`TERMINAL_SESSION_STATUSES`. `_terminal_status_transition` REFUSES any status outside that set and
returns "", so nothing can put it there, and nothing does.

WHY THIS IS A TEST AND NOT A DELETION. Removing the literal from 11 clauses changes no behaviour and
carries the risk of being wrong about one of them. The trap worth closing is the reader who sees
`recovering` in a terminal filter, concludes a terminal can be recovering, writes it -- and has the
write SILENTLY DROPPED by the allowlist, because the transition returns "" rather than raising.

`recovering` IS A REAL STATUS, of the other table. `_LIVE_SESSION_STATUSES` holds
{cli-takeover, recovering, restarting, running, starting} and those are `agent_sessions.status`
values; most `recovering` comparisons in the tree are on that column and are entirely correct. The
operator ruled on this member once already, in `44299eb6`: "`recovering` is live but not active, on
purpose -- do not unify them", with the two failure modes of unifying spelled out. Nothing here
unifies anything. This pins the boundary between the two vocabularies so a NEW foreign literal fails
instead of arriving quietly, and so the count cannot drift unnoticed in either direction.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from service.api_core.terminal_status import TERMINAL_SESSION_STATUSES

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv", "tests"}

#: Literals that filter `terminal_sessions.status` while not being terminal statuses, with the reason
#: each is tolerated. An entry here is a DECLARATION that the clause is inert, not permission for a
#: new one -- anything unlisted fails, and a listed value that stops appearing fails too, so the
#: declaration cannot rot into a name nobody checks.
FOREIGN_LITERALS = {
    "recovering": (
        "an agent_sessions status (_LIVE_SESSION_STATUSES), not a terminal one. Ruled deliberate in "
        "44299eb6 for the SET definitions; these terminal filters are inert because "
        "_terminal_status_transition refuses any status outside TERMINAL_SESSION_STATUSES."
    ),
}


from service.tests.sql_sources import literal_text, status_fragment_resolutions


def _sql_text(node: ast.AST) -> str:
    """One owner for "what SQL does this literal say", shared with the other query-scanning gates.

    This file grew its own f-string handling on 2026-08-29, minutes after
    `test_a_live_terminal_query_excludes_synthetic_rows` grew a different copy of the same thing --
    two answers to one question, written the same afternoon, which is the duplication these gates
    exist to catch in product code. `service/tests/sql_sources.py` owns it now, and its own tests
    prove it reads an f-string whole and resolves the status fragments.
    """
    return literal_text(node, status_fragment_resolutions())


def _alias_map(sql: str) -> dict[str, str]:
    """Alias -> table, from every FROM/JOIN clause, so a qualified column can be attributed."""
    aliases: dict[str, str] = {}
    # UPDATE and INSERT name their target WITHOUT a FROM, and missing them is not a small gap: an
    # `UPDATE terminal_controls` whose subquery mentions terminal_sessions left this map holding only
    # the subquery's table, so the statement's own unqualified `status` -- a terminal_controls value --
    # was attributed to the terminal vocabulary and reported as foreign.
    for target in re.findall(r"\b(?:UPDATE|INSERT\s+INTO)\s+([a-z_]+)", sql, re.I):
        aliases.setdefault(target.lower(), target.lower())
    for table, alias in re.findall(
        r"\b(?:FROM|JOIN)\s+([a-z_]+)(?:\s+(?:AS\s+)?([a-z_][\w]*))?", sql, re.I
    ):
        if alias and alias.upper() not in {"ON", "WHERE", "LEFT", "INNER", "JOIN", "GROUP", "ORDER", "SET"}:
            aliases[alias.lower()] = table.lower()
        aliases.setdefault(table.lower(), table.lower())
    return aliases


def terminal_status_literals() -> dict[str, set[str]]:
    """Every literal compared against `terminal_sessions.status`, by file.

    Attribution is by QUALIFIER. An unqualified `status` counts only when the statement names exactly
    one table -- in a join it is ambiguous, and guessing there is how a scan starts reporting another
    table's vocabulary as this one's.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(REPO.joinpath("service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            sql = _sql_text(node.args[0])
            if not sql or "terminal_sessions" not in sql.lower():
                continue
            aliases = _alias_map(sql)
            tables = set(aliases.values())
            for qualifier, values in re.finditer(
                r"([a-z_][\w]*)?\.?\bstatus\s*(?:=|IN)\s*\(?\s*((?:'[a-z_-]+'\s*,?\s*)+)\)?", sql, re.I
            ) and [(m.group(1), m.group(2)) for m in re.finditer(
                r"(?:([a-z_][\w]*)\.)?\bstatus\s*(?:=|IN)\s*\(?\s*((?:'[a-z_-]+'\s*,?\s*)+)\)?", sql, re.I
            )]:
                if qualifier:
                    if aliases.get(qualifier.lower()) != "terminal_sessions":
                        continue
                elif tables != {"terminal_sessions"}:
                    continue
                for literal in re.findall(r"'([a-z_-]+)'", values):
                    found.setdefault(rel.as_posix(), set()).add(literal.lower())
    return found


class TerminalSqlComparesTerminalStatusesTests(unittest.TestCase):
    def test_the_scan_finds_terminal_status_filters_at_all(self) -> None:
        """Positive control. Every assertion below is about what the scan FOUND, and an empty scan
        satisfies "no unknown literals" while proving nothing -- the shape this repo keeps paying for."""
        literals = terminal_status_literals()
        self.assertGreaterEqual(
            len(literals), 5, f"only {len(literals)} files filter terminal_sessions.status; scan broken"
        )
        every = set().union(*literals.values())
        self.assertIn("attached", every, "the scan missed a literal that certainly exists")
        self.assertIn("stopped", every, "the scan missed a literal that certainly exists")

    def test_the_scan_attributes_by_qualifier_rather_than_by_guessing(self) -> None:
        """The negative control. `recovering` is overwhelmingly an agent_sessions status, so a scan
        that ignored qualifiers would drag every session filter in here and report a landslide."""
        literals = terminal_status_literals()
        self.assertNotIn(
            "service/api_core/agent_sessions.py", literals,
            "a pure agent_sessions file was attributed to the terminal vocabulary; the scan is "
            "matching another table's column",
        )

    def test_every_literal_is_a_terminal_status_or_a_declared_foreign_one(self) -> None:
        offenders: dict[str, set[str]] = {}
        for rel, values in terminal_status_literals().items():
            unknown = {v for v in values if v not in TERMINAL_SESSION_STATUSES and v not in FOREIGN_LITERALS}
            if unknown:
                offenders[rel] = unknown
        self.assertEqual(
            offenders, {},
            "a terminal-status filter compares against a literal the column cannot hold. The clause "
            "is inert, and it tells the next reader the state exists -- who may then write it and "
            "have the write silently dropped by _terminal_status_transition. Either add the literal "
            "to TERMINAL_SESSION_STATUSES or declare it in FOREIGN_LITERALS with the reason.",
        )

    def test_each_declared_foreign_literal_still_appears(self) -> None:
        """A declaration for a literal that has gone is a decision about nothing, and it hides that
        the tolerated clause may have been rewritten. The list shrinks honestly or not at all."""
        every = set().union(*terminal_status_literals().values())
        for literal in FOREIGN_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(
                    literal, every,
                    f"{literal} is declared foreign but no terminal-status filter uses it any more; "
                    "drop the declaration",
                )

    def test_every_declaration_carries_a_reason(self) -> None:
        """A bare name with no argument is how a tolerated list turns into an unchecked one."""
        for literal, reason in FOREIGN_LITERALS.items():
            with self.subTest(literal=literal):
                self.assertGreater(len(reason), 80, f"{literal} is tolerated without a real reason")

    def test_the_declared_foreign_literal_really_cannot_be_stored(self) -> None:
        """The claim that makes the clause inert, asserted rather than assumed. If the transition ever
        starts accepting one of these, the clause becomes live and the declaration is wrong."""
        from service.api_core.terminal_status import _terminal_status_transition

        for literal in FOREIGN_LITERALS:
            with self.subTest(literal=literal):
                self.assertEqual(
                    _terminal_status_transition("attached", literal), "",
                    f"{literal} is now an accepted terminal status, so the filters using it are live "
                    "and it belongs in TERMINAL_SESSION_STATUSES rather than in FOREIGN_LITERALS",
                )


if __name__ == "__main__":
    unittest.main()
