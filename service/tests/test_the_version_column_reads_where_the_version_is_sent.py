"""The `bridge_version` column is fed from where a claimer actually sends its version.

MEASURED ON THE OPERATOR'S OWN HOST, 2026-09-06, and this is the whole finding: one environment row,
one live claimer, two disagreeing answers.

    COLUMN bridge_version   : '0.6.0'
    metadata bridgeVersion  : '0.6.2'

`aify-comms doctor`'s `tier-version` read the column and reported the host tier two versions behind
while aify-env was current and beating. A check that reports a false red is worse than no check: it
gets explained away, then switched off, and takes the real signal with it.

WHY THE TWO DISAGREED. aify-env sends `bridgeId` at the TOP LEVEL and the rest of its identity --
`bridgeVersion`, `bridgeStartedAt`, `bridgeKind` -- inside `metadata`. Its own `api.mjs` says why, in
a comment written after this cost a live debugging session: sending `bridgeStartedAt` at the top
level looked right and was silently ignored, because the arbitration reads it from `metadata`.

The column was written from `req.bridgeVersion`, and NOTHING has sent that since v0.6.2 deleted the
aify-comms environment-bridge cluster. `_kept()` then did exactly what it was built to do -- a
heartbeat does not blank what it said nothing about -- and preserved the last value a legacy bridge
had written. Frozen at 0.6.0, permanently, with every beat rewriting the stale value.

BOTH ENDS OF ONE FIELD. A declared field with no reader and a reader with no writer are the same
defect from opposite sides, and this repo has been caught by it before. `bridgeVersion` had a sender
and a reader; they were pointed at different carriers, which reads exactly like working.

THE ASSERTIONS READ THE COLUMN. `_environment_record_to_dict` builds the response's `bridgeVersion`
from `row["bridge_version"]` and from nowhere else, so asserting on that key is asserting on the
column -- through the same field `tier-version` consumes.
"""

from __future__ import annotations

from service.tests._base import FastApiTestCase

ENV_ID = "windows:version-column:default"

#: An aify-env heartbeat, shaped as `CommsApi.heartbeat` actually builds it: the id on the outside,
#: the rest of the identity in the blob. Copied from the sender rather than imagined, because a
#: fixture that invents the shape proves the fix against a caller that does not exist.
AIFY_ENV_BEAT = {
    "id": ENV_ID,
    "bridgeId": "bridge-host-tier",
    "metadata": {
        "bridgeVersion": "0.6.2",
        "bridgeStartedAt": "2026-09-06T14:02:21.681Z",
        "bridgeKind": "aify-env",
    },
}


class TheVersionColumnReadsWhereTheVersionIsSentTests(FastApiTestCase):
    def _beat(self, body: dict) -> dict:
        response = self.client.post("/api/v1/environments/heartbeat", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def test_POSITIVE_CONTROL_a_top_level_version_still_writes_the_column(self):
        """Every assertion below is "the column now says X", which a route that ignored the column
        entirely would fail loudly -- but a route that had stopped storing versions AT ALL would make
        the negative control below pass for the wrong reason. This pins the path that always worked.
        """
        after = self._beat({"id": ENV_ID, "bridgeId": "b-1", "bridgeVersion": "0.5.9"})
        self.assertEqual(after["bridgeVersion"], "0.5.9")

    def test_THE_DEFECT_an_aify_env_beat_sets_the_column(self):
        """The shape every live claimer sends. Before the fix this left the column empty on a fresh
        row and stale on an existing one, which is what the operator's host was showing."""
        after = self._beat(dict(AIFY_ENV_BEAT))
        self.assertEqual(
            after["bridgeVersion"], "0.6.2",
            "the claimer sent its version in metadata, where its identity lives, and the column "
            "-- the field tier-version reads -- did not receive it",
        )

    def test_A_STALE_COLUMN_HEALS_ON_THE_NEXT_BEAT(self):
        """The operator's live row, reproduced. A legacy bridge wrote 0.6.0 into the column and was
        then retired; every aify-env beat since preserved that value instead of correcting it. The
        fix has to repair the rows already in this state, not only get new ones right -- nobody is
        going to hand-edit a production column."""
        self._beat({"id": ENV_ID, "bridgeId": "legacy-bridge", "bridgeVersion": "0.6.0"})
        after = self._beat(dict(AIFY_ENV_BEAT))
        self.assertEqual(
            after["bridgeVersion"], "0.6.2",
            "the row is still reporting the retired bridge's version, so tier-version keeps "
            "failing on a host that is fully up to date",
        )

    def test_AN_ADVERTISER_WITH_NO_BRIDGE_ID_CANNOT_SET_IT(self):
        """NEGATIVE CONTROL, and the reason this fix is safe to make at all.

        Reading a value out of `metadata` widens what a caller can influence, so the question is who
        may write it. `environment_heartbeat` already answers that: a beat carrying no `bridgeId` has
        its whole `bridge*` namespace dropped before the merge, because only a bridge sends an id.
        A host advertiser therefore cannot forge a version, and `tier-version` cannot be talked out
        of a red by the tier it is judging.
        """
        self._beat({"id": ENV_ID, "bridgeId": "b-1", "bridgeVersion": "0.5.9"})
        after = self._beat({"id": ENV_ID, "metadata": {"bridgeVersion": "9.9.9"}})
        self.assertEqual(
            after["bridgeVersion"], "0.5.9",
            "an advertisement that declares no bridge forged a version into the column",
        )

    def test_A_BEAT_THAT_SAYS_NOTHING_STILL_PRESERVES(self):
        """The rule this fix must not undo. `_kept()` exists because `req.X or ''` turned "said
        nothing" into "said nothing is there", and one such beat disarmed supersession. Adding a
        second place to look for the value must not add a third way to erase it."""
        self._beat(dict(AIFY_ENV_BEAT))
        after = self._beat({"id": ENV_ID})
        self.assertEqual(after["bridgeVersion"], "0.6.2")

    def test_a_metadata_version_is_not_preferred_over_a_stated_one(self):
        """Precedence, stated rather than left to fall out. A caller that puts the version in both
        places is saying one thing twice; the top-level field is the declared one and stays first, so
        this change cannot alter what any existing sender means."""
        after = self._beat({
            "id": ENV_ID,
            "bridgeId": "b-1",
            "bridgeVersion": "0.6.3",
            "metadata": {"bridgeVersion": "0.6.2"},
        })
        self.assertEqual(after["bridgeVersion"], "0.6.3")
