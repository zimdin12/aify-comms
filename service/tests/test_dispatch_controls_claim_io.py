"""Claiming dispatch controls — the interrupt/steer path from the operator to a running agent.

`service/api_core/dispatch_controls_io.py` is named by no test file. It is one short immediate
transaction that hands an agent's pending controls to the bridge that asked for them, and everything
that can go wrong here is quiet.

A control that is never claimed is an interrupt the operator pressed that never arrives — no error,
no retry, the run simply keeps going. A control claimed by the WRONG bridge is worse: it is marked
handled and delivered to a process that is not driving the agent, so it is gone and it did nothing.

THE MACHINE GUARD IS NOT AN ERROR, and that is deliberate. A bridge on a different host gets an empty
list, not a 403 — it asked "is there work for me?" and the honest answer is no. Turning it into a
refusal would make a normal multi-host poll look like a fault.

RUN STATUS IS DELIBERATELY NOT FILTERED. Claude resident runs complete on delivery, so a filter on
`('claimed','running')` would make their controls permanently unclaimable; the module carries that
reason and this file pins it, because it is exactly the kind of clause a later reader tightens.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite
from fastapi import HTTPException

from service.api_core.dispatch_controls_io import _claim_dispatch_controls_once
from service.models import DispatchControlClaimRequest
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "dci-worker"
OTHER = "dci-other"
SENDER = "dci-sender"

THIS_HOST = "linux:test-host"
OTHER_HOST = "linux:some-other-host"


class DispatchControlsClaimTestCase(FastApiTestCase):
    DB_NAME = "aify-dispatch-controls-claim-test.db"

    def setUp(self):
        super().setUp()
        for agent_id in (AGENT, OTHER, SENDER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
            self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                return [dict(row) for row in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed_run(self, run_id: str, *, target: str = AGENT, status: str = "running") -> None:
        self._write(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,"
            " subject, body, status, requested_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, f"msg-{run_id}", SENDER, target, "dispatch", "s", "b", status,
             "2026-08-17T00:00:00Z"),
        )

    def _seed_control(self, control_id: str, run_id: str, *, action: str = "interrupt",
                      status: str = "pending", body: str = "",
                      requested_at: str = "2026-08-17T00:00:00Z") -> None:
        self._write(
            "INSERT INTO dispatch_controls (id, run_id, from_agent, source_message_id, action,"
            " body, status, requested_at) VALUES (?,?,?,?,?,?,?,?)",
            (control_id, run_id, SENDER, "", action, body, status, requested_at),
        )

    def _claim(self, *, agent_id: str = AGENT, machine_id=None, run_id=None, request=None):
        req = DispatchControlClaimRequest(agentId=agent_id, machineId=machine_id, runId=run_id)
        return asyncio.run(_claim_dispatch_controls_once(req, request))

    def _control_rows(self) -> list[dict]:
        return self._rows("SELECT * FROM dispatch_controls ORDER BY id")


class ClaimingTests(DispatchControlsClaimTestCase):
    def test_a_PENDING_control_is_returned_and_marked_claimed(self):
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        result = self._claim()
        self.assertEqual([c["id"] for c in result["controls"]], ["ctl-1"])
        self.assertEqual(self._control_rows()[0]["status"], "claimed")

    def test_an_ALREADY_CLAIMED_control_is_not_handed_out_twice(self):
        """Two bridges polling the same agent, or one retrying. A second delivery of an interrupt
        is an interrupt the agent receives after it already acted on the first."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        self.assertEqual(len(self._claim()["controls"]), 1)
        self.assertEqual(self._claim()["controls"], [])

    def test_a_control_for_ANOTHER_AGENTS_run_is_not_claimable(self):
        """The join is what scopes controls to the poller. Without it an agent's poll would claim
        and consume controls meant for someone else's run."""
        self._seed_run("run-other", target=OTHER)
        self._seed_control("ctl-1", "run-other")
        self.assertEqual(self._claim()["controls"], [])
        self.assertEqual(self._control_rows()[0]["status"], "pending")

    def test_an_ORPHANED_control_whose_run_is_gone_is_not_claimable(self):
        """An inner join. A control pointing at a deleted run has no target agent, so nobody can
        claim it — it is dead rather than mis-delivered, which is the safe direction."""
        self._seed_control("ctl-1", "run-that-never-existed")
        self.assertEqual(self._claim()["controls"], [])

    def test_the_run_STATUS_is_deliberately_not_filtered(self):
        """Claude resident runs complete on delivery. A filter on ('claimed','running') — the shape
        a later reader would reach for — makes their controls permanently unclaimable, which is an
        interrupt that can never be delivered for a whole runtime."""
        for status in ("queued", "claimed", "running", "delivered", "completed"):
            with self.subTest(run_status=status):
                self._seed_run(f"run-{status}", status=status)
                self._seed_control(f"ctl-{status}", f"run-{status}")
                claimed = [c["id"] for c in self._claim()["controls"]]
                self.assertIn(f"ctl-{status}", claimed)

    def test_nothing_pending_is_an_empty_list_not_an_error(self):
        """This is a poll. "Nothing for you" is the ordinary answer and has to be cheap."""
        self._seed_run("run-1")
        self.assertEqual(self._claim(), {"ok": True, "controls": []})


class OrderingAndLimitTests(DispatchControlsClaimTestCase):
    def test_controls_arrive_OLDEST_FIRST(self):
        """A steer followed by an interrupt has to be applied in that order; reversing them applies
        the steer to a run the interrupt already stopped."""
        self._seed_run("run-1")
        self._seed_control("ctl-b", "run-1", requested_at="2026-08-17T10:00:00Z")
        self._seed_control("ctl-a", "run-1", requested_at="2026-08-17T09:00:00Z")
        self.assertEqual([c["id"] for c in self._claim()["controls"]], ["ctl-a", "ctl-b"])

    def test_controls_requested_in_the_SAME_INSTANT_are_ordered_by_id(self):
        """The tiebreaker. Without it the order of two controls with the same timestamp is whatever
        SQLite returns, which is stable until it is not."""
        self._seed_run("run-1")
        for suffix in ("c", "a", "b"):
            self._seed_control(f"ctl-{suffix}", "run-1", requested_at="2026-08-17T09:00:00Z")
        self.assertEqual([c["id"] for c in self._claim()["controls"]],
                         ["ctl-a", "ctl-b", "ctl-c"])

    def test_at_most_TWENTY_controls_are_claimed_at_once(self):
        """A bound on one response, not a cap on delivery: the rest stay pending and arrive on the
        next poll. An unbounded claim would hand a bridge an arbitrarily large batch built from a
        backlog it cannot act on."""
        self._seed_run("run-1")
        for index in range(25):
            self._seed_control(f"ctl-{index:02d}", "run-1")
        first = self._claim()
        self.assertEqual(len(first["controls"]), 20)
        self.assertEqual(len(self._claim()["controls"]), 5)

    def test_the_leftovers_stay_PENDING(self):
        self._seed_run("run-1")
        for index in range(25):
            self._seed_control(f"ctl-{index:02d}", "run-1")
        self._claim()
        pending = [row["id"] for row in self._control_rows() if row["status"] == "pending"]
        self.assertEqual(len(pending), 5)


class RunFilterTests(DispatchControlsClaimTestCase):
    def test_a_RUN_ID_narrows_the_claim_to_that_run(self):
        self._seed_run("run-1")
        self._seed_run("run-2")
        self._seed_control("ctl-1", "run-1")
        self._seed_control("ctl-2", "run-2")
        self.assertEqual([c["id"] for c in self._claim(run_id="run-2")["controls"]], ["ctl-2"])
        self.assertEqual(
            [row["status"] for row in self._control_rows()], ["pending", "claimed"])

    def test_NO_run_id_claims_across_all_of_the_agents_runs(self):
        """`(? = '' OR dc.run_id = ?)`. The empty string is the wildcard, and a bridge that polls
        for the agent rather than for one run is the common case."""
        self._seed_run("run-1")
        self._seed_run("run-2")
        self._seed_control("ctl-1", "run-1")
        self._seed_control("ctl-2", "run-2")
        self.assertEqual(len(self._claim()["controls"]), 2)

    def test_an_UNKNOWN_run_id_claims_nothing_rather_than_everything(self):
        """The failure that matters if the wildcard test were written the other way round: a filter
        that fell open on a non-matching id would hand over every control the agent has."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        self.assertEqual(self._claim(run_id="run-nope")["controls"], [])
        self.assertEqual(self._control_rows()[0]["status"], "pending")


class MachineGuardTests(DispatchControlsClaimTestCase):
    def _bind_agent_to(self, machine_id: str) -> None:
        self._write("UPDATE agents SET machine_id = ? WHERE id = ?", (machine_id, AGENT))

    def test_a_bridge_on_ANOTHER_HOST_claims_nothing(self):
        """The control would be marked handled and delivered to a process that is not driving this
        agent — gone, and with no effect. An empty list leaves it for the right bridge."""
        self._bind_agent_to(THIS_HOST)
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        self.assertEqual(self._claim(machine_id=OTHER_HOST)["controls"], [])
        self.assertEqual(self._control_rows()[0]["status"], "pending")

    def test_the_wrong_host_is_NOT_an_error(self):
        """It is a poll from a legitimate bridge for an agent that has moved. A refusal here would
        turn a normal multi-host arrangement into a stream of faults."""
        self._bind_agent_to(THIS_HOST)
        result = self._claim(machine_id=OTHER_HOST)
        self.assertEqual(result, {"ok": True, "controls": []})

    def test_the_SAME_HOST_claims_normally(self):
        self._bind_agent_to(THIS_HOST)
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        self.assertEqual(len(self._claim(machine_id=THIS_HOST)["controls"]), 1)

    def test_a_bridge_that_names_NO_MACHINE_is_not_blocked(self):
        """Older bridges send no machineId. Blocking them would stop delivering controls to every
        agent driven by one, which is a silent regression on upgrade."""
        self._bind_agent_to(THIS_HOST)
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        self.assertEqual(len(self._claim(machine_id=None)["controls"]), 1)

    def test_an_agent_with_NO_RECORDED_MACHINE_accepts_any_bridge(self):
        """The guard needs both sides. An agent that has never reported a machine cannot be said to
        be on the wrong one."""
        self._write("UPDATE agents SET machine_id = '' WHERE id = ?", (AGENT,))
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        self.assertEqual(len(self._claim(machine_id=OTHER_HOST)["controls"]), 1)

    def test_the_CLAIMING_MACHINE_is_recorded_on_the_control(self):
        """It is how a later reader knows which bridge owes the outcome of this control."""
        self._bind_agent_to(THIS_HOST)
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        self._claim(machine_id=THIS_HOST)
        self.assertEqual(self._control_rows()[0]["claim_machine_id"], THIS_HOST)


class PayloadShapeTests(DispatchControlsClaimTestCase):
    def test_the_returned_control_carries_what_a_bridge_needs_to_act(self):
        """The action and the body ARE the instruction. A payload missing either is a control the
        bridge can acknowledge and not perform."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1", action="steer", body="do the other thing")
        control = self._claim()["controls"][0]
        self.assertEqual(control["action"], "steer")
        self.assertEqual(control["body"], "do the other thing")
        self.assertEqual(control["runId"], "run-1")
        self.assertEqual(control["from"], SENDER)

    def test_the_requester_key_is_FROM_not_from_agent(self):
        """The wire name differs from the column name. Renaming it silently would leave every
        bridge reading `undefined` for who asked."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        control = self._claim()["controls"][0]
        self.assertIn("from", control)
        self.assertNotIn("from_agent", control)

    def test_the_claim_timestamp_is_returned_AND_persisted_as_the_same_value(self):
        """The bridge reports back against it. A returned time that differs from the stored one
        makes the two records of one claim disagree.

        THE CLOCK IS MADE TO TICK, because two real `_now()` calls in the same second produce the
        same string — a version that stamped the row and the payload separately passed this test
        until the clock could tell them apart. Under the tick, one call means one value and two
        calls mean two."""
        from unittest import mock

        from service.api_core import dispatch_controls_io

        ticks = iter([f"2026-08-17T00:00:{second:02d}Z" for second in range(10)])
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        with mock.patch.object(dispatch_controls_io, "_now", lambda: next(ticks)):
            control = self._claim()["controls"][0]
        self.assertTrue(control["claimedAt"])
        self.assertEqual(self._control_rows()[0]["claimed_at"], control["claimedAt"])


class RefusalTests(DispatchControlsClaimTestCase):
    def test_an_UNKNOWN_agent_is_404(self):
        """Unlike the wrong-host case this IS an error: a bridge polling for an agent that does not
        exist is misconfigured, and answering "nothing for you" would let it poll forever."""
        with self.assertRaises(HTTPException) as caught:
            self._claim(agent_id="nobody")
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn("nobody", str(caught.exception.detail))

    def test_the_REQUEST_object_is_never_read(self):
        """`request` is in the signature and unused — the sibling terminal-controls claim takes no
        such parameter. Passing None proves it: if someone starts reading it, this fails loudly here
        rather than at whichever call site happens not to supply one."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", "run-1")
        result = self._claim(request=None)
        self.assertEqual(len(result["controls"]), 1)


if __name__ == "__main__":
    unittest.main()
