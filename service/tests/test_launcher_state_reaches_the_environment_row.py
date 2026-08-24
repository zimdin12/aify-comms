"""A launcher's version and registry fingerprint survive the trip from the bridge to the row.

v0.6 made every launcher export `HARNESS_WRAPPER_VERSION` and `HARNESS_REGISTRY_FINGERPRINT`, and the
bridge put them in its heartbeat payload as `launcherVersion` and `launcherRegistryFingerprint`. The
send side had a test. The RECEIVE side had nothing, and `EnvironmentHeartbeat` declared neither field,
so pydantic dropped both -- silently, since ignoring unknown keys is the default. The bridge was
posting them into a void and every check on the sending end still passed.

That is the whole failure mode this file exists for: a producer with a test, a consumer with no field,
and a transport that reports success either way. Asserting the row EXPOSES them is the only assertion
that can tell "sent" from "arrived".
"""
import unittest

from service.models import EnvironmentHeartbeat
from service.tests._base import FastApiTestCase

VERSION = "0.6.0"
FINGERPRINT = "feb3b6422e2f1e55"


class LauncherStateReachesTheEnvironmentRowTests(FastApiTestCase):
    def _heartbeat(self, **extra):
        payload = {
            "id": "windows:launcher-state:default",
            "label": "launcher state",
            "machineId": "windows:launcher-state",
            "os": "windows",
            "kind": "windows",
            "bridgeId": "bridge-1",
            "bridgeVersion": "0.6.0",
            **extra,
        }
        response = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def test_the_model_keeps_the_two_launcher_fields(self):
        """The narrowest statement of the bug: pydantic dropped them before anything could store them."""
        parsed = EnvironmentHeartbeat(
            id="x", launcherVersion=VERSION, launcherRegistryFingerprint=FINGERPRINT
        )
        self.assertEqual(parsed.launcherVersion, VERSION)
        self.assertEqual(parsed.launcherRegistryFingerprint, FINGERPRINT)

    def test_a_heartbeat_carrying_launcher_state_exposes_it_on_the_row(self):
        env = self._heartbeat(
            launcherVersion=VERSION, launcherRegistryFingerprint=FINGERPRINT
        )
        self.assertEqual(env["launcherVersion"], VERSION)
        self.assertEqual(env["launcherRegistryFingerprint"], FINGERPRINT)

    def test_it_survives_a_second_heartbeat_that_omits_nothing(self):
        """The UPDATE path, which is a different SQL statement from the INSERT and has been the half
        that got missed before."""
        self._heartbeat(launcherVersion=VERSION, launcherRegistryFingerprint=FINGERPRINT)
        env = self._heartbeat(
            launcherVersion="0.6.1", launcherRegistryFingerprint="0000111122223333"
        )
        self.assertEqual(env["launcherVersion"], "0.6.1")
        self.assertEqual(env["launcherRegistryFingerprint"], "0000111122223333")

    def test_a_bridge_that_reports_no_launcher_state_reads_empty_not_missing(self):
        """A resident bridge launched by hand has no launcher, so absence is normal and must not look
        like a defect. Empty rather than absent keeps the row's shape stable for readers."""
        env = self._heartbeat()
        self.assertEqual(env["launcherVersion"], "")
        self.assertEqual(env["launcherRegistryFingerprint"], "")


if __name__ == "__main__":
    unittest.main()
