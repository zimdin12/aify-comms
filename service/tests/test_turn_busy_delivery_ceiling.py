"""The delivery gates read RAW turn_busy, but an abandoned flag must not strand work forever.

`turn_busy` is set by the harness/bridge and cleared by a turn-END event. Two documented holes
mean the clear can never arrive:

  * ``_clear_turn_busy_for_dead_bridges`` deliberately skips ``turn_bridge_id`` in
    ``('', 'user-prompt-submit')`` — every hook-driven resident-claude turn — and skips any turn
    whose bridge is still alive.
  * A killed harness / failed Stop hook / transcript classifier stuck on "in flight" latches
    ``turn_busy=1`` with no further writes.

Past ``TURN_BUSY_BACKSTOP_SECONDS`` the status engine already clamps ``in_turn`` (both the push
and poll paths), so the agent READS idle. If the delivery gates keep holding on the raw flag past
that same ceiling, the dashboard shows an idle agent whose queued work can never be claimed — and
for a target without ``steer`` the claim gate's early return makes it deaf to EVERY dispatch.

These tests pin the ceiling on both gates.
"""
import asyncio
import time

from service.db import get_db
from service.routers import api_v2
# v0.5.3: the helper moved to the dispatch+messages package. The CONSTANT stays in api_v2 — the
# parity assertion below is against the router on purpose, because that is where the status engine
# reads it from too.
from service.routers.dispatch_messages import shared as dispatch_shared

from service.tests._base import FastApiTestCase


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
                return await dispatch_shared._turn_busy_holds_delivery(db, agent_id)
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
            "tbc-inside", age_seconds=api_v2.TURN_BUSY_BACKSTOP_SECONDS - 60
        )
        self.assertTrue(
            self._holds("tbc-inside"),
            "a long-but-live turn inside the ceiling must still gate delivery",
        )

    def test_abandoned_turn_busy_past_ceiling_releases_delivery(self):
        """THE REGRESSION: past the ceiling status reports not-in-turn, so the gates must
        release too — otherwise queued work strands forever and non-steer targets go deaf."""
        self._set_turn_busy(
            "tbc-abandoned", age_seconds=api_v2.TURN_BUSY_BACKSTOP_SECONDS + 60
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
            "tbc-parity", age_seconds=api_v2.TURN_BUSY_BACKSTOP_SECONDS + 5
        )
        self.assertFalse(self._holds("tbc-parity"))
        # derive()'s clamp reads the same constant; assert it is the one we bound against.
        self.assertEqual(api_v2.TURN_BUSY_BACKSTOP_SECONDS, 30 * 60)

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
                    ("tbc-idle", api_v2._now()),
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
