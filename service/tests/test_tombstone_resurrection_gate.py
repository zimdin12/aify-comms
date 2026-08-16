"""A deleted agent must not come back because an old bridge kept beating.

`_enforce_tombstone_resurrection_gate` is the guard behind that. Its incident record (2026-06-03) is
preserved in the function: the bridge sets `restoreDeleted=true` UNCONDITIONALLY on every
auto-register, so without a freshness check a still-running bridge that predates a deletion clears
the tombstone and the agent reappears in `/api/v1/agents` and the dashboard DM rail.

The rule is relaunch freshness: only a bridge whose `bridgeStartedAt` is NEWER than the tombstone's
`removed_at` may restore. A passive auto-register from an older bridge keeps the agent deleted (410,
tombstone untouched). An explicit operator restore — `restoreDeleted=true` with `autoRegister=false`,
a person asking rather than a stale beat — is still honoured.

None of that was tested. Its 410 was one of 41 operator-facing refusals in the service that no test
exercised.

TWO ASYMMETRIES ARE PINNED HERE AS OBSERVED BEHAVIOUR, because they are the parts a reader would
guess wrong:
  * a bridge that sends NO `bridgeStartedAt` cannot restore — fail SAFE, the agent stays deleted;
  * a tombstone with NO `removed_at` CAN be restored by any bridge — fail OPEN.
The second is the weaker direction. It is recorded rather than changed: `removed_at` is written when
the tombstone is created, so an empty one means a corrupt row, and what should happen then is a
judgement call rather than something the code implies.
"""

from __future__ import annotations

import asyncio
import unittest

from fastapi import HTTPException

from service.api_core.registration_gates import _enforce_tombstone_resurrection_gate

REMOVED_AT = "2026-06-03T12:00:00Z"
BEFORE = "2026-06-03T11:00:00Z"
AFTER = "2026-06-03T13:00:00Z"


class _Req:
    def __init__(self, *, restore=True, auto=True, started=None, agent_id="lc-coder"):
        self.agentId = agent_id
        self.restoreDeleted = restore
        self.autoRegister = auto
        self.bridgeStartedAt = started


class _Tombstone(dict):
    """Stands in for the sqlite3.Row: subscript and `keys()`."""

    def __getitem__(self, key):
        return dict.get(self, key)


class _FakeDb:
    """Records the DELETE the restore path issues; the caller owns the transaction."""

    def __init__(self):
        self.executed = []

    async def execute(self, sql, params=()):
        self.executed.append((" ".join(str(sql).split()), params))
        return None


def _run(req, tombstone):
    db = _FakeDb()
    try:
        asyncio.run(_enforce_tombstone_resurrection_gate(db, req, tombstone))
        return db, None
    except HTTPException as exc:
        return db, exc


def _tombstone(removed_at=REMOVED_AT):
    return _Tombstone(agent_id="lc-coder", removed_at=removed_at)


class TombstoneResurrectionGateTests(unittest.TestCase):
    # ── the incident: a lingering bridge must not resurrect ──────────────────────────────────

    def test_a_bridge_older_than_the_deletion_cannot_restore(self):
        db, exc = _run(_Req(started=BEFORE), _tombstone())
        self.assertIsNotNone(exc, "an older bridge restored a deliberately-removed agent")
        self.assertEqual(exc.status_code, 410)
        self.assertIn("lingering bridge cannot", str(exc.detail))
        self.assertIn(REMOVED_AT, str(exc.detail), "the operator needs the removal time")
        self.assertEqual(db.executed, [], "the tombstone must be left untouched on refusal")

    def test_a_bridge_with_no_start_time_cannot_restore(self):
        """Fail SAFE: absent freshness evidence keeps the agent deleted."""
        for started in (None, "", "   "):
            with self.subTest(bridgeStartedAt=started):
                db, exc = _run(_Req(started=started), _tombstone())
                self.assertIsNotNone(exc)
                self.assertEqual(exc.status_code, 410)
                self.assertEqual(db.executed, [])

    def test_a_bridge_started_at_the_exact_removal_instant_cannot_restore(self):
        """The comparison is strictly newer. Same-instant is ambiguous, and ambiguity here means
        keeping the agent deleted."""
        db, exc = _run(_Req(started=REMOVED_AT), _tombstone())
        self.assertIsNotNone(exc)
        self.assertEqual(db.executed, [])

    # ── a genuine relaunch, and a person asking ─────────────────────────────────────────────

    def test_a_bridge_started_after_the_deletion_restores(self):
        db, exc = _run(_Req(started=AFTER), _tombstone())
        self.assertIsNone(exc, "a genuine relaunch must be able to bring the agent back")
        self.assertEqual(len(db.executed), 1)
        sql, params = db.executed[0]
        self.assertIn("DELETE FROM agent_tombstones", sql)
        self.assertIn("COLLATE NOCASE", sql, "the clear must match the case-insensitive lookup")
        self.assertEqual(params, ("lc-coder",))

    def test_an_explicit_operator_restore_is_honoured_however_old_the_bridge(self):
        """`autoRegister=false` is a person asking, not a stale beat."""
        db, exc = _run(_Req(auto=False, started=BEFORE), _tombstone())
        self.assertIsNone(exc)
        self.assertEqual(len(db.executed), 1)

    def test_an_explicit_restore_works_with_no_bridge_time_at_all(self):
        db, exc = _run(_Req(auto=False, started=None), _tombstone())
        self.assertIsNone(exc)
        self.assertEqual(len(db.executed), 1)

    # ── timestamp forms must not decide the outcome ─────────────────────────────────────────

    def test_offset_and_zulu_spellings_of_the_same_instant_agree(self):
        """`Z` and `+00:00` are the same moment, and a bridge an hour later in another offset is
        still later. A lexical compare of raw strings would get these wrong."""
        db, exc = _run(_Req(started="2026-06-03T12:00:00+00:00"), _tombstone("2026-06-03T12:00:00Z"))
        self.assertIsNotNone(exc, "identical instants spelled differently must not read as newer")

        db, exc = _run(_Req(started="2026-06-03T15:00:00+02:00"), _tombstone(REMOVED_AT))
        self.assertIsNone(exc, "13:00Z spelled as 15:00+02:00 is still after 12:00Z")

    # ── the gate stays off when it does not apply ───────────────────────────────────────────

    def test_no_tombstone_means_nothing_to_guard(self):
        for tombstone in (None, {}):
            with self.subTest(tombstone=tombstone):
                db, exc = _run(_Req(started=BEFORE), tombstone)
                self.assertIsNone(exc)
                self.assertEqual(db.executed, [])

    def test_a_register_that_does_not_ask_to_restore_is_untouched(self):
        db, exc = _run(_Req(restore=False, started=BEFORE), _tombstone())
        self.assertIsNone(exc, "the gate only judges restore attempts")
        self.assertEqual(db.executed, [], "and must not clear a tombstone nobody asked to clear")

    # ── the fail-OPEN case, recorded rather than ruled ──────────────────────────────────────

    def test_a_tombstone_with_no_removal_time_can_be_restored_by_any_bridge(self):
        """Observed, not endorsed. `removed_at` is written when the tombstone is created, so an
        empty one means a corrupt row — and the code treats that as "no evidence of when", which
        lets any bridge with a start time through. The opposite reading (refuse when the tombstone
        is unreadable) is equally defensible; that is a judgement call, so this pins today's answer
        instead of inventing one."""
        db, exc = _run(_Req(started=BEFORE), _tombstone(removed_at=""))
        self.assertIsNone(exc)
        self.assertEqual(len(db.executed), 1)

    def test_neither_side_has_a_timestamp_and_the_agent_stays_deleted(self):
        """The one case the `bool(incoming_started)` guard actually decides, and the reason it is
        not redundant with the comparison beside it.

        With a `removed_at`, an absent bridge time already loses the `>` comparison, so dropping the
        guard changes nothing — a mutation removing it survived every other test here. It only bites
        when BOTH sides are empty: then `not removed_at` alone would call it a relaunch and restore
        the agent on no evidence whatsoever. The guard keeps it deleted, which is the safe reading of
        two missing timestamps.
        """
        for started in (None, ""):
            with self.subTest(bridgeStartedAt=started):
                db, exc = _run(_Req(started=started), _tombstone(removed_at=""))
                self.assertIsNotNone(exc, "restored a deleted agent with no timestamps on either side")
                self.assertEqual(exc.status_code, 410)
                self.assertEqual(db.executed, [])
