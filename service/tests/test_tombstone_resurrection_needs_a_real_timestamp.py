"""A deliberately-removed agent came back if the bridge sent a `bridgeStartedAt` that was not a time.

Two gates decide whether a registration may resurrect a row an operator deliberately removed — the
agent tombstone in `registration_gates.py` and the environment forget-tombstone in
`routers/environments.py`, which says in its own comment that it "mirrors how agent registration
honors agent_tombstones". Both decide it the same way:

    relaunched = bool(incoming_started) and (not removed_at or incoming_started > removed_at)

and both built `incoming_started` with `_timestamp_sort_key`. That helper is a SORT key: on a parse
failure it returns the raw string, so a list still orders deterministically instead of throwing.
Correct for display. Fatal here, because **letters sort above digits**:

    "now"              > "2026-08-16T10:00:00+00:00"   ->  True
    "garbage"          > "2026-08-16T10:00:00+00:00"   ->  True
    "Sat Aug 16 2026"  > "2026-08-16T10:00:00+00:00"   ->  True

So ANY bridge sending a non-ISO `bridgeStartedAt` read as a genuine fresh relaunch and cleared the
tombstone. That is precisely what the agent gate exists to stop, in its own words: the bridge "sets
restoreDeleted=true UNCONDITIONALLY on every auto/comms_register, so a still-running bridge that
predates the deletion would otherwise clear the tombstone and resurrect a deliberately-removed agent
(it reappears in /api/v1/agents and the dashboard DM rail)."

It needs no malice. A bridge build that formats the field differently — a locale date, a `Date()`
string — silently defeats both tombstones, and the failure is invisible: the agent simply reappears.

THE FIX IS AT THE HELPER, NOT AT THE CALL SITES. `_parsed_timestamp` returns "" when the value is not
a real timestamp, so `bool(incoming_started)` refuses. That is this repo's standing rule — a check
that could not gather evidence must not report a pass — applied to a comparison instead of a health
check. `_timestamp_sort_key` keeps its fallback, and now says in its docstring why it is not a trust
boundary.

BOTH SIDES IN ONE CHANGE, for the same reason as the workspace-root fix a few slices ago: when one
guard's comment says it mirrors another, they share their defects, and fixing either alone leaves the
boundary open while looking closed.
"""

from __future__ import annotations

import unittest

from service.api_core.serialization import _parsed_timestamp, _timestamp_sort_key

#: The predicate both gates evaluate, written once here so the test is about the DECISION rather
#: than about either copy of it. Kept verbatim in shape; only the builder differs.
def _relaunched(incoming: str, removed_at: str) -> bool:
    return bool(incoming) and (not removed_at or incoming > removed_at)


REMOVED_AT = "2026-08-16T10:00:00Z"


class TombstoneFreshnessTests(unittest.TestCase):
    def test_a_non_timestamp_no_longer_reads_as_a_fresh_relaunch(self):
        """THE ONE THAT MATTERS. Each of these resurrected a removed agent before the fix."""
        removed = _timestamp_sort_key(REMOVED_AT)
        for hostile in ("now", "garbage", "tomorrow", "Sat Aug 16 2026", "zzz", "latest"):
            with self.subTest(bridgeStartedAt=hostile):
                self.assertTrue(
                    _relaunched(_timestamp_sort_key(hostile), removed),
                    "…the old builder accepted it, which is what made this a defect",
                )
                self.assertFalse(
                    _relaunched(_parsed_timestamp(hostile), removed),
                    f"{hostile!r} is not a timestamp, so it is no evidence of a relaunch",
                )

    def test_a_numeric_epoch_string_is_refused_too(self):
        """Digits, so it never beat an ISO string — but it is still not a parseable timestamp, and
        accepting it would depend on `1` sorting below `2` rather than on the value meaning
        anything. Pinned so the refusal is the RULE and not an accident of collation."""
        self.assertEqual(_parsed_timestamp("1755334800"), "")
        self.assertFalse(_relaunched(_parsed_timestamp("1755334800"), _timestamp_sort_key(REMOVED_AT)))

    def test_a_genuine_relaunch_still_restores(self):
        """The gate must keep letting a real fresh bridge through, or a legitimate restore breaks."""
        removed = _parsed_timestamp(REMOVED_AT)
        for fresh in ("2026-08-16T10:00:01Z", "2026-08-16T11:00:00Z", "2026-08-16T13:00:00+02:00"):
            with self.subTest(bridgeStartedAt=fresh):
                self.assertTrue(_relaunched(_parsed_timestamp(fresh), removed))

    def test_a_bridge_that_predates_the_deletion_is_still_refused(self):
        removed = _parsed_timestamp(REMOVED_AT)
        for stale in ("2026-08-16T09:59:59Z", "2026-08-15T10:00:00Z", "2026-08-16T11:00:00+02:00"):
            with self.subTest(bridgeStartedAt=stale):
                self.assertFalse(_relaunched(_parsed_timestamp(stale), removed))

    def test_an_absent_timestamp_was_already_refused_and_still_is(self):
        removed = _parsed_timestamp(REMOVED_AT)
        for empty in ("", "   ", None):
            with self.subTest(bridgeStartedAt=empty):
                self.assertFalse(_relaunched(_parsed_timestamp(empty), removed))

    def test_the_same_instant_in_two_spellings_is_not_newer_than_itself(self):
        """`Z` and `+00:00` are the same time. A lexical compare of the RAW forms would put
        `+00:00` below `Z` — `+` is 0x2B, `Z` is 0x5A — so a relaunch at exactly the removal
        second could flip on spelling alone. Normalising to UTC before comparing is what stops it."""
        self.assertEqual(_parsed_timestamp("2026-08-16T10:00:00Z"),
                         _parsed_timestamp("2026-08-16T12:00:00+02:00"))
        self.assertFalse(_relaunched(_parsed_timestamp("2026-08-16T12:00:00+02:00"),
                                     _parsed_timestamp("2026-08-16T10:00:00Z")))

    def test_both_gates_build_the_incoming_value_with_the_strict_parser(self):
        """The two call sites, asserted because the fix is only complete at BOTH.

        A source read, and the honest form here: the builders are one expression each inside a
        function, so there is nothing to import. It proves which helper each gate calls — not what
        the gate then does, which is the class above.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        agent_gate = (root / "service/api_core/registration_gates.py").read_text(encoding="utf-8")
        env_gate = (root / "service/routers/environments.py").read_text(encoding="utf-8")
        self.assertIn("incoming_started = _parsed_timestamp(req.bridgeStartedAt)", agent_gate)
        self.assertIn('return _parsed_timestamp(metadata.get("bridgeStartedAt"))', env_gate)
        self.assertNotIn("incoming_started = _timestamp_sort_key(req.bridgeStartedAt)", agent_gate)
        self.assertNotIn('return _timestamp_sort_key(metadata.get("bridgeStartedAt"))', env_gate)

    def test_the_sort_key_keeps_its_fallback(self):
        """It is not a bug there. An unparseable value must not break an ORDERING, and the two
        helpers now differ exactly where the difference matters."""
        self.assertEqual(_timestamp_sort_key("garbage"), "garbage")
        self.assertEqual(_parsed_timestamp("garbage"), "")
        self.assertEqual(_timestamp_sort_key(REMOVED_AT), _parsed_timestamp(REMOVED_AT))
