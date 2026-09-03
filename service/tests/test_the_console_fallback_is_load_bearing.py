"""Every managed console on this fleet resolves through the FALLBACK, and nothing tested it.

MEASURED 2026-09-03 on the operator's service: **4 of 4** managed agents with a live terminal have a
NULL `runtime_state.consoleTerminal` pointer -- sc-lead, sc-tester, sc-critic, sc-coder. So
`comms_console_tail`, `comms_console_input` and every read of "which console belongs to this agent"
go through `_resolve_live_console_terminal`'s fallback branch, on every call, for every agent.

THAT IS THE DESIGNED STATE, not a broken one. The pointer is written only on a register-with-console
path, and a managed console that LAZY-STARTS on a message never takes it. The fallback exists
precisely so the MCP tools agree with the dashboard, which resolves via the live terminal row. What
was missing is a test: `grep` for the function name across `service/tests` returned ZERO before this
file. The single most-used resolution path in the console surface was held up by nobody.

WHY IT IS WRITTEN NOW. sc-manager reported a console tail showing a line their worker never
received, and I answered -- from a reading of this function -- that it is agent-scoped, live-only,
and therefore cannot return another agent's terminal or a replaced one. That answer was right, and
certifying my own claim by reading the code is the wrong way round. These are the assertions that
answer should have rested on.

WHAT EACH ONE IS FOR, since three of them look like the same test:
  * the pointer wins when it is live -- otherwise a deliberate binding is ignored;
  * the fallback answers when the pointer is NULL -- the whole fleet, today;
  * the fallback answers when the pointer names an ENDED terminal -- the wedge shape from last
    night, where a reconciler cleared or invalidated a binding under a running worker.
"""

from __future__ import annotations

import asyncio
import json

from service.api_core.agent_terminal_ops import _resolve_live_console_terminal
from service.clock import now as _now
from service.db import get_db
from service.tests._base import FastApiTestCase

ENV = "windows:console-fallback:default"
AGENT = "sc-coder"
OTHER = "sc-lead"


class TheConsoleFallbackIsLoadBearingTests(FastApiTestCase):
    DB_NAME = "aify-test-console-fallback.db"

    def setUp(self):
        super().setUp()
        beat = self._client.post("/api/v1/environments/heartbeat", json={
            "id": ENV, "kind": "windows", "os": "windows", "machineId": "win32:cf",
            "bridgeId": "bridge-1", "cwdRoots": ["C:/work"],
            "runtimes": [{"runtime": "claude-code", "available": True}], "metadata": {},
        })
        self.assertEqual(beat.status_code, 200, beat.text)
        for agent in (AGENT, OTHER):
            registered = self._client.post("/api/v1/agents", json={
                "agentId": agent, "role": "coder", "runtime": "claude-code",
                "sessionMode": "managed", "machineId": "win32:cf", "bridgeId": "bridge-1",
            })
            self.assertEqual(registered.status_code, 200, registered.text)

    def _terminal(self, terminal_id: str, *, agent: str = AGENT, status: str = "attached",
                  updated: str | None = None) -> None:
        async def go():
            db = await get_db()
            try:
                stamp = updated or _now()
                # THE SESSION ROW FIRST. `terminal_sessions.session_id` is a NOT NULL FOREIGN KEY
                # on `agent_sessions(id)`, so a terminal cannot be seeded without one -- as this
                # file discovered, eight tests at a time.
                #
                # AND `spawn_spec_id` / `spawn_request_id` MUST BE NULL, not omitted. They default to
                # the empty STRING, which is not NULL, so their foreign keys are enforced against a
                # `spawn_specs` row called "" that cannot exist -- and the failure surfaces as a bare
                # "FOREIGN KEY constraint failed" from inside aiosqlite's worker thread, naming
                # neither the table nor the column. That is the second time this schema has cost a
                # whole file's worth of red for the same reason.
                await db.execute(
                    "INSERT OR REPLACE INTO agent_sessions (id, agent_id, environment_id, runtime, "
                    "status, owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"sess-{terminal_id}", agent, ENV, "claude-code", "running", "console",
                     terminal_id, status, stamp, stamp, None, None),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO terminal_sessions (id, agent_id, session_id, "
                    "environment_id, runtime, bridge_id, command, workspace, status, output, error, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (terminal_id, agent, f"sess-{terminal_id}", ENV, "claude-code", "bridge-1",
                     "claude-aify", "C:/work", status, "", "", stamp, stamp),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _point_at(self, terminal_id: str | None, *, agent: str = AGENT) -> None:
        async def go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT runtime_state FROM agents WHERE id = ?", (agent,))).fetchone()
                state = json.loads((row["runtime_state"] if row else "") or "{}")
                if terminal_id is None:
                    state.pop("consoleTerminal", None)
                else:
                    state["consoleTerminal"] = {"terminalId": terminal_id, "bridgeId": "bridge-1"}
                await db.execute("UPDATE agents SET runtime_state = ? WHERE id = ?",
                                 (json.dumps(state), agent))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _resolve(self, agent: str = AGENT):
        async def go():
            db = await get_db()
            try:
                return await _resolve_live_console_terminal(db, agent)
            finally:
                await db.close()

        return asyncio.run(go())

    def test_THE_FALLBACK_ANSWERS_WHEN_THE_POINTER_IS_NULL(self):
        """THE STATE OF THE WHOLE FLEET, measured 4 of 4. If this stops working, every managed
        console goes dark at once and no pointer exists to fall back TO."""
        self._terminal("term-live")
        self._point_at(None)
        found = self._resolve()
        self.assertIsNotNone(found, "an agent with a live terminal and no pointer resolved nothing")
        self.assertEqual(found["id"], "term-live")

    def test_a_live_pointer_wins_over_the_fallback(self):
        """CONTROL. Without it the fallback could be answering everything, and a deliberate binding
        to an older-but-chosen terminal would be silently ignored."""
        self._terminal("term-old", updated="2026-09-01T00:00:00Z")
        self._terminal("term-new")
        self._point_at("term-old")
        self.assertEqual(self._resolve()["id"], "term-old",
                         "the pointer was ignored in favour of the newest terminal")

    def test_a_pointer_at_an_ENDED_terminal_falls_through_to_the_live_one(self):
        """LAST NIGHT'S WEDGE SHAPE. A reconciler marked a live worker's terminal `failed` and
        cleared its binding; an agent whose pointer names a dead row must still find its console, or
        the operator sees "no live console" for a worker that is plainly running."""
        self._terminal("term-dead", status="failed", updated="2026-09-01T00:00:00Z")
        self._terminal("term-live")
        self._point_at("term-dead")
        self.assertEqual(self._resolve()["id"], "term-live")

    def test_IT_CANNOT_RETURN_ANOTHER_AGENTS_TERMINAL(self):
        """THE CLAIM I MADE TO sc-manager, asserted rather than read. They saw a console line their
        worker never received and asked whether the tail could have been attached to something else.
        The answer was no, because both the pointer lookup and the fallback carry `agent_id = ?` --
        and that answer should rest on this, not on my reading of the query."""
        self._terminal("term-someone-else", agent=OTHER)
        self._point_at("term-someone-else")          # even POINTED at it
        self.assertIsNone(self._resolve(), "an agent resolved a console belonging to another agent")

    def test_IT_CANNOT_RETURN_AN_ENDED_TERMINAL_AT_ALL(self):
        """The other half of that answer: a replaced terminal cannot be served as live. An agent with
        nothing but dead terminals must resolve None, so the caller says "no live console" instead of
        rendering history as the present."""
        self._terminal("term-a", status="failed")
        self._terminal("term-b", status="stopped")
        self._point_at(None)
        self.assertIsNone(self._resolve(), "a dead terminal was served as a live console")

    def test_the_newest_live_terminal_wins_when_several_are_live(self):
        """A restart can leave two live rows for a moment. The fallback orders by `updated_at DESC`,
        which is the liveness clock the host refreshes -- so "newest" means "most recently reported
        by its host", not "created last"."""
        self._terminal("term-stale", updated="2026-09-01T00:00:00Z")
        self._terminal("term-fresh")
        self._point_at(None)
        self.assertEqual(self._resolve()["id"], "term-fresh")

    def test_a_virtual_terminal_is_not_a_PTY_console(self):
        """`vterm_` rows are synthesised frame buffers for pi/hermes, not a PTY. The fallback excludes
        them by name, and a caller that got one would try to type into something with no process."""
        self._terminal("vterm_synth")
        self._point_at(None)
        self.assertIsNone(self._resolve(), "a synthesised virtual terminal was served as a PTY console")

    def test_an_unknown_agent_resolves_nothing(self):
        """Fail closed. A missing agent row must not fall through to somebody's terminal."""
        self._terminal("term-live")
        self.assertIsNone(self._resolve("nobody-registered-this"))
