r"""An environment-tier advertisement describes the host. It does not claim to own the bridge.

WHY THIS EXISTS. `docs/ENVIRONMENT_ADVERTISEMENT.md` moves capability advertising off the host-side
aify-comms bridge and onto aify-env, which has the facts already -- the service registry, terminal
availability, process custody, and harness identification by contract marker. aify-env is not a
bridge and carries no `bridgeId`, which `EnvironmentHeartbeat` already allows: every field but `id`
is optional, and the model says in as many words that a bridge started by hand "sends neither; that
is normal rather than missing data".

THE ROW DID NOT AGREE WITH THE MODEL. `_record_environment_registration` wrote `req.bridgeId or ""`,
so an id-less heartbeat BLANKED the column -- and the blanking is not the expensive half.
Supersession is gated on both sides carrying an id:

    if existing and existing["bridge_id"].strip() and req.bridgeId.strip():

so one advertisement disarmed the arbitration between a stale bridge and a fresh one, permanently,
and the `bridgeStartedAt` comparison behind it never ran again. Nothing errors. The next relaunched
bridge simply does not supersede its predecessor, and two bridges own one environment -- the
collision the environment tier exists to end, re-created by the tier meant to end it.

Found by reading before writing an advertiser, rather than by an advertiser finding it in
production.
"""

from __future__ import annotations

from service.tests._base import FastApiTestCase

ENV_ID = "windows:advert-host:default"


class AnAdvertisementDoesNotDisarmSupersessionTests(FastApiTestCase):
    def _bridge_beat(self, bridge_id: str, started_at: str) -> dict:
        """A heartbeat from a BRIDGE: it claims ownership and stamps when it started."""
        response = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID,
            "label": "Windows on advert-host",
            "machineId": "windows:advert-host",
            "os": "windows",
            "kind": "windows",
            "bridgeId": bridge_id,
            "cwdRoots": ["C:/work"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"]}],
            "metadata": {"bridgeStartedAt": started_at},
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def _advertisement(self, **overrides) -> dict:
        """A heartbeat from the ENVIRONMENT TIER: capabilities, no ownership claim."""
        body = {
            "id": ENV_ID,
            "label": "Windows on advert-host",
            "machineId": "windows:advert-host",
            "os": "windows",
            "kind": "windows",
            "cwdRoots": ["C:/work", "C:/other"],
            "runtimes": [
                {"runtime": "claude-code", "modes": ["managed-warm"], "available": True},
                {"runtime": "hermes", "modes": ["managed-warm"], "available": False,
                 "unavailableReason": "hermes not on PATH"},
            ],
            "terminalRuntimes": ["claude-code", "hermes"],
            "terminal": True,
            "pty": True,
        }
        body.update(overrides)
        response = self.client.post("/api/v1/environments/heartbeat", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def _row(self) -> dict:
        response = self.client.get("/api/v1/environments")
        self.assertEqual(response.status_code, 200, response.text)
        rows = [e for e in response.json()["environments"] if e["id"] == ENV_ID]
        self.assertEqual(1, len(rows), "the environment under test is not in the listing")
        return rows[0]

    # -- the defect ---------------------------------------------------------------------------

    def test_THE_DEFECT_an_advertisement_leaves_supersession_armed(self):
        """The whole reason for the change. Bridge A owns the environment; the tier advertises;
        bridge B relaunches with a newer start. B must still supersede A.

        Before the fix the advertisement blanked `bridge_id`, the both-sides guard stopped firing,
        and B was recorded beside A instead of replacing it."""
        self._bridge_beat("bridge-A", "2026-08-29T10:00:00Z")
        self._advertisement()
        after_advert = self._row()
        self.assertEqual("bridge-A", after_advert["bridgeId"],
                         "the advertisement erased the bridge that owns this environment")

        self._bridge_beat("bridge-B", "2026-08-29T11:00:00Z")
        self.assertEqual("bridge-B", self._row()["bridgeId"],
                         "a newer bridge did not supersede the older one — the guard was disarmed")

    def test_an_older_bridge_still_loses_to_the_one_in_place(self):
        """The other direction of the same guard, so the fix cannot be 'always take the incoming id'.
        A late beat from a bridge that started EARLIER must not take the row back."""
        self._bridge_beat("bridge-B", "2026-08-29T11:00:00Z")
        self._advertisement()
        self._bridge_beat("bridge-A", "2026-08-29T10:00:00Z")
        self.assertEqual("bridge-B", self._row()["bridgeId"],
                         "an older bridge reclaimed an environment a newer one owns")

    # -- what the advertisement is FOR ---------------------------------------------------------

    def test_an_advertisement_updates_the_capabilities_it_carries(self):
        """The point of the change. Without this the fix above is just a column nobody writes."""
        self._bridge_beat("bridge-A", "2026-08-29T10:00:00Z")
        row = self._advertisement()
        self.assertEqual(["claude-code", "hermes"], row["terminalRuntimes"])
        self.assertIn("C:/other", row["cwdRoots"], "advertised roots did not land")
        unavailable = [r for r in row["runtimes"] if r.get("available") is False]
        self.assertEqual(1, len(unavailable), "the unavailable runtime was dropped")
        self.assertEqual("hermes not on PATH", unavailable[0].get("unavailableReason"),
                         "the REASON a runtime is unavailable is the half an operator acts on")

    def test_an_advertisement_keeps_the_environment_online(self):
        """A tier that advertises is evidence the host is up; the row must read online afterwards,
        or the advertisement would make an environment look worse than saying nothing."""
        self._bridge_beat("bridge-A", "2026-08-29T10:00:00Z")
        self._advertisement()
        self.assertEqual("online", self._row()["status"])

    # -- the boundaries of the fix -------------------------------------------------------------

    def test_a_bridge_that_names_itself_still_replaces_the_stored_id(self):
        """The fix preserves an id only when none is offered. A column that could never change
        would pass the defect test above and break every real handover."""
        self._bridge_beat("bridge-A", "2026-08-29T10:00:00Z")
        self.assertEqual("bridge-A", self._row()["bridgeId"])
        self._bridge_beat("bridge-B", "2026-08-29T11:00:00Z")
        self.assertEqual("bridge-B", self._row()["bridgeId"])

    def test_an_environment_first_seen_by_an_advertisement_has_no_bridge(self):
        """The INSERT path is deliberately untouched: a row being created has no prior bridge to
        preserve, and an empty id there is the truth rather than an oversight. It also means the
        both-sides guard correctly does nothing until a bridge actually claims the environment."""
        row = self._advertisement(id="windows:fresh-host:default", machineId="windows:fresh-host")
        self.assertEqual("", row["bridgeId"])
        self.assertEqual("online", row["status"])

    def test_the_bridge_version_follows_the_same_rule_as_the_id(self):
        """`bridgeVersion` describes the same absent party. Preserving one and blanking the other
        would leave a row claiming bridge-A at version ''."""
        self._bridge_beat("bridge-A", "2026-08-29T10:00:00Z")
        with_version = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV_ID, "bridgeId": "bridge-A", "bridgeVersion": "0.6.0",
            "metadata": {"bridgeStartedAt": "2026-08-29T10:00:00Z"},
        })
        self.assertEqual(200, with_version.status_code, with_version.text)
        self.assertEqual("0.6.0", self._row()["bridgeVersion"])
        self._advertisement()
        self.assertEqual("0.6.0", self._row()["bridgeVersion"],
                         "the advertisement erased the version of the bridge it left in place")
