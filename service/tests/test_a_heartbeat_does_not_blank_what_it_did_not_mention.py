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

`label` WAS ARGUED OUT OF THIS SET, on the grounds that `req.label or env_id` falls back to a real
default rather than a blank. That is true and it is not the point: on an UPDATE the "real default" is
the raw environment id, so one silent heartbeat replaces the operator's "Windows on StevenZ-L" with
"windows:StevenZ-L:default". Measured against the running service on 2026-08-30, not reasoned about,
and it is the tenth field in the same family. The INSERT still uses the id, because a row being
created has no prior label and its id is the honest name until somebody gives it one.
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
        """The defect, for every field at once. A caller sending only an id said nothing about any of
        them, and the row must be unchanged.

        The LISTS were the worst case and the last found: `req.cwdRoots or []` and `req.runtimes or
        []` turned "said nothing" into "said there are none", so a bare heartbeat left an environment
        advertising NOTHING SPAWNABLE. `metadata.advertisedCwdRoots` records what a CALLER claimed,
        and silence is not an advertisement of nothing. And the LABEL, whose "real default" on an
        UPDATE is the raw id: a silent heartbeat must not rename the environment after itself."""
        self._beat({**FULL, "label": "Windows on StevenZ-L", "cwdRoots": ["C:/work", "C:/other"],
                    "runtimes": [{"runtime": "claude-code", "available": True}]})
        after = self._beat({"id": ENV_ID})
        for sent_key, row_key in PRESERVED:
            with self.subTest(field=sent_key):
                self.assertEqual(FULL[sent_key], after[row_key],
                                 f"a heartbeat that never mentioned {sent_key} erased it")
        self.assertEqual(["C:/work", "C:/other"], after["cwdRoots"],
                         "a heartbeat that never mentioned roots erased them")
        self.assertEqual(1, len(after["runtimes"]),
                         "a heartbeat that never mentioned runtimes left the host unspawnable")
        self.assertEqual(["C:/work", "C:/other"], after.get("metadata", {}).get("advertisedCwdRoots"),
                         "silence was recorded as an advertisement of nothing")
        self.assertEqual("Windows on StevenZ-L", after["label"],
                         "a silent heartbeat renamed the environment after its own id")

    def test_a_field_the_caller_DOES_send_still_overwrites(self):
        """The other half. A rule that only ever preserved would freeze the row, and every real
        handover -- a relaunched bridge, an upgraded launcher, a renamed environment -- writes these
        fields, lists included."""
        self._beat({**FULL, "label": "Old Name", "cwdRoots": ["C:/work"],
                    "runtimes": [{"runtime": "claude-code", "available": True}]})
        changed = dict(FULL)
        changed.update({
            "bridgeId": "bridge-B",
            "bridgeVersion": "0.7.0",
            "launcherVersion": "0.7.0",
            "launcherRegistryFingerprint": "0000111122223333",
            "machineId": "win32:keep-host",
            "metadata": {"bridgeStartedAt": "2026-08-29T11:00:00Z"},
            "label": "New Name",
            "cwdRoots": ["D:/elsewhere"],
            "runtimes": [{"runtime": "hermes", "available": False}],
        })
        after = self._beat(changed)
        self.assertEqual("bridge-B", after["bridgeId"])
        self.assertEqual("0.7.0", after["bridgeVersion"])
        self.assertEqual("0.7.0", after["launcherVersion"])
        self.assertEqual("0000111122223333", after["launcherRegistryFingerprint"])
        self.assertEqual("New Name", after["label"])
        self.assertEqual(["D:/elsewhere"], after["cwdRoots"])
        self.assertEqual("hermes", after["runtimes"][0]["runtime"])

    def test_a_brand_new_row_gets_empty_rather_than_inherited(self):
        """The INSERT is untouched on purpose: a row being created has nothing to preserve, and an
        empty value there is the truth. Without this the fix could be 'inherit from anywhere'. Its
        label is its id: an unnamed environment showing its id beats showing nothing."""
        row = self._beat({"id": "windows:fresh-keep-host:default"})
        for _, row_key in PRESERVED:
            with self.subTest(field=row_key):
                self.assertEqual("", row[row_key], f"a new row invented a {row_key}")
        self.assertEqual("windows:fresh-keep-host:default", row["label"])

    def test_a_blank_string_is_treated_as_saying_nothing(self):
        """`""` and `"   "` are what an absent value looks like once it has been through a shell or a
        template. The rule strips before deciding, so they preserve rather than erase -- the same
        whitespace equivalence every "is required" check in this service already applies."""
        self._beat(dict(FULL))
        after = self._beat({"id": ENV_ID, "bridgeId": "   ", "launcherVersion": ""})
        self.assertEqual("bridge-A", after["bridgeId"])
        self.assertEqual("0.6.0", after["launcherVersion"])

    # -- the two LIST fields, whose rule is not the strings' rule ---------------------------------

    def test_an_EMPTY_list_is_a_claim_and_is_believed(self):
        """`None` and `[]` are different claims -- the model's own words about roots: "null means the
        service said nothing about roots -- keep what we had. An empty ARRAY means it said there are
        none." A fix that preserved on both would make it impossible to ever clear a stale list."""
        self._beat({**FULL, "cwdRoots": ["C:/work"],
                    "runtimes": [{"runtime": "claude-code", "available": True}]})
        after = self._beat({"id": ENV_ID, "cwdRoots": [], "runtimes": []})
        self.assertEqual([], after["cwdRoots"], "an explicit empty root list was ignored")
        self.assertEqual([], after["runtimes"], "an explicit empty runtime list was ignored")
