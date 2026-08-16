"""Who may type into another agent's terminal — the gate on console keystroke injection.

`POST /agents/{agent_id}/console/input` writes bytes into a live PTY. Its own docstring calls the
caller check SAFETY, and `containers/proxy.py` names this capability when explaining why the hub's
API key must never reach a sub-container: "a logging/compromised image -> full API incl. console
keystroke injection". Both of its refusals were among the operator-facing 4xx messages in this
service that no test had ever exercised.

WHAT THE GATE IS FOR. The endpoint records the caller in two places — the terminal control's
`requested_by` and an `agent_console_input` audit event carrying `{from, controlId, bytes}`. That
audit is the only account of who moved an agent's keyboard, so an unidentified or unregistered caller
must be refused BEFORE anything is queued, not recorded as empty afterwards.

THE TWO REFUSALS ARE DIFFERENT ANSWERS AND THE CODES SAY SO:
  * 400 — no `from` at all. The request is malformed; the client forgot the field.
  * 403 — a `from` that is not a registered agent. The request is well-formed and REFUSED, which is
    the one an operator needs to see in a log.
Collapsing them would tell a caller with a typo'd id that its request was malformed.

ORDER MATTERS AND IS PINNED. The unknown-AGENT 404 comes first, then the caller checks — so probing
this endpoint cannot be used to distinguish "agent exists" from "you may not talk to it" in the other
direction, and a caller with no `from` learns nothing about which agents exist.
"""

from __future__ import annotations

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase


class ConsoleInputCallerGateTests(FastApiTestCase):
    """Driven through the real app: the gate is three DB reads and their order is the subject."""

    def _register(self, agent_id: str, **overrides) -> None:
        payload = {
            "agentId": agent_id,
            "role": "coder",
            "runtime": "codex",
            "sessionMode": "managed",
            **overrides,
        }
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertIn(response.status_code, (200, 201), response.text)

    def _input(self, agent_id: str, body: dict) -> object:
        return self.client.post(f"/api/v1/agents/{agent_id}/console/input", json=body)

    def test_a_missing_from_is_a_400_and_queues_nothing(self):
        """The client forgot the field — malformed, not refused."""
        self._register("target-agent")
        for body in ({"text": "hello"}, {"text": "hello", "from": ""}, {"text": "hello", "from": "   "}):
            with self.subTest(body=body):
                response = self._input("target-agent", body)
                self.assertEqual(response.status_code, 400, response.text)
                # The FULL message, not a fragment: the refusal-coverage scan matches on the
                # distinctive text, and a test asserting half of it leaves the refusal counted
                # as unexercised — which is the measurement lying, not the code.
                self.assertEqual(
                    response.json()["detail"],
                    "console input requires a `from` caller (the requesting agent id)",
                )

    def test_an_UNREGISTERED_caller_is_a_403_not_a_400(self):
        """Well-formed and REFUSED. This is the one that belongs in an audit log, and the distinct
        code is what lets an operator tell a typo'd id from a client that omitted the field."""
        self._register("target-agent")
        response = self._input("target-agent", {"text": "hello", "from": "ghost-agent"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("'ghost-agent' is not a registered agent", response.json()["detail"])

    def test_an_unknown_TARGET_is_a_404_before_the_caller_is_even_read(self):
        """Order, pinned: the target check runs first, so a bad caller against a missing agent
        answers 404 rather than 403. Both orders are defensible; only one is what the code does, and
        a later reorder would change what a probing client can infer."""
        response = self._input("no-such-agent", {"text": "hello", "from": "also-nobody"})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("'no-such-agent' not found", response.json()["detail"])

    def test_a_registered_caller_gets_past_the_gate(self):
        """The other direction, or the tests above would pass on an endpoint that refuses everything.

        There is no live console in this fixture, so the request stops at the honest
        "no live console" answer — which is exactly the point: it got THROUGH the caller gate and was
        stopped by a fact about the world instead.
        """
        self._register("target-agent")
        self._register("caller-agent")
        response = self._input("target-agent", {"text": "hello", "from": "caller-agent"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["ok"], "queued nothing — there is no console to write to")
        self.assertFalse(payload["live"])
        self.assertIn("no live console", payload["message"])

    def test_the_caller_may_be_the_target_itself(self):
        """An agent unsticking its own TUI is the normal case for this endpoint, so the gate must not
        require caller != target."""
        self._register("self-agent")
        response = self._input("self-agent", {"text": "", "enter": True, "from": "self-agent"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["ok"])

    def test_the_refusals_carry_different_status_codes(self):
        """Stated as its own assertion because the two messages are one edit away from being merged,
        and the codes are what a caller branches on."""
        self._register("target-agent")
        missing = self._input("target-agent", {"text": "x"})
        unregistered = self._input("target-agent", {"text": "x", "from": "ghost"})
        self.assertNotEqual(
            missing.status_code, unregistered.status_code,
            "a malformed request and a refused one must not answer with the same code",
        )
        self.assertEqual({missing.status_code, unregistered.status_code}, {400, 403})
