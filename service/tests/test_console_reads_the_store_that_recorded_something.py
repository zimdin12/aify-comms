"""A dead terminal's console tail reads whichever store actually recorded the output.

THE FAILURE, from the live fleet on 2026-08-26. sc-architect died mid-turn. The operator asked why,
and `comms_console_tail` answered "(nothing was recorded)". Measured against the running service:

    store                                        sc-claude    sc-architect
    terminal_sessions.output                        63,423              18
    terminal_events (terminal_output rows)          10,913          14,773

Those eighteen characters are the terminal's own exit marker. Non-empty, so the `.strip()` gate in
`get_agent_console` passed them, the tail rendered a line that says only THAT it ended, and 14,773
characters describing what the agent was doing went unread in the events of that same terminal.

This is the endpoint whose own docstring exists because of exactly this shape -- "the cause of a
failed managed hermes launch sat in `terminal_sessions.output` for 2.5 hours ... The bytes were never
missing; nothing would serve them." It happened again, one store over.

WHY THE COLUMN IS STILL READ FIRST. It is fuller for most terminals and it is already being selected,
so the events are fetched only when it says nothing. The common path costs no extra query.

WHAT `says_what_it_was_doing` DRAWS THAT `.strip()` COULD NOT. Non-empty is not informative. A
recording holding only `[terminal exited]` describes no work. `[terminal failed] <error>` is NOT
treated the same way: `terminal-manager.mjs` writes the reason after that marker, so the line carries
content and must survive -- which is the case the last test below pins.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.terminal_diagnostics import (
    is_self_report_only,
    richest_recording,
    says_what_it_was_doing,
)
from service.tests._base import FastApiTestCase

#: The exact bytes sc-architect's `output` column held when it died.
EXIT_MARKER_ONLY = "\n[terminal exited]\n"

#: Stand-in for what its events held: a TUI's last frame, ending on real work.
REAL_WORK = (
    "\x1b[2J\x1b[H Search Files(\"build_terrain_heat_source\")\n"
    "Tuning the 0.02 test timestep merely to recover GREEN\n"
    "Claiming thermal coherence while specific_heat remains a placeholder\n"
)


class RecordingSelectionTests(FastApiTestCase):
    """The pure decision, before any HTTP."""

    def test_the_exit_marker_alone_says_nothing(self) -> None:
        self.assertTrue(is_self_report_only("[terminal exited]"))
        self.assertFalse(
            says_what_it_was_doing(EXIT_MARKER_ONLY),
            "eighteen characters of exit marker were read as an account of the death",
        )

    def test_a_strip_gate_would_have_accepted_it(self) -> None:
        """The control that names the old behaviour. If this were falsy the bug could not have
        happened and this whole file would be guarding a case that cannot occur."""
        self.assertTrue(
            EXIT_MARKER_ONLY.strip(),
            "the marker is falsy under .strip(), so the described failure is impossible",
        )

    def test_the_events_answer_when_the_column_does_not(self) -> None:
        text, source = richest_recording(EXIT_MARKER_ONLY, REAL_WORK)
        self.assertEqual(source, "events")
        self.assertIn("build_terrain_heat_source", text)

    def test_the_column_still_wins_when_it_says_something(self) -> None:
        """No regression for the 63,423-character case: the fuller store must not be displaced, and
        the events must not even be consulted."""
        text, source = richest_recording(REAL_WORK, "something else entirely")
        self.assertEqual(source, "output")
        self.assertIn("build_terrain_heat_source", text)

    def test_neither_store_saying_anything_is_its_own_answer(self) -> None:
        self.assertEqual(richest_recording(EXIT_MARKER_ONLY, "[terminal exited]"), ("", ""))

    def test_a_failure_marker_carries_its_reason_and_survives(self) -> None:
        """`[terminal failed] <error>` is the one marker that says why. Filtering it as
        self-reporting would delete the single most useful line a dying terminal writes."""
        failed = "\n[terminal failed] ENOENT: no such file or directory, spawn claude-aify\n"
        self.assertFalse(is_self_report_only("[terminal failed] ENOENT: spawn claude-aify"))
        self.assertTrue(says_what_it_was_doing(failed))
        self.assertEqual(richest_recording(failed, REAL_WORK)[1], "output")


class ConsoleTailServesTheEventsTests(FastApiTestCase):
    """End to end through the endpoint, against a terminal shaped like sc-architect's."""

    AGENT = "console-store-agent"
    TERMINAL = "term_console_store_probe"

    def setUp(self) -> None:
        super().setUp()
        # The session below carries an environment_id FOREIGN KEY, so the environment has to exist
        # before anything can hang off it.
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:test-host:default", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-a", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "hermes", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self.client.post("/api/v1/agents", json={
            "agentId": self.AGENT, "role": "coder", "runtime": "hermes",
            "sessionMode": "managed", "machineId": "linux:test-host",
        })
        self.assertEqual(registered.status_code, 200, registered.text)

    def _seed_terminal(self, *, output: str, events: list[str]) -> None:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                # The terminal's session_id is NOT NULL and a FOREIGN KEY -- it is the column that
                # makes a terminal cascade with its session, so a terminal cannot exist without one.
                # Registering an agent does not create a session (a launch does), so the fixture
                # creates the session the terminal will hang on.
                #
                # `spawn_spec_id` and `spawn_request_id` are named and set to NULL DELIBERATELY.
                # Both are FOREIGN KEYs whose column DEFAULT is the empty string, and no spawn_specs
                # or spawn_requests row has id '' -- so omitting them takes the default and fails the
                # foreign key. Every production writer names them, which is why the trap is invisible
                # until a new writer omits them and reads "FOREIGN KEY constraint failed" with no
                # column attached. See test_a_session_insert_must_name_its_spawn_columns below.
                session_id = f"sess-{self.AGENT}"
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "started_at, last_seen, spawn_spec_id, spawn_request_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (session_id, self.AGENT, "linux:test-host:default", "hermes", "ended",
                     "2026-08-26T02:00:00Z", "2026-08-26T02:07:43Z", None, None),
                )
                # `session_id` is NOT NULL: it is the column that makes a terminal cascade with its
                # session, so a terminal cannot exist without one.
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, status, output, error, created_at, updated_at, "
                    "stopped_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.TERMINAL, self.AGENT, session_id, "linux:test-host:default",
                     "hermes", "bridge-a", "hermes-aify --aify-agent x", "stopped", output, "",
                     "2026-08-26T02:07:00Z", "2026-08-26T02:07:43Z", "2026-08-26T02:07:43Z"),
                )
                for body in events:
                    await db.execute(
                        "INSERT INTO terminal_events (terminal_id, event_type, body, created_at) "
                        "VALUES (?,?,?,?)",
                        (self.TERMINAL, "terminal_output", body, "2026-08-26T02:07:42Z"),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def test_the_fixture_reproduces_the_shape_that_failed(self) -> None:
        """Positive control. If the seeded terminal did not have a nearly-empty column AND rich
        events, every assertion below would pass for the wrong reason."""
        self._seed_terminal(output=EXIT_MARKER_ONLY, events=[REAL_WORK])
        response = self.client.get(f"/api/v1/agents/{self.AGENT}/console")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body.get("historical"), "the endpoint did not take the dead-terminal path")

    def test_it_serves_what_the_events_recorded(self) -> None:
        self._seed_terminal(output=EXIT_MARKER_ONLY, events=[REAL_WORK])
        body = self.client.get(f"/api/v1/agents/{self.AGENT}/console").json()
        self.assertEqual(body.get("recordedFrom"), "events", body.get("message"))
        self.assertIn(
            "build_terrain_heat_source", body.get("output") or "",
            "the console tail served the exit marker while the events held the agent's actual work",
        )

    def test_it_still_serves_the_column_when_the_column_has_the_output(self) -> None:
        """The no-regression case: 63,423 characters in the column must keep being what is served,
        and `recordedFrom` must say so."""
        self._seed_terminal(output=REAL_WORK, events=["ignored replacement text"])
        body = self.client.get(f"/api/v1/agents/{self.AGENT}/console").json()
        self.assertEqual(body.get("recordedFrom"), "output")
        self.assertIn("build_terrain_heat_source", body.get("output") or "")
        self.assertNotIn("ignored replacement text", body.get("output") or "")

    def test_a_terminal_that_recorded_nothing_says_it_DIED_rather_than_that_it_is_idle(self) -> None:
        """The other half of the same defect. With both stores silent the handler used to fall
        through to "it lazy-starts on a message" -- which describes an agent that is idle by design,
        not one whose terminal ended. A silent death is itself the finding and must read as one."""
        self._seed_terminal(output=EXIT_MARKER_ONLY, events=[])
        body = self.client.get(f"/api/v1/agents/{self.AGENT}/console").json()
        self.assertTrue(body.get("historical"), "a dead terminal was reported as never having run")
        self.assertNotIn("lazy-starts", body.get("message") or "")
        self.assertIn("recorded NOTHING", body.get("message") or "")
        self.assertEqual(body.get("status"), "stopped")


class ForeignKeyColumnsThatDefaultToEmptyStringTests(FastApiTestCase):
    """A FOREIGN KEY column whose DEFAULT is '' can never satisfy its own constraint.

    FOUND while writing the fixture above, which is the only reason it is recorded here: the insert
    failed with a bare `FOREIGN KEY constraint failed` naming no column, and the cause was two
    columns the statement never mentioned. `agent_sessions.spawn_spec_id` and `spawn_request_id` are
    nullable FKs into `spawn_specs` and `spawn_requests`, and both DEFAULT to the empty string. NULL
    is exempt from a foreign key; '' is not, and no row has id ''. So an insert that OMITS them takes
    a default that is guaranteed to violate the constraint.

    Nothing in the product is broken by this: all three production writers name both columns. That is
    exactly what makes it a trap -- it is invisible until a new writer omits them, and the error
    SQLite gives back does not say which column it was.

    The list is DERIVED from the schema rather than typed, so a new column of the same shape is named
    by this test instead of ambushing whoever adds the next insert.
    """

    def _offending_columns(self) -> list[str]:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                fks = {
                    str(row["from"])
                    for row in await (await db.execute("PRAGMA foreign_key_list(agent_sessions)")).fetchall()
                }
                columns = await (await db.execute("PRAGMA table_info(agent_sessions)")).fetchall()
                return sorted(
                    str(c["name"]) for c in columns
                    if str(c["name"]) in fks
                    and not int(c["notnull"] or 0)
                    and str(c["dflt_value"] or "").strip("\"'") == ""
                    and c["dflt_value"] is not None
                )
            finally:
                await db.close()

        return asyncio.run(go())

    def test_the_schema_scan_finds_the_two_known_columns(self) -> None:
        """Positive control. An empty result would make the assertions below pass while proving the
        schema has no such columns -- which was true of no version of this table."""
        self.assertEqual(
            self._offending_columns(), ["spawn_request_id", "spawn_spec_id"],
            "the set of nullable FK columns defaulting to '' changed. If a column was FIXED, remove "
            "it here and from the fixture above. If one was ADDED, it carries the same trap.",
        )

    def test_omitting_them_fails_the_foreign_key(self) -> None:
        """The trap, demonstrated rather than described. If this ever stops raising, the default has
        been fixed and the explicit NULLs in the fixture above are no longer load-bearing."""
        import asyncio
        import sqlite3

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "started_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                    ("sess-trap", "trap-agent", "linux:test-host:default", "hermes", "ended",
                     "2026-08-26T02:00:00Z", "2026-08-26T02:07:43Z"),
                )
                await db.commit()
            finally:
                await db.close()

        self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:test-host:default", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-a", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "hermes", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.client.post("/api/v1/agents", json={
            "agentId": "trap-agent", "role": "coder", "runtime": "hermes",
            "sessionMode": "managed", "machineId": "linux:test-host",
        })
        with self.assertRaises(sqlite3.IntegrityError):
            asyncio.run(go())


if __name__ == "__main__":
    import unittest

    unittest.main()
