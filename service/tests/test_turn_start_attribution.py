"""`/turn-start` — and the one clause in it that decides whether a dispatch keeps its identity.

`service/routers/agents/turn_boundaries.py` is named by no test file. Both handlers are reached
(through literal URLs), and the pieces with a history have their own tests: the no-op fast path, the
superseded-bridge guard, the engine push. What had none is the clause the module was written around.

THE SITUATION IT EXISTS FOR. A managed dispatch is in flight — a bridge claimed a run, set
`turn_busy`, and recorded which run and which bridge. The operator then types directly into the same
agent's resident CLI, and the harness's UserPromptSubmit hook posts `/turn-start`. Two truths about
one agent arrive from two places, and the naive upsert overwrites the first with the second.

    turn_bridge_id = CASE
        WHEN turn_busy = 1 AND COALESCE(turn_run_id, '') != ''
             AND COALESCE(turn_bridge_id, '') NOT IN ('', 'user-prompt-submit')
        THEN turn_bridge_id
        ELSE 'user-prompt-submit'
    END

All three conditions are load-bearing and each fails differently, so each is tested alone. And note
what the statement does NOT set: `turn_run_id` is absent from the UPDATE list entirely, so the run
linkage survives a user prompt whether or not the CASE fires. That is the other half of the same
guarantee and it is invisible from the CASE alone.

WHAT BREAKS WHEN IT IS WRONG is not the turn — the agent is `working` either way. It is the
ATTRIBUTION: the dashboard shows "working on <subject>" by resolving `turn_run_id` and
`turn_bridge_id` back to a dispatch, and a clobbered attribution turns a tracked run into an
anonymous busy agent while the run itself is still open.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "tb-worker"
OTHER = "tb-other"


class TurnBoundaryTestCase(FastApiTestCase):
    DB_NAME = "aify-turn-boundaries-test.db"

    def setUp(self):
        super().setUp()
        for agent_id in (AGENT, OTHER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"},
            )
            self.assertEqual(response.status_code, 200, response.text)

    # ── db helpers ───────────────────────────────────────────────────────────────────────────

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _turn_state(self, agent_id: str = AGENT) -> dict:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM agent_turn_state WHERE agent_id = ?", (agent_id,))
                row = await cursor.fetchone()
                return dict(row) if row else {}

        return asyncio.run(run())

    def _seed_turn_state(self, *, agent_id: str = AGENT, busy: int, run_id: str, bridge_id: str,
                         updated_at: str = "2020-01-01T00:00:00Z") -> None:
        self._write(
            "INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id,"
            " turn_runtime, turn_updated_at) VALUES (?,?,?,?,?,?)",
            (agent_id, busy, run_id, bridge_id, "claude-code", updated_at),
        )

    def _turn_start(self, agent_id: str = AGENT):
        return self.client.post(f"/api/v1/agents/{agent_id}/turn-start")

    def _turn_end(self, agent_id: str = AGENT, **body):
        return self.client.post(f"/api/v1/agents/{agent_id}/turn-end", json=body or {})


class TurnStartBasicsTests(TurnBoundaryTestCase):
    def test_a_first_turn_start_creates_the_row_as_busy(self):
        self.assertEqual(self._turn_state(), {}, "precondition: no turn row yet")
        response = self._turn_start()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "agentId": AGENT})
        self.assertEqual(int(self._turn_state()["turn_busy"]), 1)

    def test_the_INSERT_path_attributes_the_turn_to_the_prompt_hook(self):
        """With no prior row there is nothing to preserve, so the new row names its source. That
        name is what later tells a reader this turn came from someone typing, not from a dispatch."""
        self._turn_start()
        self.assertEqual(self._turn_state()["turn_bridge_id"], "user-prompt-submit")

    def test_turn_start_is_IDEMPOTENT_and_keeps_refreshing_the_stamp(self):
        """The server-side staleness window resets on every call — that is the mechanism by which a
        long assistant turn keeps reading as `working` without a heartbeat saying so."""
        self._seed_turn_state(busy=1, run_id="", bridge_id="user-prompt-submit")
        self._turn_start()
        first = self._turn_state()["turn_updated_at"]
        self.assertNotEqual(first, "2020-01-01T00:00:00Z", "the stamp was not refreshed")
        self.assertEqual(int(self._turn_state()["turn_busy"]), 1)

    def test_turn_start_refreshes_LAST_SEEN(self):
        """A turn boundary is not a heartbeat, but it is evidence the process is alive — an agent
        mid-turn must not age into `offline` because nothing else beat while it worked."""
        self._write("UPDATE agents SET last_seen = ? WHERE id = ?",
                    ("2020-01-01T00:00:00Z", AGENT))
        self._turn_start()

        async def read():
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute("SELECT last_seen FROM agents WHERE id = ?", (AGENT,))
                return (await cursor.fetchone())[0]

        self.assertNotEqual(asyncio.run(read()), "2020-01-01T00:00:00Z")


class TurnStartAttributionTests(TurnBoundaryTestCase):
    """The CASE. Each condition alone, because each fails differently."""

    def test_an_IN_FLIGHT_DISPATCH_keeps_its_bridge(self):
        """All three conditions true: busy, a run, and a bridge that is not the prompt hook. The
        operator typing into the CLI must not rename the dispatch's owner."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="bridge-A")
        self._turn_start()
        self.assertEqual(self._turn_state()["turn_bridge_id"], "bridge-A")

    def test_the_RUN_LINKAGE_survives_a_user_prompt(self):
        """`turn_run_id` is not in the UPDATE list at all, so it is preserved by omission rather
        than by the CASE — invisible from the clause, and the half that carries the subject the
        dashboard displays."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="bridge-A")
        self._turn_start()
        self.assertEqual(self._turn_state()["turn_run_id"], "run-1")

    def test_an_IDLE_agent_is_attributed_to_the_prompt_hook(self):
        """First condition false. Nothing is in flight, so there is nothing to protect and the
        prompt is the true source of this turn."""
        self._seed_turn_state(busy=0, run_id="run-old", bridge_id="bridge-A")
        self._turn_start()
        self.assertEqual(self._turn_state()["turn_bridge_id"], "user-prompt-submit")

    def test_a_busy_turn_with_NO_RUN_is_attributed_to_the_prompt_hook(self):
        """Second condition false. Busy without a run is a previous prompt-driven turn; keeping its
        bridge would preserve an attribution that points at nothing."""
        self._seed_turn_state(busy=1, run_id="", bridge_id="bridge-A")
        self._turn_start()
        self.assertEqual(self._turn_state()["turn_bridge_id"], "user-prompt-submit")

    def test_a_previous_PROMPT_turn_is_not_treated_as_a_dispatch(self):
        """Third condition, first half — and NO INPUT CAN DISTINGUISH IT, which is worth writing
        down rather than leaving as apparent coverage.

        When `turn_bridge_id` is already `user-prompt-submit`, the THEN branch preserves it and the
        ELSE branch writes the same string. Deleting `'user-prompt-submit'` from the `NOT IN` list
        therefore changes nothing for any input — a mutation that removes it survives this suite,
        and it survives because the term is behaviourally REDUNDANT, not because the test is weak.

        It is left in the SQL: it states the intent (the hook is not a dispatch owner) at the only
        place a reader will look, and removing a term from a live upsert to save nothing is not a
        change worth making. The assertion stays because the OUTCOME still matters."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="user-prompt-submit")
        self._turn_start()
        self.assertEqual(self._turn_state()["turn_bridge_id"], "user-prompt-submit")

    def test_an_EMPTY_bridge_id_is_not_preserved(self):
        """Third condition, second half. An empty string is not an owner, and carrying it forward
        would leave a busy turn attributed to nobody with no way to tell it from a fresh row."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="")
        self._turn_start()
        self.assertEqual(self._turn_state()["turn_bridge_id"], "user-prompt-submit")


class TurnEndSupersededGuardTests(TurnBoundaryTestCase):
    """The guard is scoped to the POSTING bridge AND to this agent."""

    def _seed_bridge(self, bridge_id: str, agent_id: str, superseded_by: str = "") -> None:
        self._write(
            "INSERT INTO bridge_instances (id, agent_id, machine_id, runtime, session_mode,"
            " registered_at, last_seen, superseded_by) VALUES (?,?,?,?,?,?,?,?)",
            (bridge_id, agent_id, "linux:test", "claude-code", "resident",
             "2026-08-17T00:00:00Z", "2026-08-17T00:00:00Z", superseded_by),
        )

    def test_a_LIVE_bridges_turn_end_still_clears(self):
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="b-live")
        self._seed_bridge("b-live", AGENT)
        response = self._turn_end(bridgeId="b-live")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("ignored", response.json())
        self.assertEqual(int(self._turn_state()["turn_busy"]), 0)

    def test_an_UNKNOWN_bridge_id_is_not_evidence_of_supersession(self):
        """No row means no proof the poster was replaced. Refusing here would let an unregistered
        detector be silently ignored — a turn stuck busy with a 200 that says it succeeded."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="b-live")
        response = self._turn_end(bridgeId="b-unknown")
        self.assertNotIn("ignored", response.json())
        self.assertEqual(int(self._turn_state()["turn_busy"]), 0)

    def test_ANOTHER_AGENTS_superseded_bridge_does_not_block_this_one(self):
        """The lookup carries `AND agent_id = ?`. Without it, a bridge id that was superseded on
        some other agent would veto this agent's turn-end — and bridge ids are not guaranteed
        unique across agents in this schema."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="b-shared")
        self._seed_bridge("b-shared", OTHER, superseded_by="b-newer")
        response = self._turn_end(bridgeId="b-shared")
        self.assertNotIn("ignored", response.json())
        self.assertEqual(int(self._turn_state()["turn_busy"]), 0)

    def test_a_BLANK_superseded_by_is_not_supersession(self):
        """The column exists on every row and is empty for a live bridge. Reading its PRESENCE
        rather than its content would ignore every turn-end that named its bridge."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="b-live")
        self._seed_bridge("b-live", AGENT, superseded_by="   ")
        response = self._turn_end(bridgeId="b-live")
        self.assertNotIn("ignored", response.json())
        self.assertEqual(int(self._turn_state()["turn_busy"]), 0)

    def test_a_SUPERSEDED_bridges_turn_end_is_ignored_and_says_so(self):
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="b-new")
        self._seed_bridge("b-old", AGENT, superseded_by="b-new")
        response = self._turn_end(bridgeId="b-old")
        self.assertEqual(response.json().get("ignored"), "superseded_bridge")
        self.assertEqual(int(self._turn_state()["turn_busy"]), 1,
                         "a stale detector cleared a live turn")

    def test_a_BLANK_posted_bridge_id_does_not_block_the_clear(self):
        """The harness Stop hook posts no body, which is what makes it authoritative.

        The `if _posting_bridge:` around the lookup is a QUERY-AVOIDANCE guard, not a behaviour
        guard: with it removed the lookup runs against `''`, matches no row, and the clear proceeds
        exactly as it does here. That mutation survives this suite and the note is the accurate
        report — what is asserted below is the outcome, which is what the hook path depends on."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="b-old")
        self._seed_bridge("b-old", AGENT, superseded_by="b-new")
        response = self._turn_end(bridgeId="   ")
        self.assertNotIn("ignored", response.json())
        self.assertEqual(int(self._turn_state()["turn_busy"]), 0)

    def test_a_body_that_is_not_JSON_is_treated_as_no_body(self):
        """Hooks post from shell scripts. A malformed body must degrade to the authoritative
        no-bridge path rather than 500 — a turn-end that errors is a turn that never ends."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="b-old")
        response = self.client.post(
            f"/api/v1/agents/{AGENT}/turn-end", content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(int(self._turn_state()["turn_busy"]), 0)


class TurnEndBroadcastTests(TurnBoundaryTestCase):
    """The no-op path exists to remove work, and the broadcast is most of that work."""

    def test_a_REAL_clear_broadcasts(self):
        """The hop an operator waits on — "send the queued work now that it is ready". Without the
        push it waits out the poll and looks stuck."""
        self._seed_turn_state(busy=1, run_id="run-1", bridge_id="b-live")
        before = len(self.ws.broadcasts)
        self._turn_end()
        self.assertGreater(len(self.ws.broadcasts), before, "a real clear pushed nothing")

    def test_the_NO_OP_path_broadcasts_NOTHING(self):
        """A KEEP-CLEARED re-assert fires every ~45s for the whole idle life of every agent. The
        write is skipped and so is the push, or the fast path would still cost a fan-out to every
        connected dashboard on every beat."""
        self._seed_turn_state(busy=0, run_id="", bridge_id="")
        before = len(self.ws.broadcasts)
        response = self._turn_end()
        self.assertEqual(response.json().get("noop"), "already-cleared")
        self.assertEqual(len(self.ws.broadcasts), before, "the no-op path pushed a broadcast")


class TurnBoundaryRefusalTests(TurnBoundaryTestCase):
    def test_turn_start_on_an_UNKNOWN_agent_is_404(self):
        response = self.client.post("/api/v1/agents/nobody/turn-start")
        self.assertEqual(response.status_code, 404, response.text)

    def test_turn_end_on_an_UNKNOWN_agent_is_404(self):
        response = self.client.post("/api/v1/agents/nobody/turn-end", json={})
        self.assertEqual(response.status_code, 404, response.text)

    def test_an_unknown_agent_leaves_NO_turn_row_behind(self):
        """The refusal comes before the upsert. Creating turn state for an agent that does not
        exist would leave a row nothing ever clears — and the tombstone check above it is only
        meaningful if the write is genuinely downstream of both."""
        self.client.post("/api/v1/agents/nobody/turn-start")
        self.assertEqual(self._turn_state("nobody"), {})


if __name__ == "__main__":
    unittest.main()
