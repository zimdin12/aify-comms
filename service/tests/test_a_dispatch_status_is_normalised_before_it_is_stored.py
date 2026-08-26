"""A dispatch run's status is normalised before it is stored, because everything downstream assumes so.

`update_dispatch_run` lowercased the REQUESTED status for its monotonic guard and then wrote the RAW
one. Three consumers each test that written value against a lowercase literal:

    params.append(effective_status)                        -> the column every reconciler queries
    if effective_status == "running"                       -> stamps started_at
    if effective_status in _DISPATCH_TERMINAL_STATUSES     -> settles the run

A status of "Completed" passed the guard (which compared "completed"), was written verbatim, matched
neither check, and then matched no reconciler either. Every dispatch sweep selects on lowercase:

    dispatch_lifecycle.py:88    WHERE status = 'delivered'
    dispatch_lifecycle.py:367   WHERE r.status IN ('completed', 'failed', 'cancelled')
    dispatch_lifecycle.py:400   WHERE require_reply = 1 AND status IN ('failed', 'cancelled')
    dispatch_queue.py:258       WHERE r.status = 'claimed'
    dispatch_queue.py:337       WHERE dr.status = 'queued'

So the row is finished to the caller and unfinished to the system: require_reply never settles and
cleanup never deletes it. That is the `lost` incident's exact shape, quoted in
`test_terminal_status_vocabulary.py` -- a gate written as `status IN (...)` treats an unlisted value
as still-running -- on a table that has NO status vocabulary gate to catch it.

NO LIVE DEFECT TODAY, and the reason is worth stating rather than assuming. Measured across
`mcp/stdio` with `tests/` and `fixtures/` pruned: the bridge sends exactly five status literals on
`/dispatch/runs/{id}` -- completed, delivered, failed, queued, running -- and all five are lowercase. What made this worth fixing anyway
is that `status` is `Optional[str]` on `DispatchRunUpdate` with no validator, the bridge is host-side
and routinely a different build from the service, and the guard one line above already lowercases,
which is the author expecting case to vary in the same expression that then does not handle it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.api_core.dispatch_state import _DISPATCH_TERMINAL_STATUSES
from service.tests._base import FastApiTestCase


class DispatchStatusNormalisationTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    SENDER = "norm-sender"
    TARGET = "norm-target"

    def setUp(self) -> None:
        super().setUp()
        for agent_id in (self.SENDER, self.TARGET):
            response = self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code",
                "sessionMode": "resident", "machineId": "linux:test-host",
            })
            self.assertEqual(response.status_code, 200, response.text)

    #: Seeded directly rather than created through POST /dispatch. The subject here is what the PATCH
    #: STORES, and routing a request through dispatch preflight would make every assertion depend on
    #: delivery rules that have nothing to do with it -- the first attempt did, and every case failed
    #: because a resident Claude target without channelEnabled is refused before a run exists.
    _seq = 0

    def _run_id(self) -> str:
        import asyncio

        from service.db import get_db

        type(self)._seq += 1
        run_id = f"run-norm-{type(self)._seq:03d}"

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, "
                    "dispatch_mode, execution_mode, subject, body, status, require_reply, "
                    "requested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, None, self.SENDER, self.TARGET, "start_if_possible", "managed",
                     "s", "b", "queued", 0, "2026-08-26T02:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())
        return run_id

    def _stored_status(self, run_id: str) -> str:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT status, started_at, finished_at FROM dispatch_runs WHERE id = ?",
                    (run_id,),
                )).fetchone()
                return dict(row) if row else {}
            finally:
                await db.close()

        return asyncio.run(go())

    def _patch(self, run_id: str, status: str):
        return self.client.patch(f"/api/v1/dispatch/runs/{run_id}", json={"status": status})

    def test_the_readers_really_do_assume_lowercase(self) -> None:
        """The control for the whole file. If the terminal set held mixed case, a verbatim write
        would still match and none of this would be a defect.

        The PROPERTY is asserted, not the membership. An earlier version of this control spelled the
        three members out and `test_status_set_literal_twins_are_frozen.py` caught it -- correctly,
        and for the reason that gate exists: a second copy of a status set is how the `lost` incident
        happened. It would also have failed the day someone legitimately added a terminal status,
        which is a control that costs more than it proves.
        """
        self.assertTrue(_DISPATCH_TERMINAL_STATUSES, "the terminal status set is empty")
        for status in _DISPATCH_TERMINAL_STATUSES:
            with self.subTest(status=status):
                self.assertEqual(
                    status, status.lower(),
                    "a terminal status is not lowercase, so normalising the written value would "
                    "stop matching it -- this whole file rests on that assumption",
                )

    def test_a_lowercase_status_is_stored_unchanged(self) -> None:
        """No regression for every status the bridge actually sends."""
        run_id = self._run_id()
        self.assertEqual(self._patch(run_id, "running").status_code, 200)
        self.assertEqual(self._stored_status(run_id)["status"], "running")

    def test_a_mixed_case_status_is_stored_as_the_readers_expect_it(self) -> None:
        run_id = self._run_id()
        self.assertEqual(self._patch(run_id, "Completed").status_code, 200)
        stored = self._stored_status(run_id)
        self.assertEqual(
            stored["status"], "completed",
            "a mixed-case status was written verbatim. Every dispatch reconciler selects on "
            "lowercase, so this row is finished to its caller and unfinished to the system: "
            "require_reply never settles and cleanup never deletes it.",
        )

    def test_a_mixed_case_running_still_stamps_started_at(self) -> None:
        """The second consumer. `effective_status == "running"` is what records when work began, so
        a verbatim "Running" left the run with no start time and nothing to age it by."""
        run_id = self._run_id()
        self.assertEqual(self._patch(run_id, "Running").status_code, 200)
        stored = self._stored_status(run_id)
        self.assertEqual(stored["status"], "running")
        self.assertTrue(stored["started_at"], "started_at was never stamped for a mixed-case running")

    def test_a_mixed_case_terminal_status_still_settles_the_run(self) -> None:
        """The third consumer, and the costly one: membership in _DISPATCH_TERMINAL_STATUSES is what
        stamps finished_at. Missing it leaves the row permanently unsettled."""
        run_id = self._run_id()
        self.assertEqual(self._patch(run_id, "FAILED").status_code, 200)
        stored = self._stored_status(run_id)
        self.assertEqual(stored["status"], "failed")
        self.assertTrue(stored["finished_at"], "a terminal status did not settle the run")

    def test_surrounding_whitespace_does_not_become_part_of_the_status(self) -> None:
        """The same class one step over: ' completed ' is not 'completed' to any SQL comparison."""
        run_id = self._run_id()
        self.assertEqual(self._patch(run_id, "  completed  ").status_code, 200)
        self.assertEqual(self._stored_status(run_id)["status"], "completed")

    def test_the_monotonic_guard_still_refuses_to_reopen_a_finished_run(self) -> None:
        """Normalising must not buy correctness by weakening the guard beside it: once a run is
        terminal, a different status must still be refused."""
        run_id = self._run_id()
        self.assertEqual(self._patch(run_id, "completed").status_code, 200)
        self.assertEqual(self._patch(run_id, "running").status_code, 200)
        self.assertEqual(
            self._stored_status(run_id)["status"], "completed",
            "a finished run was reopened; the monotonic guard stopped holding",
        )

    def test_a_mixed_case_reopen_attempt_is_refused_too(self) -> None:
        """The guard compared lowercase already, so this held before -- pinned so normalising the
        write cannot accidentally route around it."""
        run_id = self._run_id()
        self.assertEqual(self._patch(run_id, "completed").status_code, 200)
        self.assertEqual(self._patch(run_id, "RUNNING").status_code, 200)
        self.assertEqual(self._stored_status(run_id)["status"], "completed")

    def test_an_empty_status_leaves_the_run_alone(self) -> None:
        """`status` is optional on the model, and most PATCHes carry only an event or a summary."""
        run_id = self._run_id()
        before = self._stored_status(run_id)["status"]
        self.assertEqual(self.client.patch(
            f"/api/v1/dispatch/runs/{run_id}", json={"summary": "no status here"},
        ).status_code, 200)
        self.assertEqual(self._stored_status(run_id)["status"], before)


if __name__ == "__main__":
    import unittest

    unittest.main()
