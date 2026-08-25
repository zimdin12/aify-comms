"""Two columns spell a session's mode differently, and the normaliser maps one onto the wrong answer.

    agents.session_mode        'managed' | 'resident'
    agent_sessions.mode        'managed-warm' | 'resident'
    spawn_requests.mode        'managed-warm'

`_normalize_session_mode` keeps a value only if it is in `_SESSION_MODES` — which is
{'managed', 'resident'} — and returns 'resident' for anything else. So:

    _normalize_session_mode('managed-warm')  ->  'resident'

the OPPOSITE of what that row says. Feed it a value from the session or spawn tables and a managed
agent reads as resident, which decides whether the service cold-starts a worker, whether a dispatch is
claimable, and whether a stop is honoured.

NOTHING DOES THAT TODAY, and this file exists to keep it that way. Every call site was enumerated
2026-08-25: all but one pass `session_mode` (the coarse column), and the exception —
`session_mode.py`, which takes a mode straight from an HTTP body — validates against `_SESSION_MODES`
first and answers 400. The safety is real and entirely implicit: nothing states that the fine-grained
vocabulary must never reach the normaliser, and the two spellings are one hyphen apart.

The sibling case on the bridge side is a KNOWN OPEN ISSUE awaiting an operator ruling: a managed shell
can convert its agent to resident because the JS `normalizeSessionMode` fails toward 'resident' in the
same way. That is not fixed here, and this file does not pretend to cover it.

Measured while checking: of 290 sessions joined to their agent, 24 are managed sessions under an agent
now marked resident — every one of them 'ended' or 'stopped'. That is history, not disagreement:
agents switch mode and old sessions stay. Recorded because it looks alarming in a GROUP BY and is not.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.api_core.runtime import _SESSION_MODES, _normalize_session_mode

ROOT = Path(__file__).resolve().parents[2]
MODE_ROUTER = ROOT / "service" / "routers" / "agents" / "session_mode.py"


class SessionModeVocabulariesStayApart(unittest.TestCase):
    def test_the_coarse_vocabulary_is_what_the_normaliser_knows(self):
        self.assertEqual(set(_SESSION_MODES), {"managed", "resident"})
        self.assertEqual(_normalize_session_mode("managed"), "managed")
        self.assertEqual(_normalize_session_mode("resident"), "resident")

    def test_the_fine_grained_spelling_normalises_to_the_WRONG_answer(self):
        """Stated as a fact about the function, not a complaint about it.

        Failing toward 'resident' is deliberate — an unknown mode must not be treated as managed and
        cold-start a worker. The hazard is that 'managed-warm' is not unknown, it is the same concept
        spelled longer, and the safe default is the wrong answer for it."""
        for spelling in ("managed-warm", "managed-wrapper-child", "MANAGED-WARM"):
            self.assertEqual(
                _normalize_session_mode(spelling), "resident",
                f"{spelling} no longer folds to resident; re-read every call site before relying on it",
            )

    def test_an_absent_mode_still_fails_toward_resident(self):
        for value in ("", None, "  ", "nonsense"):
            self.assertEqual(_normalize_session_mode(value), "resident")

    def test_the_one_route_that_takes_a_raw_mode_validates_before_using_it(self):
        """The single call site that does not read the coarse column.

        Parsed rather than grepped, so a comment mentioning the check cannot satisfy it. If this route
        stops validating, `mode: "managed-warm"` in a request body silently converts a managed agent
        to resident instead of answering 400."""
        tree = ast.parse(MODE_ROUTER.read_text(encoding="utf-8"))
        source = MODE_ROUTER.read_text(encoding="utf-8")
        self.assertIn(
            "_normalize_session_mode(req.mode)", source,
            "the route stopped normalising a raw mode; this file's premise changed",
        )
        compares = [
            ast.unparse(node) for node in ast.walk(tree)
            if isinstance(node, ast.Compare) and "_SESSION_MODES" in ast.unparse(node)
        ]
        self.assertTrue(
            compares,
            "the route no longer checks the raw mode against _SESSION_MODES, so a fine-grained "
            "spelling would be coerced to resident instead of refused",
        )

    def test_no_service_module_normalises_a_session_or_spawn_mode_column(self):
        """The rule the safety actually rests on: the normaliser sees `session_mode`, never `mode`.

        Scans the arguments rather than the file, so a call passing `row["mode"]` or `spec["mode"]`
        fails here even in a module nobody thought to check."""
        offenders = []
        for path in (ROOT / "service").rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name not in {"_normalize_session_mode", "normalize_session_mode"}:
                    continue
                arg = ast.unparse(node.args[0]) if node.args else ""
                if "session_mode" in arg or "sessionMode" in arg:
                    continue
                if "req.mode" in arg:          # the validated route above
                    continue
                offenders.append(f"{path.name}: {arg[:70]}")
        self.assertEqual(
            offenders, [],
            "these normalise something other than a session_mode column, and 'managed-warm' folds to "
            "'resident': " + "; ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
