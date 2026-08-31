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
    turn_started_at TEXT NOT NULL DEFAULT '', ready INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE agent_status_state (
    agent_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'offline',
    in_turn INTEGER DEFAULT 0, awaiting_input INTEGER DEFAULT 0,
    turn_run_id TEXT DEFAULT '', last_event TEXT DEFAULT '', last_event_at TEXT,
    turn_started_at TEXT NOT NULL DEFAULT '', updated_at TEXT
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
            # NAMED COLUMNS, not positional: a bare VALUES list breaks the moment a column is added in the
            # middle, and it silently wrote the wrong column before it broke.
            "INSERT INTO agent_status_state (agent_id, in_turn, awaiting_input, turn_run_id, "
            "last_event, last_event_at, turn_started_at, updated_at) "
            "VALUES (?,1,0,?,'turn_start',?,?,?)", (agent, run, BEFORE, BEFORE, BEFORE))

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
    """The fixture above hand-copies real tables, so they can go stale. Catch that here.

    IT DID GO STALE TWICE, and the second time this gate was already watching. On 2026-08-30
    `turn_started_at` was added to `agent_turn_state` and the copy was not updated; five tests died
    with "no column named turn_started_at". This class was written to make that loud. On 2026-08-31
    the SAME column was added to `agent_status_state` -- the OTHER table the same fixture copies --
    and eight tests died the same way, because this gate NAMED `agent_turn_state` and checked
    nothing else.

    A gate that hardcodes one of the things it guards is a gate with a hole the shape of everything
    else. The tables are DERIVED from the fixture now, so a third copy is covered the moment it is
    added rather than the moment it breaks.
    """

    @staticmethod
    def _columns(block: str) -> set:
        """Column names out of a CREATE TABLE body, whichever way it is laid out.

        The real schema puts one column per line; the fixture packs several per line. Splitting on
        both newlines and commas reads either, so the two need not agree on formatting to be
        compared on content.
        """
        import re
        found = set()
        body = block.split("(", 1)[1]
        for line in body.splitlines():
            line = line.split("--", 1)[0]
            for chunk in line.split(","):
                name = chunk.strip().split(" ")[0].strip()
                if not name or not re.fullmatch(r"[a-z_]+", name):
                    continue
                if name.upper() in ("FOREIGN", "PRIMARY", "UNIQUE", "KEY", "REFERENCES"):
                    continue
                found.add(name)
        return found

    @staticmethod
    def _migration_columns(table: str) -> set:
        """Columns an EXISTING database gains for `table` through db.py's migration dicts."""
        import re as _re

        from service import db as db_module

        wanted = set()
        for name in dir(db_module):
            if not name.endswith("_MIGRATIONS"):
                continue
            value = getattr(db_module, name)
            if not isinstance(value, dict):
                continue
            for column, statement in value.items():
                if _re.search(rf"ALTER TABLE\s+{_re.escape(table)}\b", str(statement)):
                    wanted.add(column)
        return wanted

    @staticmethod
    def _table_block(sql: str, marker: str) -> str:
        block = sql[sql.index(marker):]
        return block[: block.index(");")]

    def _fixture_tables(self) -> list:
        """Every table the fixture declares, read off the fixture itself."""
        import re
        return re.findall(r"CREATE TABLE (\w+)", SCHEMA)

    def test_the_fixture_declares_every_column_the_real_table_has(self):
        from pathlib import Path

        real_sql = Path(__file__).resolve().parents[1].joinpath("schema.py").read_text(encoding="utf-8")
        tables = self._fixture_tables()
        self.assertGreaterEqual(len(tables), 2,
                                f"the fixture parser found {tables}; it must find every copied table")

        for table in tables:
            with self.subTest(table=table):
                real = self._columns(self._table_block(real_sql, f"CREATE TABLE IF NOT EXISTS {table}"))
                # PLUS WHAT THE MIGRATIONS ADD. `schema.py` is what a FRESH database gets; an
                # existing one gets the same columns through a `*_MIGRATIONS` dict, and a column
                # added only there would be invisible to this comparison. That is the remaining
                # one-way edge: the gate would call a fixture complete while production had a
                # column it did not. Derived from db.py rather than listed, so a new migration
                # joins this check without anyone remembering to.
                migrated = self._migration_columns(table)
                real |= migrated
                fixture = self._columns(self._table_block(SCHEMA, f"CREATE TABLE {table}"))
                self.assertTrue(real, f"no columns parsed out of the REAL {table}; the parser is broken")
                self.assertEqual(
                    real - fixture, set(),
                    f"the inline {table} fixture is missing {sorted(real - fixture)}. Add them to "
                    f"SCHEMA above -- the tests in this file build their database from it, so a "
                    f"missing column fails as 'no such column' and says nothing about the change "
                    f"that caused it.",
                )
                # AND THE OTHER DIRECTION. A one-way check passes a fixture carrying a column the
                # real table does not have -- so a test could assert against a column production
                # cannot produce, and be green for ever. That is the same class as the drift above,
                # pointing the other way, and it costs one assertion to close.
                self.assertEqual(
                    fixture - real, set(),
                    f"the inline {table} fixture declares {sorted(fixture - real)}, which the real "
                    f"table does not have. A test written against it would assert on a column that "
                    f"cannot exist in production.",
                )

    def test_the_migration_scan_actually_finds_columns(self):
        """ANTI-VACUITY for the union above. A scan returning an empty set widens nothing and the
        comparison keeps passing -- the gate would look strengthened while checking exactly what it
        checked before. Both fixture tables gained a column through a migration, so both must be
        found, and a table that has none must come back empty rather than matching everything."""
        self.assertIn("turn_started_at", self._migration_columns("agent_turn_state"))
        self.assertIn("turn_started_at", self._migration_columns("agent_status_state"))
        self.assertEqual(self._migration_columns("no_such_table_anywhere"), set())

    def test_the_parser_can_actually_tell_a_missing_column(self):
        """ANTI-VACUITY. A parser returning empty sets would pass the comparison above for every
        table, which is exactly how this gate reported green while a copy it did not look at drifted."""
        one_per_line = "CREATE TABLE x (\n    a TEXT,\n    b TEXT\n)"
        real = self._columns(one_per_line)
        self.assertEqual(real, {"a", "b"})
        # And the packed layout the fixture uses, so the parser is proven on BOTH shapes it reads.
        self.assertEqual(self._columns("CREATE TABLE x (a TEXT, b TEXT)"), {"a", "b"})
        self.assertEqual(real - self._columns("CREATE TABLE x (a TEXT)"), {"b"})
