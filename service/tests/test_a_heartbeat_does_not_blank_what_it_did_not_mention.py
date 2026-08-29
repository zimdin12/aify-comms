r"""Absent and empty are different claims, and the environment UPDATE conflated them on seven fields.

THE MODEL ALREADY STATES THE RULE, one field over. `cwdRoots` is handled with: "`null` means the
service said nothing about roots -- keep what we had. An empty ARRAY means it said there are none."
Every other optional field was written as `req.X or ""`, which turns "said nothing" into "said
nothing is there".

THAT COLLAPSE IS NOT COSMETIC, and `02045701` is the proof: an id-less heartbeat blanked `bridge_id`,
and supersession is gated on BOTH sides carrying one, so a single such beat disarmed the arbitration
between a stale bridge and a fresh one -- permanently, silently. That fix covered the two fields the
guard reads. Tracing forward to write an advertiser found the same shape on five more, none of which
had been looked at because no caller omitted them yet:

    machineId, os, kind, launcherVersion, launcherRegistryFingerprint

An environment-tier advertisement omits several of those by design -- it describes the host, not the
launcher a bridge was started from -- so the first advertiser would have erased them on its first
beat.

`label` is deliberately NOT in the set. `req.label or env_id` falls back to a real default rather
than a blank, which is a different behaviour and a correct one.
"""

from __future__ import annotations

from service.tests._base import FastApiTestCase

ENV_ID = "windows:keep-host:default"

#: The seven fields under one rule, as (heartbeat key, response key). Written as a table because the
#: assertion is the same for each and a loop that names them is how the SIXTH one gets noticed.
PRESERVED = (
    ("machineId", "machineId"),
    ("os", "os"),
    ("kind", "kind"),
    ("bridgeId", "bridgeId"),
    ("bridgeVersion", "bridgeVersion"),
    ("launcherVersion", "launcherVersion"),
    ("launcherRegistryFingerprint", "launcherRegistryFingerprint"),
)

FULL = {
    "id": ENV_ID,
    "machineId": "win32:keep-host",
    "os": "windows",
    "kind": "windows",
    "bridgeId": "bridge-A",
    "bridgeVersion": "0.6.0",
    "launcherVersion": "0.6.0",
    "launcherRegistryFingerprint": "feb3b6422e2f1e55",
    "metadata": {"bridgeStartedAt": "2026-08-29T10:00:00Z"},
}


class AHeartbeatDoesNotBlankWhatItDidNotMentionTests(FastApiTestCase):
    def _beat(self, body: dict) -> dict:
        response = self.client.post("/api/v1/environments/heartbeat", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def test_a_field_the_caller_omitted_keeps_its_stored_value(self):
        """The defect, for all seven at once. A caller sending only an id said nothing about any of
        them, and the row must be unchanged."""
        self._beat(dict(FULL))
        after = self._beat({"id": ENV_ID})
        for sent_key, row_key in PRESERVED:
            with self.subTest(field=sent_key):
                self.assertEqual(FULL[sent_key], after[row_key],
                                 f"a heartbeat that never mentioned {sent_key} erased it")

    def test_a_field_the_caller_DOES_send_still_overwrites(self):
        """The other half. A rule that only ever preserved would freeze the row, and every real
        handover -- a relaunched bridge, an upgraded launcher -- writes these fields."""
        self._beat(dict(FULL))
        changed = dict(FULL)
        changed.update({
            "bridgeId": "bridge-B",
            "bridgeVersion": "0.7.0",
            "launcherVersion": "0.7.0",
            "launcherRegistryFingerprint": "0000111122223333",
            "machineId": "win32:keep-host",
            "metadata": {"bridgeStartedAt": "2026-08-29T11:00:00Z"},
        })
        after = self._beat(changed)
        self.assertEqual("bridge-B", after["bridgeId"])
        self.assertEqual("0.7.0", after["bridgeVersion"])
        self.assertEqual("0.7.0", after["launcherVersion"])
        self.assertEqual("0000111122223333", after["launcherRegistryFingerprint"])

    def test_an_advertisement_shaped_beat_keeps_the_launcher_facts(self):
        """The caller this was found for. An environment tier describes the HOST -- it has no
        launcher version to report, because it is not a launcher -- and must not erase the bridge's."""
        self._beat(dict(FULL))
        after = self._beat({"kind": "windows", "hostname": "keep-host",
                            "machineId": "win32:keep-host", "os": "windows"})
        self.assertEqual("0.6.0", after["launcherVersion"],
                         "an advertisement erased the launcher version of the bridge it left in place")
        self.assertEqual("feb3b6422e2f1e55", after["launcherRegistryFingerprint"])
        self.assertEqual("bridge-A", after["bridgeId"])

    def test_a_brand_new_row_gets_empty_rather_than_inherited(self):
        """The INSERT is untouched on purpose: a row being created has nothing to preserve, and an
        empty value there is the truth. Without this the fix could be 'inherit from anywhere'."""
        row = self._beat({"id": "windows:fresh-keep-host:default"})
        for _, row_key in PRESERVED:
            with self.subTest(field=row_key):
                self.assertEqual("", row[row_key], f"a new row invented a {row_key}")

    def test_a_blank_string_is_treated_as_saying_nothing(self):
        """`""` and `"   "` are what an absent value looks like once it has been through a shell or a
        template. The rule strips before deciding, so they preserve rather than erase -- the same
        whitespace equivalence every "is required" check in this service already applies."""
        self._beat(dict(FULL))
        after = self._beat({"id": ENV_ID, "bridgeId": "   ", "launcherVersion": ""})
        self.assertEqual("bridge-A", after["bridgeId"])
        self.assertEqual("0.6.0", after["launcherVersion"])
