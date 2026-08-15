"""No statement may follow a return/raise/break/continue in the same block.

WHY THIS EXISTS. On 2026-08-16 `service/api_core/send_preflight.py` was found ending with

    await TERMINAL_OUTPUT_WRITES.flush_all()

as the last statement of `_preflight_live_send_recipients`, twenty-seven lines after that function's
`return`. It arrived when `TerminalOutputWriteQueue` and its singleton moved to
`service/terminal_write_queue.py`: the move deleted the class but left one line of a moved function's
body behind, and because comments and blank lines do NOT close a Python block, the orphan landed
inside the function above it rather than at module scope.

NOTHING IN THE REPO COULD SEE IT. It parses, so `py_compile` passes. The name resolves, so the
undefined-name sweep passes. It never executes, so all 2198 tests stay green. It even kept its own
import looking live — `dead_bindings()` counts a name as reached if it appears in a string literal
anywhere under service/, mcp/ or scripts/, and `"TERMINAL_OUTPUT_WRITES"` appears in
`test_single_production_singletons.py`, so the import that only this dead line referenced was never
reported. Four independent checks, none of which measures reachability.

The v0.5.x series moved several hundred declarations between modules. Every one of those moves could
have left a fragment; this gate is what makes the next one a failing test instead of a line nobody
runs. It is the mechanical half of the per-slice receipt's "no stale definition" step, which until
now was a human reading a diff.

SCOPE: non-test `.py`, repo-wide, pruning the same directories as the oversized-source gate. Tests
are excluded deliberately — a test may write unreachable code on purpose to assert something about
it, and this file is itself an example of the shape it forbids.
"""
from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "tests", "fixtures", "__pycache__", ".git", ".venv"}
TERMINATORS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def unreachable_statements(tree: ast.AST) -> list[tuple[int, str]]:
    """Every statement that follows a terminator in the SAME block.

    Pure and separately callable so the tests below can put known-bad source through it rather than
    trusting that a clean repo means a working detector -- an unguarded population reports green
    exactly like a guarded one.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for index, statement in enumerate(block):
                if isinstance(statement, TERMINATORS) and index + 1 < len(block):
                    for dead in block[index + 1:]:
                        found.append((dead.lineno, ast.unparse(dead).splitlines()[0][:80]))
    return found


def _product_sources() -> list[pathlib.Path]:
    return [
        path
        for path in sorted(REPO.rglob("*.py"))
        if not PRUNE & set(path.relative_to(REPO).parts)
    ]


def test_no_product_source_has_an_unreachable_statement():
    offenders: list[str] = []
    for path in _product_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # a genuinely unparseable file is the syntax gate's business, not this one
        for lineno, text in unreachable_statements(tree):
            offenders.append(f"{path.relative_to(REPO).as_posix()}:{lineno}: {text}")

    assert not offenders, (
        "unreachable statement(s) — code after a return/raise/break/continue in the same block.\n"
        "This is what a relocation leaves behind when it deletes a declaration but not every line of\n"
        "its body; comments and blank lines do not close a block, so an orphan joins the function\n"
        "above it. Delete the orphan (and re-check any import that only it referenced).\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_covers_a_real_population():
    """An empty offender list must mean 'checked and clean', not 'checked nothing'."""
    sources = _product_sources()
    # 239 at the time of writing. The floor is well below that because the point is to catch a walk
    # that returns nothing or a handful, not to pin a count that every new module would have to move.
    assert len(sources) > 200, f"only {len(sources)} product .py files found — the walk is broken"
    names = {path.name for path in sources}
    assert "send_preflight.py" in names, "the file this gate was built from is not in scope"
    assert "control_plane.py" in names
    assert "sse_server.py" in names, (
        "mcp/sse_server.py ships in the container and must be governed — the oversized-source gate "
        "read service/** only until 2026-08-15 and left fifteen files ungoverned exactly this way"
    )
    assert not any("tests" in path.relative_to(REPO).parts for path in sources)


def test_the_detector_actually_detects():
    """Anti-vacuity: the exact shape that was missed, plus the shapes that must NOT be flagged."""
    # The real bug, reduced: a statement after `return`, separated by a comment and blank lines.
    missed = ast.parse(
        "async def f():\n"
        "    return 1\n"
        "\n"
        "# a comment does not close the block\n"
        "\n"
        "    await q.flush_all()\n"
    )
    found = unreachable_statements(missed)
    assert len(found) == 1, found
    assert "flush_all" in found[0][1]

    for source in (
        "def f():\n    raise ValueError('x')\n    print('never')\n",
        "def f():\n    for i in x:\n        continue\n        print('never')\n",
        "def f():\n    for i in x:\n        break\n        print('never')\n",
    ):
        assert unreachable_statements(ast.parse(source)), f"missed: {source!r}"

    # A terminator as the LAST statement of its block is the normal case and must stay silent --
    # including one in an `if` arm followed by code at the OUTER level, which is reachable.
    for source in (
        "def f():\n    return 1\n",
        "def f():\n    if x:\n        return 1\n    return 2\n",
        "def f():\n    for i in x:\n        if i:\n            continue\n        print(i)\n",
        "def f():\n    try:\n        return 1\n    finally:\n        cleanup()\n",
        "def f():\n    while x:\n        break\n    print('reachable')\n",
    ):
        assert not unreachable_statements(ast.parse(source)), f"false positive: {source!r}"
