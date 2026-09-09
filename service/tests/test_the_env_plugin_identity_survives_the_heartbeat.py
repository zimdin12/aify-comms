"""What aify-env's plugin says about ITSELF reaches the code on this side that acts on it.

X-2 JUDGES TOP-LEVEL FIELDS AND STOPS THERE, and the heartbeat is the one request with a nested
object. Everything the host tier asserts about itself rides in `metadata`: `bridgeVersion`, which
`aify-comms doctor`'s `tier-version` row compares against this install; `bridgeStartedAt`, which
supersession is arbitrated on; and `bridgeKind`, which is the whole of Round 8's H4 fix -- a service
that cannot tell a host tier from a legacy bridge hands the claimer role to whichever started later.

THE FAILURE IS SILENT AND HAS ALREADY HAPPENED ONCE. `service/api_core/environment_registration.py`
records it measured on the operator's own host on 2026-09-06: `metadata.bridgeVersion` read `0.6.2`
while the published field beside it was fed from a top-level carrier nothing had sent since v0.6.2.
A key that does not arrive is not an error anywhere -- the POST answers 200, the heartbeat is
"accepted", and the capability keyed on that value simply stops happening. `api.mjs` says the same
thing in its own words: a heartbeat accepted and ignored is indistinguishable from one that worked.

EACH WITNESS IS KEYED ON THE CONSUMER, NEVER ON STORAGE, and the first version of this file is why.
It asserted that every key the plugin nested came back in the stored row, which passed first time
and proved nothing: this service keeps `metadata` VERBATIM, unknown keys included. Renaming
`bridgeKind` to `bridge_kind` in `mintBridgeIdentity` was driven against it and SURVIVED -- the
renamed key round-tripped intact while the arbitration that reads it saw nothing. An expectation
satisfied by the service storing whatever it was handed is satisfied by anything.

So the claim under test is not "the key arrives" but "the value reaches the thing that acts on it",
and each is settled by driving that behaviour: the version through the field `tier-version` reads,
the kind through an arbitration a legacy bridge would otherwise win.

WHAT IT DOES NOT COVER: `bridgeStartedAt` has no witness here -- ordinary start-time arbitration is
already covered by this suite's own lifecycle tests, and a second one would be cost without
coverage. Nor does it cover the top-level `bridgeVersion` carrier that `environment_registration.py`
documents as the one that wins, any other response, or auth.

IT SKIPS BY NAME rather than passing when the checkout is absent, like its siblings.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.test_the_env_plugin_addresses_routes_this_service_serves import (
    PLUGIN_DIR, env_repo,
)

MACHINE_ID = "linux:probe-host"
ENVIRONMENT_ID = f"{MACHINE_ID}:default"

#: A version this repo could never be, so a value that "arrived" cannot be one the service supplied
#: from its own build stamp. An earlier draft used a plausible `0.6.3` and could not tell them apart.
PROBE_VERSION = "9.9.9-probe"

#: How much later the legacy bridge claims to have started. It has to WIN on start time for the
#: host-tier preference to be the thing under test at all -- a legacy bridge that started earlier
#: loses to ordinary arbitration and the witness passes without `bridgeKind` being read.
#:
#: A FAR-FUTURE CONSTANT DOES NOT WORK, and driving the mutants is what established that: this file
#: first used `2099-01-01`, and removing the host-tier preference outright left the witness GREEN.
#: The route BOUNDS a future `bridgeStartedAt` on the way in (Round 8 M1), so 2099 was clamped to
#: `now + skew` and the arbitration under test never ran. Sixty seconds is comfortably inside the
#: 120-second tolerance and comfortably after a stamp minted moments earlier.
LEGACY_STARTS_THIS_MUCH_LATER = timedelta(seconds=60)

#: The nested keys with a CONSUMER on this side, so the control can say which witness went unfed.
#: Deliberately NOT the whole population: `mintBridgeIdentity` may add a field this service has no
#: reader for yet, and that is a plugin decision rather than a failure here.
CONSUMED_KEYS = {"bridgeVersion", "bridgeKind"}

#: Node, calling the plugin's OWN `heartbeat()` and reporting the body its transport received.
#: The advertisement is a real one -- an id, a machine and a host capability -- because the body is
#: built by spreading it, and a probe that spreads to nothing produces a body no host ever sends.
HARNESS = """
import { CommsApi, mintBridgeIdentity } from '%(api)s';
let sent = null;
const api = new CommsApi({
  endpoint: 'http://probe.invalid:1',
  credential: async () => '',
  identity: mintBridgeIdentity({ version: '%(version)s' }),
  fetchImpl: async (url, init) => {
    sent = JSON.parse(String(init.body));
    return { ok: true, status: 200, json: async () => ({}), text: async () => '{}' };
  },
});
await api.heartbeat({ id: '%(env)s', machineId: '%(machine)s', terminal: true });
if (!sent) { console.error('the plugin sent no heartbeat at all'); process.exit(3); }
console.log(JSON.stringify(sent));
"""


class TheEnvPluginIdentitySurvivesTheHeartbeat(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.repo, reason = env_repo()
        if self.repo is None:
            self.skipTest(f"{reason}, so the plugin's heartbeat was NOT driven through this "
                          "service's route")

    # ── the plugin's own body ────────────────────────────────────────────────────────────────

    def _heartbeat_the_plugin_sends(self) -> dict:
        """The real body, from the real class, through the injection it already exposes."""
        api = (self.repo / PLUGIN_DIR / "api.mjs").as_uri()
        script = Path(tempfile.mkdtemp()) / "drive-the-heartbeat.mjs"
        script.write_text(
            HARNESS % {"api": api, "version": PROBE_VERSION,
                       "env": ENVIRONMENT_ID, "machine": MACHINE_ID},
            encoding="utf-8")
        done = subprocess.run(["node", str(script)], cwd=self.repo,
                              capture_output=True, text=True)
        # A PAYLOAD FROM A PROCESS THAT DID NOT SUCCEED IS NOT EVIDENCE — the hole this repo has now
        # written twice, so it is checked here the first time.
        if done.returncode != 0:
            raise AssertionError(
                f"the plugin's heartbeat could not be driven (exit {done.returncode}): "
                f"{done.stdout[-400:]}{done.stderr[-400:]}")
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        if not lines:
            raise AssertionError(f"the harness printed no body: {done.stdout[-400:]}")
        return json.loads(lines[-1])

    # ── the round trip ───────────────────────────────────────────────────────────────────────

    def _row(self) -> dict:
        """The environment as this service PUBLISHES it — the shape every consumer reads."""
        listed = self.client.get("/api/v1/environments")
        self.assertEqual(listed.status_code, 200, listed.text)
        rows = [row for row in listed.json().get("environments", [])
                if row.get("id") == ENVIRONMENT_ID]
        self.assertEqual(len(rows), 1,
                         f"expected exactly one {ENVIRONMENT_ID} row, got {len(rows)}")
        return rows[0]

    def _beat(self, body: dict) -> None:
        posted = self.client.post("/api/v1/environments/heartbeat", json=body)
        self.assertEqual(posted.status_code, 200, posted.text)

    @staticmethod
    def _shortly_after(started_at: str, gap: timedelta = LEGACY_STARTS_THIS_MUCH_LATER) -> str:
        """A start time genuinely later than `started_at`, and inside the route's future bound.

        DERIVED FROM THE STAMP THE HOST TIER ACTUALLY SENT rather than from a constant, because the
        thing that must be true is a RELATION between the two starts. A literal cannot express it:
        the plugin mints `new Date()` at run time, so any fixed value is either already past or far
        enough ahead to be clamped — and the clamped version is what left a mutant alive here.
        """
        base = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        return (base + gap).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _legacy_beat(bridge_id: str, started_at: str) -> dict:
        """What a pre-0.6.2 aify-comms environment bridge sends: the same facts, NO kind.

        The absence is the point. `_is_host_tier` reads a positive marker precisely because every
        legacy sender is silent here, so a missing value is a fact about the sender.
        """
        return {"id": ENVIRONMENT_ID, "machineId": MACHINE_ID, "bridgeId": bridge_id,
                "metadata": {"bridgeVersion": "0.6.1-legacy", "bridgeStartedAt": started_at}}

    # ── the control ──────────────────────────────────────────────────────────────────────────

    def test_the_plugin_nested_something_for_this_to_judge(self):
        """POSITIVE CONTROL, and it is the one that matters here.

        Every witness below quantifies over what the plugin nested. An empty `metadata` — a plugin
        that stopped sending an identity, or a harness that captured the wrong request — satisfies
        all of them silently. This is the run that says the population is real.
        """
        body = self._heartbeat_the_plugin_sends()
        nested = body.get("metadata") or {}
        self.assertTrue(
            nested,
            "the plugin nested NOTHING inside metadata, so every check in this file is vacuous: "
            f"the body it sent was {body}")
        self.assertEqual(
            set(nested) & CONSUMED_KEYS, CONSUMED_KEYS,
            f"the plugin no longer sends {sorted(CONSUMED_KEYS - set(nested))}, so this file's "
            "witness for each missing key is exercising a capability nothing feeds. That is a "
            "change to the seam, not to this test — decide it deliberately")

    # ── one witness per key, keyed on the CONSUMER rather than on storage ────────────────────

    def test_the_version_reaches_the_field_tier_version_compares(self):
        """`bridgeVersion` — read by `aify-comms doctor`'s `tier-version` row.

        THE PUBLISHED FIELD, NOT THE METADATA COPY, and the difference is the defect this file was
        written from: `environment_registration.py` records `metadata.bridgeVersion` reading `0.6.2`
        on the operator's own host while the field beside it was fed from a carrier nothing had sent
        for a release. Asserting the nested copy would have agreed with that deployment.
        """
        self._beat(self._heartbeat_the_plugin_sends())
        self.assertEqual(
            self._row().get("bridgeVersion"), PROBE_VERSION,
            "the version tier-version compares against this install is not the one the host tier "
            "sent, so that row reports a comparison it did not make")

    def test_the_host_tier_OUTRANKS_a_legacy_bridge_that_started_later(self):
        """`bridgeKind` — read by `_is_host_tier`, which arbitration consults.

        THE BEHAVIOUR, NOT THE KEY. A test that the stored row contains `bridgeKind` is satisfied by
        this service keeping metadata verbatim, which it does for every key including ones nothing
        reads. Driven that way, renaming the field in `mintBridgeIdentity` SURVIVED: the renamed key
        arrived intact and proved nothing.

        The decision is Round 8 H4: a legacy aify-comms environment bridge on a host that never
        re-ran install.sh took this row by starting later, and then became the only party allowed to
        claim. Two spawners on one host is the collision the environment tier exists to end.
        """
        self._beat(self._heartbeat_the_plugin_sends())
        row = self._row()
        host_tier_bridge = row.get("bridgeId")
        self.assertTrue(host_tier_bridge, "the host tier did not take the row to begin with")
        host_tier_start = (row.get("metadata") or {}).get("bridgeStartedAt")
        self.assertTrue(host_tier_start,
                        "the host tier's own start time was not stored, so the legacy beat below "
                        "cannot be built to start later than it and this proves nothing")

        legacy_start = self._shortly_after(host_tier_start)
        self._beat(self._legacy_beat("legacy-bridge", legacy_start))
        # THE PRECONDITION, ASSERTED RATHER THAN ASSUMED. If the route clamped this back to at or
        # before the host tier's start, the legacy bridge loses on start time and the preference
        # under test never runs — which is exactly how a mutant removing that preference survived.
        self.assertGreater(
            self._shortly_after(legacy_start, timedelta(0)), host_tier_start,
            "the legacy bridge does not claim a later start than the host tier, so ordinary "
            "arbitration would refuse it whatever bridgeKind says")
        self.assertEqual(
            self._row().get("bridgeId"), host_tier_bridge,
            "a LEGACY environment bridge took the row from the host tier by starting later — which "
            "is what happens when bridgeKind does not reach the arbitration, and it makes that "
            "bridge the only party allowed to claim spawns on a host aify-env is running")

    def test_the_host_tier_TAKES_a_row_a_legacy_bridge_holds(self):
        """The OTHER arbitration branch, and it had no witness until a mutant showed it.

        Removing the "host tier takes the row from a legacy bridge" branch outright left the witness
        above GREEN, because that one drives the opposite case — a legacy beat arriving at a row the
        host tier already holds. Two branches decide this, and a label bound to the wrong one reads
        as coverage.

        THIS IS THE UPGRADE PATH: a host that has just started aify-env, where a legacy bridge is
        still beating and got there first. The host tier claims a start time EARLIER than the
        incumbent's, so start-time arbitration alone would refuse it and only the kind can admit it.
        """
        body = self._heartbeat_the_plugin_sends()
        host_tier_start = (body.get("metadata") or {}).get("bridgeStartedAt")
        self.assertTrue(host_tier_start, "the plugin sent no start time to arbitrate on")

        self._beat(self._legacy_beat(
            "legacy-incumbent", self._shortly_after(host_tier_start)))
        self.assertEqual(self._row().get("bridgeId"), "legacy-incumbent",
                         "the legacy bridge did not take the row first, so this proves nothing")

        self._beat(body)
        self.assertEqual(
            self._row().get("bridgeId"), body.get("bridgeId"),
            "the host tier could not take the row from a legacy bridge that started later — which "
            "is the upgrade path: aify-env comes up on a host whose retired bridge is still "
            "beating, and loses to it for as long as that bridge keeps running")

    def test_the_refused_legacy_bridge_is_TOLD_it_was_refused_and_why(self):
        """The half a status code cannot carry, and this service has paid for it before.

        A refused beat returns `ok: True` like an accepted one, so the route says so in the body:
        without `claimer.accepted == False` the bridge keeps beating every 30 seconds believing it
        is the claimer while `bridgeLastSeen` never moves and every spawn is refused. The reason
        also has to be actionable — this is not a clock problem, and re-registering will not help.
        """
        self._beat(self._heartbeat_the_plugin_sends())
        refused = self.client.post(
            "/api/v1/environments/heartbeat",
            json=self._legacy_beat("legacy-bridge", datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")))
        self.assertEqual(refused.status_code, 200, refused.text)
        claimer = refused.json().get("claimer") or {}
        self.assertIs(claimer.get("accepted"), False,
                      f"a refused legacy beat was not told it was refused: {refused.json()}")
        self.assertIn(
            "install.sh", str(claimer.get("reason") or ""),
            "the refusal does not tell the operator what to actually do about the retired bridge: "
            f"{claimer.get('reason')!r}")

    def test_a_legacy_bridge_STILL_TAKES_a_row_no_host_tier_holds(self):
        """THE PAIRED CONTROL, driven by removing what the witness above watches.

        A service that refused every legacy beat would pass that test for the wrong reason, and
        would also break every host that has not upgraded yet. The preference has to be a
        preference: with no host tier in the row, ordinary start-time arbitration still applies and
        the legacy bridge is admitted.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        first_start = self._shortly_after(now, -timedelta(hours=1))
        self._beat(self._legacy_beat("legacy-first", first_start))
        self.assertEqual(self._row().get("bridgeId"), "legacy-first",
                         "a legacy bridge could not take a row nothing else held")
        self._beat(self._legacy_beat("legacy-second", self._shortly_after(first_start)))
        self.assertEqual(
            self._row().get("bridgeId"), "legacy-second",
            "a later legacy bridge could not supersede an earlier one, so the witness above passes "
            "because legacy beats are refused outright rather than because the host tier outranks "
            "them")


if __name__ == "__main__":
    unittest.main()
