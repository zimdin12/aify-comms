"""A heartbeat the arbitration threw away used to answer exactly like one it kept.

THE SILENCE THIS ENDS. `/environments/heartbeat` arbitrates supersession: when the stored bridge
started later than the incoming one -- or when the incoming beat carries no `metadata.bridgeStartedAt`
to arbitrate on -- it keeps the incumbent and returns without writing anything. It returned
`{"ok": true, "environment": ...}`, byte-identical in shape to an accepted beat.

WHAT THAT COST, measured 2026-09-02. A claimer sent its start time at the top level instead of inside
`metadata`. Every beat landed on that branch, was discarded, and answered `ok: true`. The caller kept
beating every 30 seconds believing it was the claimer; `metadata.bridgeLastSeen` never moved; `/spawn`
refused every request with a 409 nobody could connect to a cause. Both sides reported healthy for
hours. The bug was in the caller -- and it was UNFINDABLE from either end, because the one message
that could have named it said the opposite.

THE PAIR IS THE POINT. `accepted: False` is only readable against an `accepted: True` a caller can
also observe; otherwise a missing field has to be read as success, which is the same silence one
version later. `ok` stays TRUE in both: the request was well-formed and the row is fine. What is added
is WHO the service recognises as the claimer, which a caller can compare against its own id.

This is the shape the repo has already fixed three times under "no evidence is not a pass" -- a check
that could not gather evidence must not read as a pass.
"""

from __future__ import annotations

from service.tests._base import FastApiTestCase


class ARefusedHeartbeatSaysSoTests(FastApiTestCase):
    DB_NAME = "aify-test-refused-heartbeat.db"
    ENV = "windows:arbitration:default"

    def _beat(self, **overrides):
        body = {
            "id": self.ENV, "kind": "windows", "os": "windows",
            "machineId": "win32:arbitration",
            "runtimes": [{"runtime": "pi", "available": True}],
        }
        body.update(overrides)
        return self._client.post("/api/v1/environments/heartbeat", json=body)

    def test_an_ACCEPTED_claim_names_the_claimer_it_accepted(self):
        response = self._beat(bridgeId="bridge-a", metadata={"bridgeStartedAt": "2026-09-03T00:00:00Z"})
        self.assertEqual(response.status_code, 200, response.text)
        claimer = response.json()["claimer"]
        self.assertTrue(claimer["accepted"])
        self.assertEqual(claimer["bridgeId"], "bridge-a")

    def test_a_REFUSED_claim_says_refused_and_names_who_holds_it(self):
        """The whole defect: this used to be indistinguishable from the case above."""
        self._beat(bridgeId="bridge-incumbent", metadata={"bridgeStartedAt": "2026-09-03T00:10:00Z"})
        # An EARLIER start time loses the arbitration, correctly.
        response = self._beat(bridgeId="bridge-late", metadata={"bridgeStartedAt": "2026-09-03T00:05:00Z"})
        self.assertEqual(response.status_code, 200, response.text)
        claimer = response.json()["claimer"]
        self.assertFalse(claimer["accepted"], "a discarded beat reported itself as accepted")
        self.assertEqual(claimer["bridgeId"], "bridge-incumbent",
                         "and it must name who DOES hold the row, or the caller cannot act on it")
        self.assertTrue(claimer["reason"], "a refusal with no reason sends the reader nowhere")

    def test_THE_2026_09_02_BEAT_verbatim_is_refused_and_says_why(self):
        """The exact shape that cost the day: a bridgeId with no `metadata.bridgeStartedAt`, because
        the caller put its start time at the top level where nothing reads it."""
        self._beat(bridgeId="bridge-incumbent", metadata={"bridgeStartedAt": "2026-09-03T00:10:00Z"})
        response = self._beat(bridgeId="bridge-new", bridgeStartedAt="2026-09-03T00:20:00Z")
        claimer = response.json()["claimer"]
        self.assertFalse(claimer["accepted"])
        self.assertIn("bridgeStartedAt", claimer["reason"],
                      "the reason must name the field, since the caller's bug IS the field's place")

    def test_the_refusal_and_the_acceptance_are_DISTINGUISHABLE(self):
        """CONTROL. If both answered the same, every assertion above could hold on a field that is
        hardcoded -- which is the failure being fixed, one layer up."""
        self._beat(bridgeId="bridge-incumbent", metadata={"bridgeStartedAt": "2026-09-03T00:10:00Z"})
        refused = self._beat(bridgeId="bridge-late", metadata={"bridgeStartedAt": "2026-09-03T00:05:00Z"})
        accepted = self._beat(bridgeId="bridge-incumbent", metadata={"bridgeStartedAt": "2026-09-03T00:10:00Z"})
        self.assertNotEqual(refused.json()["claimer"]["accepted"], accepted.json()["claimer"]["accepted"])

    def test_a_beat_with_NO_bridgeId_is_not_reported_as_a_claimer(self):
        """An advertisement describes the host and claims nothing. Reporting it accepted would invent
        the authority the advertise/claim split exists to withhold -- and that split is the reason
        `bridgeLastSeen` is a separate field at all."""
        response = self._beat()
        claimer = response.json()["claimer"]
        self.assertFalse(claimer["accepted"])
        self.assertEqual(claimer["bridgeId"], "")
        self.assertIn("does not claim", claimer["reason"])

    def test_the_row_still_reflects_the_ACCEPTED_bridge_after_a_refusal(self):
        """The behaviour is unchanged -- only the reporting is new. A refusal that also mutated the
        row would be a far worse bug than the silence."""
        self._beat(bridgeId="bridge-incumbent", metadata={"bridgeStartedAt": "2026-09-03T00:10:00Z"})
        self._beat(bridgeId="bridge-late", metadata={"bridgeStartedAt": "2026-09-03T00:05:00Z"})
        listed = self._client.get("/api/v1/environments").json()["environments"]
        row = [item for item in listed if item["id"] == self.ENV][0]
        self.assertEqual(row["bridgeId"], "bridge-incumbent")
