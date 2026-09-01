"""A queued spawn_request of any age answered "a worker is coming". Age is the whole question.

`_has_claimable_spawn_request` decides whether a dispatch may sit queued behind a spawn that has not
happened yet. Unbounded, it answered yes from a request nobody had claimed in hours: every dispatch to
that agent queued behind a promise nothing was keeping, and the agent read as merely busy.

TWO WAYS TO PRODUCE ONE, both real. A runtime no environment can serve leaves a request that never
resolves. And an environment that is simply DOWN leaves one too -- this repo produced exactly that on
2026-09-01, a five-minute window with no aify-env.

WHY A BOUND AND NOT A REAPER, which was the first plan. A sweep deleting stale requests must tell "no
environment can ever serve this" from "the environment is down for a moment", and it cannot. With a
short threshold it would have destroyed the legitimate queued spawns waiting out that same outage.
Freshness asks something answerable instead: has anything touched this recently.

THE WINDOW IS NOT A NEW NUMBER. `managed_env.SPAWN_INFLIGHT_WINDOW_SECONDS` already bounds the
`starting`/`running` arm of the sibling predicate for the same stated reason. The last test here
asserts the two stay equal, because that file records what two independent numbers cost: 300 in one
place and 180 in another left a window where the status said "idle, send something" while the code
refused to start a worker.
"""

from __future__ import annotations

import time
import unittest

import aiosqlite

from service.api_core.managed_env import SPAWN_INFLIGHT_WINDOW_SECONDS
from service.api_core.spawn_request_state import (
    SPAWN_CLAIM_WINDOW_SECONDS,
    _has_claimable_spawn_request,
)

ISO = "%Y-%m-%dT%H:%M:%SZ"


def stamp(seconds_ago: float) -> str:
    return time.strftime(ISO, time.gmtime(time.time() - seconds_ago))


class StaleSpawnRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.execute(
            """
            CREATE TABLE spawn_requests (
              id TEXT PRIMARY KEY, agent_id TEXT, status TEXT,
              created_at TEXT, updated_at TEXT, finished_at TEXT
            )
            """
        )
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def _add(self, rid, agent, status, age, *, updated=None):
        await self.db.execute(
            "INSERT INTO spawn_requests (id, agent_id, status, created_at, updated_at, finished_at)"
            " VALUES (?,?,?,?,?,'')",
            (rid, agent, status, stamp(age), "" if updated is None else stamp(updated)),
        )
        await self.db.commit()

    async def test_a_fresh_request_still_backs_a_dispatch(self):
        """POSITIVE CONTROL, and the case that must not regress: this is the ordinary cold start.

        A bound that answered no to everything would 'fix' the strand by refusing every dispatch to
        every agent whose worker is still booting.
        """
        await self._add("r1", "sc-coder", "queued", age=5)
        self.assertTrue(await _has_claimable_spawn_request(self.db, "sc-coder"))

    async def test_a_stale_request_is_not_evidence_that_anybody_is_coming(self):
        """THE DEFECT. Nothing has touched this in an hour; it promises nothing."""
        await self._add("r1", "sc-coder", "queued", age=3600)
        self.assertFalse(
            await _has_claimable_spawn_request(self.db, "sc-coder"),
            "an hour-old queued request still reads as a worker on its way, so every dispatch to this "
            "agent queues behind a promise nothing is keeping",
        )

    async def test_claimed_is_bounded_too_not_only_queued(self):
        """`claimed` means a bridge took it -- and a bridge that then died leaves it claimed for ever.

        Bounding only `queued` would move the strand rather than end it.
        """
        await self._add("r1", "sc-coder", "claimed", age=3600)
        self.assertFalse(await _has_claimable_spawn_request(self.db, "sc-coder"))
        await self._add("r2", "sc-other", "claimed", age=5)
        self.assertTrue(await _has_claimable_spawn_request(self.db, "sc-other"))

    async def test_the_boundary_is_where_it_says_it_is(self):
        """Either side of the window, so the bound is the stated one and not approximately it."""
        await self._add("inside", "a", "queued", age=SPAWN_CLAIM_WINDOW_SECONDS - 30)
        self.assertTrue(await _has_claimable_spawn_request(self.db, "a"))
        await self._add("outside", "b", "queued", age=SPAWN_CLAIM_WINDOW_SECONDS + 30)
        self.assertFalse(await _has_claimable_spawn_request(self.db, "b"))

    async def test_a_touch_refreshes_it_because_something_is_still_working_on_it(self):
        """`updated_at` wins over `created_at`: an old request a bridge touched a moment ago IS live
        evidence, and ageing it from creation would refuse a spawn that is genuinely progressing."""
        await self._add("r1", "sc-coder", "claimed", age=3600, updated=10)
        self.assertTrue(await _has_claimable_spawn_request(self.db, "sc-coder"))

    async def test_other_statuses_and_other_agents_are_not_borrowed(self):
        """NEGATIVE CONTROLS. A predicate that answered yes too readily would hide the strand again."""
        await self._add("r1", "sc-coder", "failed", age=5)
        self.assertFalse(await _has_claimable_spawn_request(self.db, "sc-coder"))
        await self._add("r2", "somebody-else", "queued", age=5)
        self.assertFalse(await _has_claimable_spawn_request(self.db, "sc-coder"))
        self.assertFalse(await _has_claimable_spawn_request(self.db, "nobody"))

    def test_the_window_matches_the_one_the_sibling_already_uses(self):
        """One number, asserted rather than trusted.

        It is duplicated instead of imported because importing `managed_env` here is the cycle this
        module's header exists to describe. Duplication with an agreement test is this project's
        standing answer; duplication without one is how 300 and 180 drifted apart.
        """
        self.assertEqual(
            SPAWN_CLAIM_WINDOW_SECONDS, SPAWN_INFLIGHT_WINDOW_SECONDS,
            "the spawn-claim window and the in-flight window have drifted apart. Two numbers for one "
            "idea is what produced a window where the status said 'idle, send something' while the "
            "code refused to start a worker.",
        )


if __name__ == "__main__":
    unittest.main()
