"""A Claude channel's successor owns its agent, and nothing the old channel sends late takes that back (0.9.11 slice 3).

The channel registers with a per-process OWNER id and claims with the stable per-agent DELIVERY id
(`mcp/stdio/resident-duties.mjs`). Review asked for the property, not the labels: after a successor registers, the
old process's late owner beat, late claim and exit must neither restore the old owner nor change the successor's.
Driven through the real routes, in the order a quick relaunch produces them.
"""

import asyncio
import json
import sqlite3

import httpx

from service.api_core import same_mode_bridge_gate

from service.tests._base import FastApiTestCase

AGENT = "relaunched"
HANDLE = "11111111-2222-4333-8444-555555555555"
OLD, NEW = "owner-old", "owner-new"
STARTED = {OLD: "2026-10-27T05:00:00.000Z", NEW: "2026-10-27T05:00:05.000Z", "owner-held": "2026-10-27T05:00:06.000Z",
           "owner-successor": "2026-10-27T05:00:07.000Z", "owner-newest": "2026-10-27T05:00:09.000Z"}
DELIVERY = f"channel-linux:test-host-{AGENT}"


class ARetiredChannelOwnerStaysRetired(FastApiTestCase):
    def _body(self, owner):
        return {
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "resident", "machineId": "linux:test-host",
            "bridgeId": owner, "sessionHandle": HANDLE, "runtimeConfig": {"channelEnabled": True}, "autoRegister": True,
            "bridgeStartedAt": STARTED[owner],
        }

    def _post(self, owner):
        return self.client.post("/api/v1/agents", json=self._body(owner))

    def _register(self, owner):
        res = self._post(owner)
        self.assertEqual(res.status_code, 200, res.text)

    def _state(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            agent = conn.execute("SELECT runtime_state, status FROM agents WHERE id = ?", (AGENT,)).fetchone()
            rows = {r["id"]: r["superseded_by"] for r in conn.execute("SELECT id, superseded_by FROM bridge_instances WHERE agent_id = ?", (AGENT,))}
        finally:
            conn.close()
        return json.loads(agent["runtime_state"] or "{}").get("bridgeInstanceId"), rows

    def setUp(self):
        super().setUp()
        self._register(OLD)
        # The relaunch: the same session, a new process.
        self._register(NEW)
        owner, rows = self._state()
        self.assertEqual(owner, NEW, "control: the successor's registration did not take the owner")
        self.assertTrue(rows.get(OLD), f"control: the old owner was not retired: {rows}")

    def test_a_late_beat_from_the_old_owner_does_not_revive_it(self):
        self.client.post(f"/api/v1/agents/{AGENT}/heartbeat", json={"bridgeId": OLD, "bridgeKind": "resident", "liveness": True})
        owner, rows = self._state()
        self.assertEqual(owner, NEW)
        self.assertTrue(rows.get(OLD), "the old owner's row was revived by its own beat")

    def test_a_late_claim_on_the_delivery_id_does_not_touch_the_owner(self):
        self.client.post("/api/v1/dispatch/claim", json={
            "agentId": AGENT, "machineId": "linux:test-host", "bridgeId": DELIVERY, "bridgeKind": "channel-sidecar",
            "executionModes": ["channel", "resident"], "waitMs": 0,
        })
        owner, rows = self._state()
        self.assertEqual(owner, NEW)
        self.assertTrue(rows.get(OLD))

    def test_the_old_channels_exit_is_ignored(self):
        res = self.client.post(f"/api/v1/agents/{AGENT}/resident-lost", json={
            "bridgeId": OLD, "machineId": "linux:test-host", "runtime": "claude-code", "reason": "old channel closed",
        })
        self.assertEqual((res.status_code, res.json().get("reason")), (200, "bridge_not_current"))
        owner, rows = self._state()
        self.assertEqual(owner, NEW)
        self.assertFalse(rows.get(NEW), "the successor's owner row was retired by the old channel's exit")

    def test_a_late_registration_from_the_older_process_does_not_take_the_session_back(self):
        """Its registration answered after the successor's: refused, so its exit later finds nothing it owns."""
        res = self._post(OLD)
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn("is held by a process that started after this one; an older process does not take the session back", res.text)
        owner, rows = self._state()
        self.assertEqual(owner, NEW)
        self.assertFalse(rows.get(NEW), "the successor was superseded by the older process")

    def test_an_older_registration_held_after_its_check_does_not_overwrite_a_successor(self):
        """The older request passes its check and is held; the successor registers; the older one resumes."""
        held, release = "owner-held", asyncio.Event()
        original = same_mode_bridge_gate._fresh_same_mode_bridge_conflict

        async def check_then_hold(db, **kwargs):
            conflict = await original(db, **kwargs)
            if kwargs["new_bridge_id"] == held:
                await release.wait()
            return conflict

        async def interleave():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self._app), base_url="http://test") as client:
                older = asyncio.create_task(client.post("/api/v1/agents", json=self._body(held)))
                await asyncio.sleep(0.3)
                successor = asyncio.create_task(client.post("/api/v1/agents", json=self._body("owner-successor")))
                await asyncio.sleep(0.3)
                release.set()
                return await older, await successor

        same_mode_bridge_gate._fresh_same_mode_bridge_conflict = check_then_hold
        try:
            older, successor = asyncio.run(interleave())
        finally:
            same_mode_bridge_gate._fresh_same_mode_bridge_conflict = original
        self.assertEqual((older.status_code, successor.status_code), (200, 200), (older.text, successor.text))
        owner, rows = self._state()
        self.assertEqual(owner, "owner-successor", "the held older registration overwrote its successor")
        self.assertFalse(rows.get("owner-successor"), "the successor was superseded")

    def test_control_a_newer_process_of_the_session_still_takes_over(self):
        self._register("owner-newest")
        owner, rows = self._state()
        self.assertEqual(owner, "owner-newest")
        self.assertTrue(rows.get(NEW), "the newer process did not supersede the successor")

    def test_control_the_successors_own_exit_is_acted_on(self):
        res = self.client.post(f"/api/v1/agents/{AGENT}/resident-lost", json={
            "bridgeId": NEW, "machineId": "linux:test-host", "runtime": "claude-code", "reason": "closed",
        })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotEqual(res.json().get("reason"), "bridge_not_current")
        self.assertTrue(self._state()[1].get(NEW), "the current owner's exit changed nothing, so the ignore above proves nothing")
