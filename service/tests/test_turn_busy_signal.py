"""Who is allowed to say a turn ENDED, tested by calling the signal directly.

`_apply_turn_busy_signal` was inline in `agent_heartbeat` until v0.5.4, so exercising it meant
driving `POST /agents/{id}/heartbeat`. It is now a leaf and these tests run it against a real sqlite
database.

THE ASYMMETRY IS THE DESIGN and is the reason this is worth testing directly:

    turnBusy MISSING  -> liveness only. Old bridges that never send the field keep working.
    turnBusy TRUE     -> the LATEST bridge wins, unconditionally.
    turnBusy FALSE    -> ONLY the owning bridge, and only for the owning run, may clear.

The false case is guarded because a stale `false` — from a bridge that has been superseded, or for a
run that already finished — would otherwise wipe a NEWER active turn. The agent would then report
idle while it was still working, which is a wrong status rather than an error: nothing raises, and
the dashboard simply lies until the next real event.

`in_turn` is cleared INSIDE that same guard rather than beside it, so it can never clear where the
`turn_busy = 0` write would not. Each of those is asserted separately below, because the two live in
different tables and a future edit could easily move one out of the guard.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.turn_busy_signal import _apply_turn_busy_signal

SCHEMA = """
CREATE TABLE agent_turn_state (
    agent_id TEXT PRIMARY KEY, turn_busy INTEGER DEFAULT 0, turn_run_id TEXT DEFAULT '',
    turn_bridge_id TEXT DEFAULT '', turn_runtime TEXT DEFAULT '', turn_updated_at TEXT,
    turn_started_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE agent_status_state (
    agent_id TEXT PRIMARY KEY, in_turn INTEGER DEFAULT 0, awaiting_input INTEGER DEFAULT 0,
    turn_run_id TEXT DEFAULT '', last_event TEXT DEFAULT '', last_event_at TEXT, updated_at TEXT
);
"""
#: `agent_id TEXT PRIMARY KEY` on agent_turn_state is copied from the real schema deliberately: the
#: busy write is an ON CONFLICT(agent_id) upsert, which without the key would silently insert a
#: second row per beat and these tests would assert against a table production cannot produce.

BEFORE = "2026-08-15T10:00:00Z"
NOW = "2026-08-15T12:00:00Z"


class TurnBusySignalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _open_turn(self, *, agent="a1", bridge="b1", run="run-1"):
        # Columns NAMED, not positional. A bare `VALUES (...)` binds by ordinal, so adding a column
        # to the table breaks every one of these inserts with an arity error that names neither the
        # column nor the change -- which is exactly what `turn_started_at` did on 2026-08-30.
        await self.db.execute(
            "INSERT INTO agent_turn_state "
            "(agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at) "
            "VALUES (?,1,?,?,'hermes',?)", (agent, run, bridge, BEFORE))
        await self.db.execute(
            "INSERT INTO agent_status_state VALUES (?,1,0,?,'turn_start',?,?)", (agent, run, BEFORE, BEFORE))

    async def _signal(self, body, *, agent="a1", bridge="b1", turn_flip=False):
        return await _apply_turn_busy_signal(self.db, agent, bridge, body, NOW, turn_flip)

    async def _turn(self, agent="a1"):
        return await (await self.db.execute(
            "SELECT * FROM agent_turn_state WHERE agent_id = ?", (agent,))).fetchone()

    async def _status(self, agent="a1"):
        return await (await self.db.execute(
            "SELECT * FROM agent_status_state WHERE agent_id = ?", (agent,))).fetchone()

    # ---- the field is absent -------------------------------------------------

    async def test_a_beat_without_turnBusy_touches_nothing(self):
        """Old-bridge safety. A bridge that never learned the field must not clear turns."""
        await self._open_turn()
        flip = await self._signal({"liveness": True})
        self.assertFalse(flip)
        self.assertEqual(1, (await self._turn())["turn_busy"])
        self.assertEqual(BEFORE, (await self._turn())["turn_updated_at"])

    # ---- turnBusy = true -----------------------------------------------------

    async def test_turnBusy_true_opens_a_turn_and_reports_the_flip(self):
        flip = await self._signal({"turnBusy": True, "turnRunId": "run-9", "turnRuntime": "codex"})
        row = await self._turn()
        self.assertTrue(flip, "a to-working transition must be reported so dashboards are pushed")
        self.assertEqual(1, row["turn_busy"])
        self.assertEqual("run-9", row["turn_run_id"])
        self.assertEqual("b1", row["turn_bridge_id"])
        self.assertEqual("codex", row["turn_runtime"])

    async def test_a_second_true_on_an_already_busy_agent_is_NOT_a_flip(self):
        """Only a real change is worth broadcasting; a 3s beat stream would otherwise push forever."""
        await self._open_turn()
        flip = await self._signal({"turnBusy": True, "turnRunId": "run-1"})
        self.assertFalse(flip)
        self.assertEqual(1, (await self._turn())["turn_busy"])

    async def test_the_LATEST_bridge_wins_a_true_unconditionally(self):
        """No ownership guard on the busy path: whoever reports work is doing it."""
        await self._open_turn(bridge="b-old", run="run-old")
        await self._signal({"turnBusy": True, "turnRunId": "run-new"}, bridge="b-new")
        row = await self._turn()
        self.assertEqual("b-new", row["turn_bridge_id"])
        self.assertEqual("run-new", row["turn_run_id"])

    async def test_a_true_records_turn_start_in_the_status_engine_too(self):
        """The heartbeat is the DOMINANT turn signal for managed runtimes.

        It used to write only agent_turn_state, so the proof-based engine showed the agent
        online/idle in the middle of a turn.
        """
        await self._signal({"turnBusy": True, "turnRunId": "run-9"})
        row = await self._status()
        self.assertEqual(1, row["in_turn"])
        self.assertEqual("run-9", row["turn_run_id"])

    # ---- turnBusy = false: the guarded path ---------------------------------

    async def test_the_owning_bridge_may_clear_its_own_run(self):
        await self._open_turn(bridge="b1", run="run-1")
        flip = await self._signal({"turnBusy": False, "turnRunId": "run-1"}, bridge="b1")
        self.assertTrue(flip, "a to-ready transition must be reported")
        self.assertEqual(0, (await self._turn())["turn_busy"])
        self.assertEqual(0, (await self._status())["in_turn"])

    async def test_a_DIFFERENT_bridge_cannot_clear_a_live_turn(self):
        """The defect the guard exists for: a stale false wiping a newer active turn.

        Nothing raises. The agent reports idle while it is still working, and the dashboard lies
        until the next real event.
        """
        await self._open_turn(bridge="b-live", run="run-1")
        flip = await self._signal({"turnBusy": False, "turnRunId": "run-1"}, bridge="b-superseded")
        self.assertFalse(flip)
        self.assertEqual(1, (await self._turn())["turn_busy"])
        self.assertEqual(1, (await self._status())["in_turn"], "in_turn must not clear either")

    async def test_the_owning_bridge_cannot_clear_a_DIFFERENT_run(self):
        await self._open_turn(bridge="b1", run="run-live")
        flip = await self._signal({"turnBusy": False, "turnRunId": "run-finished"}, bridge="b1")
        self.assertFalse(flip)
        self.assertEqual(1, (await self._turn())["turn_busy"])

    async def test_a_stored_turn_with_NO_run_id_may_be_cleared_by_its_bridge(self):
        """`not stored_run or stored_run == turn_run_id` — an unattributed turn is not unclearable.

        A turn opened without a run id (a resident wake, say) would otherwise be permanently stuck
        busy, since no incoming run id could ever match an empty stored one.
        """
        await self._open_turn(bridge="b1", run="")
        flip = await self._signal({"turnBusy": False, "turnRunId": "run-anything"}, bridge="b1")
        self.assertTrue(flip)
        self.assertEqual(0, (await self._turn())["turn_busy"])

    async def test_clearing_an_agent_with_no_turn_row_is_a_no_op(self):
        flip = await self._signal({"turnBusy": False, "turnRunId": "run-1"})
        self.assertFalse(flip)
        self.assertIsNone(await self._turn())

    async def test_clearing_an_ALREADY_idle_agent_is_not_a_flip(self):
        """`turn_flip = _prev_busy` — the write happens, but there was no transition to announce."""
        await self.db.execute(
            "INSERT INTO agent_turn_state "
            "(agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at) "
            "VALUES ('a1',0,'run-1','b1','hermes',?)", (BEFORE,))
        flip = await self._signal({"turnBusy": False, "turnRunId": "run-1"})
        self.assertFalse(flip)

    async def test_turn_flip_is_OVERWRITTEN_not_accumulated(self):
        """A parameter that looks like an accumulator and is not. Pinned so nobody assumes it is.

        `turn_flip` is a parameter only because the extraction needed it to be one — after the split
        it would otherwise be a helper local the caller still reads. The body ASSIGNS it
        (`turn_flip = not _prev_busy`), so an incoming True is discarded on the busy path. That is
        harmless today for one reason and one only: the caller sets `turn_flip = False` on the line
        immediately above the call. If a future edit ever passes a meaningful value in, this stops
        being harmless, so the behaviour is asserted here rather than assumed from the signature.
        """
        await self._open_turn()
        flip = await self._signal({"turnBusy": True, "turnRunId": "run-1"}, turn_flip=True)
        self.assertFalse(flip, "the helper overwrites turn_flip; it does not OR it in")

    def test_the_caller_seeds_turn_flip_False_immediately_before_the_call(self):
        """The precondition that makes the overwrite above safe, checked against the real caller."""
        import ast
        from pathlib import Path

        liveness = (Path(__file__).resolve().parent.parent / "routers" / "agents" / "liveness.py")
        source = liveness.read_text(encoding="utf-8")
        handler = next(
            n for n in ast.parse(source).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "agent_heartbeat"
        )
        seeds = [
            node.lineno for node in ast.walk(handler)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "turn_flip" for t in node.targets)
            and isinstance(node.value, ast.Constant) and node.value.value is False
        ]
        calls = [
            node.lineno for node in ast.walk(handler)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_apply_turn_busy_signal"
        ]
        self.assertEqual(1, len(seeds), "turn_flip must be seeded exactly once, with False")
        self.assertEqual(1, len(calls))
        self.assertLess(seeds[0], calls[0], "the seed must precede the call")


if __name__ == "__main__":
    unittest.main()


class TheInlineSchemaMustNotDriftFromTheRealOneTests(unittest.TestCase):
    """The fixture above hand-copies `agent_turn_state`, so it can go stale. Catch that here.

    IT DID GO STALE, on 2026-08-30. `turn_started_at` was added to the real table and this copy was
    not updated; five tests in this file died with "no column named turn_started_at" — a failure
    that says nothing about the change and everything about the duplicate. The copy is deliberate
    (see the note beside it: the real ON CONFLICT upsert needs the primary key), so the answer is
    not to delete it but to make the drift loud and immediate.
    """

    def test_the_fixture_declares_every_column_the_real_table_has(self):
        import re
        from pathlib import Path

        real_sql = Path(__file__).resolve().parents[1].joinpath("schema.py").read_text(encoding="utf-8")
        block = real_sql[real_sql.index("CREATE TABLE IF NOT EXISTS agent_turn_state"):]
        block = block[: block.index(");")]
        # Column names are the first token of each non-comment, non-constraint line.
        def columns(sql: str) -> set[str]:
            found = set()
            for line in sql.splitlines()[1:]:
                line = line.strip().rstrip(",")
                if not line or line.startswith("--") or line.upper().startswith(("FOREIGN KEY", "PRIMARY KEY", "UNIQUE")):
                    continue
                name = re.split(r"\s", line)[0]
                if re.fullmatch(r"[a-z_]+", name):
                    found.add(name)
            return found

        real = columns(block)
        fixture_block = SCHEMA[SCHEMA.index("CREATE TABLE agent_turn_state"):]
        fixture_block = fixture_block[: fixture_block.index(");")]
        # The fixture packs several columns per line; split on commas too.
        fixture = set()
        for chunk in fixture_block.split("(", 1)[1].replace("\n", " ").split(","):
            name = chunk.strip().split(" ")[0]
            if re.fullmatch(r"[a-z_]+", name):
                fixture.add(name)

        # POSITIVE CONTROL: both readers found a real table, so a missing column is a real
        # difference and not one of them silently parsing nothing.
        self.assertIn("turn_busy", real, "the real-schema reader found nothing usable")
        self.assertIn("turn_busy", fixture, "the fixture reader found nothing usable")
        self.assertEqual(
            set(), real - fixture,
            "the inline fixture is missing columns the real agent_turn_state has. Add them here, "
            "or the tests in this file exercise a table production cannot produce.",
        )
