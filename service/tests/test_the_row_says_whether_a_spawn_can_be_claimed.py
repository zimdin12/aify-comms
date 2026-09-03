"""One derivation of "can a spawn be claimed here", on the row every caller already has.

THE DEFECT THIS CLOSES, measured 2026-09-02. `status` and `lastSeen` are refreshed by aify-env
ADVERTISING the host. `/spawn` reads `metadata.bridgeLastSeen`, which only something offering to
CLAIM work writes. A row read `online, lastSeen 17:26:41Z` beside a `bridgeLastSeen` from the
previous day -- and the doctor, the `comms_envs` MCP tool and the dashboard all reported it ready
while `/spawn` refused six times and was the only one telling the truth. The operator lost a day;
an agent correctly trusting its tool told them the fleet was ready.

WHY A FIELD RATHER THAN A THIRD PREDICATE. The doctor and `comms_envs` each grew their own copy of
the rule, and each got it wrong once, in the same direction. A rule copied into N callers agrees
until one is fixed. This is the only version that cannot drift: the row carries the answer, derived
once by the service that also owns `/spawn`.

WHAT IT IS NOT. It cannot resolve the ABSENT case -- `/spawn` settles that against
`bridge_instances`, which no listing queries -- so `state` reports four answers and `canClaim` is
false there. A caller must be able to tell "stopped" from "corrupt" from "cannot tell", because the
three send a reader to three different places.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from service.api_core.records import _environment_record_to_dict
from service.env_status import (
    BRIDGE_STAMP_ABSENT,
    BRIDGE_STAMP_FRESH,
    BRIDGE_STAMP_INVALID,
    BRIDGE_STAMP_STALE,
    SPAWN_CLAIMER_FRESH_SECONDS,
    environment_has_live_bridge,
)


def _stamp(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")


class _Row(dict):
    """A sqlite3.Row stand-in: `row["k"]` plus `row.keys()`, which the serializer probes."""

    def keys(self):  # noqa: D102 - dict already has it; named for the reader
        return super().keys()


def _row(metadata: dict | None = None, status: str = "online", last_seen: str | None = None) -> _Row:
    return _Row({
        "id": "windows:host:default", "label": "", "machine_id": "m1", "os": "windows",
        "kind": "windows", "bridge_id": "", "bridge_version": "", "launcher_version": "",
        "launcher_registry_fingerprint": "", "cwd_roots": "[]", "runtimes": "[]",
        "status": status, "metadata": __import__("json").dumps(metadata or {}),
        "registered_at": "", "last_seen": last_seen if last_seen is not None else _stamp(5),
    })


class TheRowSaysWhetherASpawnCanBeClaimedTests(unittest.TestCase):
    def test_a_fresh_claimer_stamp_reads_as_claimable(self):
        record = _environment_record_to_dict(_row({"bridgeLastSeen": _stamp(5)}))
        self.assertEqual(record["spawnClaim"]["state"], BRIDGE_STAMP_FRESH)
        self.assertTrue(record["spawnClaim"]["canClaim"])

    def test_THE_ROW_THAT_COST_A_DAY_reads_advertised_and_NOT_claimable(self):
        """Online, freshly advertised, and nothing has claimed for a day. Both facts on one row, and
        this is the pair every instrument collapsed."""
        record = _environment_record_to_dict(
            _row({"bridgeLastSeen": _stamp(26 * 3600)}, status="online", last_seen=_stamp(2)),
        )
        self.assertEqual(record["status"], "online", "it really is advertised -- that was never wrong")
        self.assertEqual(record["spawnClaim"]["state"], BRIDGE_STAMP_STALE)
        self.assertFalse(record["spawnClaim"]["canClaim"])

    def test_the_two_fields_can_DISAGREE_which_is_the_whole_point(self):
        """CONTROL. If `spawnClaim` merely restated `status`, every assertion above would hold and
        the field would prove nothing -- the exact shape that let three callers ship the same bug."""
        advertised_but_dead = _environment_record_to_dict(
            _row({"bridgeLastSeen": _stamp(26 * 3600)}, status="online", last_seen=_stamp(2)),
        )
        self.assertNotEqual(
            advertised_but_dead["status"] == "online",
            advertised_but_dead["spawnClaim"]["canClaim"],
            "the fields agreed, so this field adds nothing",
        )

    def test_an_ABSENT_stamp_is_reported_as_absent_and_not_as_claimable(self):
        """Every row registered before the field existed is this shape. `/spawn` resolves it against
        `bridge_instances`; a listing cannot, so it must say so rather than answer either way."""
        record = _environment_record_to_dict(_row({}))
        self.assertEqual(record["spawnClaim"]["state"], BRIDGE_STAMP_ABSENT)
        self.assertFalse(record["spawnClaim"]["canClaim"])
        self.assertEqual(record["spawnClaim"]["bridgeLastSeen"], "")

    def test_an_UNREADABLE_stamp_is_corrupt_data_not_a_missing_bridge(self):
        """Different remedies: starting a bridge fixes a stale stamp and does nothing for this one."""
        record = _environment_record_to_dict(_row({"bridgeLastSeen": "not-a-date"}))
        self.assertEqual(record["spawnClaim"]["state"], BRIDGE_STAMP_INVALID)
        self.assertFalse(record["spawnClaim"]["canClaim"])

    def test_the_field_AGREES_WITH_THE_ENDPOINT_that_actually_refuses(self):
        """The property that matters more than any single answer: this field and `/spawn`'s own gate
        must never disagree. They are two call sites of one rule today; a future edit to either is
        what this catches."""
        for metadata in (
            {"bridgeLastSeen": _stamp(5)},
            {"bridgeLastSeen": _stamp(SPAWN_CLAIMER_FRESH_SECONDS + 30)},
            {"bridgeLastSeen": "not-a-date"},
            {},
        ):
            record = _environment_record_to_dict(_row(metadata))
            # `bridge_rows_say_live=None` is what a LISTING knows: it did not ask the authority.
            endpoint = environment_has_live_bridge(record, bridge_rows_say_live=None)
            self.assertEqual(
                record["spawnClaim"]["canClaim"], endpoint,
                f"the row says {record['spawnClaim']} and the endpoint says {endpoint} for {metadata}",
            )

    def test_the_claim_window_is_NOT_the_operators_advertisement_window(self):
        """`environment_offline_seconds` ages the ADVERTISEMENT. Judging a claimer by it would let a
        raised setting make this field say yes while `/spawn` says no -- the drift the field exists
        to remove, reintroduced through the field."""
        stale = {"bridgeLastSeen": _stamp(SPAWN_CLAIMER_FRESH_SECONDS + 60)}
        record = _environment_record_to_dict(_row(stale), offline_seconds=86_400)
        self.assertEqual(record["status"], "online", "the generous window did reach the status")
        self.assertFalse(record["spawnClaim"]["canClaim"], "and must not have reached the claim")


if __name__ == "__main__":
    unittest.main()
