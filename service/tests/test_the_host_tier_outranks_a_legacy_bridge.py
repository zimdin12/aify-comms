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


class AFutureStartTimeCannotWinForEverTests(FastApiTestCase):
    """A `bridgeStartedAt` in the future outranked every real bridge until the clock caught up.

    EXTERNAL REVIEW, Round 8 M1. Supersession prefers the LATER start time and nothing bounded how
    late. That needs no bad actor: this project has already measured a container clock 4.1 seconds
    ahead of its host -- enough to make `doctor` report every fresh heartbeat as bogus -- and a host
    with a badly set clock sends a start time days out. It then holds the environment against every
    correct bridge, and the only remedy is waiting.

    CLAMPED TO NOW, NOT REFUSED. Refusing would lock a skewed host out of its own environment
    entirely; treating "I started in the future" as "I started now" leaves it able to take an idle row
    and unable to outrank a live incumbent for ever. That is the safe direction to be wrong in.
    """

    DB_NAME = "aify-test-future-start.db"
    ENV = "windows:clockskew:default"

    def _beat(self, bridge_id: str, started_at: str):
        return self._client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": self.ENV, "kind": "windows", "os": "windows",
                "machineId": "win32:clockskew",
                "bridgeId": bridge_id,
                "metadata": {"bridgeStartedAt": started_at, "bridgeKind": "aify-env"},
            },
        )

    def _stored_start(self):
        """What the row actually RECORDS as this bridge's start time."""
        answer = self._client.get("/api/v1/environments")
        rows = (answer.json() or {}).get("environments") or []
        row = next((r for r in rows if r.get("id") == self.ENV), None)
        assert row is not None, f"the environment was not stored at all: {answer.text}"
        return str(((row.get("metadata") or {}).get("bridgeStartedAt")) or "")

    def test_a_future_start_time_is_NOT_STORED_as_sent(self):
        # THE PROPERTY, AND THE ONE MY FIRST TEST GOT WRONG. It asserted that an honestly-clocked
        # bridge sending an EARLIER time would win against the skewed incumbent -- which is false by
        # design and should be: arbitration prefers the later start, and a value clamped to `now` did
        # effectively start more recently than a bridge that started at noon. Asserting that would
        # have demanded the clamp break supersession.
        #
        # What the clamp actually guarantees is that the stored value is BOUNDED, so real time moves
        # past it and the next genuinely-later bridge wins normally. Unclamped, nothing could take
        # this row until 2099.
        self._beat("skewed", "2099-01-01T00:00:00.000Z")
        stored = self._stored_start()
        self.assertNotIn(
            "2099", stored,
            f"the row recorded {stored!r}. Arbitration prefers the later start time, so this bridge "
            "holds the environment against every correctly-clocked one for seventy-three years.",
        )
        self.assertTrue(stored, "the start time was dropped entirely rather than bounded")

    def test_the_clamp_is_at_WRITE_time_so_it_expires_instead_of_following_the_clock(self):
        # WHY THIS IS A SEPARATE TEST: clamping only in the READER was my first fix, and it is worse
        # than it looks. A reader's ceiling moves with the clock, so a poisoned row reads as "now" on
        # every arbitration for ever -- converting "holds the row until 2099" into "holds the row
        # permanently". The stored value is what proves the bound was taken once.
        self._beat("skewed", "2099-01-01T00:00:00.000Z")
        first = self._stored_start()
        self._beat("skewed", "2099-01-01T00:00:00.000Z")
        second = self._stored_start()
        self.assertEqual(
            first[:16], second[:16],
            f"the recorded start time moved from {first!r} to {second!r} across two beats. A ceiling "
            "that tracks the clock never expires, so the skewed bridge keeps outranking everything.",
        )

    def test_an_ORDINARY_later_start_time_still_wins_though(self):
        # THE CONTROL. A clamp that also flattened real start times would break supersession
        # outright -- a restarted bridge must still take over from its predecessor.
        self._beat("older", "2026-09-04T10:00:00.000Z")
        answer = self._beat("newer", "2026-09-04T11:00:00.000Z")
        claimer = (answer.json() or {}).get("claimer") or {}
        self.assertIsNot(
            claimer.get("accepted"), False,
            "an ordinary restart could no longer supersede its predecessor; the clamp is eating real "
            "start times",
        )
