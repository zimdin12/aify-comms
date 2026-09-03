"""A terminal whose host is still reporting it must not be released over a bridge id.

THE DEFECT, measured on the operator's fleet 2026-09-03, and it is the root of a night of lost work.

`_active_terminal_for_agent` released a terminal whose `bridge_id` differed from the environment
row's current one. That row holds whichever claimer last won arbitration, and aify-env mints a fresh
bridge id every time its plugin starts -- so after a restart EVERY existing terminal mismatched and
was released as a "stale Console owner". A release means the next delivery COLD-STARTS a terminal
instead of reaching the live worker, and the new one begins from nothing.

Measured: sc-lead's terminal carried `ab14b870` while the environment row said `d908664d`; its
console sat at `starting` for four minutes with a live process behind it, and every message to that
lane started a replacement. Four working sessions were lost in ten minutes, including a lead
mid-design-note.

THE ID WAS A PROXY FOR "IS THE OWNER STILL THERE", AND A BAD ONE: it changes for reasons that have
nothing to do with the terminal. The direct answer now exists. Since 2026-09-03 a host reports every
terminal it is still running on every control pass, which bumps `updated_at` -- so a freshly-touched
row is an owner saying "this is mine and it is alive", from the only tier that can know.

THE FUNCTION ALREADY HELD THE OTHER HALF of this rule and stopped one step short. Its own comment
says never release on output-AGE, because a live worker can be quiet for minutes between turns. The
inverse was missing: never release something that is provably NOT quiet.
"""

from __future__ import annotations

import asyncio
import json

from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.clock import now as _now
from service.db import get_db
from service.tests._base import FastApiTestCase


class AReportingHostOwnsItsTerminalTests(FastApiTestCase):
    DB_NAME = "aify-test-terminal-ownership.db"
    ENV = "windows:ownership-host:default"
    AGENT = "sc-lead"
    SESSION = "sess-ownership"
    TERMINAL = "term-ownership"

    def setUp(self):
        super().setUp()
        self._seed(env_bridge="bridge-current", terminal_bridge="bridge-current")

    def _seed(self, *, env_bridge: str, terminal_bridge: str, terminal_updated: str | None = None):
        heartbeat = self._client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "kind": "windows", "os": "windows", "machineId": "win32:ownership",
            "bridgeId": env_bridge, "cwdRoots": ["C:/work"],
            "runtimes": [{"runtime": "claude-code", "available": True}],
            "metadata": {"bridgeStartedAt": "2026-09-03T05:00:00Z"},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        registered = self._client.post("/api/v1/agents", json={
            "agentId": self.AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "win32:ownership", "bridgeId": env_bridge,
        })
        self.assertEqual(registered.status_code, 200, registered.text)

        stamp = terminal_updated or _now()

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR REPLACE INTO agent_sessions (id, agent_id, environment_id, runtime, "
                    "status, owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.SESSION, self.AGENT, self.ENV, "claude-code", "running", "console",
                     self.TERMINAL, "attached", stamp, stamp, None, None),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO terminal_sessions (id, agent_id, session_id, "
                    "environment_id, runtime, bridge_id, command, workspace, status, output, error, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.TERMINAL, self.AGENT, self.SESSION, self.ENV, "claude-code",
                     terminal_bridge, "claude-aify --aify-agent sc-lead", "C:/work", "attached",
                     "", "", stamp, stamp),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _resolve(self):
        async def go():
            db = await get_db()
            try:
                return await _active_terminal_for_agent(db, self.AGENT)
            finally:
                await db.close()

        return asyncio.run(go())

    def test_a_matching_bridge_id_keeps_the_terminal(self):
        """CONTROL. The ordinary case must still work, or every assertion below is about a function
        that never returns anything."""
        self.assertIsNotNone(self._resolve(), "the ordinary case stopped resolving")

    def test_A_MISMATCHED_ID_KEEPS_THE_TERMINAL_WHEN_THE_HOST_IS_REPORTING_IT(self):
        """THE DEFECT. aify-env mints a fresh bridge id on every plugin start, so after a restart
        every terminal mismatched -- and a release cold-starts a replacement over a live worker."""
        self._seed(env_bridge="bridge-new", terminal_bridge="bridge-old")
        self.assertIsNotNone(
            self._resolve(),
            "a terminal its host is actively reporting was released over a bridge id, so the next "
            "delivery will cold-start a replacement on top of a live worker",
        )

    def test_a_mismatched_id_DOES_release_when_nothing_has_reported_it(self):
        """THE OTHER DIRECTION, and the reason the check exists at all: a terminal belonging to a
        bridge that is genuinely gone must be released, or a dead console blocks every dispatch."""
        self._seed(
            env_bridge="bridge-new", terminal_bridge="bridge-old",
            terminal_updated="2026-09-02T00:00:00Z",
        )
        self.assertIsNone(self._resolve(), "a terminal nobody has reported for a day was kept")

    def test_THE_TWO_CASES_ARE_DISTINGUISHABLE(self):
        """CONTROL for the pair. If freshness were ignored, both would answer the same and this fix
        would be doing nothing -- which is exactly the shape of the bug it replaces, where an id
        stood in for a fact it could not observe."""
        self._seed(env_bridge="bridge-new", terminal_bridge="bridge-old")
        fresh = self._resolve()
        self._seed(
            env_bridge="bridge-new", terminal_bridge="bridge-old",
            terminal_updated="2026-09-02T00:00:00Z",
        )
        stale = self._resolve()
        self.assertIsNotNone(fresh)
        self.assertIsNone(stale)

    def test_an_offline_environment_still_releases_regardless_of_freshness(self):
        """The environment check above this one is untouched: a host that is not there cannot be
        running anything, and that judgement does not depend on a timestamp it wrote earlier."""
        self._seed(env_bridge="bridge-current", terminal_bridge="bridge-current")

        async def age_the_environment():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE environments SET last_seen = ?, status = 'offline' WHERE id = ?",
                    ("2026-09-01T00:00:00Z", self.ENV),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(age_the_environment())
        self.assertIsNone(self._resolve(), "a terminal on a dead environment was kept")
