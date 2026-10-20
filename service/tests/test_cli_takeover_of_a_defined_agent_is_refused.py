"""A CLI takeover of a defined agent is refused before it writes anything (B2, path 2).

`cli_takeover` stopped the managed worker in comms alone: the agent read `stopped` with `launch_mode='none'`, and
aify-env, which owns a defined agent's lifecycle, never heard of it. Handing it to aify-env as a stop is no better:
aify-env's stop fact publishes `stopped` ahead of any process (its agent-state derivation), so the operator's CLI
would run under a stopped row. A defined agent's takeover is a flip of its definition to resident, which comms
follows; the refusal says so.

THE FENCE IS THE TAKEOVER HANDLER'S OWN, AND IT COMES BEFORE THE SESSION READ. Whose session it is, the definition
check and an undefined agent's legacy writes all share one `BEGIN IMMEDIATE`, the lock both a rename and a definition
push take. Read outside it, a rename could move the session to another agent and a push define that agent between the
read and the check, and the check would judge the old owner while the writes landed on the new one's session (review
of b34a8118). The race tests drive a rival into each gap; `write_fence_race.py` says how the order is read back from
the database.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import service.routers.session_control as session_control
from service.api_core.agent_rename_writes import _rewrite_agent_references_for_rename
from service.routers.api_v2 import router  # noqa: F401 -- the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define as define_on_host
from service.tests.stored_unwoken import assert_stored_unwoken
from service.tests.write_fence_race import Rival, define, dump, racing

ENV = {"env": "win32:host-a:default", "machine": "win32:host-a", "bridge": "bridge-a"}
AGENT = "takeover-worker"
RENAMED = "takeover-renamed"
SESSION = "sess-takeover"
NOW = "2026-10-20T00:00:00Z"


async def rename_then_define(db) -> None:
    """The product's own rename write, then a definition of the new id: two operators' commits, as one rival."""
    await _rewrite_agent_references_for_rename(db, AGENT, RENAMED, NOW, SimpleNamespace(requestedBy="dashboard"))
    await define(db, RENAMED, ENV["machine"])


async def taken_over(db) -> bool:
    row = await (await db.execute("SELECT status FROM agent_sessions WHERE id = ?", (SESSION,))).fetchone()
    return row["status"] == "cli-takeover"


class CliTakeoverOfADefinedAgentIsRefused(FastApiTestCase):
    DB_NAME = "aify-b2-takeover.db"

    def setUp(self):
        super().setUp()
        beat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV["env"], "machineId": ENV["machine"], "os": "win32", "kind": "win32", "bridgeId": ENV["bridge"],
            "cwdRoots": ["/work"], "runtimes": [{"runtime": "claude-code", "available": True}], "metadata": {}})
        self.assertEqual(beat.status_code, 200, beat.text)

    def _execute(self, sql: str, params=()) -> None:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(sql, params)
            db.commit()
        finally:
            db.close()

    def _one(self, sql: str, params=()):
        db = sqlite3.connect(self._db_path)
        try:
            return db.execute(sql, params).fetchone()
        finally:
            db.close()

    def _undefined_agent(self) -> None:
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
            "launchMode": "managed", "machineId": ENV["machine"]})
        self.assertEqual(response.status_code, 200, response.text)

    def _defined_agent(self) -> None:
        define_on_host(self, AGENT, environment_id=ENV["env"], machine_id=ENV["machine"], bridge_id=ENV["bridge"],
                       workspace="/work")

    def _live_work(self) -> None:
        """Everything a takeover touches: the session, a queued spawn, a running run and a live terminal."""
        self._execute("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, workspace, status, session_handle,"
                      " started_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                      (SESSION, AGENT, ENV["env"], "claude-code", "/work", "running", "handle-1", NOW, NOW))
        self._execute("INSERT INTO spawn_requests (id, spawn_spec_id, environment_id, agent_id, runtime, status, created_at,"
                      " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      ("spawn-queued", "spec-1", ENV["env"], AGENT, "claude-code", "queued", NOW, NOW))
        self._execute("INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at) VALUES (?,?,?,?,?)",
                      ("run-live", "dashboard", AGENT, "running", NOW))
        self._execute("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, bridge_id, runtime, status,"
                      " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      ("term-live", SESSION, AGENT, ENV["env"], ENV["bridge"], "claude-code", "running", NOW, NOW))

    def _takeover(self):
        return self.client.post(f"/api/v1/sessions/{SESSION}/control",
                                json={"action": "cli_takeover", "from_agent": "dashboard"})

    def _assert_refused_unchanged(self, owner: str) -> None:
        before, broadcasts = dump(self._db_path), len(self.ws.broadcasts)
        refused = self._takeover()
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn('" is defined by its host, so its session cannot be taken over here', refused.json()["detail"])
        self.assertIn(owner, refused.json()["detail"])
        self.assertEqual(dump(self._db_path), before, "a refused takeover wrote to the database")
        self.assertEqual(len(self.ws.broadcasts), broadcasts, "a refused takeover broadcast")

    def test_a_defined_agents_takeover_is_refused_and_writes_nothing(self):
        self._defined_agent()
        self._live_work()
        self._assert_refused_unchanged(AGENT)

    def test_a_session_renamed_to_a_defined_agent_before_the_takeover_is_refused(self):
        """The definition-first order of the race below, committed before the request: the session is judged by
        the agent that owns it now."""
        import asyncio

        import aiosqlite

        self._undefined_agent()
        self._live_work()

        async def commit_rival():
            db = await aiosqlite.connect(self._db_path)
            try:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                await rename_then_define(db)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(commit_rival())
        self.assertEqual(self._one("SELECT agent_id FROM agent_sessions WHERE id = ?", (SESSION,)), (RENAMED,))
        self._assert_refused_unchanged(RENAMED)

    def test_CONTROL_an_undefined_agents_takeover_keeps_every_legacy_effect(self):
        """The legacy takeover end to end, including what follows it: a send is stored without a run while the
        agent is paused, and the CLI's own registration takes the session over with its new handle."""
        self._undefined_agent()
        self._live_work()
        taken = self._takeover()
        self.assertEqual(taken.status_code, 200, taken.text)
        self.assertEqual(self._one("SELECT status FROM agent_sessions WHERE id = ?", (SESSION,)), ("cli-takeover",))
        self.assertEqual(self._one("SELECT status, launch_mode FROM agents WHERE id = ?", (AGENT,)), ("stopped", "none"))
        self.assertIn("Launch the agent's CLI under its *-aify launcher",
                      self._one("SELECT status_note FROM agents WHERE id = ?", (AGENT,))[0])
        self.assertEqual(self._one("SELECT status FROM spawn_requests WHERE id = 'spawn-queued'"), ("cancelled",))
        self.assertEqual(self._one("SELECT COUNT(*) FROM terminal_controls WHERE terminal_id = 'term-live' AND action = 'stop'"), (1,))
        self.assertTrue(taken.json()["interruptControlId"], "the running run was interrupted")
        self.assertIn("session_control_requested", [args[0] for args, _ in self.ws.broadcasts])

        sent = self.client.post("/api/v1/messages/send", json={
            "from_agent": "dashboard", "to": AGENT, "type": "request", "subject": "while paused", "body": "hello",
            "trigger": True})
        self.assertEqual(sent.status_code, 200, sent.text)
        assert_stored_unwoken(self, sent.json(), AGENT)

        registered = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "sessionHandle": "thread-from-cli", "launchMode": "detached", "machineId": ENV["machine"]})
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(self._one("SELECT session_handle FROM agent_sessions WHERE id = ?", (SESSION,)),
                         ("thread-from-cli",))

    def _race(self, read: str, change) -> tuple:
        """Run the takeover with a rival committing `change` the moment the handler's `read` returns."""
        self._undefined_agent()
        self._live_work()
        rival = Rival(self._db_path, change, committed_before=taken_over)
        restore = racing(session_control, read, rival)
        try:
            taken = self._takeover()
        finally:
            restore()
        rival.finish()
        self.assertTrue(rival.committed, "the rival never committed")
        return rival, taken

    def _assert_legal_order(self, rival, taken) -> None:
        if rival.handler_committed_first:
            self.assertEqual(taken.status_code, 200, taken.text)
        else:
            self.assertEqual(taken.status_code, 409, "the rival committed first, then the takeover wrote a defined agent's session")
            self.assertEqual(self._one("SELECT status FROM agent_sessions WHERE id = ?", (SESSION,)), ("running",))
            self.assertEqual(self._one("SELECT COUNT(*) FROM terminal_controls"), (0,))
        self.assertTrue(rival.blocked, "the rival was not blocked after the read: the takeover held no write fence")

    def test_a_rename_and_definition_racing_the_session_read_land_after_the_takeover(self):
        """The earlier gap: right after the handler learns whose session this is, the session is renamed to a new id
        and that id defined. Under the fence both wait for the takeover's commit."""
        rival, taken = self._race("_session_row", rename_then_define)
        self._assert_legal_order(rival, taken)

    def test_a_definition_racing_the_check_lands_after_the_takeover_commits(self):
        """The later gap: right after the definition check, the agent itself is defined."""
        rival, taken = self._race("defined_on", lambda db: define(db, AGENT, ENV["machine"]))
        self._assert_legal_order(rival, taken)
