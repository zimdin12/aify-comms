"""The delivery gates read RAW turn_busy, but an abandoned flag must not strand work forever.

`turn_busy` is set by the harness/bridge and cleared by a turn-END event. Two documented holes
mean the clear can never arrive:

  * ``_clear_turn_busy_for_dead_bridges`` deliberately skips ``turn_bridge_id`` in
    ``('', 'user-prompt-submit')`` — every hook-driven resident-claude turn — and skips any turn
    whose bridge is still alive.
  * A killed harness / failed Stop hook / transcript classifier stuck on "in flight" latches
    ``turn_busy=1`` with no further writes.
  * AND THE ONE THAT ACTUALLY STRANDED A FLEET: a latch that is still being WRITTEN. A managed
    hermes agent's ``pre_llm_call`` hook POSTs /turn-start before every model call, re-stamping
    the row roughly every 45 seconds. Everything above this line ages a row and then stops
    touching it, so all of it passed while a real agent held every queued dispatch for 38
    minutes. ``LatchedButStillBeatingTests`` covers that shape; the ceiling now ages against
    ``turn_started_at``, which no re-stamp moves.

Past ``TURN_BUSY_BACKSTOP_SECONDS`` the status engine already clamps ``in_turn`` (both the push
and poll paths), so the agent READS idle. If the delivery gates keep holding on the raw flag past
that same ceiling, the dashboard shows an idle agent whose queued work can never be claimed — and
for a target without ``steer`` the claim gate's early return makes it deaf to EVERY dispatch.

These tests pin the ceiling on both gates.
"""
import asyncio
import time

from service.db import get_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now
# v0.5.3: the helper moved to the dispatch+messages package. The CONSTANT stays in api_v2 — the
# parity assertion below is against the router on purpose, because that is where the status engine
# reads it from too.
from service.routers.dispatch_messages import shared as dispatch_shared

from service.tests._base import FastApiTestCase
from service.api_core import liveness  # v0.5.4: call the OWNER
from service.api_core import claim_gating  # v0.5.4: call the OWNER, not a re-export
from service.clock import now as _now


class TurnBusyDeliveryCeilingTests(FastApiTestCase):
    DB_NAME = "aify-turn-busy-ceiling-test.db"

    def _set_turn_busy(self, agent_id, *, age_seconds, bridge_id="user-prompt-submit"):
        """Latch turn_busy=1 with turn_updated_at aged by `age_seconds`."""
        stamp = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_seconds)
        )

        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO agent_turn_state
                        (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
                    VALUES (?, 1, '', ?, '', ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        turn_busy = 1,
                        turn_bridge_id = excluded.turn_bridge_id,
                        turn_updated_at = excluded.turn_updated_at
                    """,
                    (agent_id, bridge_id, stamp),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _holds(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                return await claim_gating._turn_busy_holds_delivery(db, agent_id)
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_fresh_turn_busy_still_holds_delivery(self):
        """A real in-flight turn MUST still hold. The detectors KEEP-FRESH re-stamp
        turn-start, so any genuinely running turn keeps turn_updated_at advancing."""
        self._set_turn_busy("tbc-fresh", age_seconds=5)
        self.assertTrue(self._holds("tbc-fresh"), "a fresh turn must still gate delivery")

    def test_turn_busy_just_inside_ceiling_still_holds(self):
        """Long turns are legitimate — hold right up to the ceiling."""
        self._set_turn_busy(
            "tbc-inside", age_seconds=liveness.TURN_BUSY_BACKSTOP_SECONDS - 60
        )
        self.assertTrue(
            self._holds("tbc-inside"),
            "a long-but-live turn inside the ceiling must still gate delivery",
        )

    def test_abandoned_turn_busy_past_ceiling_releases_delivery(self):
        """THE REGRESSION: past the ceiling status reports not-in-turn, so the gates must
        release too — otherwise queued work strands forever and non-steer targets go deaf."""
        self._set_turn_busy(
            "tbc-abandoned", age_seconds=liveness.TURN_BUSY_BACKSTOP_SECONDS + 60
        )
        self.assertFalse(
            self._holds("tbc-abandoned"),
            "an abandoned turn_busy past TURN_BUSY_BACKSTOP_SECONDS must not hold delivery",
        )

    def test_ceiling_matches_the_status_engine_clamp(self):
        """The bound is only correct because it is the SAME ceiling derive() uses to clamp
        in_turn. If someone decouples them, delivery and displayed status can disagree
        permanently — pin them together."""
        self._set_turn_busy(
            "tbc-parity", age_seconds=liveness.TURN_BUSY_BACKSTOP_SECONDS + 5
        )
        self.assertFalse(self._holds("tbc-parity"))
        # derive()'s clamp reads the same constant; assert it is the one we bound against.
        self.assertEqual(liveness.TURN_BUSY_BACKSTOP_SECONDS, 30 * 60)

    def test_turn_busy_zero_never_holds(self):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO agent_turn_state
                        (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
                    VALUES (?, 0, '', '', '', ?)
                    """,
                    ("tbc-idle", _now()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())
        self.assertFalse(self._holds("tbc-idle"))

    def test_missing_row_never_holds(self):
        self.assertFalse(self._holds("tbc-never-seen"))

    def test_unstampable_turn_busy_must_not_hold(self):
        """A latched flag with NO usable timestamp must release, not hold.

        Review follow-up 2026-07-26: the first cut returned "hold" here, which reproduced the very
        strand this helper prevents — nothing to age against means no ceiling can ever fire, so a
        non-steer target would go permanently deaf. Every writer stamps turn_updated_at via
        _now(), so a blank/malformed value is a corrupt row, not a live turn. Releasing risks one
        mid-turn delivery (recoverable); holding risks an agent that never gets work again.
        """
        for label, stamp in (
            ("blank", ""),
            ("garbage", "not-a-timestamp"),
            ("half-iso", "2026-07-26T"),
        ):
            with self.subTest(stamp=label):
                agent = f"tbc-bad-{label}"

                async def _run():
                    db = await get_db()
                    try:
                        await db.execute("PRAGMA foreign_keys=OFF")
                        await db.execute(
                            """
                            INSERT INTO agent_turn_state
                                (agent_id, turn_busy, turn_run_id, turn_bridge_id,
                                 turn_runtime, turn_updated_at)
                            VALUES (?, 1, '', '', '', ?)
                            """,
                            (agent, stamp),
                        )
                        await db.commit()
                    finally:
                        await db.close()

                asyncio.run(_run())
                self.assertFalse(
                    self._holds(agent),
                    f"turn_updated_at={stamp!r} gives no ceiling to age against — holding here "
                    "is a permanent strand",
                )

    def test_future_turn_updated_at_must_not_hold(self):
        """R4 (review 2026-07-26). A FUTURE timestamp makes `now - seen` NEGATIVE, which trivially
        satisfies `<= CEILING` — so a clock-skewed or bad write would hold delivery FOREVER, the
        exact permanent strand this ceiling exists to bound. I closed the missing-timestamp hole in
        the same predicate and missed this one."""
        for label, ahead in (("1 min ahead", 60), ("1 day ahead", 86400), ("1 year ahead", 31536000)):
            with self.subTest(skew=label):
                agent = f"tbc-future-{ahead}"
                self._set_turn_busy(agent, age_seconds=-ahead)
                self.assertFalse(
                    self._holds(agent),
                    f"turn_updated_at {label} must not hold delivery — a negative age is not "
                    "'inside the window', it is a broken clock",
                )

    def test_zero_age_still_holds(self):
        """Boundary: an age of exactly 0 is a legitimate just-written turn."""
        self._set_turn_busy("tbc-now", age_seconds=0)
        self.assertTrue(self._holds("tbc-now"))


class LatchedButStillBeatingTests(FastApiTestCase):
    """The ceiling must fire on a latch that is STILL BEING REFRESHED.

    THE HOLE THIS FILE LEFT. The docstring at the top of this module says a latched flag comes with
    "no further writes". The one that actually stranded the operator's fleet came WITH writes: a
    managed hermes agent's `pre_llm_call` hook POSTs /turn-start before every model call, which
    re-stamps `turn_updated_at` roughly every 45 seconds. Every test above ages a row and stops
    touching it, so all nine pass while the real shape holds delivery for ever.

    MEASURED 2026-08-30 on the live fleet: `graph-senior-dev` held every queued dispatch for 38
    minutes; two direct reads 45s apart showed `turn_updated_at` advancing 18:23:11Z -> 18:23:56Z,
    so the 1800s ceiling saw an age of ~45s and never fired. The dead-bridge sweep could not help
    either -- it deliberately skips the hook marker this agent was stamped with.

    The fix is to age against WHEN THE TURN BEGAN, which no re-stamp moves.
    """

    DB_NAME = "aify-turn-busy-latched-test.db"

    def _latch(self, agent_id, *, started_age, updated_age, bridge_id="user-prompt-submit"):
        """A turn that began `started_age` ago and was last re-stamped `updated_age` ago."""
        def stamp(age):
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age))

        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO agent_turn_state
                        (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime,
                         turn_updated_at, turn_started_at)
                    VALUES (?, 1, '', ?, 'hermes', ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        turn_busy = 1,
                        turn_updated_at = excluded.turn_updated_at,
                        turn_started_at = excluded.turn_started_at
                    """,
                    (agent_id, bridge_id, stamp(updated_age), stamp(started_age)),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _holds(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                return await claim_gating._turn_busy_holds_delivery(db, agent_id)
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_a_latch_kept_warm_by_a_re_stamping_poster_still_ages_out(self):
        """THE DEFECT. Two hours into a 'turn', re-stamped 45 seconds ago."""
        self._latch("tbl-warm", started_age=7200, updated_age=45)
        self.assertFalse(
            self._holds("tbl-warm"),
            "a turn that began 2 hours ago held delivery because something re-stamped it 45s ago. "
            "The ceiling must measure from the START, or any timer-driven poster defeats it.",
        )

    def test_a_genuinely_long_turn_inside_the_ceiling_still_holds(self):
        """The other side of the same bound: real work must not be cut off early."""
        self._latch("tbl-real", started_age=60, updated_age=5)
        self.assertTrue(self._holds("tbl-real"), "a turn one minute old must still gate delivery")

    def test_a_row_with_no_start_anchor_falls_back_to_the_old_column(self):
        """Rows written before this column existed must behave exactly as they did.

        Their anchor is backfilled at boot, but a row that reaches the reader without one must not
        become un-gateable -- that would deliver mid-turn to every legacy agent at once.
        """
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO agent_turn_state
                        (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime,
                         turn_updated_at, turn_started_at)
                    VALUES (?, 1, '', 'user-prompt-submit', '', ?, '')
                    """,
                    ("tbl-legacy", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())
        self.assertTrue(
            self._holds("tbl-legacy"),
            "with no start anchor the reader must fall back to turn_updated_at, not release",
        )

    def test_the_ceiling_is_the_one_constant_not_a_second_number(self):
        """A separate bound here would drift from the status engine's clamp and reopen the
        disagreement this ceiling exists to close."""
        self._latch("tbl-edge", started_age=liveness.TURN_BUSY_BACKSTOP_SECONDS - 5, updated_age=1)
        self.assertTrue(self._holds("tbl-edge"), "just inside the ceiling must still hold")
        self._latch("tbl-past", started_age=liveness.TURN_BUSY_BACKSTOP_SECONDS + 5, updated_age=1)
        self.assertFalse(self._holds("tbl-past"), "just past the ceiling must release")


class TheWritersMustNotMoveTheAnchorTests(FastApiTestCase):
    """A reader anchored to turn_started_at is worthless if a writer re-stamps it every 45s.

    This is the half that would have stayed green while the bug survived: the reader tests above
    set the column directly, so they pass whatever the writers do. `/turn-start` is re-entered by
    the hermes `pre_llm_call` hook before every model call, which is precisely the poster that
    defeated the previous anchor.
    """

    DB_NAME = "aify-turn-anchor-writer-test.db"

    def _post_turn_start(self, agent_id):
        return self.client.post(f"/api/v1/agents/{agent_id}/turn-start", json={})

    def _row(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                return await (await db.execute(
                    "SELECT turn_busy, turn_updated_at, turn_started_at "
                    "FROM agent_turn_state WHERE agent_id = ?", (agent_id,))).fetchone()
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_a_second_turn_start_moves_the_clock_but_NOT_the_anchor(self):
        """The anchor is BACKDATED before the second post, so a re-stamp is visible.

        The first version of this test compared two stamps taken inside one second and could not
        tell a preserved anchor from a rewritten one -- a mutation that re-stamped on every post
        left it green. These timestamps are second-resolution, so the only way to observe the write
        is to make the existing value one the writer could not have produced.
        """
        self.client.post("/api/v1/agents", json={
            "agentId": "anchor-a", "role": "coder", "runtime": "hermes"})
        self._post_turn_start("anchor-a")
        self.assertTrue(self._row("anchor-a")["turn_started_at"], "the first post must set an anchor")

        long_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200))
        aged_touch = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))

        async def _backdate():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE agent_turn_state SET turn_started_at = ?, turn_updated_at = ? "
                    "WHERE agent_id = ?", (long_ago, aged_touch, "anchor-a"))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_backdate())
        self._post_turn_start("anchor-a")     # the hook re-entering mid-turn
        after = self._row("anchor-a")

        self.assertEqual(
            after["turn_started_at"], long_ago,
            "a re-entered turn-start REWROTE the anchor. That is the defect: `pre_llm_call` fires "
            "before every model call, so the 30-minute ceiling would be postponed for ever.",
        )
        # CONTROL: the last-touch column DID move, so this test observes a real write and is not
        # passing because the endpoint did nothing at all.
        self.assertNotEqual(
            after["turn_updated_at"], aged_touch,
            "turn_updated_at must still advance -- it is what freshness checks read",
        )

    def test_a_NEW_turn_after_an_end_takes_a_fresh_anchor(self):
        """The other direction. If the anchor were kept across turns, the next real turn would
        start already past the ceiling and gate nothing."""
        self.client.post("/api/v1/agents", json={
            "agentId": "anchor-b", "role": "coder", "runtime": "hermes"})
        self._post_turn_start("anchor-b")
        first = self._row("anchor-b")

        async def _end_and_backdate():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE agent_turn_state SET turn_busy = 0, turn_started_at = ? WHERE agent_id = ?",
                    (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 9999)), "anchor-b"))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_end_and_backdate())
        backdated = self._row("anchor-b")["turn_started_at"]
        self._post_turn_start("anchor-b")
        second = self._row("anchor-b")
        # Again against the backdated sentinel: `first` and the new anchor are both "now".
        self.assertNotEqual(
            second["turn_started_at"], backdated,
            "a turn-start on a NOT-busy row must take a fresh anchor",
        )
        self.assertEqual(second["turn_started_at"], first["turn_started_at"][:11] + second["turn_started_at"][11:],
                         "sanity: the fresh anchor is a current stamp, not the backdated one")
        self.assertTrue(int(second["turn_busy"]), "and it must latch busy again")
