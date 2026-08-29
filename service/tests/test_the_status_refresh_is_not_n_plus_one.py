"""The fleet-wide status refresh reads two signals ONCE, not once per agent -- and derives the same.

WHY THIS PATH AND NOT ANOTHER. Measured 2026-08-27 by counting `aiosqlite.Connection.execute` calls,
because wall-clock is unmeasurable on this host -- the same code timed 44-47ms and then 22-25ms minutes
later, since the live fleet is the load. Round-trips are deterministic and attributable to a call site.

    `_refresh_expired_agent_live_states` with no limit -- the reconcile sweep's own call:
        1 agent -> 9 round-trips, 5 -> 37, 20 -> 142, 40 -> 282.  Exactly 7.0 per added agent.

A raw N+1 count is not on its own a reason to touch a path this safety-sensitive. The number that
decided it is the SHARE: of every round-trip in one whole reconcile pass, the per-agent refresh was
31% at 5 agents, 54% at 20, and 61% at 40. It grows with fleet size, so on a real fleet it is the
dominant term rather than a rounding error inside something bigger. After the change: 5.0 per agent,
and 49% of the pass at 40 agents.

`GET /agents` was never the problem and is not the target: it caps the recompute per request, so it is
flat in fleet size (65 round-trips at 20 agents and at 40). It got faster anyway -- 51 -- because its
capped batch prefetches too.

WHAT THIS FILE ASSERTS, in the order that matters:
  1. The two readers DERIVE THE SAME THING. A faster wrong answer is not an optimisation, and this is
     the status engine's input gatherer -- the module states it "MUST produce the same StatusInputs".
  2. The round-trip count, as a PROPERTY (flat in fleet size) rather than a total, so it does not fail
     on an unrelated query added elsewhere in the sweep.
  3. That an absent prefetch still READS. A guard that skips when its input is missing is decoration;
     here it would derive every agent from a missing signal.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import aiosqlite

from service.clock import now as clock_now
from service.tests._base import FastApiTestCase
from service.tests.frozen_clock import frozen_service_clock


def _count_round_trips(coro_factory):
    """Run an async callable, returning (result, [sql...]) for every round-trip it made."""
    calls: list[str] = []
    orig = aiosqlite.Connection.execute

    async def spy(self, sql, *a, **k):
        calls.append(" ".join(str(sql).split()))
        return await orig(self, sql, *a, **k)

    async def go():
        from service.db import get_db
        db = await get_db()
        try:
            aiosqlite.Connection.execute = spy
            try:
                return await coro_factory(db)
            finally:
                aiosqlite.Connection.execute = orig
        finally:
            await db.close()

    return asyncio.run(go()), calls


class StatusRefreshIsNotNPlusOneTests(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    #: The two reads this change batched, named by TABLE. A scan keyed on the exact SQL text would go
    #: quietly to zero after any edit to the query and report a perfect result.
    BATCHED_TABLES = ("FROM agent_status_state", "FROM agent_console_signal")

    def _register(self, n):
        for i in range(n):
            response = self.client.post("/api/v1/agents", json={
                "agentId": "npo-{}".format(i), "role": "coder", "runtime": "claude-code",
                "sessionMode": "resident", "machineId": "linux:npo-host",
            })
            self.assertEqual(response.status_code, 200, response.text)

    @staticmethod
    def _clear_cache():
        from service.reconcilers import status_cache
        status_cache._LIVE_STATE_CACHE.clear()

    def _refresh_all(self):
        from service.api_core.status_refresh import _refresh_expired_agent_live_states
        self._clear_cache()
        return _count_round_trips(lambda db: _refresh_expired_agent_live_states(db))

    def test_the_probe_can_see_round_trips_at_all(self):
        """POSITIVE CONTROL. A counter that never increments looks exactly like a perfect endpoint, so
        every count below is worthless without this."""
        self._register(3)
        refreshed, calls = self._refresh_all()
        self.assertEqual(refreshed, 3)
        self.assertGreater(len(calls), 5, "the probe recorded almost nothing; it is not wired in")

    def test_the_batched_tables_are_read_ONCE_for_the_whole_fleet(self):
        """The measurement, as a gate. Before this change each was read once per agent."""
        self._register(6)
        _, calls = self._refresh_all()
        for table in self.BATCHED_TABLES:
            hits = [c for c in calls if table in c]
            self.assertEqual(
                len(hits), 1,
                "{} was read {} times for 6 agents; it must be one batched read. queries: {}".format(
                    table, len(hits), hits[:3]),
            )

    def test_per_agent_cost_does_not_grow_when_the_fleet_does(self):
        """The property, not the constant. Pinning a total would fail on any unrelated query added to
        the sweep; what must hold is that these two reads are FLAT in fleet size."""
        counts = {}
        for n in (2, 8):
            self.setUp()  # a fresh DB per size, so the two runs cannot share rows
            self._register(n)
            _, calls = self._refresh_all()
            counts[n] = sum(1 for c in calls if any(t in c for t in self.BATCHED_TABLES))
        self.assertEqual(counts[2], counts[8], "batched reads grew with the fleet: {}".format(counts))

    def test_a_single_agent_does_not_pay_for_a_batch_of_one(self):
        """The prefetch is skipped below two agents: an IN clause around the same two reads is not a
        saving, and the single-agent callers are the hot ones (registration, heartbeat)."""
        self._register(1)
        _, calls = self._refresh_all()
        for table in self.BATCHED_TABLES:
            self.assertEqual(len([c for c in calls if table in c]), 1)
        self.assertNotIn("IN (?)", " ".join(calls), "a batch of one still built an IN clause")

    def _seed_signal_rows(self, agent_id, *, last_event_at=None):
        """Give an agent REAL rows in both batched tables, so the columns are actually read.

        WITHOUT THIS THE COMPARISON IS VACUOUS, and was: a freshly-registered agent has no row in
        either table, so both readers returned None and no column of either query was ever consulted.
        A mutation that dropped `last_event_at` from the prefetched SELECT SURVIVED the equivalence
        test until this existed.

        `last_event_at` IS WRITTEN FRESH, which is the opposite of what it looks like it should be.
        The column feeds a staleness clamp that clears a latched `in_turn`. Seed it STALE and the
        clamp fires, `in_turn` comes back False, and the agent derives exactly like an unseeded one --
        so the row is present, the answer is unchanged, and nothing is proven. That is not a guess:
        the anti-vacuity check below caught precisely that on the first attempt. Fresh, the clamp does
        NOT fire, `in_turn` survives, and a prefetch that lost the column reads as ancient and clears
        it -- which is the difference this file exists to detect.
        """
        from service.api_core.status_inputs import _now
        started = self.client.post(
            "/api/v1/agents/{}/status-event".format(agent_id),
            json={"kind": "turn_start", "runId": "run-npo"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        # EVERY SEEDED COLUMN GETS A NON-DEFAULT VALUE, or the pinning below is vacuous for it: a
        # mutation replacing `subagents_at` with '' is invisible when the fixture leaves it '', and
        # the same for `awaiting_input` at 0. Both survived until these two lines existed.
        working = self.client.post(
            "/api/v1/agents/{}/console-working".format(agent_id), json={"subagents": True})
        self.assertEqual(working.status_code, 200, working.text)
        blocked = self.client.post(
            "/api/v1/agents/{}/status-event".format(agent_id), json={"kind": "blocked"})
        self.assertEqual(blocked.status_code, 200, blocked.text)

        async def age_it():
            from service.db import get_db
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE agent_status_state SET in_turn = 1, last_event_at = ? WHERE agent_id = ?",
                    (last_event_at or _now(), agent_id),
                )
                await db.commit()
                row = await (await db.execute(
                    "SELECT in_turn, awaiting_input, last_event_at FROM agent_status_state "
                    "WHERE agent_id = ?", (agent_id,),
                )).fetchone()
                console = await (await db.execute(
                    "SELECT working_at, subagents_at FROM agent_console_signal WHERE agent_id = ?",
                    (agent_id,),
                )).fetchone()
                return row, console
            finally:
                await db.close()

        row, console = asyncio.run(age_it())
        # POSITIVE CONTROL on the fixture itself: if the seed did not land, every comparison below
        # silently reverts to the vacuous all-None case this method exists to escape.
        self.assertIsNotNone(row, "the status_state seed did not land for {}".format(agent_id))
        self.assertEqual(row["in_turn"], 1)
        self.assertTrue(str(row["last_event_at"] or "").strip(), "the seed left last_event_at empty")
        self.assertTrue(row["awaiting_input"], "the seed left awaiting_input at its default")
        self.assertIsNotNone(console, "the seed produced no console_signal row")
        self.assertTrue(str(console["working_at"] or "").strip(), "the seed left working_at empty")
        self.assertTrue(str(console["subagents_at"] or "").strip(),
                        "the seed left subagents_at at its default, so pinning it proves nothing")

    @contextlib.contextmanager
    def _frozen_clock(self):
        """One `now` for a whole comparison.

        A REAL FLAKE, THREE TIMES, and the first two fixes never touched the clock this path reads.
        These tests derive the same agents through both readers and compare the WHOLE dict, which
        carries a wall-clock stamp; under load two derivations straddle a second boundary and the
        dicts differ by a field that has nothing to do with the prefetch.

        WHAT THE EARLIER FIXES FROZE. `status_refresh._now` -- and `_refresh_agent_live_state`, the
        function under test, never calls it. It takes `now` as a parameter, defaults it to None, and
        hands that to `_compute_live_status_cache`, which stamps `updated_at` from
        `status_inputs._now`. The frozen name belonged to a sibling function this test does not
        call, so the freeze had never once worked; it passed because two derivations usually land in
        the same second, which is also why the flake was rare rather than absent.

        Reproduced on demand rather than waited for, by putting a real 1.2s sleep between the two
        derivations: the only differing field was `updated_at`, live `...:34:32Z` against prefetched
        `...:34:33Z`.

        76 modules bind `service.clock.now` as their own `_now`, so freezing one name freezes one
        seventy-sixth of the clock. `frozen_service_clock` derives the set instead and refuses to
        run if it reaches almost nothing.

        FROZEN RATHER THAN EXCLUDED. Dropping the field from the comparison would also stop it being
        compared, and it is one of the things the prefetch has to carry correctly.
        """
        with frozen_service_clock() as frozen:
            yield frozen

    def test_prefetched_and_live_readers_DERIVE_THE_SAME_CACHE(self):
        """The assertion that matters most. Everything else here is about cost; this is about truth.

        The same agents are derived twice -- once with each reader -- and every field of the resulting
        live-state cache must match. A faster wrong answer is not an optimisation, and this feeds the
        status chip, the claim gates and every deliverability check.
        """
        from service.api_core.status_refresh import _refresh_agent_live_state
        from service.api_core.status_signal_prefetch import PrefetchedStatusSignals

        self._register(4)
        ids = ["npo-{}".format(i) for i in range(4)]
        # HALF SEEDED, half bare, so one comparison covers both shapes: an agent whose columns are
        # read, and an agent with no row at all.
        self._seed_signal_rows("npo-0")
        self._seed_signal_rows("npo-2")

        async def derive(db, signals):
            out = {}
            for aid in ids:
                self._clear_cache()
                out[aid] = await _refresh_agent_live_state(db, aid, status_signals=signals)
            return out

        async def both(db):
            live = await derive(db, None)
            prefetched = await derive(db, await PrefetchedStatusSignals.load(db, ids))
            return live, prefetched

        with self._frozen_clock():
            (live, prefetched), _ = _count_round_trips(both)
        self.assertEqual(set(live), set(ids))
        for aid in ids:
            self.assertIsNotNone(live[aid], "{} derived nothing at all".format(aid))
            self.assertEqual(
                live[aid], prefetched[aid],
                "{} derived differently through the prefetch than through the query".format(aid),
            )
        # ANTI-VACUITY. If a seeded agent derived identically to a bare one, the seeded rows changed
        # nothing and the equality above would hold no matter what the prefetch returned.
        self.assertNotEqual(
            live["npo-0"], live["npo-1"],
            "a seeded agent derived the same as a bare one, so the seed influences nothing and this "
            "comparison proves nothing",
        )

    def test_EVERY_COLUMN_survives_the_prefetch(self):
        """The two readers return the SAME ROW, column for column, for an agent that has one.

        THIS IS WHERE THE COLUMNS ARE PINNED, and it exists because comparing derived output does not
        pin them. Measured: of the four columns these two queries carry, exactly ONE (`in_turn`)
        reaches the derived cache for a resident agent -- `working_at` is ignored for an agent that is
        offline, `awaiting_input` needs a `blocked` event, and an empty `last_event_at` reads as falsy
        so the staleness clamp does not fire and it behaves just like a fresh one. Mutations that
        replaced each of the other three with a constant in the prefetched SELECT all SURVIVED the
        derive-level comparison.

        Contriving a fixture that makes every column reach the status chip would be a fixture built to
        satisfy a test. Comparing the rows asks the question directly, and catches a dropped column
        whether or not today's derive path happens to consume it.
        """
        from service.api_core.status_signal_prefetch import (
            LIVE_STATUS_SIGNALS,
            PrefetchedStatusSignals,
        )
        self._register(2)
        self._seed_signal_rows("npo-0")

        async def compare(db):
            pre = await PrefetchedStatusSignals.load(db, ["npo-0", "npo-1"])
            out = []
            for reader in ("status_state", "console_signal"):
                live = await getattr(LIVE_STATUS_SIGNALS, reader)(db, "npo-0")
                fetched = await getattr(pre, reader)(db, "npo-0")
                out.append((reader, live, fetched))
            return out

        rows, _ = _count_round_trips(compare)
        for reader, live, fetched in rows:
            self.assertIsNotNone(live, "the seed produced no {} row, so this proves nothing".format(reader))
            self.assertIsNotNone(fetched, "the prefetch lost the {} row entirely".format(reader))
            # Keyed off the LIVE row's own columns: the batched query carries an extra `agent_id` it
            # needs for keying, and demanding identical key sets would fail on that alone.
            for column in live.keys():
                self.assertEqual(
                    live[column], fetched[column],
                    "{}.{} differs: query gave {!r}, prefetch gave {!r}".format(
                        reader, column, live[column], fetched[column]),
                )
            self.assertGreaterEqual(len(live.keys()), 2, "the row carries fewer columns than expected")

    def test_a_STALE_last_event_at_still_clamps_through_the_prefetch(self):
        """The clamp is the one place `last_event_at` changes the answer, so it gets its own agent.

        A latched `in_turn` past the staleness backstop must come back False. Seeded fresh it stays
        True; seeded stale it clears -- and a prefetch that lost the column would read as falsy, skip
        the clamp, and report an agent as mid-turn forever.
        """
        from service.api_core.status_refresh import _refresh_agent_live_state
        from service.api_core.status_signal_prefetch import PrefetchedStatusSignals

        self._register(2)
        self._seed_signal_rows("npo-0")
        self._seed_signal_rows("npo-1", last_event_at="2020-01-01T00:00:00Z")

        async def derive(db):
            out = {}
            pre = await PrefetchedStatusSignals.load(db, ["npo-0", "npo-1"])
            for aid in ("npo-0", "npo-1"):
                for label, signals in (("live", None), ("pre", pre)):
                    self._clear_cache()
                    out[(aid, label)] = await _refresh_agent_live_state(db, aid, status_signals=signals)
            return out

        with self._frozen_clock():
            out, _ = _count_round_trips(derive)
        fresh = out[("npo-0", "live")]["status_inputs"]
        stale = out[("npo-1", "live")]["status_inputs"]
        # THE CONTROL: if the clamp did not fire, both agents look identical and the comparison below
        # holds for any prefetch at all.
        self.assertTrue(fresh.in_turn, "a freshly-stamped turn did not survive")
        self.assertFalse(stale.in_turn, "the staleness clamp never fired, so this proves nothing")
        for aid in ("npo-0", "npo-1"):
            self.assertEqual(out[(aid, "live")], out[(aid, "pre")], aid)

    def test_an_agent_with_no_signal_rows_reads_the_same_either_way(self):
        """The case the prefetch could most easily get wrong. These tables are written only once an
        agent has reported, so a freshly-registered agent has NO row -- the common case, not an edge.
        The single-row query returns None for it, and the prefetch must agree rather than falling back
        to a query, which would restore the N+1 for exactly the cheapest agents."""
        from service.api_core.status_signal_prefetch import (
            LIVE_STATUS_SIGNALS,
            PrefetchedStatusSignals,
        )
        self._register(2)

        async def compare(db):
            pre = await PrefetchedStatusSignals.load(db, ["npo-0", "npo-1"])
            results = []
            for aid in ("npo-0", "npo-1", "no-such-agent"):
                results.append((
                    await LIVE_STATUS_SIGNALS.status_state(db, aid),
                    await pre.status_state(db, aid),
                    await LIVE_STATUS_SIGNALS.console_signal(db, aid),
                    await pre.console_signal(db, aid),
                ))
            return results

        results, _ = _count_round_trips(compare)
        for live_st, pre_st, live_cw, pre_cw in results:
            self.assertEqual(live_st, pre_st)
            self.assertEqual(live_cw, pre_cw)

    def test_no_prefetch_still_READS_rather_than_skipping(self):
        """Guards fail closed. `status_signals=None` must mean "read it", never "assume nothing"."""
        from service.api_core.status_signal_prefetch import LiveStatusSignals, status_signals_or_live
        self.assertIsInstance(status_signals_or_live(None), LiveStatusSignals)
        sentinel = object()
        self.assertIs(status_signals_or_live(sentinel), sentinel)


class TheAnalyticsBoardGetsThePrefetchToo(FastApiTestCase):
    """The OTHER caller, which the first version of this fix left out.

    `ed5caf61` batched the two signals inside `_refresh_expired_agent_live_states` -- the reconcile
    SWEEP's entry point. `GET /api/v1/analytics/pulse` builds its board by calling
    `_compute_agent_status` per agent instead, so it never reached the batch and kept paying both
    reads per agent. Fixing one of two callers is the shape that has caught me repeatedly today.

    MEASURED through the endpoint, counting `aiosqlite.Connection.execute`, on a cold cache:

        agents      6     12     24      slope
        before     50     92    176      7.0 per agent
        after      40     70    130      5.0 per agent
    """

    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    BATCHED_TABLES = ("FROM agent_status_state", "FROM agent_console_signal")

    def _register(self, n):
        for i in range(n):
            response = self.client.post("/api/v1/agents", json={
                "agentId": "pulse-{}".format(i), "role": "coder", "runtime": "claude-code",
                "sessionMode": "resident", "machineId": "linux:pulse-host",
            })
            self.assertEqual(response.status_code, 200, response.text)

    def _pulse_round_trips(self):
        from service.reconcilers import status_cache

        status_cache._LIVE_STATE_CACHE.clear()
        calls = []
        orig = aiosqlite.Connection.execute

        async def spy(self, sql, *a, **k):
            calls.append(" ".join(str(sql).split()))
            return await orig(self, sql, *a, **k)

        aiosqlite.Connection.execute = spy
        try:
            response = self.client.get("/api/v1/analytics/pulse?range=1h")
        finally:
            aiosqlite.Connection.execute = orig
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(calls, "POSITIVE CONTROL: the pulse made zero round-trips")
        return calls

    def test_the_batched_tables_are_read_once_for_the_whole_board(self):
        self._register(6)
        calls = self._pulse_round_trips()
        for table in self.BATCHED_TABLES:
            hits = [c for c in calls if table in c]
            self.assertEqual(
                len(hits), 1,
                "{} was read {} times for 6 agents on the pulse; it must be one batched read".format(
                    table, len(hits)),
            )

    def test_that_cost_does_not_grow_with_the_fleet(self):
        """The property, not a total: an unrelated query added to the pulse must not fail this."""
        counts = {}
        for n in (2, 8):
            self.setUp()
            self._register(n)
            calls = self._pulse_round_trips()
            counts[n] = sum(1 for c in calls if any(t in c for t in self.BATCHED_TABLES))
        self.assertEqual(counts[2], counts[8], "batched reads grew with the fleet: {}".format(counts))

    def test_a_single_agent_does_not_pay_for_a_batch_of_one(self):
        self._register(1)
        calls = self._pulse_round_trips()
        self.assertNotIn("IN (?)", " ".join(calls), "a batch of one still built an IN clause")

    def test_the_pulse_still_ANSWERS(self):
        """ANTI-VACUITY: an endpoint that errored would make every count above small and green.

        `onlineAgents` is ZERO here and that is correct, not a broken fixture. A freshly-registered
        resident agent has no live bridge, so `derive()` returns `offline` -- and the board excludes
        non-live statuses. My first version asserted 3 and was wrong about the product, not the other
        way round.
        """
        self._register(3)
        body = self.client.get("/api/v1/analytics/pulse?range=1h").json()
        self.assertTrue(body.get("ok"))
        for key in ("onlineAgents", "workingNow", "fleetWorkingMinutes", "agents"):
            self.assertIn(key, body, "the pulse stopped reporting {}".format(key))
        self.assertEqual(body.get("onlineAgents"), 0, "a registered-but-offline agent counted as live")
        self.assertEqual(body.get("agents"), [], "the board listed an agent it does not count")

    def test_the_board_IS_populated_when_an_agent_is_live(self):
        """The other half: if the board were empty for EVERY input, the counts above would be green
        for the wrong reason.

        A managed agent on a heartbeated environment derives `available` -- live, and countable --
        where a resident with no bridge derives `offline`. The only manual status is `stopped`, which
        is non-live, so there is no shortcut here; the environment has to exist.
        """
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:pulse-host:default", "machineId": "linux:pulse-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-p", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        for i in range(2):
            response = self.client.post("/api/v1/agents", json={
                "agentId": "live-{}".format(i), "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "linux:pulse-host", "bridgeId": "bridge-p",
                "capabilities": ["managed-run"],
            })
            self.assertEqual(response.status_code, 200, response.text)

        from service.reconcilers import status_cache
        status_cache._LIVE_STATE_CACHE.clear()
        body = self.client.get("/api/v1/analytics/pulse?range=1h").json()
        self.assertEqual(body.get("onlineAgents"), 2, "a live agent did not reach the board")
        self.assertEqual(len(body.get("agents") or []), 2)
        for agent in body["agents"]:
            self.assertNotIn(agent["status"], ("offline", "stopped", "misconfigured"))


if __name__ == "__main__":
    import unittest

    unittest.main()
