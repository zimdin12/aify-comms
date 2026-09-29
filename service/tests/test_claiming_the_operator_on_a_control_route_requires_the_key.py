"""With `OPERATOR_KEY` set, naming the operator on a spawn, agent control, run control or compaction needs the key.

THE DEFECT (external review of 0.7.6, S4). The key gated the three sending routes and the three deletions,
and nothing else, while the name `dashboard` grants more than a label on four other routes:

  * `POST /spawn-requests`: the creator becomes the sender of the spawn brief once the worker is up
    (`running_spawn.py`), and `dashboard` makes the start REPLACE a live instance (`start_intent.py`).
    An omitted `createdBy` is stored as `dashboard`, so it is gated as one.
  * `POST /agents/{id}/control`: a `dashboard` start REPLACES; an interrupt or stop records an omitted
    name as `dashboard`, so it is gated as one too.
  * `POST /dispatch/runs/{id}/control`: the agent is told who stopped or steered it. An omitted name is an
    agent's ordinary `comms_run_interrupt` and is stored empty, so only an explicit claim is gated.
  * `POST /agents/{id}/compact/native`: `dashboard` is let through without being a registered agent.
  * `POST /agents/{id}/stop-worker`, `POST /sessions/{id}/control` and `PATCH /agents/{id}/session-mode`
    (force included): each stops, restarts or re-modes an agent in the name it records, `dashboard` when
    the caller gave none (review of 0.7.6, the S4 follow-up). The bridge's `comms_restart` and aify-env
    name themselves on session control, so gating the omitted name refuses no ordinary caller.

THE RULE is `authorize_operator`'s, through `refuse_an_unproven_operator_claim`: with no key the claim is
granted (the API key is the boundary), with one it must present `X-Aify-Operator-Key`.

Every control below reaches the route's own answer (a 404 for the record that does not exist), which a
refusal at the gate cannot produce, so the refusals cannot pass by refusing everything.
"""

from __future__ import annotations

from service.api_core.operator_authz import OPERATOR_KEY_HEADER
from service.tests._base import FastApiTestCase

SECRET = "s3cret-operator-key"
MISSING = 404


class ClaimingTheOperatorOnAControlRouteRequiresTheKey(FastApiTestCase):
    DB_NAME = "aify-operator-claim-routes.db"

    def _set_key(self, key: str):
        self.client.app.state.config.operator_key = key

    def _routes(self):
        """route -> send(actor, headers). `actor=None` omits the field. Every target is absent on purpose."""
        def spawn(actor, headers):
            body = {"environmentId": "no-such-env", "agentId": "new-agent", "runtime": "claude-code"}
            if actor is not None:
                body["createdBy"] = actor
            return self.client.post("/api/v1/spawn-requests", headers=headers, json=body)

        def agent_control(actor, headers):
            body = {"action": "interrupt"}
            if actor is not None:
                body["from_agent"] = actor
            return self.client.post("/api/v1/agents/no-such-agent/control", headers=headers, json=body)

        def run_control(actor, headers):
            body = {"action": "steer", "body": "stop what you are doing"}
            if actor is not None:
                body["from_agent"] = actor
            return self.client.post("/api/v1/dispatch/runs/no-such-run/control", headers=headers, json=body)

        def compact(actor, headers):
            body = {} if actor is None else {"from": actor}
            return self.client.post("/api/v1/agents/no-such-agent/compact/native", headers=headers, json=body)

        def stop_worker(actor, headers):
            body = {} if actor is None else {"requestedBy": actor}
            return self.client.post("/api/v1/agents/no-such-agent/stop-worker", headers=headers, json=body)

        def session_control(actor, headers):
            body = {"action": "stop"}
            if actor is not None:
                body["from_agent"] = actor
            return self.client.post("/api/v1/sessions/no-such-session/control", headers=headers, json=body)

        def session_mode(actor, headers):
            body = {"mode": "managed", "force": True}
            if actor is not None:
                body["requestedBy"] = actor
            return self.client.patch("/api/v1/agents/no-such-agent/session-mode", headers=headers, json=body)

        return {"spawn": spawn, "agent control": agent_control, "run control": run_control, "compact": compact,
                "stop worker": stop_worker, "session control": session_control, "session mode": session_mode}

    def test_with_the_key_set_an_unproven_operator_claim_is_refused(self):
        self._set_key(SECRET)
        for actor in ("dashboard", "Dashboard", "operator"):
            for route, send in self._routes().items():
                with self.subTest(actor=actor, route=route):
                    response = send(actor, {})
                    self.assertEqual(response.status_code, 403, f"{route}: {response.text[:200]}")
                    self.assertIn("does not grant permission", response.text)

    def test_with_the_key_set_an_omitted_name_the_route_records_as_dashboard_is_refused(self):
        self._set_key(SECRET)
        for route in ("spawn", "agent control", "stop worker", "session control", "session mode"):
            with self.subTest(route=route):
                self.assertEqual(self._routes()[route](None, {}).status_code, 403)

    def test_an_omitted_name_on_a_run_control_is_an_agents_call_and_passes(self):
        """`comms_run_interrupt` makes `from` optional, and the control stores it empty, not as the operator."""
        self._set_key(SECRET)
        self.assertEqual(self._routes()["run control"](None, {}).status_code, MISSING)

    def test_with_the_key_set_a_wrong_key_is_refused(self):
        self._set_key(SECRET)
        for route, send in self._routes().items():
            with self.subTest(route=route):
                self.assertEqual(send("dashboard", {OPERATOR_KEY_HEADER: "nope"}).status_code, 403)

    def test_CONTROL_the_dashboard_presenting_the_key_reaches_the_route(self):
        self._set_key(SECRET)
        for route, send in self._routes().items():
            with self.subTest(route=route):
                response = send("dashboard", {OPERATOR_KEY_HEADER: SECRET})
                self.assertEqual(response.status_code, MISSING, f"{route}: {response.text[:200]}")

    def test_CONTROL_an_ordinary_agent_needs_no_operator_key(self):
        self._set_key(SECRET)
        for route, send in self._routes().items():
            with self.subTest(route=route):
                self.assertEqual(send("some-agent", {}).status_code, MISSING, route)

    def test_CONTROL_with_no_key_configured_the_claim_is_granted(self):
        self._set_key("")
        for route, send in self._routes().items():
            with self.subTest(route=route):
                self.assertEqual(send("dashboard", {}).status_code, MISSING, route)
