"""Requesting an interrupt or steer on a run, and reporting how it went — three untested refusals.

A CONTROL IS NOT A RUN. It is a request made ABOUT a run while it is active: the run keeps going and
the control is a separate row with its own lifecycle (pending, then completed or failed). Three of
its 4xx messages were in the untested set:

    POST  /dispatch/runs/{id}/control   400 Unsupported control action
                                        409 Run '<id>' is not active
    PATCH /dispatch/controls/{id}       400 Unsupported control status

THE 409 IS THE INTERESTING ONE. `interrupt` and `steer` only mean anything against a run a bridge is
currently holding — `{claimed, running}`. Against a queued run there is nothing to interrupt, and
against a finished one the control would sit `pending` forever with no claimer, which is how a
control strands a run rather than closing it. So the refusal is about what the control could
possibly DO, not about permissions, and the accepted set is asserted against every status a run can
hold rather than one good value.

TWO ALLOWLISTS IN ONE FILE, AND THEY DISAGREED. `action` was `.strip().lower()`ed before its check;
`status` was compared RAW — while the sibling that does the identical job for environment controls
normalises. Two endpoints, same field name, same values, same purpose, different answer to
"Completed". Fixed here and covered on the accepting side, because the writer is the BRIDGE and a
refused control update leaves the control pending forever.
"""

from __future__ import annotations

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

RUN_STATUSES_THAT_ACCEPT_A_CONTROL = ("claimed", "running")
RUN_STATUSES_THAT_REFUSE = ("queued", "delivered", "completed", "failed", "cancelled")


class DispatchControlRefusalTests(FastApiTestCase):
    def _seed_run(self, run_id: str, status: str) -> None:
        """Insert a dispatch_run directly: the control endpoints read only its id and status, and
        driving a real dispatch to each of seven statuses would test the dispatch path instead."""
        import asyncio

        import aiosqlite

        async def write():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at)"
                    " VALUES (?,?,?,?,?)",
                    (run_id, "sender", "target", status, "2026-08-16T00:00:00Z"),
                )
                await db.commit()

        asyncio.run(write())

    def _stored_status(self, control_id: str) -> str:
        """Read the COLUMN, not the reply. A gate that normalises and then writes the caller's
        spelling answers `completed` either way — the defect is only visible in the row."""
        import asyncio

        import aiosqlite

        async def read():
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT status FROM dispatch_controls WHERE id = ?", (control_id,),
                )
                row = await cursor.fetchone()
                return row[0] if row else ""

        return asyncio.run(read())

    def _control(self, run_id: str, body: dict):
        return self.client.post(f"/api/v1/dispatch/runs/{run_id}/control", json=body)

    def _update(self, control_id: str, body: dict):
        """Settle a control, supplying the ACTOR unless the caller states one.

        The endpoint has required `handledBy` since 2026-08-18 (comms-senior-dev's ruling: the actor
        is mandatory and service-enforced, and actor-absent callers fail closed). Every test in this
        file is about the STATUS allowlist and its normalisation, so each one supplies a valid actor
        by default rather than repeating it — otherwise they would all be measuring the new refusal
        instead of the thing they were written for.

        A default could in principle mask a regression where the endpoint stops requiring the actor.
        It cannot here: `test_dispatch_control_settlement_names_its_actor.py` asserts the refusal
        directly, and this helper lets a caller override `handledBy` to exercise it.
        """
        payload = {"handledBy": "ctl-test-actor", **body}
        return self.client.patch(f"/api/v1/dispatch/controls/{control_id}", json=payload)

    # ── the action allowlist ─────────────────────────────────────────────────────────────────

    def test_the_action_allowlist_is_exactly_interrupt_and_steer(self):
        """As a SET on both sides. One good value and one bad one passes just as well on a gate that
        accepts everything except the value tested."""
        self._seed_run("run-active", "running")
        for action in ("interrupt", "steer"):
            with self.subTest(accepted=action):
                response = self._control("run-active", {"action": action, "from_agent": "op"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["action"], action)
        for action in ("stop", "cancel", "kill", "", "interrupt-now", "steering"):
            with self.subTest(refused=action):
                response = self._control("run-active", {"action": action, "from_agent": "op"})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "Unsupported control action")

    def test_the_action_is_normalised_before_the_allowlist(self):
        self._seed_run("run-active", "running")
        for action in ("INTERRUPT", "Steer", "  interrupt  "):
            with self.subTest(action=action):
                response = self._control("run-active", {"action": action, "from_agent": "op"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    response.json()["action"], action.strip().lower(),
                    "the STORED action is the normalised one, not the caller's spelling",
                )

    def test_the_action_is_checked_before_the_run_is_even_looked_up(self):
        """Order, pinned: a bad action against a missing run answers 400, not 404. It is the cheaper
        check and it needs no database, which is why it goes first."""
        response = self._control("no-such-run", {"action": "nonsense", "from_agent": "op"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "Unsupported control action")

    # ── the run must be ACTIVE ───────────────────────────────────────────────────────────────

    def test_a_control_is_refused_unless_the_run_is_claimed_or_running(self):
        """The whole status vocabulary, not one example. Against a queued run there is nothing to
        interrupt; against a finished one the control would sit `pending` with no claimer, which
        strands the run rather than closing it."""
        for status in RUN_STATUSES_THAT_REFUSE:
            with self.subTest(status=status):
                self._seed_run(f"run-{status}", status)
                response = self._control(f"run-{status}", {"action": "interrupt", "from_agent": "op"})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"], f"Run 'run-{status}' is not active")

    def test_a_control_is_accepted_for_every_ACTIVE_status(self):
        for status in RUN_STATUSES_THAT_ACCEPT_A_CONTROL:
            with self.subTest(status=status):
                self._seed_run(f"ok-{status}", status)
                response = self._control(f"ok-{status}", {"action": "steer", "from_agent": "op"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "pending", "a new control starts pending")

    def test_an_unknown_run_is_404_not_409(self):
        """Different answers: "there is no such run" and "there is, and it is finished" are different
        things for a caller to act on."""
        response = self._control("no-such-run", {"action": "interrupt", "from_agent": "op"})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("'no-such-run' not found", response.json()["detail"])

    # ── the status allowlist ─────────────────────────────────────────────────────────────────

    def test_the_status_allowlist_is_exactly_completed_and_failed(self):
        for status in ("pending", "claimed", "done", "", "complete", "ok"):
            with self.subTest(refused=status):
                response = self._update("no-such-control", {"status": status})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "Unsupported control status")

    def test_a_recognised_status_gets_past_the_allowlist_in_ANY_casing(self):
        """The fix this slice made. `action` was normalised and `status` was not, while the sibling
        endpoint for ENVIRONMENT controls normalises — so `"Completed"` was accepted by one and
        refused by the other. The writer is the bridge, and a refused update leaves the control
        pending forever."""
        for status in ("completed", "failed", "COMPLETED", "Failed", "  completed  "):
            with self.subTest(status=status):
                response = self._update("no-such-control", {"status": status})
                self.assertEqual(
                    response.status_code, 404,
                    "past the allowlist and stopped by the missing control, not by its"
                    f" spelling — got {response.text}",
                )

    def test_a_completed_control_reports_and_STORES_the_normalised_status(self):
        """The stored value, not just the reply. Normalising at the gate and then writing the
        caller's spelling to the column is the split `test_no_column_is_read_two_ways.py` exists to
        prevent — and the reply would still read `completed`, so asserting only the payload cannot
        see it. Every reader of `dispatch_controls.status` compares raw lowercase literals."""
        self._seed_run("run-active", "running")
        created = self._control("run-active", {"action": "interrupt", "from_agent": "op"})
        control_id = created.json()["controlId"]
        response = self._update(control_id, {"status": "COMPLETED", "response": "done"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(self._stored_status(control_id), "completed")

    def test_a_failed_control_stores_the_normalised_status_too(self):
        self._seed_run("run-active", "running")
        control_id = self._control(
            "run-active", {"action": "steer", "from_agent": "op"},
        ).json()["controlId"]
        self.assertEqual(self._update(control_id, {"status": "  Failed "}).status_code, 200)
        self.assertEqual(self._stored_status(control_id), "failed")
