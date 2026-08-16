"""Confirm vs keep: the two halves of resolving a session-identity change, and their shared refusals.

When an agent reports a session handle that differs from its pinned one and the change is not
auto-confirmable, `_park_pending_session_handle_change` parks the new id in `pending_session_id` and
answers `session-changed`. Delivery keeps targeting the OLD handle until an operator resolves it, one
of two ways:

    POST /agents/{id}/session/confirm   adopt the new id   (session_handle := pending)
    POST /agents/{id}/session/keep      keep the pinned id (pending cleared, handle untouched)

Both 409s — "has no pending session id to confirm" / "…to keep" — were among the operator-facing
refusals in this service that nothing had ever exercised.

THEY ARE A MATCHED PAIR ONE EDIT FROM DIVERGING. Same 404, same 410-for-a-tombstone, same 409, and
they must agree about WHEN they refuse while doing opposite things when they do not. So the shared
shape is asserted for both in the same loop, and the divergence — which field each writes — is
asserted per route. A test written per handler would let one drift.

WHY THE 409 IS THE INTERESTING REFUSAL. Both routes are documented idempotent, and the docstrings say
409 means "nothing to resolve". That makes it the answer an operator gets for clicking twice, so it
must be a clean refusal rather than a second write: confirming an already-confirmed change must not
re-pin a handle that has since moved on.
"""

from __future__ import annotations

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

RESOLVE_ROUTES = ("confirm", "keep")


class SessionResolvePairTests(FastApiTestCase):
    def _register(self, agent_id: str, *, session_handle: str = "") -> None:
        payload = {
            "agentId": agent_id,
            "role": "coder",
            "runtime": "codex",
            "sessionMode": "resident",
        }
        if session_handle:
            payload["sessionHandle"] = session_handle
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertIn(response.status_code, (200, 201), response.text)

    def _agent(self, agent_id: str) -> dict:
        response = self.client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["agent"]

    def _park_pending(self, agent_id: str, new_handle: str) -> None:
        """Drive the real parking path rather than writing the column by hand.

        A test that INSERTed `pending_session_id` directly would pass against a service that can no
        longer produce that state — the fixture would be asserting its own setup. This goes through
        the PATCH the bridge uses, and the anti-vacuity assertion at the end is what caught my first
        two attempts.

        REACHING THE PARK PATH NEEDS BOTH CONDITIONS, and I had neither at first:

          * `requestedBy` must be EXACTLY `"bridge-heartbeat"`. Any other caller — a dashboard set, a
            console attach — is a deliberate operator re-pin and is unguarded by design, so it adopts
            the id straight away.
          * `auto_confirm_session_id` must be OFF. It defaults to TRUE (2026-06-04) because auto-
            confirming "breaks the managed-claude session-changed -> stale-console-owner -> recycle
            loop", so on a default install a drifting id is ADOPTED and these two routes never see a
            pending id at all.

        That second point is the useful one: `confirm`/`keep` are the ORIGINAL sticky-identity
        governance behaviour and are only reachable when an operator has turned auto-confirm off.
        """
        response = self.client.patch(
            f"/api/v1/agents/{agent_id}/session-handle",
            json={"sessionHandle": new_handle, "requestedBy": "bridge-heartbeat"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self._agent(agent_id).get("pendingSessionId"), new_handle,
            "the fixture must actually park a pending id, or every test below is vacuous",
        )

    def _resolve(self, agent_id: str, action: str):
        return self.client.post(f"/api/v1/agents/{agent_id}/session/{action}", json={"requestedBy": "op"})

    def setUp(self):
        super().setUp()
        # Auto-confirm OFF — see `_park_pending`. With the default (ON) a drifting id is adopted and
        # the two routes under test are unreachable, so this is not tuning: it is the configuration
        # in which they exist at all.
        settings = self.client.get("/api/v1/settings").json()
        settings["auto_confirm_session_id"] = False
        applied = self.client.put("/api/v1/settings", json=settings)
        self.assertEqual(applied.status_code, 200, applied.text)
        self._register("pinned-agent", session_handle="handle-one")

    # ── the shared shape: both routes must agree about WHEN they refuse ──────────────────────

    def test_both_routes_409_when_there_is_nothing_pending(self):
        """The answer to clicking twice. Documented idempotent, so this must be a clean refusal.

        THE MESSAGES ARE SPELLED OUT RATHER THAN BUILT. An f-string over `action` reads better and
        leaves both refusals counted as UNEXERCISED — the refusal-coverage scan greps the test tree
        for the distinctive text, and an interpolated message appears nowhere as a literal. I made
        that exact mistake here and in the console-gate slice; the fix is to write the strings.
        """
        expected = {
            "confirm": "Agent 'pinned-agent' has no pending session id to confirm",
            "keep": "Agent 'pinned-agent' has no pending session id to keep",
        }
        for action in RESOLVE_ROUTES:
            with self.subTest(action=action):
                response = self._resolve("pinned-agent", action)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"], expected[action])

    def test_both_routes_404_for_an_unknown_agent(self):
        for action in RESOLVE_ROUTES:
            with self.subTest(action=action):
                response = self._resolve("no-such-agent", action)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertIn("'no-such-agent' not found", response.json()["detail"])

    def test_both_routes_410_for_a_removed_agent(self):
        """A tombstone is a different answer from "not found", and both routes check it before the
        404 — an operator resolving a session on an agent they deleted should be told so."""
        removed = self.client.delete("/api/v1/agents/pinned-agent")
        self.assertEqual(removed.status_code, 200, removed.text)
        for action in RESOLVE_ROUTES:
            with self.subTest(action=action):
                response = self._resolve("pinned-agent", action)
                self.assertEqual(response.status_code, 410, response.text)
                self.assertIn("was intentionally removed", response.json()["detail"])

    def test_both_routes_refuse_a_hostile_agent_id(self):
        for action in RESOLVE_ROUTES:
            for hostile in ("a b", "a;rm", ".hidden"):
                with self.subTest(action=action, agent_id=hostile):
                    response = self._resolve(hostile, action)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn("Invalid agent ID", response.json()["detail"])

    # ── the divergence: what each one actually does ──────────────────────────────────────────

    def test_confirm_ADOPTS_the_pending_id(self):
        self._park_pending("pinned-agent", "handle-two")
        response = self._resolve("pinned-agent", "confirm")
        self.assertEqual(response.status_code, 200, response.text)
        agent = self._agent("pinned-agent")
        self.assertEqual(agent["sessionHandle"], "handle-two", "the new id is now the live handle")
        self.assertFalse(agent.get("pendingSessionId"), "and nothing is left pending")

    def test_keep_RETAINS_the_pinned_id(self):
        self._park_pending("pinned-agent", "handle-two")
        response = self._resolve("pinned-agent", "keep")
        self.assertEqual(response.status_code, 200, response.text)
        agent = self._agent("pinned-agent")
        self.assertEqual(agent["sessionHandle"], "handle-one", "the pinned id is untouched")
        self.assertFalse(agent.get("pendingSessionId"), "…but the change is resolved")

    def test_the_two_routes_leave_the_agent_in_DIFFERENT_states(self):
        """Asserted directly, because it is the whole point of there being two routes and the only
        thing that stops them being one. Two agents, same parked change, opposite outcomes."""
        self._register("other-agent", session_handle="handle-one")
        self._park_pending("pinned-agent", "handle-two")
        self._park_pending("other-agent", "handle-two")

        self.assertEqual(self._resolve("pinned-agent", "confirm").status_code, 200)
        self.assertEqual(self._resolve("other-agent", "keep").status_code, 200)

        self.assertEqual(self._agent("pinned-agent")["sessionHandle"], "handle-two")
        self.assertEqual(self._agent("other-agent")["sessionHandle"], "handle-one")

    def test_resolving_twice_refuses_the_second_time_without_changing_anything(self):
        """The idempotence the docstrings claim, checked as STATE rather than as a status code: the
        second call must not re-pin a handle that has since moved on."""
        self._park_pending("pinned-agent", "handle-two")
        self.assertEqual(self._resolve("pinned-agent", "confirm").status_code, 200)

        self._park_pending("pinned-agent", "handle-three")
        self.assertEqual(self._resolve("pinned-agent", "keep").status_code, 200)
        self.assertEqual(self._agent("pinned-agent")["sessionHandle"], "handle-two")

        # Nothing pending now, so BOTH routes refuse and the handle stays where it is.
        for action in RESOLVE_ROUTES:
            with self.subTest(action=action):
                self.assertEqual(self._resolve("pinned-agent", action).status_code, 409)
                self.assertEqual(self._agent("pinned-agent")["sessionHandle"], "handle-two")

    def test_keep_surfaces_a_resume_command_for_the_pinned_id(self):
        """`keep` exists so the operator can re-attach the agent to the id it kept, so the response
        has to carry the command that does it — sourced from the runtime adapter, which is the Python
        mirror of the JS `resumeCommand` contract the bridge gate also checks."""
        self._park_pending("pinned-agent", "handle-two")
        payload = self._resolve("pinned-agent", "keep").json()
        note = str(payload.get("statusNote") or payload.get("agent", {}).get("statusNote") or "")
        self.assertIn("handle-one", note, "the note names the id being kept")
