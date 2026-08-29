r"""A managed agent's own registration does not overwrite the environment bridge that owns it.

DRIVEN THROUGH THE REAL `POST /api/v1/agents` ROUTE, because the first attempt at this fix was not.

WHAT HAPPENED. `runtimeState.bridgeInstanceId` is read as "which bridge owns this agent" by
`aify-comms doctor`'s `managed-orphans`, by the bridge's `managed-ownership.mjs`, and by this
service's own `bridge_not_current` guards. For a MANAGED agent it means the ENVIRONMENT BRIDGE hosting
the delivery loop. Observed live on 2026-08-29::

    live environment bridge          e720826b-741c-455f-b8bd-e4659777e0c7
    comms-senior-dev, 03:44          cd17b4c8-ebdb-4980-9492-b2a69435b590
    comms-senior-dev, minutes later  a0897fff-a6c4-4c99-8349-8b9d750dd22a
    ef-manager (just spawned)        e720826b-741c-455f-b8bd-e4659777e0c7

Doctor then reported that agent as an orphaned delivery loop "bound to no live bridge" while it was
answering messages, advising a bridge relaunch -- which reaps the managed fleet.

THE FIRST FIX GUARDED THE WRONG CALL. `fba45bde` suppressed the follow-up PATCH from the sidecar, and
a reviewer showed the registration POST that precedes it still carried `bridgeId`, which this route
wrote into a FRESH `runtime_state` unconditionally. Before ``{"bridgeInstanceId":
"environment-bridge"}``, after ``{"bridgeInstanceId": "sidecar-bridge"}``, with all seven new
bridge-side tests green. A guard placed after the load-bearing write is decoration, and this repo has
a rule about that shape.

So these tests drive the ROUTE. The mutant at the end restores the original assignment and requires
this file to go RED -- because a test that has never been watched fail is a rumour.
"""
from __future__ import annotations

import json

from service.tests._base import FastApiTestCase

ENVIRONMENT_BRIDGE = "environment-bridge-0001"
SIDECAR_BRIDGE = "sidecar-bridge-0002"


class SidecarDoesNotTakeOwnershipTests(FastApiTestCase):
    def _register(self, agent_id: str, **over) -> None:
        payload = {
            "agentId": agent_id, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "launchMode": "detached",
        }
        payload.update(over)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)

    def _own(self, agent_id: str, bridge_instance_id: str) -> None:
        """Record an environment bridge as the owner, the way the spawn path does."""
        response = self.client.patch(
            f"/api/v1/agents/{agent_id}/runtime-state",
            json={"runtimeState": {"bridgeInstanceId": bridge_instance_id, "environmentId": "env-1"}},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _owner(self, agent_id: str) -> str:
        body = self.client.get(f"/api/v1/agents/{agent_id}").json()
        return str((body["agent"].get("runtimeState") or {}).get("bridgeInstanceId") or "")

    def test_THE_DEFECT_a_managed_wrapper_child_does_not_take_ownership(self):
        """The reviewer's exact reproduction."""
        self._register("owned-agent", bridgeId=ENVIRONMENT_BRIDGE)
        self._own("owned-agent", ENVIRONMENT_BRIDGE)
        self.assertEqual(self._owner("owned-agent"), ENVIRONMENT_BRIDGE, "the seed did not take")

        self._register("owned-agent", bridgeId=SIDECAR_BRIDGE, managedWrapperChild=True)
        self.assertEqual(self._owner("owned-agent"), ENVIRONMENT_BRIDGE, (
            "the agent's own sidecar overwrote the environment bridge that hosts its delivery loop; "
            "doctor reads this field to decide whether a running loop is an orphan, and its remedy "
            "for an orphan is to relaunch the bridge, which reaps the fleet"
        ))

    def test_a_managed_registration_without_the_flag_is_refused_too(self):
        """The declared MODE is enough on its own. `managedWrapperChild` is the belt; the mode is the
        braces, and a launcher that stopped setting the flag must not reopen this."""
        self._register("mode-only", bridgeId=ENVIRONMENT_BRIDGE)
        self._own("mode-only", ENVIRONMENT_BRIDGE)
        self._register("mode-only", bridgeId=SIDECAR_BRIDGE)
        self.assertEqual(self._owner("mode-only"), ENVIRONMENT_BRIDGE)

    def test_A_RESIDENT_STILL_OWNS_ITSELF(self):
        """THE CONTROL, and it is the one that matters. For a resident agent the field genuinely means
        its own MCP bridge, so a fix that silenced every writer would break the case that was right."""
        self._register("resident-agent", sessionMode="resident", bridgeId=SIDECAR_BRIDGE)
        self.assertEqual(self._owner("resident-agent"), SIDECAR_BRIDGE, (
            "a resident agent's own bridge no longer records itself as owner, which is the opposite "
            "defect: nothing would name the owner at all"
        ))

    def test_a_brand_new_managed_agent_records_no_owner_rather_than_a_wrong_one(self):
        """There is no prior owner to keep and no authority in the request. Empty is the honest
        answer, and the guards that read this field fail closed on empty -- while a WRONG owner sends
        work to a process that hosts nothing."""
        self._register("fresh-managed", bridgeId=SIDECAR_BRIDGE, managedWrapperChild=True)
        self.assertEqual(self._owner("fresh-managed"), "")

    def test_the_environment_bridge_can_still_claim_it_afterwards(self):
        """The path that must keep working: the environment bridge adopts or spawns the agent and
        PATCHes the field. If registration blocked that too, a managed agent would never have an owner
        and every one of them would read as an orphan -- the same alarm, inverted."""
        self._register("adoptable", bridgeId=SIDECAR_BRIDGE, managedWrapperChild=True)
        self.assertEqual(self._owner("adoptable"), "")
        self._own("adoptable", ENVIRONMENT_BRIDGE)
        self.assertEqual(self._owner("adoptable"), ENVIRONMENT_BRIDGE)

    def test_the_rest_of_runtime_state_is_untouched_by_the_guard(self):
        """Scope. The guard decides ONE key; a registration that also reset `environmentId` would
        break adoption in a way no test here would notice, since every assertion above reads one
        field."""
        self._register("keeps-env", bridgeId=ENVIRONMENT_BRIDGE)
        self._own("keeps-env", ENVIRONMENT_BRIDGE)
        self._register("keeps-env", bridgeId=SIDECAR_BRIDGE, managedWrapperChild=True)
        body = self.client.get("/api/v1/agents/keeps-env").json()
        state = body["agent"].get("runtimeState") or {}
        self.assertEqual(state.get("bridgeInstanceId"), ENVIRONMENT_BRIDGE)
        self.assertIn("environmentId", json.dumps(state), (
            "the registration dropped `environmentId`, which the sync pass uses to decide whether an "
            "agent belongs to this environment at all"
        ))
