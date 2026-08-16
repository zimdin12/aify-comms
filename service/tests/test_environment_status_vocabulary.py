"""`environments.status` had no owner — the only complete statement of it was in JavaScript.

Three sites write that column and one derives it, and the set of values they can produce was written
down in exactly two places: the docstring of `environment_effective_status` ("only ever written
`online|degraded|offline` by a registration, plus `forgotten`/`disabled` server-side"), and
`ENV_KNOWN_STATES` in `mcp/stdio/doctor-predicates.js`, which reports any status outside it as
unrecognised. Prose is not read by the suite, so the complete version lived in a file a Python change
could not break — and doctor is the tool whose whole purpose is to not report a false green.

WHY AN UNDECLARED VALUE WOULD BE WORSE THAN A WRONG ONE. `environment_effective_status` ages a silent
bridge to `offline` only when the stored status is in `_ENVIRONMENT_HEARTBEAT_STATUSES`; everything
else is returned untouched, deliberately, because `forgotten`/`disabled` are decisions rather than
observations. A status outside the vocabulary therefore inherits the DECISION path: it never ages,
so a dead bridge holding it reads as live forever. That is the exact false-green this module's
docstring records being fixed once already, when the staleness check was gated on `online` and
`degraded` never aged out.

WHAT THIS GATE DOES: enumerate every literal that reaches `environments.status`, from the SQL and
from the parameter tuples, and require each to be a declared member. It reads the write sites rather
than trusting the constant, so adding a status without declaring it fails here and names the file.

THE PARAMETER TUPLE IS WHY THE SCAN IS NOT A SQL REGEX. `disabled` is written by
`UPDATE environments SET status = ? WHERE id = ?` with `("disabled", environment_id)` — my first
scan matched literals inside SQL only and reported that `disabled` was read by two guards and written
by nothing. It would have been a clean, plausible, entirely wrong finding.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

from service.env_status import (
    ENVIRONMENT_REGISTRABLE_STATUSES,
    ENVIRONMENT_STATUSES,
    _ENVIRONMENT_HEARTBEAT_STATUSES,
    environment_effective_status,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv", "tests"}

#: Where a value reaches `environments.status`, and which values each site can write.
#: Read off the code, not invented: shrinking this means a write path was removed.
#: `service/api_core/environment_registration.py` is deliberately ABSENT. It writes the already-
#: clamped `requested_status` parameter and no literal of its own, so it cannot widen the vocabulary
#: — the clamp in `routers/environments.py` is the whole guard, which is why that clamp reading the
#: constant is asserted below rather than left to inspection.
WRITE_SITES: dict[str, set[str]] = {
    "service/routers/environments.py": {"forgotten", "disabled"},
}


def _sources() -> list[tuple[str, str]]:
    out = []
    for path in sorted((REPO / "service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        out.append((rel.as_posix(), path.read_text(encoding="utf-8")))
    return out


def _environment_status_writes(sources) -> dict[str, set[str]]:
    """Literals that reach `environments.status`, from SQL text AND from parameter tuples.

    A statement is in scope when its SQL both names the `environments` table and assigns `status`.
    For `SET status = 'x'` the value is in the SQL; for `SET status = ?` it is the corresponding
    element of the parameter tuple, which is why this walks the AST rather than grepping strings.
    """
    writes: dict[str, set[str]] = {}
    for rel, src in sources:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            sql = node.args[0]
            if not (isinstance(sql, ast.Constant) and isinstance(sql.value, str)):
                continue
            text = sql.value
            # WRITES ONLY. Matching any statement that mentions `environments` and `status =` also
            # matched `ORDER BY CASE WHEN status = 'online'` inside a SELECT, and reported
            # `session_mode_env_binding.py` as a write site. A read is not a way the vocabulary can
            # grow, and a scan that over-reports gets acted on exactly like one that under-reports.
            if not re.search(r"\bUPDATE\s+environments\b|\bINSERT\s+INTO\s+environments\b", text, re.I):
                continue
            for value in re.findall(r"\bstatus\s*=\s*'([a-z_-]+)'", text, re.I):
                writes.setdefault(rel, set()).add(value.lower())
            # `status = ?` / an INSERT column list: the value rides in the parameter tuple.
            if re.search(r"\bstatus\s*=\s*\?", text, re.I) or "INSERT INTO" in text.upper():
                for arg in node.args[1:]:
                    elements = arg.elts if isinstance(arg, (ast.Tuple, ast.List)) else []
                    for element in elements:
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            writes.setdefault(rel, set()).add(element.value.lower())
    return writes


class EnvironmentStatusVocabularyTests(unittest.TestCase):
    def test_every_written_literal_is_a_declared_status(self):
        """THE ONE THAT MATTERS. A status nobody declared never ages out of `offline`."""
        writes = _environment_status_writes(_sources())
        for rel, values in sorted(writes.items()):
            with self.subTest(file=rel):
                undeclared = sorted(values - ENVIRONMENT_STATUSES)
                self.assertEqual(
                    undeclared, [],
                    f"{rel} writes environments.status = {undeclared}, which "
                    f"`env_status.ENVIRONMENT_STATUSES` does not contain. Nothing ages a status "
                    f"outside the heartbeat set, so a dead bridge holding it reads as live forever "
                    f"— and doctor's ENV_KNOWN_STATES will report it as unrecognised.",
                )

    def test_the_write_site_census_is_frozen(self):
        writes = _environment_status_writes(_sources())
        self.assertEqual(
            {rel: sorted(values) for rel, values in sorted(writes.items())},
            {rel: sorted(values) for rel, values in sorted(WRITE_SITES.items())},
            "the files writing environments.status changed. A NEW site means a new place the "
            "vocabulary can grow; declare it here with the literals it writes.",
        )

    def test_the_scan_finds_the_parameter_tuple_write_a_sql_regex_misses(self):
        """Anti-vacuity, and the specific mistake this scan was built to survive."""
        writes = _environment_status_writes(_sources())
        self.assertIn(
            "disabled", writes.get("service/routers/environments.py", set()),
            "`disabled` is written as a PARAMETER, not a SQL literal — a scan that misses it reports "
            "a status that is guarded against but never produced, which is a plausible non-finding",
        )
        self.assertIn("forgotten", writes.get("service/routers/environments.py", set()))
        fixture = [(
            "service/fake.py",
            'await db.execute("UPDATE environments SET status = ? WHERE id = ?", ("wat", eid))\n',
        )]
        self.assertEqual(_environment_status_writes(fixture), {"service/fake.py": {"wat"}})
        self.assertEqual(
            _environment_status_writes([("x.py", 'await db.execute("UPDATE agents SET status = \'x\'")')]),
            {},
            "a write to another table must not be attributed to environments",
        )

    def test_the_three_sets_nest(self):
        self.assertLess(set(_ENVIRONMENT_HEARTBEAT_STATUSES), set(ENVIRONMENT_REGISTRABLE_STATUSES))
        self.assertLess(set(ENVIRONMENT_REGISTRABLE_STATUSES), set(ENVIRONMENT_STATUSES))
        self.assertEqual(
            sorted(ENVIRONMENT_STATUSES - ENVIRONMENT_REGISTRABLE_STATUSES), ["disabled", "forgotten"],
            "the states a registering bridge may NOT request are the two an operator action writes",
        )

    def test_a_registering_bridge_cannot_ask_for_an_operator_decision(self):
        """The clamp is the reason `forgotten`/`disabled` stay decisions.

        Without it a bridge could register itself `forgotten` and vanish from `/environments`, which
        filters on exactly that value — self-tombstoning by heartbeat.
        """
        for status in sorted(ENVIRONMENT_STATUSES - ENVIRONMENT_REGISTRABLE_STATUSES):
            self.assertNotIn(status, ENVIRONMENT_REGISTRABLE_STATUSES, status)
        source = (REPO / "service/routers/environments.py").read_text(encoding="utf-8")
        self.assertIn(
            "if requested_status not in ENVIRONMENT_REGISTRABLE_STATUSES:", source,
            "the registration clamp must READ the constant — it was a hand-typed set literal, which "
            "is how a vocabulary grows in one place and not the other",
        )

    def test_only_the_heartbeat_states_age_out(self):
        """The derivation's contract, which is what makes an undeclared status dangerous."""
        stale = {"status": None, "last_seen": "2000-01-01T00:00:00+00:00"}
        for status in sorted(ENVIRONMENT_STATUSES):
            row = dict(stale, status=status)
            derived = environment_effective_status(row, offline_seconds=15)
            if status in _ENVIRONMENT_HEARTBEAT_STATUSES:
                self.assertEqual(derived, "offline", f"{status} must age out when the bridge goes silent")
            else:
                self.assertEqual(derived, status, f"{status} is a decision and must survive untouched")
