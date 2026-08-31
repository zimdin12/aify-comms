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
        """Call the PRODUCTION clamp against a row read by the PRODUCTION query.

        This used to recompute the comparison itself from a row this file selected itself. That is
        the shape the review caught, and it is worse than it looks: a test that writes its own
        SELECT can never notice that a production query omits a column, and a test that recomputes
        the arithmetic can never notice that the two call sites disagree. Both of those were true --
        `status_signal_prefetch.py` had two queries without the anchor, and the fix was inert on the
        served path while this file was green.

        So the row comes from `signals.status_state`, which is the reader the served path uses, and
        the verdict comes from `_in_turn_survives_the_ceiling`, which is what both call sites call.
        """
        async def _run():
            db = await get_db()
            try:
                from service.api_core.status_signal_prefetch import status_signals_or_live
                row = await status_signals_or_live(None).status_state(db, agent_id)
                if not row or not row["in_turn"]:
                    return False
                return status_inputs._in_turn_survives_the_ceiling(row)
            finally:
                await db.close()

        return asyncio.run(_run())

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
                    # RE-READ, because the anchor this branch just wrote is `now - 7200` at the
                    # CURRENT second, not at the second `before` was captured. These stamps are
                    # second-resolution (see `_backdate_anchor`), so comparing every later kind
                    # against the original value fails the moment the loop crosses a one-second
                    # boundary -- and only then, which is what made it intermittent: three full-suite
                    # runs on 2026-08-31, two green and one red on exactly `turn_start` and
                    # `unblocked`, the two kinds that follow `turn_end` in sorted order. Reproduced
                    # deterministically by sleeping 1.1s here, which reddened those same two.
                    before = self._anchor("sw-all")

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


class BothProductionBuildersRenewAVerifiedTurnTests(FastApiTestCase):
    """A 47-minute turn with a LIVE bridge must read `working` on both builders.

    THE HALF-FIX THE REVIEW CAUGHT. Sharing one policy function is not the same as sharing its
    ANSWER: both `agent_status_state` clamps called it with the default `renewable=False`, so a turn
    delivery correctly held for up to four hours read as finished on status after thirty minutes.
    Agreement in form, not in fact.

    THESE DRIVE `_gather_status_inputs` AND `_compute_live_status_cache` THEMSELVES, because that is
    the only thing that would have caught it. Every earlier test in this file went through the pure
    clamp, which is exactly where the constant was NOT -- the constant was at the call sites.
    """

    DB_NAME = "aify-status-anchor-builders-test.db"

    def _seed(self, agent_id, *, started_age, bridge_id, bridge_last_seen_age):
        """A turn `started_age` old, owned by a bridge that beat `bridge_last_seen_age` ago."""
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    "INSERT INTO agents (id, name, role, status, session_mode, launch_mode,"
                    " registered_at, last_seen) "
                    "VALUES (?,?,'coder','online','resident','detached',?,?) "
                    "ON CONFLICT(id) DO UPDATE SET status='online'",
                    (agent_id, agent_id, _stamp(3600), _stamp(5)))
                await db.execute(
                    "INSERT INTO agent_status_state (agent_id, in_turn, awaiting_input, turn_run_id,"
                    " last_event, last_event_at, turn_started_at, updated_at)"
                    " VALUES (?,1,0,'','turn_start',?,?,?)"
                    " ON CONFLICT(agent_id) DO UPDATE SET in_turn=1,"
                    " last_event_at=excluded.last_event_at,"
                    " turn_started_at=excluded.turn_started_at",
                    (agent_id, _stamp(5), _stamp(started_age), _stamp(5)))
                await db.execute(
                    "INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id,"
                    " turn_runtime, turn_updated_at, turn_started_at)"
                    " VALUES (?,1,'',?,'hermes',?,?)"
                    " ON CONFLICT(agent_id) DO UPDATE SET turn_busy=1,"
                    " turn_bridge_id=excluded.turn_bridge_id,"
                    " turn_updated_at=excluded.turn_updated_at,"
                    " turn_started_at=excluded.turn_started_at",
                    (agent_id, bridge_id, _stamp(5), _stamp(started_age)))
                if bridge_id:
                    await db.execute(
                        "INSERT INTO bridge_instances (id, agent_id, machine_id, last_seen,"
                        " superseded_by, registered_at) VALUES (?,?,'m1',?,'',?)"
                        " ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,"
                        " agent_id=excluded.agent_id, superseded_by=''",
                        (bridge_id, agent_id, _stamp(bridge_last_seen_age), _stamp(7200)))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _both_builders(self, agent_id):
        """The two production builders, run against the same row."""
        async def _run():
            db = await get_db()
            try:
                db.row_factory = __import__("aiosqlite").Row
                agent_row = await (await db.execute(
                    "SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
                gathered = await status_inputs._gather_status_inputs(db, agent_row)
                cached = await status_inputs._compute_live_status_cache(db, agent_row)
                # THE SERVED PATH RETURNS A DERIVED STATUS, not the raw flag -- it feeds `in_turn`
                # into `derive()` and hands back the answer. Asserting on a key it does not publish
                # read as False for every case and would have "passed" the negative tests while
                # proving nothing, which is the vacuity this file has already been caught by once.
                return bool(gathered.in_turn), str(cached.get("status") or "")
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_a_47_MINUTE_turn_with_a_LIVE_bridge_reads_working_on_BOTH(self):
        """THE DEFECT the review named, in the units it named it in. Past the strict ceiling, well
        inside the absolute bound, with an independently observable claimant."""
        self._seed("sb-live", started_age=47 * 60, bridge_id="br-live",
                   bridge_last_seen_age=3)
        gathered, served_status = self._both_builders("sb-live")
        self.assertTrue(gathered, "the authoritative builder ended a verified 47-minute turn")
        self.assertEqual(served_status, "working",
                         "the SERVED builder ended a verified 47-minute turn")

    def test_the_same_turn_with_a_DEAD_bridge_is_still_cut_at_the_strict_anchor(self):
        """ANTI-VACUITY, and the whole point of the operator's ruling: renewal is for claims that are
        independently observable. A bridge that stopped beating proves nothing, so the strict
        thirty-minute anchor applies and the latch is released."""
        self._seed("sb-dead", started_age=47 * 60, bridge_id="br-dead",
                   bridge_last_seen_age=6000)
        gathered, served_status = self._both_builders("sb-dead")
        self.assertFalse(gathered, "an unverifiable 47-minute turn held on the authoritative path")
        self.assertNotEqual(served_status, "working",
                            "an unverifiable 47-minute turn held on the served path")

    def test_and_past_the_ABSOLUTE_bound_even_a_live_bridge_does_not_hold_it(self):
        """A renewable lease with no ceiling is the permanent strand again in a better hat."""
        self._seed("sb-forever", started_age=5 * 60 * 60, bridge_id="br-live2",
                   bridge_last_seen_age=3)
        gathered, served_status = self._both_builders("sb-forever")
        self.assertFalse(gathered)
        self.assertNotEqual(served_status, "working")


class TheTwoTABLES_MUST_NOT_CARRY_DIFFERENT_ANSWERSTests(FastApiTestCase):
    """Sharing the policy and the renewal verdict is still not sharing the EVIDENCE.

    `agent_status_state` carries the status engine's start and last-event; `agent_turn_state` carries
    the harness signal's start and last touch. Delivery ages the second pair. A previous round aged
    status on the FIRST pair even while renewing, so the same function and the same ownership verdict
    produced two answers whenever the tables disagreed -- which is reachable any time one writer
    updates one table without the other, the exact drift class these parallel tables have produced
    before.

    THE 47-MINUTE TEST ABOVE CANNOT CATCH THIS: it seeds both clocks identically, so the carriers
    agree by construction and the seam is invisible. These seed them apart on purpose, in both
    directions, and require all THREE readers to agree.
    """

    DB_NAME = "aify-status-carrier-parity-test.db"

    def _seed(self, agent_id, *, status_touch_age, turn_touch_age, started_age=47 * 60,
              bridge_id="br-parity", bridge_age=3):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    "INSERT INTO agents (id, name, role, status, session_mode, launch_mode,"
                    " registered_at, last_seen) VALUES (?,?,'coder','online','resident','detached',?,?)"
                    " ON CONFLICT(id) DO UPDATE SET status='online'",
                    (agent_id, agent_id, _stamp(3600), _stamp(5)))
                await db.execute(
                    "INSERT INTO agent_status_state (agent_id, in_turn, awaiting_input, turn_run_id,"
                    " last_event, last_event_at, turn_started_at, updated_at)"
                    " VALUES (?,1,0,'','turn_start',?,?,?)"
                    " ON CONFLICT(agent_id) DO UPDATE SET in_turn=1,"
                    " last_event_at=excluded.last_event_at, turn_started_at=excluded.turn_started_at",
                    (agent_id, _stamp(status_touch_age), _stamp(started_age), _stamp(status_touch_age)))
                await db.execute(
                    "INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id,"
                    " turn_runtime, turn_updated_at, turn_started_at)"
                    " VALUES (?,1,'',?,'hermes',?,?)"
                    " ON CONFLICT(agent_id) DO UPDATE SET turn_busy=1,"
                    " turn_bridge_id=excluded.turn_bridge_id,"
                    " turn_updated_at=excluded.turn_updated_at,"
                    " turn_started_at=excluded.turn_started_at",
                    (agent_id, bridge_id, _stamp(turn_touch_age), _stamp(started_age)))
                await db.execute(
                    "INSERT INTO bridge_instances (id, agent_id, machine_id, last_seen,"
                    " superseded_by, registered_at) VALUES (?,?,'m1',?,'',?)"
                    " ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,"
                    " agent_id=excluded.agent_id, superseded_by=''",
                    (bridge_id, agent_id, _stamp(bridge_age), _stamp(7200)))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _all_three(self, agent_id):
        """Delivery, the authoritative builder, and the served builder — on one row."""
        async def _run():
            db = await get_db()
            try:
                db.row_factory = __import__("aiosqlite").Row
                from service.api_core import claim_gating
                delivery = await claim_gating._turn_busy_holds_delivery(db, agent_id)
                agent_row = await (await db.execute(
                    "SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
                gathered = await status_inputs._gather_status_inputs(db, agent_row)
                cached = await status_inputs._compute_live_status_cache(db, agent_row)
                return (bool(delivery), bool(gathered.in_turn),
                        str(cached.get("status") or "") == "working")
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_status_fresh_turn_state_stale_must_not_let_status_outlive_delivery(self):
        """DISCRIMINATOR ONE. The status row was touched NOW, the harness row 31 minutes ago. Ageing
        status on its own carrier would renew it while delivery expired."""
        self._seed("cp-a", status_touch_age=0, turn_touch_age=31 * 60)
        delivery, gathered, served = self._all_three("cp-a")
        self.assertEqual(
            (delivery, gathered, served), (delivery, delivery, delivery),
            f"the three readers disagree: delivery={delivery} authoritative={gathered} "
            f"served={served}. Once a claim is trusted they must age the SAME evidence.",
        )

    def test_status_stale_turn_state_fresh_must_not_let_delivery_outlive_status(self):
        """DISCRIMINATOR TWO, the mirror. Delivery renews on a fresh harness touch; status must not
        expire underneath it and report an idle agent whose work is still held."""
        self._seed("cp-b", status_touch_age=31 * 60, turn_touch_age=0)
        delivery, gathered, served = self._all_three("cp-b")
        self.assertEqual(
            (delivery, gathered, served), (delivery, delivery, delivery),
            f"the three readers disagree: delivery={delivery} authoritative={gathered} "
            f"served={served}.",
        )

    def test_the_discriminators_are_not_vacuous(self):
        """ANTI-VACUITY. If both shapes happened to be dead everywhere, agreement would be trivial —
        so at least one of them must be a LIVE turn, and a dead bridge must still end all three."""
        self._seed("cp-live", status_touch_age=0, turn_touch_age=0)
        self.assertEqual(self._all_three("cp-live"), (True, True, True),
                         "a fresh verified turn was not live on all three readers")
        self._seed("cp-dead", status_touch_age=0, turn_touch_age=0, bridge_age=9000)
        self.assertEqual(self._all_three("cp-dead"), (False, False, False),
                         "a 47-minute turn with a DEAD bridge held somewhere")
