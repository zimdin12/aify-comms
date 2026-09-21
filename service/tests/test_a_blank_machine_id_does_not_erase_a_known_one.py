"""A write that does not name a machine must not erase the machine already on the agent row.

MEASURED ON THE OPERATOR'S HOST, 2026-09-21. `mp-manager` was missing from `aify-env`'s start menu
for two days. Not refused -- invisible: `startabilityOf` offers only agents whose `machineId` equals
this host's, and `notOffered` scopes the same way, so an agent with NO machine is offered by nobody
and explained by nothing. Its `agents.machine_id` was the empty string while every other signal
about it read healthy.

The chain that emptied it: aify-env's `aify-comms` plugin claimed spawn requests without passing a
`machineId` (its `runClaimPass` accepted one and the call site never sent it), so every
`spawn_requests.claim_machine_id` written since aify-env took claiming over on 2026-09-03 was
blank -- and this service copies that value onto `agents.machine_id` when the spawn reaches
`running`. A worker that came up re-registered seconds later and put its machine back, which is why
only agents whose spawns FAILED were left blank, and why nothing noticed for two days.

aify-env now sends the machine. This file pins the other half: a blank arriving here means "the
writer did not say", never "the agent moved host", so the known value survives. Both controls below
prove the guard still lets a real machine id through -- a guard that ignored every incoming value
would pass the first two tests and break the product.

COVERED: all three sites that write a machine id from a request -- the `running` spawn upsert
(`running_spawn.py`), the registration upsert and the console-adoption UPDATE (both in
`agent_registration_writes.py`).
"""
from __future__ import annotations

import asyncio

from service.db import get_db
from service.tests._base import FastApiTestCase

ENVIRONMENT_ID = "wsl:laputa:default"
BRIDGE_ID = "bridge-machine-id"
HOST_MACHINE = "wsl:laputa"


class ABlankMachineIdDoesNotEraseAKnownOne(FastApiTestCase):
    DB_NAME = "aify-blank-machine-id-test.db"

    def setUp(self):
        super().setUp()
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": ENVIRONMENT_ID, "label": "laputa", "machineId": HOST_MACHINE, "os": "linux",
                "kind": "linux", "bridgeId": BRIDGE_ID, "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": "hermes", "available": True}], "status": "online",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    # --- reads -------------------------------------------------------------------------------

    def _machine_id(self, agent_id: str) -> str:
        async def run():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT machine_id FROM agents WHERE id = ?", (agent_id,))).fetchone()
                return None if row is None else (row["machine_id"] or "")
            finally:
                await db.close()
        return asyncio.run(run())

    # --- the two writers ---------------------------------------------------------------------

    def _register(self, agent_id: str, *, machine_id=None):
        payload = {"agentId": agent_id, "role": "coder", "runtime": "hermes", "sessionMode": "managed"}
        if machine_id is not None:
            payload["machineId"] = machine_id
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)

    def _spawn_and_run(self, agent_id: str, *, claim_machine_id=None):
        """The whole real path: queue a request, CLAIM it as aify-env does, then settle it running.

        Driven through HTTP rather than seeded, because the defect lived in the join between the
        claim (which stamps `claim_machine_id`) and the settle (which copies it onto the agent).
        Seeding the row would have proved the copy and missed the blank.
        """
        created = self.client.post("/api/v1/spawn-requests", json={
            "environmentId": ENVIRONMENT_ID, "agentId": agent_id, "runtime": "hermes",
            "workspace": "/workspace/proj", "createdBy": "operator",
        })
        self.assertEqual(created.status_code, 200, created.text)
        spawn_id = created.json()["spawnRequest"]["id"]

        claim_body = {"environmentId": ENVIRONMENT_ID, "bridgeId": BRIDGE_ID}
        if claim_machine_id is not None:
            claim_body["machineId"] = claim_machine_id
        claimed = self.client.post("/api/v1/spawn-requests/claim", json=claim_body)
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(
            (claimed.json().get("spawnRequest") or {}).get("id"), spawn_id,
            "the claim returned some other request, so the rest of this test proves nothing",
        )

        for status in ("starting", "running"):
            settled = self.client.patch(f"/api/v1/spawn-requests/{spawn_id}",
                                        json={"status": status, "bridgeId": BRIDGE_ID})
            self.assertEqual(settled.status_code, 200, settled.text)

    # --- the spawn path ----------------------------------------------------------------------

    def test_a_spawn_CLAIMED_WITHOUT_A_MACHINE_leaves_the_agents_own_machine_alone(self):
        """This is `mp-manager`. The agent already belongs to a host; the claimer just did not say."""
        self._register("mp-manager", machine_id=HOST_MACHINE)
        self.assertEqual(self._machine_id("mp-manager"), HOST_MACHINE, "setup did not take")

        self._spawn_and_run("mp-manager")

        self.assertEqual(
            self._machine_id("mp-manager"), HOST_MACHINE,
            "a claim that named no machine erased the one the agent belonged to -- which is what "
            "took it out of aify-env's start menu for two days",
        )

    def test_CONTROL_a_spawn_claimed_WITH_a_machine_writes_that_machine(self):
        """The guard must not have turned into "ignore the claimer". A claim that names a machine is
        the service's only way to learn which host took the work."""
        self._register("moved-worker", machine_id="wsl:old-box")

        self._spawn_and_run("moved-worker", claim_machine_id=HOST_MACHINE)

        self.assertEqual(
            self._machine_id("moved-worker"), HOST_MACHINE,
            "the claiming host named itself and the agent row kept a stale machine",
        )

    def test_a_spawn_for_an_agent_with_no_machine_yet_still_records_one(self):
        """An agent first seen BY the spawn has nothing to keep, so the claim's value is all there
        is. The guard must not read a missing row as a value worth protecting."""
        self._spawn_and_run("fresh-worker", claim_machine_id=HOST_MACHINE)
        self.assertEqual(self._machine_id("fresh-worker"), HOST_MACHINE)

    # --- the registration path ---------------------------------------------------------------

    def test_a_RE_REGISTRATION_that_names_no_machine_keeps_the_known_one(self):
        """Re-register is a full state refresh for everything except `description` (DECISIONS.md), and
        a runtime that does not know its host id is the ordinary case rather than a broken one."""
        self._register("resident", machine_id=HOST_MACHINE)
        self._register("resident")

        self.assertEqual(
            self._machine_id("resident"), HOST_MACHINE,
            "re-registering without a machineId blanked the agent's machine",
        )

    def test_CONTROL_a_re_registration_that_names_a_different_machine_moves_the_agent(self):
        self._register("traveller", machine_id="wsl:old-box")
        self._register("traveller", machine_id=HOST_MACHINE)

        self.assertEqual(
            self._machine_id("traveller"), HOST_MACHINE,
            "an agent that reported a new host was left on the old one",
        )

    # --- the registration path that adopts a console terminal --------------------------------

    def _seed_active_terminal(self, terminal_id: str, agent_id: str):
        """A resident agent re-registering with a live terminal takes a different write entirely --
        `_adopt_console_terminal_on_register`'s UPDATE rather than the upsert above. Same erasure,
        second statement."""
        session_id = f"sess-{terminal_id}"
        when = "2026-09-21T00:00:00Z"

        async def run():
            db = await get_db()
            try:
                await db.execute(
                    """INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, mode,
                         terminal_id, status, started_at, last_seen, spawn_spec_id, spawn_request_id)
                       VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)""",
                    (session_id, agent_id, ENVIRONMENT_ID, "claude", "resident", terminal_id,
                     "running", when, when),
                )
                await db.execute(
                    """INSERT INTO terminal_sessions (id, agent_id, environment_id, runtime, status,
                         session_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
                    (terminal_id, agent_id, ENVIRONMENT_ID, "claude", "attached",
                     session_id, when, when),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(run())

    def _register_resident_with_terminal(self, agent_id: str, terminal_id: str, *, machine_id=None):
        payload = {"agentId": agent_id, "role": "coder", "runtime": "claude",
                   "sessionMode": "resident", "terminalId": terminal_id}
        if machine_id is not None:
            payload["machineId"] = machine_id
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)

    def test_ADOPTING_A_CONSOLE_TERMINAL_without_a_machine_keeps_the_known_one(self):
        self._register("console-resident", machine_id=HOST_MACHINE)
        self._seed_active_terminal("term-1", "console-resident")

        self._register_resident_with_terminal("console-resident", "term-1")

        self.assertEqual(
            self._machine_id("console-resident"), HOST_MACHINE,
            "adopting a console terminal blanked the agent's machine",
        )

    def test_CONTROL_adopting_a_console_terminal_WITH_a_machine_writes_it(self):
        self._register("console-traveller", machine_id="wsl:old-box")
        self._seed_active_terminal("term-2", "console-traveller")

        self._register_resident_with_terminal("console-traveller", "term-2", machine_id=HOST_MACHINE)

        self.assertEqual(
            self._machine_id("console-traveller"), HOST_MACHINE,
            "the adoption path ignored a machine the agent reported",
        )
