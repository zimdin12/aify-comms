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

    def _rows(self, sql: str, params: tuple = ()):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                return [dict(r) for r in await cursor.fetchall()]

        return asyncio.run(run())

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

    # ── the happy path ───────────────────────────────────────────────────────────────────────

    def test_a_pending_control_is_handed_to_the_runs_TARGET(self):
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1")
        response = self._claim()
        self.assertEqual(response.status_code, 200, response.text)
        controls = response.json()["controls"]
        self.assertEqual([c["id"] for c in controls], ["ctl-1"])
        self.assertEqual(controls[0]["runId"], "run-1")
        self.assertEqual(controls[0]["action"], "interrupt")
        self.assertEqual(controls[0]["body"], "stop please",
                         "a steer's body IS the instruction — dropping it delivers an empty steer")

    def test_claiming_MARKS_it_claimed_so_it_is_not_delivered_twice(self):
        """A second poll re-delivering the same interrupt is not harmless: the bridge would
        interrupt the NEXT turn as well."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1")
        self.assertEqual(len(self._claim().json()["controls"]), 1)
        self.assertEqual(self._claim().json()["controls"], [])
        row = self._rows("SELECT status, claimed_at FROM dispatch_controls WHERE id = 'ctl-1'")[0]
        self.assertEqual(row["status"], "claimed")
        self.assertTrue(row["claimed_at"], "a claim with no timestamp cannot be aged out later")

    def test_the_claiming_MACHINE_is_recorded(self):
        """It is how a superseded bridge's claims are told apart from the live one's."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1")
        self._claim(machineId=MACHINE)
        row = self._rows("SELECT claim_machine_id FROM dispatch_controls WHERE id = 'ctl-1'")[0]
        self.assertEqual(row["claim_machine_id"], MACHINE)

    def test_controls_arrive_OLDEST_FIRST(self):
        """An interrupt followed by a steer must not arrive as a steer followed by an interrupt —
        the second would be applied to a turn the first already stopped."""
        self._seed_run("run-1")
        self._seed_control("ctl-late", run_id="run-1", requested_at="2026-08-16T02:00:00Z")
        self._seed_control("ctl-early", run_id="run-1", requested_at="2026-08-16T01:00:00Z")
        controls = self._claim().json()["controls"]
        self.assertEqual([c["id"] for c in controls], ["ctl-early", "ctl-late"])

    def test_a_run_that_has_already_FINISHED_still_yields_its_controls(self):
        """Deliberate, and it looks like a missing filter. Claude resident runs complete immediately
        on delivery, so a status filter would make their controls permanently unclaimable."""
        for status in ("completed", "failed", "cancelled", "queued"):
            with self.subTest(run_status=status):
                self._seed_run(f"run-{status}", status=status)
                self._seed_control(f"ctl-{status}", run_id=f"run-{status}")
                claimed = {c["id"] for c in self._claim().json()["controls"]}
                self.assertIn(f"ctl-{status}", claimed)

    def test_a_specific_runId_narrows_the_claim(self):
        """The channel bridge polls per run. Handing it another run's controls would interrupt a
        turn it is not driving."""
        self._seed_run("run-a")
        self._seed_run("run-b")
        self._seed_control("ctl-a", run_id="run-a")
        self._seed_control("ctl-b", run_id="run-b")
        controls = self._claim(runId="run-a").json()["controls"]
        self.assertEqual([c["id"] for c in controls], ["ctl-a"])
        self.assertEqual(
            self._rows("SELECT status FROM dispatch_controls WHERE id = 'ctl-b'")[0]["status"],
            "pending", "another run's control was claimed and lost",
        )

    def test_an_empty_runId_means_ALL_runs_rather_than_none(self):
        """`(? = '' OR dc.run_id = ?)` — the bridge's general poll sends no run id, and reading that
        as "match nothing" would silently stop every interrupt in the fleet."""
        self._seed_run("run-a")
        self._seed_run("run-b")
        self._seed_control("ctl-a", run_id="run-a")
        self._seed_control("ctl-b", run_id="run-b")
        for body in ({}, {"runId": ""}, {"runId": None}):
            with self.subTest(body=body):
                self._write("UPDATE dispatch_controls SET status = 'pending'")
                claimed = {c["id"] for c in self._claim(**body).json()["controls"]}
                self.assertEqual(claimed, {"ctl-a", "ctl-b"})

    # ── who does NOT get it ──────────────────────────────────────────────────────────────────

    def test_a_control_for_ANOTHER_agents_run_is_not_claimable(self):
        self._write(
            "INSERT INTO agents (id, role, name, status, registered_at, last_seen)"
            " VALUES (?,?,?,?,?,?)",
            ("lc-other", "coder", "lc-other", "active", "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
        )
        self._seed_run("run-other", target="lc-other")
        self._seed_control("ctl-other", run_id="run-other")
        self.assertEqual(self._claim().json()["controls"], [])

    def test_an_ALREADY_CLAIMED_control_is_not_handed_out_again(self):
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1", status="claimed")
        self.assertEqual(self._claim().json()["controls"], [])

    def test_a_COMPLETED_control_is_not_re_delivered(self):
        """The run is closed; re-delivering its interrupt would stop unrelated work."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1", status="completed")
        self.assertEqual(self._claim().json()["controls"], [])

    def test_an_unknown_agent_is_404_rather_than_an_empty_list(self):
        """An empty list reads as "no work"; a bridge polling with a typo'd id would look healthy
        forever."""
        response = self._claim(agentId="lc-never-existed")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("lc-never-existed", response.json()["detail"])

    # ── machine routing ──────────────────────────────────────────────────────────────────────

    def test_a_bridge_on_a_DIFFERENT_HOST_claims_nothing(self):
        """It cannot deliver the interrupt — the agent's process is not there. Claiming it anyway
        marks the control `claimed` and the real bridge never sees it."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1")
        response = self._claim(machineId="win32:other-box")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["controls"], [])
        self.assertEqual(
            self._rows("SELECT status FROM dispatch_controls WHERE id = 'ctl-1'")[0]["status"],
            "pending", "a foreign bridge consumed a control it could not deliver",
        )

    def test_the_linux_and_WSL_spellings_of_ONE_machine_match(self):
        """2026-06-02: the same machine registers as `linux:host` or `wsl-<distro>:host` depending
        on whether WSL_DISTRO_NAME reached that process. An exact comparison made deliveries sit
        queued forever."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1")
        controls = self._claim(machineId="wsl-ubuntu:box").json()["controls"]
        self.assertEqual([c["id"] for c in controls], ["ctl-1"])

    def test_a_bridge_that_names_NO_machine_is_not_treated_as_foreign(self):
        """An older bridge sends none. Refusing it would stop every interrupt from that host."""
        self._seed_run("run-1")
        self._seed_control("ctl-1", run_id="run-1")
        self.assertEqual(len(self._claim(machineId="").json()["controls"]), 1)

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
