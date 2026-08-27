"""A send to an agent that can NEVER start is reported, not quietly queued.

`_preflight_live_send_recipients` decides which recipients can handle a message now. Its own
docstring: "Normal chat is live-wake-only: do not leave future inbox work behind when a recipient
cannot start handling the message now."

IT CHECKED A HAND-WRITTEN SET: `{"offline", "stale", "stopped"}`. Third instance of the same literal
in this codebase, with the same two faults as the analytics count (22e471f7) and the working-promotion
(4295554f):

  * `stale` is not a canonical status -- "Proof-based: no time-decay states, no `idle`, no `stale`".
  * `misconfigured` is missing, and the vocabulary defines it as "Identity exists but can never start.
    Not send-recoverable; a human must fix the config."

An agent that can never start is the strongest possible case for this check, and it was the one case
the check did not make. The send fell through to the launchable path: accepted, queued, sender told
nothing.

HOW MISCONFIGURED ARISES, and why this is reachable rather than theoretical: `_agent_config_defect`
returns a defect for a MANAGED agent whose runtime is not in `_LAUNCHABLE_RUNTIMES`, and `runtime` is
unvalidated at registration (`Optional[str]`, normalised but not checked against a vocabulary). A
typo'd runtime on a managed agent produces exactly this state.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.status_engine import NON_LIVE_AGENT_STATUSES
from service.tests._base import FastApiTestCase


class ASendToAnUnstartableAgentIsRefused(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    def setUp(self):
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:pf-host:default", "machineId": "linux:pf-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-pf", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)

    def _register(self, agent_id, runtime="claude-code"):
        response = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": runtime,
            "sessionMode": "managed", "machineId": "linux:pf-host", "bridgeId": "bridge-pf",
            "capabilities": ["managed-run"],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _status(self, agent_id):
        from service.reconcilers import status_cache
        status_cache._LIVE_STATE_CACHE.clear()
        body = self.client.get(f"/api/v1/agents/{agent_id}").json()
        return (body.get("agent") or {}).get("status")

    def _preflight(self, agent_id):
        import asyncio

        from service.api_core.send_preflight import _preflight_live_send_recipients
        from service.db import get_db
        from service.reconcilers import status_cache

        async def go():
            status_cache._LIVE_STATE_CACHE.clear()
            db = await get_db()
            try:
                return await _preflight_live_send_recipients(db, [agent_id])
            finally:
                await db.close()

        return asyncio.run(go())

    def test_the_FIXTURE_really_produces_a_misconfigured_agent(self):
        """POSITIVE CONTROL on the setup. If the runtime typo did not produce `misconfigured`, every
        assertion below would be testing a differently-broken agent and passing for another reason."""
        self._register("pf-broken", runtime="clade-code")
        self.assertEqual(self._status("pf-broken"), "misconfigured")

    def test_a_MISCONFIGURED_recipient_is_not_launchable(self):
        """The case the old set missed. It can never start, so a message left for it is inbox work
        that nothing will ever pick up."""
        self._register("pf-broken", runtime="clade-code")
        launchable, not_started = self._preflight("pf-broken")
        self.assertEqual(launchable, [], "a send to an unstartable agent was accepted")
        self.assertEqual(len(not_started), 1, not_started)
        self.assertEqual(not_started[0].get("recipientStatus"), "misconfigured")

    def test_the_REASON_names_the_status_so_the_sender_can_act(self):
        """A refusal that does not say why sends the operator looking in the wrong place -- which is
        what this whole class of defect keeps producing."""
        self._register("pf-broken", runtime="clade-code")
        _, not_started = self._preflight("pf-broken")
        blob = repr(not_started[0])
        self.assertIn("misconfigured", blob, blob)

    def test_a_HEALTHY_recipient_is_still_launchable(self):
        """ANTI-VACUITY. A preflight that refused everything would satisfy every assertion above and
        stop the fleet from talking to itself."""
        self._register("pf-ok")
        launchable, not_started = self._preflight("pf-ok")
        self.assertEqual(not_started, [], not_started)
        self.assertEqual([r[0] for r in launchable], ["pf-ok"])

    def test_the_set_is_DERIVED_from_the_partition(self):
        """So a fourth non-live status is covered without anyone remembering this file -- and so the
        retired `stale` cannot come back."""
        from service.api_core import send_preflight

        source = Path(send_preflight.__file__).read_text(encoding="utf-8")
        assignment = [l for l in source.split("\n") if l.strip().startswith("unavailable_statuses =")]
        self.assertEqual(len(assignment), 1, assignment)
        self.assertIn("NON_LIVE_AGENT_STATUSES", assignment[0])
        self.assertNotIn("stale", assignment[0])

    def test_every_non_live_status_would_be_refused(self):
        """The property behind the set, asserted without needing an agent in each state."""
        from service.api_core import send_preflight  # noqa: F401

        self.assertEqual(sorted(NON_LIVE_AGENT_STATUSES), ["misconfigured", "offline", "stopped"])


if __name__ == "__main__":
    unittest.main()
