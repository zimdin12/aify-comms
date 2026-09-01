"""Every declared field, driven through the REAL heartbeat route, one row at a time.

WHY THIS EXISTS SEPARATELY FROM THE CENSUS. `test_every_host_owned_field_is_declared.py` proves the
MEMBERSHIP is right: the declared request fields and the handler's `if req.X is None` guards agree in
both directions. It says nothing about what each tuple MAPS to.

Review proved that gap with a cause-specific mutant. Changing one storage key --

    ("terminal", METADATA_CARRIER, "terminal")  ->  ("terminal", METADATA_CARRIER, "terminalBROKEN")

-- left the census at 8/8 and a neighbouring suite at 7/7, while the live route erased the stored fact:
a first beat stored `terminal=true`, a second beat omitted it, and the result came back `false`. The
authority can name the right five members while carrying a storage identity that breaks preservation.

So this file asserts the mapping instead of the names, against the route rather than a reimplementation
of it. For each declared field:

    1. a beat SETS a distinctive sentinel
    2. a beat OMITS the field, and the stored value must still be that exact sentinel
    3. a beat CONTRADICTS it, and the stored value must change

Step 3 is what stops a frozen value passing. Without it, code that ignored the field entirely -- never
writing it at all -- would satisfy step 2 perfectly, which is the same "instrument agrees with itself"
failure this whole area keeps producing.

AND THE VALUE IS READ THROUGH THE DECLARED KEY, at its declared carrier: metadata members are read
from the metadata blob by their key, column members straight out of the database column. That is what
makes a wrong key or a wrong carrier fail, because nothing else in the suite ever looks the value up
the way the declaration says to.
"""

from __future__ import annotations

import json
import sqlite3

from service.routers.environments import COLUMN_CARRIER, HOST_OWNED_FIELDS, METADATA_CARRIER
from service.tests._base import FastApiTestCase

ENV_ID = "windows:carrier-host:default"

#: A distinctive value per field, and something that CONTRADICTS it. The contradiction matters as much
#: as the sentinel: a handler that never writes the field would preserve the sentinel forever and pass
#: a preservation-only test.
SENTINELS = {
    "terminal": (True, False),
    "pty": (True, False),
    "terminalRuntimes": (["claude-code", "hermes"], []),
    "runtimes": (
        [{"runtime": "claude-code", "modes": ["managed-warm"], "available": True}],
        [],
    ),
    "cwdRoots": (["C:/sentinel-root"], []),
}


class EachDeclaredHostFieldSurvivesOmissionTests(FastApiTestCase):
    DB_NAME = "carrier-mapping.sqlite3"

    def _beat(self, **fields) -> None:
        """One heartbeat carrying exactly the fields named, and nothing else optional."""
        body = {
            "id": ENV_ID,
            "label": "Windows on carrier-host",
            "machineId": "windows:carrier-host",
            "os": "windows",
            "kind": "windows",
        }
        body.update(fields)
        response = self.client.post("/api/v1/environments/heartbeat", json=body)
        self.assertEqual(response.status_code, 200, response.text)

    def _stored(self, carrier: str, key: str):
        """The value as the DECLARATION says it is stored -- which is the thing under test.

        Read from the database rather than the API response, because a response field is a projection
        someone chose and the declaration is about STORAGE. A column member is read from its column by
        the declared name, so a wrong column name raises rather than quietly reading something else.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM environments WHERE id = ?", (ENV_ID,)).fetchone()
            self.assertIsNotNone(row, "the environment row was never written")
            if carrier == METADATA_CARRIER:
                metadata = json.loads(row["metadata"] or "{}")
                return metadata.get(key, "<<absent from metadata>>")
            self.assertIn(key, row.keys(), f"declared column `{key}` is not a column of `environments`")
            raw = row[key]
            try:
                return json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                return raw
        finally:
            conn.close()

    # -- the matrix ------------------------------------------------------------------------------

    def test_every_declared_field_survives_omission_and_yields_to_contradiction(self) -> None:
        for field, carrier, key in HOST_OWNED_FIELDS:
            with self.subTest(field=field, carrier=carrier, key=key):
                sentinel, contradiction = SENTINELS[field]

                self._beat(**{field: sentinel})
                self.assertEqual(
                    self._stored(carrier, key), sentinel,
                    f"`{field}` was not stored at its declared {carrier} key `{key}` in the first place",
                )

                # OMISSION MUST PRESERVE. This is the erasure the whole area exists to prevent.
                self._beat()
                self.assertEqual(
                    self._stored(carrier, key), sentinel,
                    f"omitting `{field}` erased it -- the declared carrier/key `{carrier}:{key}` does "
                    "not match the path that actually preserves it",
                )

                # AND CONTRADICTION MUST OVERWRITE, or a field nothing ever writes would pass above.
                self._beat(**{field: contradiction})
                self.assertEqual(
                    self._stored(carrier, key), contradiction,
                    f"`{field}` did not yield to an explicit value -- preservation here would be a "
                    "frozen value rather than a preserved one",
                )

    def test_every_declared_field_has_a_sentinel(self) -> None:
        """A field added to the declaration with no sentinel here would be silently untested.

        The matrix above iterates the declaration, so a missing entry raises KeyError rather than
        skipping -- but this says so directly, and fails with the field's name rather than a traceback.
        """
        missing = sorted(field for field, _c, _k in HOST_OWNED_FIELDS if field not in SENTINELS)
        self.assertEqual(missing, [], f"declared but carrying no sentinel in this matrix: {missing}")

    def test_the_sentinel_and_its_contradiction_differ(self) -> None:
        """A pair that did not differ would make the overwrite assertion vacuous."""
        for field, (sentinel, contradiction) in SENTINELS.items():
            with self.subTest(field=field):
                self.assertNotEqual(
                    sentinel, contradiction,
                    f"`{field}`'s sentinel and contradiction are equal, so the overwrite check proves "
                    "nothing",
                )

    def test_both_carriers_are_actually_exercised(self) -> None:
        """POSITIVE CONTROL on the matrix itself.

        If every declared member were a metadata member, the column half of `_stored` would never run
        and a broken column key could not fail. The declaration currently carries both, and this fails
        loudly if that stops being true rather than quietly testing one carrier twice.
        """
        carriers = {carrier for _f, carrier, _k in HOST_OWNED_FIELDS}
        self.assertEqual(carriers, {METADATA_CARRIER, COLUMN_CARRIER})
