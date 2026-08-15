"""A local bound under one `if` and read under a DIFFERENT one — safe only by implication.

FOUR EXIST, ALL SAFE TODAY, AND NONE OF THEM SAYS SO. The shape is:

    if A:
        x = ...
    ...
    if B:
        use(x)          # fine only while B implies A

Python does not check it and neither did anything here. Each site is correct because the second
condition cannot hold unless the first did, but that implication lives thirty lines apart in the
reader's head — and if an edit ever breaks it, the failure is an UnboundLocalError raised from a route
handler, in production, on the path nobody exercised.

THIS IS NOT HYPOTHETICAL. v0.5.4 introduced exactly this defect while extracting a block out of
`send_message`: `prefer_steer` was hoisted to a call site where it was not yet bound, 48 tests went
red and the route returned 500. That produced the extract-method gate's conditionally-bound-argument
rule, which refuses the shape in a SPLIT. This is its counterpart for code that was written that way
to begin with.

WHAT IT DOES NOT DO: evaluate conditions. It cannot know that `dispatch_recipients` being non-empty
implies `recipients` was, so it cannot certify a site as safe — only notice one and demand a ruling.
That is the honest direction: the alternative is a checker that silently blesses the case it cannot
analyse.

THE COUNT WENT 86 -> 33 -> 4 BEFORE IT WAS WORTH READING, and the two narrowings are the interesting
part. Treating `with ... as x` and `try:` bodies as conditional produced dozens of false positives —
a `with` item binds whenever the `with` is reached. And an `if` WITH an `else` binds whatever BOTH
arms bind; without that carve-out this reports `register_agent`'s `description_value`, which is the
same false positive the extract-method gate had to fix, for the same reason.
"""

from __future__ import annotations

import ast
import builtins
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

#: Each known site, with why it is safe. A site NOT listed here has not been reasoned about.
RULINGS = {
    ("service/api_core/status_inputs.py", "_gather_status_inputs", "env_reachable"): (
        "Bound under `if mode == \"managed\":` and read under a SECOND `if mode == \"managed\":` a few "
        "lines later. The two conditions are textually identical, so the second block runs exactly "
        "when the first did. The safest of the four, and the only one where the implication is "
        "visible without reading the surrounding derivations."
    ),
    ("service/routers/dispatch_messages/messages.py", "send_message", "prefer_steer"): (
        "Bound inside `if req.trigger:` and read inside a later guard that only holds when a trigger "
        "was requested. This is the exact name and shape that broke the suite when a v0.5.4 "
        "extraction hoisted it to an unguarded call site."
    ),
    ("service/routers/dispatch_messages/messages.py", "send_message", "settings"): (
        "Bound on the trigger path and read on the dispatch-run path, which is reached only from it."
    ),
    ("service/routers/channel_send.py", "send_channel_message", "prefer_steer"): (
        "Bound inside `if should_trigger and recipients:` and read inside `if should_trigger and "
        "dispatch_recipients:`. Safe because `dispatch_recipients` is DERIVED from `recipients`, so "
        "an empty `recipients` makes both conditions false, and `should_trigger` gates both. That "
        "implication spans thirty lines and two derivations — it is why the coldstart extraction in "
        "this file deliberately stops short of the block that reads this name."
    ),
}


def _walk_scoped(node, path=()):
    """(child, enclosing `if` bodies) — not entering nested defs, which have their own scope."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        deeper = path + (node,) if isinstance(node, ast.If) else path
        yield child, deeper
        yield from _walk_scoped(child, deeper)


def _stored_in(statements) -> set[str]:
    return {
        n.id
        for stmt in statements
        for n in ast.walk(stmt)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }


def unruled_reads(fn) -> list[str]:
    """Names read where no binding's guard path encloses the read."""
    safe = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
    if fn.args.vararg:
        safe.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        safe.add(fn.args.kwarg.arg)

    bindings: dict[str, list[tuple]] = {}
    reads: list[tuple[str, tuple]] = []
    for node, guards in _walk_scoped(fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            safe.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            safe.update(node.names)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in node.generators:
                safe.update(n.id for n in ast.walk(gen.target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bindings.setdefault(node.name, []).append(guards)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bindings.setdefault(node.id, []).append(guards)
            else:
                reads.append((node.id, guards))

    # An `if` with an `else` binds whatever BOTH arms bind.
    for node, guards in _walk_scoped(fn):
        if isinstance(node, ast.If) and node.orelse:
            for name in _stored_in(node.body) & _stored_in(node.orelse):
                bindings.setdefault(name, []).append(guards)

    out = set()
    for name, read_guards in reads:
        if name in safe or hasattr(builtins, name) or name not in bindings:
            continue
        if any(read_guards[: len(g)] == g for g in bindings[name]):
            continue
        out.add(name)
    return sorted(out)


def sites() -> dict[tuple[str, str, str], None]:
    found = {}
    for base in ("service", "mcp"):
        root = REPO / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            parts = path.parts
            if "__pycache__" in parts or "tests" in parts or "node_modules" in parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            rel = path.relative_to(REPO).as_posix()
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for name in unruled_reads(fn):
                        found[(rel, fn.name, name)] = None
    return found


class NoUnruledConditionalBindingsTests(unittest.TestCase):
    def test_every_conditional_binding_has_a_ruling(self):
        found = sites()
        new = sorted(k for k in found if k not in RULINGS)
        self.assertEqual(
            [], new,
            "a local is read where no binding dominates the read. It is safe ONLY if the guard on the "
            "read cannot hold unless the guard on the binding did — say why in RULINGS, or bind the "
            "name before the branch:\n  "
            + "\n  ".join(f"{f}:{fn}() -> {n}" for f, fn, n in new),
        )

    def test_no_ruling_describes_a_site_that_is_gone(self):
        """A stale ruling exempts nothing and hides that the shape was fixed."""
        found = sites()
        for key in RULINGS:
            self.assertIn(
                key, found,
                f"{key} no longer has a conditional binding — delete the ruling rather than leave it "
                "exempting a site that does not exist",
            )

    def test_the_scan_is_not_vacuous(self):
        """A detector that stopped parsing would report no sites forever."""
        self.assertGreaterEqual(len(sites()), len(RULINGS))
        probe = ast.parse(
            "def f(flag):\n"
            "    if flag:\n"
            "        x = 1\n"
            "    if not flag:\n"
            "        return x\n"
        ).body[0]
        self.assertEqual(["x"], unruled_reads(probe), "the detector must flag the basic shape")

    def test_a_binding_that_DOMINATES_is_not_reported(self):
        probe = ast.parse(
            "def f(flag):\n"
            "    x = 0\n"
            "    if flag:\n"
            "        x = 1\n"
            "    return x\n"
        ).body[0]
        self.assertEqual([], unruled_reads(probe))

    def test_an_if_with_an_else_binds_what_BOTH_arms_bind(self):
        """Without this the scan reports `register_agent`'s `description_value` — 33 hits, not 4."""
        probe = ast.parse(
            "def f(flag):\n"
            "    if flag:\n"
            "        x = 1\n"
            "    else:\n"
            "        x = 2\n"
            "    if flag:\n"
            "        return x\n"
        ).body[0]
        self.assertEqual([], unruled_reads(probe))

    def test_a_with_item_binds_unconditionally(self):
        """Treating `with ... as x` as conditional was most of the 86 the first version reported."""
        probe = ast.parse(
            "def f(ctx):\n"
            "    with ctx as handle:\n"
            "        return handle\n"
        ).body[0]
        self.assertEqual([], unruled_reads(probe))


if __name__ == "__main__":
    unittest.main()
