"""Claiming dispatch controls — how an interrupt or a steer actually reaches a bridge.

`_claim_dispatch_controls_once` and its long-poll route `claim_dispatch_controls` were among the 71
service functions the suite never entered. A control that is never claimed is an interrupt an
operator pressed that nothing acted on: the run keeps going, the dashboard shows the request
"pending" forever, and the only symptom is that the agent ignored them.

THE THREE THINGS THAT DECIDE WHO GETS A CONTROL:

  * the TARGET agent. Controls are claimed by the agent the run is aimed at, not by the requester —
    getting that backwards hands the interrupt to whoever pressed the button;
  * the MACHINE. A bridge on another host must not claim work for an agent that lives elsewhere,
    and the comparison is deliberately tolerant across the linux/WSL family, because the same
    machine registers as both `linux:host` and `wsl-ubuntu:host` depending on which process asked
    (2026-06-02: deliveries sat queued forever under an exact comparison);
  * the STATUS. Only `pending` controls are claimable, and claiming one moves it to `claimed`, so a
    second poll cannot deliver the same interrupt twice.

THERE IS DELIBERATELY NO FILTER ON RUN STATUS, and that is worth pinning because it looks like an
omission. Claude resident runs complete immediately on delivery, so a control for one would never be
claimable under a `('claimed','running')` filter — the channel bridge polls independently and
delivers regardless of run state.

THIS FILE DRIVES THE HTTP ROUTE. The claim itself -- target, machine guard, run filter, ordering,
marking, payload -- is pinned once, against `_claim_dispatch_controls_once`, in
`test_dispatch_controls_claim_io.py`. What stays here is what only the route adds or what that file
does not reach: the long-poll wrapper (whose waiting poll is also the one happy path through the
wire), a non-pending control that
is not `claimed`, and the linux/WSL spelling of one machine.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

TARGET = "lc-target"
REQUESTER = "lc-operator"
MACHINE = "linux:box"


class DispatchControlClaimTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent_id in (TARGET, REQUESTER):
            response = self.client.post(
                "/api/v1/agents",
                json={"agentId": agent_id, "role": "coder", "machineId": MACHINE},
            )
            self.assertEqual(response.status_code, 200, response.text)

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _seed_run(self, run_id: str, *, target: str = TARGET, status: str = "running") -> None:
        self._write(
            "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at)"
            " VALUES (?,?,?,?,?)",
            (run_id, REQUESTER, target, status, "2026-08-16T00:00:00Z"),
        )

    def _seed_control(self, control_id: str, *, run_id: str, action: str = "interrupt",
                      status: str = "pending", requested_at: str = "2026-08-16T00:00:00Z") -> None:
        self._write(
            "INSERT INTO dispatch_controls (id, run_id, from_agent, action, body, status,"
            " requested_at) VALUES (?,?,?,?,?,?,?)",
            (control_id, run_id, REQUESTER, action, "stop please", status, requested_at),
        )

    def _claim(self, **body):
        payload = {"agentId": TARGET}
        payload.update(body)
        return self.client.post("/api/v1/dispatch/controls/claim", json=payload)

    # ── who does NOT get it ──────────────────────────────────────────────────────────────────

    def test_a_COMPLETED_control_is_not_re_delivered(self):
        """The run is closed; re-delivering its interrupt would stop unrelated work."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1", status="completed")
        self.assertEqual(self._claim().json()["controls"], [])

    # ── machine routing ──────────────────────────────────────────────────────────────────────

    def test_the_linux_and_WSL_spellings_of_ONE_machine_match(self):
        """2026-06-02: the same machine registers as `linux:host` or `wsl-<distro>:host` depending
        on whether WSL_DISTRO_NAME reached that process. An exact comparison made deliveries sit
        queued forever."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1")
        controls = self._claim(machineId="wsl-ubuntu:box").json()["controls"]
        self.assertEqual([c["id"] for c in controls], ["ctl-1"])

    # ── the long-poll wrapper ────────────────────────────────────────────────────────────────

    def test_with_no_wait_the_claim_returns_immediately(self):
        """`waitMs=0` is the legacy immediate mode and still has to work — a bridge that polls in a
        loop must not be held."""
        response = self._claim(waitMs=0)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["controls"], [])

    def test_a_waiting_poll_returns_as_soon_as_there_IS_a_control(self):
        """The long poll exists so an interrupt is delivered in milliseconds rather than on the next
        poll tick. A control that is already pending must not be made to wait out the budget."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1")
        response = self._claim(waitMs=2000)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([c["id"] for c in response.json()["controls"]], ["ctl-1"])
