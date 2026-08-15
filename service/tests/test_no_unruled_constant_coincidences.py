"""Two modules holding the SAME set under DIFFERENT names is a ruling waiting to be made.

`test_no_forked_declarations.py` catches a fork by NAME. This gate keys on VALUE instead, which finds
two things that one cannot: a fork under DIFFERENT names, where nothing about `_TERMINAL_END_STATUSES`
in one file suggests going to look for `_SESSION_DELETE_ALLOWED_STATUSES` in another; and a fork in a
module outside that gate's POPULATION, which is `service/api_core/*.py` versus the control plane.

BOTH SHOWED UP ON THE FIRST RUN, which is the evidence a synthetic probe cannot give. The by-name miss
was real: `_NATIVE_MANAGED_RUNTIMES` is declared in `service/api_core/runtime.py` AND in
`service/db.py` — same name, same members — and was invisible because `db.py` is not in the other
gate's population. Its comment claimed it was "kept in sync with ... service/routers/api_v2.py", a file
that has declared nothing since v0.5.4.

The class is not hypothetical. v0.5.4 removed three constants from the control plane that nothing read,
two of them undetected value-forks: `_TERMINAL_DEAD_STATUSES` was character-identical to
`api_core/terminal_status._TERMINAL_END_STATUSES`, and `TERMINAL_DEAD_STATUSES` was a THIRD variant
differing by `exited` versus `completed`. Those were dead, so nothing broke — but a live third variant
is exactly finding N7, where two managed-worker sweeps disagreed about `degraded`.

COINCIDENCE IS NOT IDENTITY, AND THIS GATE DOES NOT SAY MERGE. This repo has already ruled the other
way once, in `service/routers/sessions.py`: `SESSION_CLEAN_HISTORY_STATUSES` is "deliberately NOT the
same set as `_borrowed_session_delete_allowed_statuses()`, and the difference is load-bearing" —
"safe to eventually delete" is not "not worth showing". Collapsing sets because they happen to match
today is how that regression happened. So a group here needs a RULING, not a merge: either these are
one concept and should have one owner, or they are different questions with the same answer today and
must be free to diverge.

WHAT IS NOT REPORTED: a group inside a single module (that is the by-name gate's job), and anything
already ruled below. A new group fails until someone decides which kind it is.
"""

from __future__ import annotations

import ast
import unittest
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

#: The known coincidences: {value: (exact holders, ruling)}. A group listed here has been looked at;
#: a group not listed here has not.
#:
#: THE HOLDERS ARE PART OF THE RULING, and that is not decoration. The first version keyed only on the
#: value, and a probe adding a FOURTH holder of an already-ruled set passed silently — the exemption
#: covered the value forever, for any number of future declarations. A ruling is a judgement about
#: specific places, so it expires the moment the places change: a new holder has not been ruled on and
#: must fail until someone extends the entry.
RULINGS = {
    frozenset({"stopped", "failed", "lost", "ended", "completed", "cancelled"}): ({
        "service/api_core/terminal_status.py:_TERMINAL_END_STATUSES",
        "service/api_core/tuning.py:_SESSION_DELETE_ALLOWED_STATUSES",
        "service/routers/sessions.py:_TERMINAL_DELETE_ALLOWED_STATUSES",
        "service/api_core/agent_sessions.py:ENDED_AGENT_SESSION_STATUSES",
    }, (
        "FOUR QUESTIONS, ONE ANSWER TODAY, deliberately not merged.\n"
        "  api_core/terminal_status._TERMINAL_END_STATUSES  — which terminal statuses mean the\n"
        "      terminal has ENDED. Its ordered twin is derived from it and a test pins the pair.\n"
        "  api_core/tuning._SESSION_DELETE_ALLOWED_STATUSES — which SESSION statuses may be deleted.\n"
        "  routers/sessions._TERMINAL_DELETE_ALLOWED_STATUSES — which TERMINAL rows may be deleted.\n"
        "  api_core/agent_sessions.ENDED_AGENT_SESSION_STATUSES — which AGENT SESSION statuses mean\n"
        "      the session has ended, so a dead row cannot shadow the live one.\n"
        "The fourth was found by pinning holders: three were recorded from an earlier scan and the\n"
        "group was exempt before its membership was ever read, which is the failure this entry now\n"
        "documents twice over.\n"
        "Deleting a row and ending a terminal are different permissions, and the same file already\n"
        "records a regression from treating a coinciding set as the same concept: "
        "SESSION_CLEAN_HISTORY_STATUSES is deliberately narrower than the delete set, because "
        "'safe to eventually delete' is not 'not worth showing'."
    )),
    frozenset({"codex", "pi", "opencode", "hermes"}): ({
        "service/api_core/runtime.py:_NATIVE_MANAGED_RUNTIMES",
        "service/db.py:_NATIVE_MANAGED_RUNTIMES",
    }, (
        "ONE CONCEPT, TWO COPIES, AND THE SECOND CANNOT BECOME AN IMPORT.\n"
        "  api_core/runtime._NATIVE_MANAGED_RUNTIMES — the owner.\n"
        "  db._NATIVE_MANAGED_RUNTIMES — same name, same members, declared as a tuple.\n"
        "`service/db.py` sits BELOW api_core (three api_core modules import it; it imports none), so\n"
        "importing the owner would invert the layering and close a package-level cycle. There is a\n"
        "THIRD copy in `mcp/stdio/dispatch-execution.js`, which cannot import a Python name at all.\n"
        "Governed by content comparison in `test_native_managed_runtimes_parity.py` — the response a\n"
        "duplication finding earns is an agreement test, not a merge."
    )),
}

#: Sets this small carry no information — `{0, 1}` or `{'a'}` coinciding says nothing about design.
MIN_ELEMENTS = 3


def _product_sources():
    for base in ("service", "mcp"):
        root = REPO / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            yield path


def _literal(node: ast.AST):
    """The node's value if it is a plain collection literal, else None."""
    if not isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return None
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    if not all(isinstance(v, (str, int, float, bool)) for v in value):
        return None
    return frozenset(value)


def constant_groups() -> dict[frozenset, list[tuple[str, str, int]]]:
    """{value: [(module, name, line)]} for every top-level collection constant."""
    groups: dict[frozenset, list[tuple[str, str, int]]] = defaultdict(list)
    for path in _product_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - another test's failure
            continue
        relative = path.relative_to(REPO).as_posix()
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            value = _literal(node.value)
            if value is None or len(value) < MIN_ELEMENTS:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    groups[value].append((relative, target.id, node.lineno))
    return groups


def holders(places: list[tuple[str, str, int]]) -> set[str]:
    """`module:NAME` per declaration — the identity a ruling is made about."""
    return {f"{module}:{name}" for module, name, _ in places}


def unruled() -> list[tuple[frozenset, list[tuple[str, str, int]]]]:
    """Cross-module value groups that no ruling covers, INCLUDING ones whose holders have changed."""
    out = []
    for value, places in constant_groups().items():
        if len({module for module, _, _ in places}) < 2:
            continue
        ruled = RULINGS.get(value)
        if ruled and holders(places) == ruled[0]:
            continue
        out.append((value, places))
    return out


class NoUnruledConstantCoincidencesTests(unittest.TestCase):
    def test_every_cross_module_value_match_has_a_ruling(self):
        offenders = unruled()
        self.assertEqual(
            [], offenders,
            "these constants hold the SAME value in different modules under different names. That is "
            "either one concept with two owners, or two concepts that agree today and must be free to "
            "diverge — decide which, and record it in RULINGS:\n  "
            + "\n  ".join(
                f"{sorted(v)}\n      " + "\n      ".join(f"{m}:{l} {n}" for m, n, l in places)
                for v, places in offenders
            ),
        )

    def test_the_scan_finds_a_plausible_number_of_constants(self):
        """Anti-vacuity: a detector that parsed nothing would report no coincidences forever.

        The floor is set BELOW the measured count (19 groups across 15 modules when this was written),
        not at it — a floor pinned to the exact reading fails on the next honest constant added or
        removed, and a gate that cries wolf gets its number raised rather than read. What it has to
        catch is a scan that collapsed to nothing, which is the way this fails silently.
        """
        groups = constant_groups()
        self.assertGreater(len(groups), 10, f"only {len(groups)} collection constants found")
        modules = {m for places in groups.values() for m, _, _ in places}
        self.assertGreater(len(modules), 8, "the scan should span many modules")

    def test_the_recorded_coincidence_is_STILL_a_coincidence(self):
        """A stale ruling is worse than none: it silently exempts a group that no longer exists.

        If a ruled group ever stops matching — which the status sets are explicitly allowed to do — the
        ruling has served its purpose and should be deleted rather than left exempting nothing.
        """
        groups = constant_groups()
        for value in RULINGS:
            places = groups.get(value, [])
            self.assertGreaterEqual(
                len({m for m, _, _ in places}), 2,
                f"the ruling for {sorted(value)} exempts a group that no longer spans two modules; "
                "delete the entry",
            )

    def test_every_ruling_names_the_holders_it_actually_covers(self):
        """The recorded holders must BE the live ones, or the entry is ruling on something else."""
        groups = constant_groups()
        for value, (recorded, _reason) in RULINGS.items():
            self.assertEqual(
                recorded, holders(groups.get(value, [])),
                f"the ruling for {sorted(value)} names holders that are not the live ones",
            )

    def test_a_NEW_holder_of_an_already_ruled_value_is_still_reported(self):
        """The hole the first version had: an exemption keyed only on the value never expires.

        A ruling is a judgement about specific declarations. A fourth file joining a ruled group has
        not been judged, and inheriting the exemption is how an allowlist quietly stops meaning
        anything. Asserted on the predicate directly — a probe file would have to be added to the tree
        and removed again, and a mutation that shares a process with its own assertion is how this
        suite has produced a false PASS before.
        """
        value, (recorded, _reason) = next(iter(RULINGS.items()))
        joined = [(h.split(":")[0], h.split(":")[1], 1) for h in recorded]
        joined.append(("service/some_new_module.py", "_COPIED_STATUSES", 1))
        self.assertNotEqual(recorded, holders(joined), "a new holder must change the holder set")

    def test_the_detector_reports_a_SYNTHETIC_coincidence(self):
        """Scanning a clean tree can never prove the rule fires. A broken detector looks identical."""
        first = ast.parse("A = {'x', 'y', 'z'}\n")
        second = ast.parse("B = {'z', 'y', 'x'}\n")
        values = [
            _literal(n.value) for tree in (first, second) for n in tree.body
            if isinstance(n, ast.Assign)
        ]
        self.assertEqual(2, len(values))
        self.assertEqual(values[0], values[1], "order must not affect the grouping")
        self.assertIsNone(_literal(ast.parse("C = compute()\n").body[0].value),
                          "a computed value is not a literal and must not be grouped")

    def test_small_sets_are_not_reported(self):
        """`{0, 1}` coinciding says nothing, and reporting it would train people to ignore this."""
        tiny = _literal(ast.parse("A = {'a', 'b'}\n").body[0].value)
        self.assertIsNotNone(tiny)
        self.assertLess(len(tiny), MIN_ELEMENTS, "the floor must exclude a two-element set")


if __name__ == "__main__":
    unittest.main()
