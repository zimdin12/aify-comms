"""A spawned agent's brief must be a message it can read and reply to.

FOUND BY AN END-TO-END PROBE, 2026-08-18, and it is the argument for running one: the whole suite was
green while this was broken, and a single spawned agent reported it in its first reply.

`_hand_settled_spawn_to_dispatch` created the initial-message run with `message_id=None`, so:

  * the agent's `comms_inbox` was EMPTY — while the dispatch text it received said "Full details are
    in the inbox. Read them there if you need the complete context", an instruction that could not be
    followed;
  * the dispatch event carried `message_id=""`, so the agent had no id for `inReplyTo` and could not
    thread its reply to the brief it was answering. The probe said so itself: *"no inReplyTo
    available: the dispatch event carried message_id='' and comms_inbox(filter=all) is empty, so
    run_… may not auto-close."*

The brief IS a message — one agent asking another to do something — so it now gets a row like any
other rather than a special case every reader downstream has to know about.
"""

from __future__ import annotations

import asyncio
import unittest

from service.db import get_db
from service.tests._base import FastApiTestCase

ENVIRONMENT_ID = "linux:test-host:default"
BRIDGE_ID = "bridge-spawn-brief"


class TheSpawnBriefIsARealMessage(FastApiTestCase):
    DB_NAME = "aify-spawn-brief-test.db"

    def setUp(self):
        super().setUp()
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": ENVIRONMENT_ID, "label": "test", "machineId": "linux:test-host", "os": "linux",
                "kind": "linux", "bridgeId": BRIDGE_ID, "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": "codex", "available": True}], "status": "online",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.client.post("/api/v1/agents", json={"agentId": "spawner", "role": "manager"})

    def _spawn(self, *, initial_message: str, subject: str = "First task") -> str:
        response = self.client.post("/api/v1/spawn-requests", json={
            "environmentId": ENVIRONMENT_ID, "agentId": "fresh-worker", "runtime": "codex",
            "workspace": "/workspace/proj", "createdBy": "spawner",
            "initialMessage": initial_message, "subject": subject,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["spawnRequest"]["id"]

    def _settle(self, spawn_id: str):
        # The bridge's own report that the worker is live — the transition that hands the waiting
        # brief to dispatch.
        for status in ("claimed", "starting", "running"):
            r = self.client.patch(f"/api/v1/spawn-requests/{spawn_id}",
                                  json={"status": status, "bridgeId": BRIDGE_ID})
            self.assertEqual(r.status_code, 200, r.text)

    def _query(self, sql, params=()):
        async def run():
            db = await get_db()
            try:
                return await (await db.execute(sql, params)).fetchall()
            finally:
                await db.close()
        return asyncio.run(run())

    def test_the_brief_reaches_the_agents_INBOX(self):
        """The instruction in the dispatch text says the full details are in the inbox. Before this
        fix there was nothing there at all."""
        self._settle(self._spawn(initial_message="Please audit the parser and report back."))
        rows = self._query(
            "SELECT id, from_agent, to_agent, subject, body, type FROM messages WHERE to_agent = ?",
            ("fresh-worker",),
        )
        self.assertEqual(len(rows), 1, "the spawn brief left no message for the agent to read")
        self.assertEqual(rows[0]["from_agent"], "spawner", "the brief lost its author")
        self.assertEqual(rows[0]["body"], "Please audit the parser and report back.")
        self.assertEqual(rows[0]["subject"], "First task")

    def test_the_RUN_points_at_that_message_so_a_reply_can_thread(self):
        """`message_id` is what the agent receives as the id to put in `inReplyTo`. Empty, the reply
        cannot be threaded to the brief and the contract has to be closed some other way."""
        self._settle(self._spawn(initial_message="do the thing"))
        runs = self._query(
            "SELECT id, message_id, require_reply FROM dispatch_runs WHERE target_agent = ?",
            ("fresh-worker",),
        )
        self.assertEqual(len(runs), 1, runs)
        self.assertTrue(str(runs[0]["message_id"] or "").strip(),
                        "the run has no source message, so the agent has nothing to reply to")

        messages = self._query("SELECT id FROM messages WHERE to_agent = ?", ("fresh-worker",))
        self.assertEqual(runs[0]["message_id"], messages[0]["id"],
                         "the run points at a different message than the one that was stored")

    def test_a_spawn_with_NO_brief_creates_no_message(self):
        """ANTI-VACUITY, and the case that must not regress: a spawn without an initial message must
        not manufacture an empty one for the agent to puzzle over."""
        self._settle(self._spawn(initial_message=""))
        self.assertEqual(
            self._query("SELECT id FROM messages WHERE to_agent = ?", ("fresh-worker",)), [],
            "a spawn with no brief still put a message in the agent's inbox",
        )

    def test_settling_TWICE_does_not_duplicate_the_brief(self):
        """The handoff is guarded on the row having only just reached `running`. If that guard ever
        stops holding, the agent gets the same brief twice — and now a duplicate inbox row too."""
        spawn_id = self._spawn(initial_message="only once please")
        self._settle(spawn_id)
        again = self.client.patch(f"/api/v1/spawn-requests/{spawn_id}",
                                  json={"status": "running", "bridgeId": BRIDGE_ID})
        self.assertEqual(again.status_code, 200, again.text)
        rows = self._query("SELECT id FROM messages WHERE to_agent = ?", ("fresh-worker",))
        self.assertEqual(len(rows), 1, "the brief was delivered twice")


if __name__ == "__main__":
    unittest.main()
