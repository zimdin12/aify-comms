"""Every spawn request the service writes is written by `definition_start.insert_spawn_request` (P0 C7).

That function's INSERT carries the WHERE that asks again whether the agent is defined or withdrawn, so a
start whose binding read was overtaken by a push or a withdrawal queues nothing. A start written by its
own INSERT skips that question: review of 8de83233 (N6) found the undefined old-spec restart doing that.

WHAT THIS GATE ENFORCES, AND WHAT IT CANNOT SEE (review of 2662ceaa, N7). It parses every product
source file under `service/` (walked on disk, so an untracked file is included) and reads its string
literals from the syntax tree, so adjacent literals arrive concatenated and an f-string contributes its
literal parts. A literal is an insert into this table when it holds `INSERT [OR <action>] INTO` before
`spawn_requests`, bare or quoted ("", ``, []). Each one is bound to the function that holds it, and the
only one allowed is in `insert_spawn_request`, exactly once. SQL assembled at run time (with `+`,
`join`, or a formatted table name) is outside this grammar; the census in review of 2662ceaa found
none in the tree.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]
RAW_INSERT = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+[\"`\[]?spawn_requests[\"`\]]?(?![\w])", re.IGNORECASE)
ALLOWED = [("api_core/definition_start.py", "insert_spawn_request")]


def inserts_in(source: str, name: str) -> list[tuple[str, str]]:
    """(file, enclosing function) for every string literal in `source` that inserts into spawn_requests."""
    found: list[tuple[str, str]] = []

    def visit(node, function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and RAW_INSERT.search(node.value):
            found.append((name, function))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source), "<module>")
    return found


def product_inserts() -> list[tuple[str, str]]:
    found = []
    for path in sorted(SERVICE.rglob("*.py")):
        relative = path.relative_to(SERVICE)
        if "tests" not in relative.parts:
            found += inserts_in(path.read_text(encoding="utf-8"), relative.as_posix())
    return found


class EveryStartGoesThroughTheGuardedInsert(unittest.TestCase):
    def test_the_grammar_sees_the_forms_an_insert_takes(self):
        """Each specimen is a false green the first version of this gate passed (review of 2662ceaa)."""
        specimens = {
            "a plain insert": 'async def w(db):\n    await db.execute("INSERT INTO spawn_requests (id) VALUES (?)", ("x",))\n',
            "a quoted table": "async def w(db):\n    await db.execute('INSERT INTO \"spawn_requests\" (id) VALUES (?)')\n",
            "adjacent literals": 'async def w(db):\n    await db.execute("INSERT INTO " "spawn_requests (id) VALUES (?)")\n',
            "an f-string": 'async def w(db, c):\n    await db.execute(f"INSERT OR IGNORE INTO spawn_requests ({c}) VALUES (?)")\n',
            "bracket quoting": 'def w():\n    return "insert into [spawn_requests] (id) values (?)"\n',
        }
        for label, source in specimens.items():
            with self.subTest(label):
                self.assertEqual(inserts_in(source, "x.py"), [("x.py", "w")])
        self.assertEqual(inserts_in('X = "INSERT INTO spawn_requests_archive (id) VALUES (?)"\n', "x.py"), [],
                         "another table is not this one")

    def test_a_second_writer_beside_the_allowed_one_is_its_own_finding(self):
        allowed = (SERVICE / ALLOWED[0][0]).read_text(encoding="utf-8")
        sibling = allowed + '\n\nasync def sneak(db):\n    await db.execute("INSERT INTO spawn_requests (id) VALUES (?)", ("x",))\n'
        self.assertEqual(inserts_in(sibling, ALLOWED[0][0]), ALLOWED + [(ALLOWED[0][0], "sneak")])

    def test_only_the_guarded_insert_writes_a_spawn_request(self):
        self.assertEqual(product_inserts(), ALLOWED)
