"""Required fields and length caps — seven small refusals, each guarding a different kind of write.

None of these had a test, and all of them read as exercised until fe1e22ad because
`service/tests/data/` holds pre-split copies of the handlers:

    PATCH /agents/{id}/description        400 description must be 2000 chars or fewer
    PATCH /agents/{id}/session-handle     400 sessionHandle must be 512 characters or fewer
    POST  /agents/{id}/environment        400 environmentId is required
    POST  /agents/{id}/claimer-lease      400 action must be 'acquire' or 'release'
    POST  /environments/heartbeat         400 Environment id is required
    POST  /usage                          400 source_id is required
    POST  /messages/conversation/clear    400 Need agentId and peerId

SMALL GATES ARE WHERE OFF-BY-ONE AND MISSING-STRIP LIVE, so each is tested AT the boundary rather
than well past it: exactly at the cap must pass, one over must fail. A test that sends 10,000
characters proves only that some limit exists somewhere.

WHITESPACE IS THE OTHER HALF. Every "is required" check here runs `.strip()` first, so `"   "` is
the same as absent — and that is the case a reader drops, because a caller sending spaces looks like
a caller sending something. Each required-field test covers absent, empty and whitespace.

THE CONVERSATION-CLEAR ONE NEEDS BOTH SIDES AND SAYS SO. It deletes every message between two
agents; with only one id the "conversation" is unbounded, which is a very different operation from
the one the caller asked for. That is why it refuses instead of defaulting.
"""

from __future__ import annotations

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT_ID = "lc-agent"
PEER_ID = "lc-peer"

#: The blank spellings that reach the HANDLER. `None` and an omitted field are refused earlier, by
#: the request model — see `test_an_absent_field_is_the_models_job_not_the_handlers`. Two layers
#: refusing the same thing for different reasons is fine; conflating them in one assertion is not,
#: because a 422 proves nothing about the handler's own check.
BLANKS = ("", "   ", "\t")


class InputLimitRefusalTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent_id in (AGENT_ID, PEER_ID):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"},
            )
            self.assertEqual(response.status_code, 200, response.text)

    # ── length caps, tested AT the boundary ──────────────────────────────────────────────────

    def test_a_description_of_exactly_2000_characters_is_accepted(self):
        response = self.client.patch(
            f"/api/v1/agents/{AGENT_ID}/description", json={"description": "x" * 2000},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_description_one_character_over_the_cap_is_refused(self):
        response = self.client.patch(
            f"/api/v1/agents/{AGENT_ID}/description", json={"description": "x" * 2001},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"], "description must be 2000 chars or fewer",
        )

    def test_the_description_cap_counts_CHARACTERS_not_bytes(self):
        """2000 emoji is 2000 characters and roughly 8000 bytes. A cap that had drifted to a byte
        count would refuse a description well inside the documented limit, and the message would
        name a number the caller had not exceeded."""
        response = self.client.patch(
            f"/api/v1/agents/{AGENT_ID}/description", json={"description": "🙂" * 2000},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_session_handle_of_exactly_512_characters_is_accepted(self):
        response = self.client.patch(
            f"/api/v1/agents/{AGENT_ID}/session-handle", json={"sessionHandle": "h" * 512},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_session_handle_one_character_over_the_cap_is_refused(self):
        response = self.client.patch(
            f"/api/v1/agents/{AGENT_ID}/session-handle", json={"sessionHandle": "h" * 513},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"], "sessionHandle must be 512 characters or fewer",
        )

    def test_the_handle_is_SANITISED_before_it_is_measured(self):
        """An unexpanded shell placeholder is dropped rather than stored, and the length check sees
        the sanitised value. Order matters: measuring first would refuse a 600-character
        `${VAR}`-laden string that sanitises to nothing."""
        response = self.client.patch(
            f"/api/v1/agents/{AGENT_ID}/session-handle",
            json={"sessionHandle": "$HERMES_SESSION_ID"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        info = self.client.get(f"/api/v1/agents/{AGENT_ID}")
        self.assertEqual(
            info.json()["agent"]["sessionHandle"], "",
            "a literal placeholder must never be stored as a resume handle",
        )

    # ── required fields, including the whitespace case ───────────────────────────────────────

    def test_assigning_an_environment_needs_one_named(self):
        for value in BLANKS:
            with self.subTest(environmentId=value):
                response = self.client.post(
                    f"/api/v1/agents/{AGENT_ID}/environment", json={"environmentId": value},
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "environmentId is required")

    def test_an_environment_heartbeat_needs_an_id(self):
        for value in BLANKS:
            with self.subTest(id=value):
                response = self.client.post(
                    "/api/v1/environments/heartbeat",
                    json={"id": value, "machineId": "linux:box", "os": "linux"},
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "Environment id is required")

    def test_a_usage_report_needs_a_source(self):
        """Usage is stored keyed by source; without one there is nothing to attribute it to and the
        next reader would see a quota figure belonging to nobody."""
        for value in BLANKS:
            with self.subTest(source_id=value):
                response = self.client.post("/api/v1/usage", json={"source_id": value, "pct": 50})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "source_id is required")

    def test_a_usage_report_with_a_source_is_stored(self):
        response = self.client.post(
            "/api/v1/usage", json={"source_id": "anthropic", "weeklyPctLeft": 42},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source_id"], "anthropic")

    def test_clearing_a_conversation_needs_BOTH_sides(self):
        """It deletes every message between two agents. With one id the "conversation" is unbounded
        — a different operation from the one the caller asked for — so it refuses rather than
        defaulting to something plausible."""
        for body in (
            {"agentId": AGENT_ID, "peerId": ""},
            {"agentId": AGENT_ID, "peerId": "  "},
            {"agentId": "   ", "peerId": PEER_ID},
            {"agentId": "", "peerId": ""},
        ):
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/v1/messages/conversation/clear", json=body,
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "Need agentId and peerId")

    def test_an_absent_field_is_the_models_job_not_the_handlers(self):
        """The layer above. `environmentId`, `id`, `agentId` and `peerId` are REQUIRED strings, so
        omitting one or sending null is a 422 before the handler runs, and its own 400 is
        unreachable for that case. Pinned because the two look identical to a caller and are refused
        by different layers — and because making a field Optional would silently move a 422 into the
        handler's 400 with nobody noticing which check is now doing the work."""
        cases = (
            (f"/api/v1/agents/{AGENT_ID}/environment", {"environmentId": None}),
            ("/api/v1/environments/heartbeat", {"id": None, "machineId": "linux:box"}),
            ("/api/v1/messages/conversation/clear", {}),
            ("/api/v1/messages/conversation/clear", {"agentId": AGENT_ID}),
            ("/api/v1/messages/conversation/clear", {"peerId": PEER_ID}),
        )
        for path, body in cases:
            with self.subTest(path=path, body=sorted(body)):
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 422, response.text)

    def test_clearing_a_conversation_with_both_sides_works(self):
        response = self.client.post(
            "/api/v1/messages/conversation/clear",
            json={"agentId": AGENT_ID, "peerId": PEER_ID},
        )
        self.assertEqual(response.status_code, 200, response.text)

    # ── the claimer lease, whose two actions are opposites ───────────────────────────────────

    def test_the_claimer_lease_action_is_exactly_acquire_or_release(self):
        """Two actions that undo each other, so anything unrecognised must not fall through to
        either. `""` is included because an omitted action reads as one."""
        for action in ("", "take", "drop", "claim", "acquired", "RELEASE-ALL"):
            with self.subTest(action=action):
                response = self.client.post(
                    f"/api/v1/agents/{AGENT_ID}/claimer-lease",
                    json={"action": action, "bridgeId": "b1"},
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"], "action must be 'acquire' or 'release'",
                )

    def test_both_lease_actions_are_accepted_in_any_casing(self):
        for action in ("acquire", "release", "ACQUIRE", "  Release "):
            with self.subTest(action=action):
                response = self.client.post(
                    f"/api/v1/agents/{AGENT_ID}/claimer-lease",
                    json={"action": action, "bridgeId": "b1"},
                )
                self.assertEqual(response.status_code, 200, response.text)
