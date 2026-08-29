r"""An environment bridge that restarts can take its managed agents back.

DRIVEN THROUGH THE REAL `PATCH /api/v1/agents/{id}/runtime-state`, which is the endpoint the
environment bridge re-adopts through (`managed-environment-sync.mjs` PATCHes its own
`BRIDGE_INSTANCE_ID` for every agent it hosts).

THE DEFECT. The route refused EVERY change to a managed agent's `bridgeInstanceId`::

    if _normalize_session_mode(...) == "managed":
        if current_bridge and next_bridge and current_bridge != next_bridge:
            next_state["bridgeInstanceId"] = current_bridge

so the first non-empty value was the owner for life. The environment bridge's own re-adoption was
silently reverted, and the agent stayed bound to the dead predecessor.

WHERE IT CAME FROM, because the shape is worth more than the line. Until `e3c3ce8c`
("fix(sessions): make ownership switching manual", 2026-05-26) the same guard fired only when the
incoming id was a PENDING RESIDENT TAKEOVER candidate -- one named writer, refused for one stated
reason. That commit deleted `pendingResidentTakeover` and kept the ACTION while dropping the
CONDITION that justified it. Nothing in the suite noticed, because a guard that refuses more than it
should still refuses everything the old tests asked it to refuse.

MEASURED ON THE OPERATOR'S HOST, 2026-08-29, with one environment online (`e720826b-...`): 19 of 24
managed agents carried a `bridgeInstanceId` naming some other bridge, six of them sharing a single
dead generation. The two that read correctly were the two the CURRENT bridge had spawned -- the spawn
path writes `runtime_state` directly and never meets this guard.

WHAT READS IT, each at the strength the source actually supports, because the first version of this
paragraph inflated the worst one:

  * `claim_block_reason.py` returns `bridge_not_current` when the recorded owner differs from the
    claiming bridge, for any agent that is not managed-with-an-`environmentId`. A managed agent
    missing that key -- which is the state a re-registration used to leave behind -- then has no
    valid claimer at all and its run sits queued. READ FROM SOURCE, not executed here.
  * `aify-comms doctor`'s `managed-orphans` calls a working agent an orphan "bound to no live
    bridge" and prescribes relaunching the environment bridge, which reaps the managed fleet. A
    false alarm whose remedy is destructive.
  * `reap-managed-survivors.js` skips a survivor owned by a DIFFERENT LIVE bridge. A stale owner
    removes that protection, so one environment's boot sweep can reap another's agents. This needs
    two live environments to bite and this host has one, so it is a latent consequence rather than
    an active one -- and on a boot with `treatSelfAsOrphan` the outcome is the same either way.

None of the three is claimed as an observed event: each needs a bridge restart to demonstrate, which
is forbidden while the operator's fleet is live.

THE RULE NOW: an id that belongs to a currently-ONLINE environment bridge may take ownership.
Anything else leaves the recorded owner alone, including when liveness cannot be determined.
"""
from __future__ import annotations

import asyncio

from service.db import get_db
from service.tests._base import FastApiTestCase

DEAD_BRIDGE = "dead-bridge-0001"
LIVE_BRIDGE = "live-bridge-0002"
SIDECAR_BRIDGE = "sidecar-bridge-0003"
ENVIRONMENT_ID = "windows:test-host:default"


class LiveBridgeCanReAdoptTests(FastApiTestCase):
    def _execute(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _environment(self, bridge_id: str, environment_id: str = ENVIRONMENT_ID) -> None:
        response = self.client.post("/api/v1/environments/heartbeat", json={
            "id": environment_id,
            "label": "Windows on test-host",
            "machineId": "windows:test-host",
            "os": "windows",
            "kind": "windows",
            "bridgeId": bridge_id,
            "cwdRoots": ["C:/work"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"]}],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _agent(self, agent_id: str, session_mode: str = "managed") -> None:
        response = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "claude-code",
            "sessionMode": session_mode, "launchMode": "detached",
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _patch(self, agent_id: str, runtime_state: dict) -> None:
        response = self.client.patch(
            f"/api/v1/agents/{agent_id}/runtime-state", json={"runtimeState": runtime_state},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _state(self, agent_id: str) -> dict:
        body = self.client.get(f"/api/v1/agents/{agent_id}").json()
        return body["agent"].get("runtimeState") or {}

    def _owner(self, agent_id: str) -> str:
        return str(self._state(agent_id).get("bridgeInstanceId") or "")

    def _owned_by_a_dead_bridge(self, agent_id: str) -> None:
        """The state every adopted agent is in after its environment bridge restarts."""
        self._agent(agent_id)
        self._patch(agent_id, {"bridgeInstanceId": DEAD_BRIDGE, "environmentId": ENVIRONMENT_ID})
        self.assertEqual(self._owner(agent_id), DEAD_BRIDGE, "the seed did not take")

    # ---- the correction ---------------------------------------------------------------------

    def test_THE_DEFECT_a_live_environment_bridge_takes_its_agent_back(self):
        self._owned_by_a_dead_bridge("adopted")
        self._environment(LIVE_BRIDGE)
        self._patch("adopted", {"bridgeInstanceId": LIVE_BRIDGE, "environmentId": ENVIRONMENT_ID})
        self.assertEqual(self._owner("adopted"), LIVE_BRIDGE, (
            "the environment bridge hosting this agent's delivery loop could not record that it "
            "hosts it; the boot reaper reads this field and kills survivors whose owner is neither "
            "itself nor live"
        ))

    def test_an_id_belonging_to_no_environment_is_still_refused(self):
        """The case the guard was built for, and the reason it cannot simply be deleted. A managed
        agent's per-session sidecar PATCHes its own MCP bridge id, which owns nothing."""
        self._owned_by_a_dead_bridge("sidecar-target")
        self._environment(LIVE_BRIDGE)
        self._patch("sidecar-target", {"bridgeInstanceId": SIDECAR_BRIDGE})
        self.assertEqual(self._owner("sidecar-target"), DEAD_BRIDGE)

    def test_an_OFFLINE_environments_bridge_is_refused(self):
        """THE NEGATIVE CONTROL for the test above it. Both ids are in the environments table; only
        one is answering. A rule that keyed on PRESENCE rather than LIVENESS would pass the accept
        test and hand a dead bridge somebody else's agent."""
        self._owned_by_a_dead_bridge("stale-claimant")
        self._environment(LIVE_BRIDGE, environment_id="wsl:test-host:default")
        self._execute(
            "UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?",
            ("wsl:test-host:default",),
        )
        self._patch("stale-claimant", {"bridgeInstanceId": LIVE_BRIDGE})
        self.assertEqual(self._owner("stale-claimant"), DEAD_BRIDGE)

    def test_liveness_is_read_at_PATCH_TIME_not_at_registration(self):
        """An environment that WAS online and has gone quiet must stop being an authority. Same row,
        same bridge id, two different answers, and only the clock moved."""
        self._owned_by_a_dead_bridge("clock")
        self._environment(LIVE_BRIDGE)
        self._execute(
            "UPDATE environments SET last_seen = '2020-01-01T00:00:00Z' WHERE id = ?", (ENVIRONMENT_ID,),
        )
        self._patch("clock", {"bridgeInstanceId": LIVE_BRIDGE})
        self.assertEqual(self._owner("clock"), DEAD_BRIDGE, "a silent environment kept its authority")
        self._environment(LIVE_BRIDGE)
        self._patch("clock", {"bridgeInstanceId": LIVE_BRIDGE})
        self.assertEqual(self._owner("clock"), LIVE_BRIDGE, "heartbeating again did not restore it")

    def test_a_DEGRADED_environment_may_not_take_an_agent(self):
        """`degraded` means the bridge is answering but unhealthy. That is enough to keep the work it
        already has and not enough to be handed somebody else's -- the same line `spawn_lifecycle`
        has always drawn, which is why both now read one function instead of two copies of it."""
        self._owned_by_a_dead_bridge("degraded-claimant")
        self._environment(LIVE_BRIDGE)
        self._execute("UPDATE environments SET status = 'degraded' WHERE id = ?", (ENVIRONMENT_ID,))
        self._patch("degraded-claimant", {"bridgeInstanceId": LIVE_BRIDGE})
        self.assertEqual(self._owner("degraded-claimant"), DEAD_BRIDGE)

    # ---- what the change must not break -----------------------------------------------------

    def test_an_omitted_id_keeps_the_recorded_owner(self):
        """A DELIBERATE CHANGE, not a side effect. The route used to let a PATCH that carried no
        `bridgeInstanceId` at all wipe the field, on the same code path that carefully preserves
        `virtualTerminalId` for exactly that reason. An omitted key is a caller that did not know the
        value, never a request to disown the agent."""
        self._owned_by_a_dead_bridge("partial")
        self._patch("partial", {"sessionId": "abc-123"})
        self.assertEqual(self._owner("partial"), DEAD_BRIDGE)
        self.assertEqual(self._state("partial").get("sessionId"), "abc-123", "the rest was dropped")

    def test_a_resident_agent_still_owns_itself(self):
        """For a resident agent the field means its own MCP bridge, so its own PATCH is the
        authority. A fix that made every writer prove environment liveness would break the case that
        was already right -- no environment row names a resident sidecar."""
        self._agent("resident", session_mode="resident")
        self._patch("resident", {"bridgeInstanceId": SIDECAR_BRIDGE})
        self.assertEqual(self._owner("resident"), SIDECAR_BRIDGE)
        self._patch("resident", {"bridgeInstanceId": "resident-bridge-restarted"})
        self.assertEqual(self._owner("resident"), "resident-bridge-restarted")

    def test_a_refused_claim_keeps_the_environment_it_named_too(self):
        """`environmentId` travels with `bridgeInstanceId`: `managed-environment-sync` reads it to
        decide whether an agent belongs to this environment at all, so half-applying a refused claim
        would strand the agent between two."""
        self._owned_by_a_dead_bridge("paired")
        self._patch("paired", {"bridgeInstanceId": SIDECAR_BRIDGE, "environmentId": "some:other:env"})
        state = self._state("paired")
        self.assertEqual(state.get("bridgeInstanceId"), DEAD_BRIDGE)
        self.assertEqual(state.get("environmentId"), ENVIRONMENT_ID)

    def test_an_accepted_claim_brings_its_own_environment(self):
        """The other half of the pair. An accepted claim must NOT have the old environment restored
        over it -- a bridge that adopts an agent into its environment writes both together."""
        self._owned_by_a_dead_bridge("moved")
        self._environment(LIVE_BRIDGE, environment_id="wsl:test-host:default")
        self._patch("moved", {"bridgeInstanceId": LIVE_BRIDGE, "environmentId": "wsl:test-host:default"})
        state = self._state("moved")
        self.assertEqual(state.get("bridgeInstanceId"), LIVE_BRIDGE)
        self.assertEqual(state.get("environmentId"), "wsl:test-host:default")
