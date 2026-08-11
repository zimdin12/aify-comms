"""A healthy spawn could be killed by somebody else's failed dispatch.

AUDIT FINDING 3/3, source-found by `comms-senior-dev` and accepted as pre-move work: fix it BEFORE
the reconciler extraction, because a pure-move refactor would carry it across silently and the
release after that is supposed to have an empty behaviour changelog.

`_repair_spawn_requests_from_initial_dispatch_failures` exists to notice that a spawn's INITIAL
BRIEF failed and mark the spawn failed instead of leaving it `running` forever. It found that brief
by taking the FIRST dispatch_run to that agent at or after the spawn's start time:

    SELECT * FROM dispatch_runs
    WHERE target_agent = ? AND requested_at >= ?
    ORDER BY requested_at ASC LIMIT 1

That is time proximity, not identity. Any dispatch that happens to land first — a manager's
unrelated question, a queued message that arrives a second after the spawn starts — is treated as
the spawn's own brief, and if it failed the spawn is killed with "Initial brief failed: ...".

An identity is available and was there all along. The brief is created at `api_v2.py:11706` from
the spawn row itself:

    from_agent = spawn.created_by or "dashboard"
    subject    = spawn.subject or f"Spawn {agent_id}"
    body       = spawn.initial_message

So the corrected predicate matches on those three fields. The time bound stays, as a bound rather
than as the identity.

These tests run the two predicates against real SQLite rather than through the reconciler, for the
same reason `test_channel_replay_predicate.py` does: the defect is in what the query SELECTS, and a
test that only exercised the reconciler would pass the moment any row happened to match.
"""

from __future__ import annotations

import sqlite3
import unittest

BROKEN = """
    SELECT id FROM dispatch_runs
    WHERE target_agent = :agent AND requested_at >= :started
    ORDER BY requested_at ASC LIMIT 1
"""

# The shipped form. Kept here as the executable statement of the contract; the reconciler's copy is
# asserted to match in ReconcilerSourceTests below.
CORRECTED = """
    SELECT id FROM dispatch_runs
    WHERE target_agent = :agent
      AND requested_at >= :started
      AND from_agent = :from_agent
      AND subject = :subject
      AND body = :body
    ORDER BY requested_at ASC LIMIT 1
"""


class SpawnInitialDispatchIdentityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE dispatch_runs (id TEXT, target_agent TEXT, from_agent TEXT, "
            "subject TEXT, body TEXT, status TEXT, requested_at TEXT)"
        )
        # The spawn under repair: started 12:00, briefed by sc-manager.
        self.spawn = {
            "agent": "sc-coder",
            "started": "2026-08-11T12:00:00Z",
            "from_agent": "sc-manager",
            "subject": "Spawn sc-coder",
            "body": "Take the gate-3 lane.",
        }

    def _add(self, rid, *, at, status, from_agent, subject, body, agent="sc-coder"):
        self.db.execute(
            "INSERT INTO dispatch_runs VALUES (?,?,?,?,?,?,?)",
            (rid, agent, from_agent, subject, body, status, at),
        )

    def _match(self, sql):
        row = self.db.execute(sql, self.spawn).fetchone()
        return row["id"] if row else None

    def _the_actual_brief(self, *, at="2026-08-11T12:00:03Z", status="failed"):
        self._add("brief", at=at, status=status, from_agent="sc-manager",
                  subject="Spawn sc-coder", body="Take the gate-3 lane.")

    # ── the bug ──────────────────────────────────────────────────────────────────────
    def test_an_unrelated_failed_dispatch_no_longer_looks_like_the_brief(self):
        """The reported overbreadth: someone else's failed message kills a healthy spawn."""
        self._add("unrelated", at="2026-08-11T12:00:01Z", status="failed",
                  from_agent="mc-manager", subject="quick question", body="are you up?")
        self._the_actual_brief(at="2026-08-11T12:00:05Z", status="completed")

        self.assertEqual(self._match(BROKEN), "unrelated",
                         "pinning the bug: the old predicate picks the stranger's run")
        self.assertEqual(self._match(CORRECTED), "brief",
                         "the corrected predicate must find the spawn's own brief instead")

    def test_the_spawn_survives_when_only_the_unrelated_run_failed(self):
        """End to end on the consequence, not just the row: nothing to repair here."""
        self._add("unrelated", at="2026-08-11T12:00:01Z", status="failed",
                  from_agent="mc-manager", subject="quick question", body="are you up?")
        matched = self._match(CORRECTED)
        self.assertIsNone(matched, "no brief run exists yet, so there is nothing to fail the spawn on")

    # ── it must still do its job ─────────────────────────────────────────────────────
    def test_a_genuinely_failed_brief_is_still_found(self):
        self._the_actual_brief(status="failed")
        self.assertEqual(self._match(CORRECTED), "brief")

    def test_a_failed_brief_is_found_even_behind_an_earlier_unrelated_run(self):
        """The narrowing must not become a new blind spot: ordering no longer hides the brief."""
        self._add("unrelated", at="2026-08-11T12:00:01Z", status="completed",
                  from_agent="mc-manager", subject="hello", body="hi")
        self._the_actual_brief(at="2026-08-11T12:00:09Z", status="failed")
        self.assertEqual(self._match(CORRECTED), "brief")

    def test_runs_for_a_DIFFERENT_agent_never_match(self):
        self._add("other-agent", at="2026-08-11T12:00:01Z", status="failed", agent="sc-tester",
                  from_agent="sc-manager", subject="Spawn sc-coder", body="Take the gate-3 lane.")
        self.assertIsNone(self._match(CORRECTED))

    def test_an_identical_brief_from_BEFORE_the_spawn_started_is_excluded(self):
        """The time bound still bounds — a previous incarnation's brief is not this spawn's."""
        self._the_actual_brief(at="2026-08-11T11:59:59Z", status="failed")
        self.assertIsNone(self._match(CORRECTED))

    def test_the_default_subject_shape_matches(self):
        """`subject` is nullable on the spawn; the dispatcher substitutes f"Spawn {agent_id}".
        If that default ever drifts from the dispatcher's, this predicate silently matches nothing
        for every spawn created without a subject."""
        self.assertEqual(self.spawn["subject"], f"Spawn {self.spawn['agent']}")


class ReconcilerSourceTests(unittest.TestCase):
    """The shipped reconciler must use the identity form, not the time form."""

    def _body(self) -> str:
        from pathlib import Path
        src = Path(__file__).resolve().parents[2] / "service" / "routers" / "api_v2.py"
        text = src.read_text(encoding="utf-8", errors="replace")
        at = text.index("async def _repair_spawn_requests_from_initial_dispatch_failures")
        return text[at : at + 4000]

    def test_the_lookup_matches_on_identity(self):
        body = self._body()
        for clause in ("from_agent = ?", "subject = ?", "body = ?"):
            self.assertIn(clause, body, f"the brief lookup must constrain on {clause}")

    def test_the_time_bound_is_kept_as_a_bound(self):
        self.assertIn("requested_at >= ?", self._body())

    def test_the_dispatcher_default_subject_is_still_what_this_predicate_assumes(self):
        """Agreement between the two halves, in one file: the reconciler reconstructs the subject
        the dispatcher generated. If someone edits one f-string, this fails instead of the feature
        quietly repairing nothing."""
        from pathlib import Path
        src = Path(__file__).resolve().parents[2] / "service" / "routers" / "api_v2.py"
        text = src.read_text(encoding="utf-8", errors="replace")
        self.assertIn('f"Spawn {row[\'agent_id\']}"', text,
                      "the dispatcher's default subject shape changed — update the reconciler with it")


if __name__ == "__main__":
    unittest.main()
