"""The STATUS clamp must fire on a latch that is still being written — the same defect, second table.

`agent_turn_state` holds DELIVERY and its ceiling was anchored on 2026-08-30. `agent_status_state`
holds what the DASHBOARD shows, and its `in_turn` clamp was still aging against `last_event_at` —
the column `_apply_status_event` refreshes on every event it is handed. The hermes hook path applies
a `turn_start` event before EVERY model call, so the clamp that exists to clear a stuck `working`
was reading a clock the latch itself keeps winding. An agent could therefore display `working` for
ever even after the fixed delivery ceiling had correctly released its queued work.

WHY BOTH CLAMPS ARE TESTED. `status_inputs.py` carries two, and its own comments promise they
"MUST produce the same StatusInputs". Two clamps on one question reading different columns is
exactly how a documented parity stops holding, so the anchor is read through one helper and both
paths are driven here.

WHAT WOULD PROVE THIS WRONG, pre-registered: a row whose turn began long ago but whose events keep
arriving still reads `in_turn` — that is the live shape, and the first test below is it.
"""

from __future__ import annotations

import asyncio
import time

from service.api_core import status_inputs
from service.api_core.status_events import _apply_status_event
from service.db import get_db
from service.tests._base import FastApiTestCase


def _stamp(age_seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_seconds))


class TheStatusClampIsAnchoredToTheTurnStartTests(FastApiTestCase):
    DB_NAME = "aify-status-anchor-test.db"

    def _latch(self, agent_id, *, started_age, last_event_age):
        """A turn that began `started_age` ago whose last event landed `last_event_age` ago."""
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO agent_status_state
                        (agent_id, in_turn, awaiting_input, turn_run_id, last_event,
                         last_event_at, turn_started_at, updated_at)
                    VALUES (?, 1, 0, '', 'turn_start', ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        in_turn = 1,
                        last_event_at = excluded.last_event_at,
                        turn_started_at = excluded.turn_started_at
                    """,
                    (agent_id, _stamp(last_event_age), _stamp(started_age), _stamp(last_event_age)),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _row(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                db.row_factory = None
                cursor = await db.execute(
                    "SELECT in_turn, awaiting_input, last_event, last_event_at, turn_started_at "
                    "FROM agent_status_state WHERE agent_id=?", (agent_id,))
                got = await cursor.fetchone()
                return {
                    "in_turn": got[0], "awaiting_input": got[1], "last_event": got[2],
                    "last_event_at": got[3], "turn_started_at": got[4],
                }
            finally:
                await db.close()

        return asyncio.run(_run())

    def _clamped_in_turn(self, agent_id) -> bool:
        """Run the clamp the way both call sites do: anchor, compare, decide."""
        row = self._row(agent_id)
        if not row["in_turn"]:
            return False
        anchor = status_inputs._iso_to_epoch(status_inputs._turn_anchor(row))
        if anchor and (time.time() - anchor) > status_inputs.TURN_BUSY_BACKSTOP_SECONDS:
            return False
        return True

    def test_a_latch_kept_warm_by_a_re_stamping_hook_still_ages_out(self):
        """THE DEFECT. Two hours into a 'turn', with an event 45 seconds ago — which is what a
        `pre_llm_call` hook produces on a managed hermes agent."""
        self._latch("sa-warm", started_age=7200, last_event_age=45)
        self.assertFalse(
            self._clamped_in_turn("sa-warm"),
            "an agent whose turn began 2 hours ago still read `working` because an event arrived "
            "45s ago. The clamp must measure from the START, or any timer-driven poster defeats it.",
        )

    def test_a_genuinely_fresh_turn_is_untouched(self):
        """ANTI-VACUITY. Without this, a clamp that cleared everything would pass the test above."""
        self._latch("sa-fresh", started_age=30, last_event_age=5)
        self.assertTrue(self._clamped_in_turn("sa-fresh"))

    def test_the_boundary_is_the_backstop_and_it_is_shared_with_delivery(self):
        inside = status_inputs.TURN_BUSY_BACKSTOP_SECONDS - 5
        outside = status_inputs.TURN_BUSY_BACKSTOP_SECONDS + 5
        self._latch("sa-inside", started_age=inside, last_event_age=1)
        self._latch("sa-outside", started_age=outside, last_event_age=1)
        self.assertTrue(self._clamped_in_turn("sa-inside"))
        self.assertFalse(self._clamped_in_turn("sa-outside"))


class TheWriterMustNotMoveTheAnchorTests(FastApiTestCase):
    """A reader anchored to a column its writer re-stamps is worthless, so the WRITER is the gate.

    This is the half that carries the fix: `_apply_status_event` is called on every hook fire, and
    if it rewrote `turn_started_at` each time then the clamp above would age against a fresh value
    for ever and nothing in the reader tests would notice.
    """

    DB_NAME = "aify-status-anchor-writer-test.db"

    def _apply(self, agent_id, kind):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                return await _apply_status_event(db, agent_id, {"kind": kind})
            finally:
                await db.close()

        return asyncio.run(_run())

    def _anchor(self, agent_id) -> str:
        async def _run():
            db = await get_db()
            try:
                db.row_factory = None
                cursor = await db.execute(
                    "SELECT turn_started_at FROM agent_status_state WHERE agent_id=?", (agent_id,))
                got = await cursor.fetchone()
                return got[0] if got else None
            finally:
                await db.close()

        return asyncio.run(_run())

    def _backdate_anchor(self, agent_id, age_seconds):
        """BACKDATED FIRST, so a re-stamp is a value the writer could not have produced.

        Its sibling gate learned this the hard way: a mutation that re-stamped the anchor on every
        post came back INERT, because both posts landed inside one second and these stamps are
        second-resolution — the test could not tell a preserved anchor from a rewritten one.
        """
        async def _run():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE agent_status_state SET turn_started_at=? WHERE agent_id=?",
                    (_stamp(age_seconds), agent_id))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def test_the_anchor_is_stamped_when_the_turn_BEGINS(self):
        self._apply("sw-begin", "turn_start")
        self.assertTrue(self._anchor("sw-begin"), "a turn began and no anchor was written")

    def test_a_SECOND_turn_start_does_not_move_it(self):
        """THE WHOLE FIX. The hermes hook applies `turn_start` before every model call."""
        self._apply("sw-keep", "turn_start")
        self._backdate_anchor("sw-keep", 7200)
        before = self._anchor("sw-keep")
        self._apply("sw-keep", "turn_start")
        self.assertEqual(
            self._anchor("sw-keep"), before,
            "a repeated turn_start rewrote the anchor, so the ceiling can be postponed for ever "
            "by exactly the poster it was built to survive",
        )

    def test_NO_event_kind_moves_the_anchor_while_the_turn_runs(self):
        """DERIVED FROM THE VOCABULARY, not a hand-picked pair: any kind that leaves the agent busy
        must leave the anchor alone. A list here would go stale the day a kind is added."""
        from service.status_engine import KNOWN_EVENT_KINDS

        self._apply("sw-all", "turn_start")
        self._backdate_anchor("sw-all", 7200)
        before = self._anchor("sw-all")
        for kind in sorted(KNOWN_EVENT_KINDS):
            with self.subTest(kind=kind):
                state = self._apply("sw-all", kind)
                if state["in_turn"]:
                    self.assertEqual(self._anchor("sw-all"), before, f"{kind} moved the anchor")
                else:
                    # Ending the turn CLEARS it, so the next turn cannot inherit a stale start.
                    self.assertEqual(self._anchor("sw-all"), "", f"{kind} ended the turn but kept an anchor")
                    self._apply("sw-all", "turn_start")
                    self._backdate_anchor("sw-all", 7200)

    def test_ending_the_turn_clears_the_anchor(self):
        """Otherwise the NEXT turn starts already aged, and its ceiling fires immediately."""
        self._apply("sw-clear", "turn_start")
        self._apply("sw-clear", "turn_end")
        self.assertEqual(self._anchor("sw-clear"), "")


class EveryWriterOfThisColumnAgreesTests(FastApiTestCase):
    """DERIVED, not listed: whoever writes `agent_status_state` must maintain the anchor.

    FIX 1 in the sibling table established this the hard way -- a reader anchored to a column one of
    its writers ignores is worth nothing on the rows that writer touches. There are two here, and the
    second was found by grep rather than by memory: `_clear_status_state_in_turn` in `turn_state.py`
    exists precisely because "the busy SETTERS feed both tables, but several reaper/clear paths
    cleared only agent_turn_state". A clear path that skipped the anchor is the same shape again.
    """

    DB_NAME = "aify-status-anchor-writers-test.db"

    def test_the_writer_set_is_the_one_this_test_knows_about(self):
        """If a THIRD writer appears, this fails and says so -- rather than the anchor quietly
        going stale on whatever rows that writer touches."""
        import re
        from pathlib import Path as _Path

        root = _Path(__file__).resolve().parents[1]
        writers = set()
        for path in root.rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"(INSERT INTO|UPDATE)\s+agent_status_state", text):
                writers.add(str(path.relative_to(root)).replace("\\", "/"))

        self.assertEqual(
            writers,
            {"api_core/status_events.py", "api_core/turn_state.py", "db.py"},
            "the set of files writing agent_status_state changed. Each must maintain "
            "`turn_started_at`: stamp it when in_turn goes 0->1, leave it alone while 1, clear it "
            "when the turn ends. A writer that ignores it makes the ceiling unreachable for the "
            "rows it touches, which is exactly the defect this column was added to fix.",
        )

    def test_the_reaper_clear_path_also_clears_the_anchor(self):
        """The second writer, driven rather than read."""
        import asyncio as _asyncio

        from service.api_core.turn_state import _clear_status_state_in_turn

        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    "INSERT INTO agent_status_state "
                    "(agent_id, in_turn, awaiting_input, turn_run_id, last_event, last_event_at, "
                    " turn_started_at, updated_at) VALUES ('sw-reap',1,0,'','turn_start',?,?,?)",
                    (_stamp(10), _stamp(7200), _stamp(10)))
                await db.commit()
                await _clear_status_state_in_turn(db, "sw-reap")
                await db.commit()
                db.row_factory = None
                cursor = await db.execute(
                    "SELECT in_turn, turn_started_at FROM agent_status_state WHERE agent_id=?",
                    ("sw-reap",))
                return await cursor.fetchone()
            finally:
                await db.close()

        in_turn, anchor = _asyncio.run(_run())
        self.assertEqual(in_turn, 0)
        self.assertEqual(anchor, "", "the reaper ended the turn and left its start behind")
