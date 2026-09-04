"""A retired aify-comms environment bridge must not take the claimer role from aify-env.

THE DEFECT, found by external review 2026-09-04 (Round 8, H4). `/environments/heartbeat` arbitrated
supersession on `metadata.bridgeStartedAt` and NOTHING ELSE -- there was no bridge-kind or version
gate anywhere in the router. So a legacy aify-comms environment bridge, running on any host that has
not re-run `install.sh`, won the row simply by having started later, and then became the only party
`_claim_spawn_request_once` would let claim.

Two spawners on one host is the exact collision the environment tier exists to end, and v0.6.2
deleting that cluster makes it MORE live rather than less: this checkout can no longer start such a
bridge, so every surviving one is old code nobody is tracking.

THE SERVICE HAD NO SIGNAL TO IGNORE. aify-env's identity and a legacy bridge's carried the same three
fields, so this is a two-ended fix: aify-env sends `metadata.bridgeKind = "aify-env"`, and the router
reads it. ABSENT MEANS LEGACY, which is what every pre-0.6.2 sender is.

WHAT IS ASSERTED HERE is the rule from `TARGET_ARCHITECTURE.md`, in both directions plus the two
cases that must NOT change -- because a preference that also fires between two hosts of the same kind
would break the arbitration this repo spent a day getting right.
"""

from __future__ import annotations

from service.tests._base import FastApiTestCase

EARLY = "2026-09-04T10:00:00.000Z"
LATE = "2026-09-04T12:00:00.000Z"


class HostTierOutranksLegacyBridgeTests(FastApiTestCase):
    DB_NAME = "aify-test-host-tier-outranks.db"
    ENV = "windows:tier:default"

    def _beat(self, bridge_id: str, started_at: str, kind: str | None):
        """One heartbeat. `kind=None` is a LEGACY bridge: it sends no marker at all."""
        metadata: dict = {"bridgeStartedAt": started_at}
        if kind is not None:
            metadata["bridgeKind"] = kind
        return self._client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": self.ENV, "kind": "windows", "os": "windows",
                "machineId": "win32:tier",
                "bridgeId": bridge_id,
                "metadata": metadata,
            },
        )

    @staticmethod
    def _claimer(response):
        assert response.status_code == 200, response.text
        return response.json().get("claimer") or {}

    def test_the_host_tier_takes_the_row_from_a_legacy_bridge_that_started_later(self):
        # THE DEFECT ITSELF. The legacy bridge started LATER, so start-time arbitration handed it the
        # row and refused aify-env for ever.
        self._beat("legacy-1", LATE, None)
        answer = self._beat("aify-env-1", EARLY, "aify-env")
        claimer = self._claimer(answer)
        self.assertIsNot(
            claimer.get("accepted"), False,
            "a retired aify-comms bridge kept the claimer role against the host tier. It would then "
            "be the only party allowed to claim a spawn, which is the collision the environment "
            f"tier exists to end. Answer: {answer.json()}",
        )

    def test_a_legacy_bridge_is_REFUSED_when_the_host_tier_holds_the_row(self):
        # The other direction, and the one that matters on a host where aify-env is already working:
        # an old wrapper relaunching must not be able to take the row back.
        self._beat("aify-env-1", EARLY, "aify-env")
        claimer = self._claimer(self._beat("legacy-1", LATE, None))
        self.assertIs(
            claimer.get("accepted"), False,
            "a legacy bridge that started later took the row from the aify-env host tier",
        )
        self.assertIn(
            "install.sh", str(claimer.get("reason") or ""),
            "the refusal must say what to DO. This is not a clock problem and re-registering will "
            f"not help -- the bridge is the thing that should not be running. Reason: {claimer}",
        )

    def test_between_two_HOST_TIERS_the_start_time_still_decides(self):
        # THE CONTROL THAT KEEPS THE PREFERENCE NARROW. If `bridgeKind` also fired between two hosts
        # of the same kind, it would break the supersession this repo spent a day getting right --
        # a restarted aify-env must still be able to take over from its own predecessor.
        self._beat("aify-env-old", EARLY, "aify-env")
        claimer = self._claimer(self._beat("aify-env-new", LATE, "aify-env"))
        self.assertIsNot(
            claimer.get("accepted"), False,
            "a restarted aify-env could not take over from its own predecessor",
        )

    def test_between_two_LEGACY_bridges_nothing_changes_at_all(self):
        # A fleet that has not upgraded must behave exactly as it did. Both sides silent on
        # `bridgeKind` means this rule says nothing and start time decides.
        self._beat("legacy-new", LATE, None)
        claimer = self._claimer(self._beat("legacy-old", EARLY, None))
        self.assertIs(
            claimer.get("accepted"), False,
            "start-time arbitration between two legacy bridges changed. Nothing about this fix "
            "should be visible to a fleet that has not upgraded.",
        )

    def test_an_UNKNOWN_kind_is_treated_as_legacy_rather_than_trusted(self):
        # A guard that passes on an unrecognised value is decoration. Only the one name this service
        # knows may outrank a bridge; anything else is a sender it has never heard of.
        self._beat("aify-env-1", EARLY, "aify-env")
        claimer = self._claimer(self._beat("stranger-1", LATE, "something-else"))
        self.assertIs(
            claimer.get("accepted"), False,
            "an unrecognised bridgeKind was allowed to outrank the host tier",
        )
