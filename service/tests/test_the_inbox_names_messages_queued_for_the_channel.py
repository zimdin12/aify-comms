"""The inbox's opt-in queued-channel projection (service/api_core/queued_for_channel.py), which the notify hook in a
live channel session reads to leave out bodies the channel will bring (mcp/stdio/notify-notice.mjs).

A message is queued-for-channel only when its newest run for the reader is unambiguously a channel run in
`queued`. Every other case keeps the body: claimed, delivered (a steer's run, whose control is still pending),
failed, a tie that disagrees, no run at all, a run for another agent. The production-created run comes from a real
send; the shapes no single send produces are written as rows with the real schema and read through the real route.
"""

import sqlite3

from service.api_core.queued_for_channel import is_queued_for_channel
from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define

AGENT = "claude-cold"
HOST = {"environment_id": "linux:test-host:default", "machine_id": "linux:test-host", "bridge_id": "bridge-current"}


def run(requested_at, mode="channel", status="queued"):
    return {"requested_at": requested_at, "execution_mode": mode, "status": status}


class TheDecision(FastApiTestCase):
    def test_only_an_unambiguous_newest_queued_channel_run_counts(self):
        cases = {
            "queued channel": ([run("t1")], True),
            "no run": ([], False),
            "claimed": ([run("t1", status="claimed")], False),
            "delivered steer": ([run("t1", status="delivered")], False),
            "queued resident": ([run("t1", mode="resident")], True),
            "queued managed": ([run("t1", mode="managed")], False),
            "older failed, newer queued": ([run("t1", status="failed"), run("t2")], True),
            "older queued, newer failed": ([run("t1"), run("t2", status="failed")], False),
            "tied queued and failed": ([run("t2"), run("t2", status="failed")], False),
            "tied, both queued channel": ([run("t2"), run("t2")], True),
        }
        for label, (runs, expected) in cases.items():
            with self.subTest(label):
                self.assertIs(is_queued_for_channel(runs), expected)


class TheInboxRoute(FastApiTestCase):
    def setUp(self):
        super().setUp()
        response = self.client.post("/api/v1/environments/heartbeat", json={
            "id": HOST["environment_id"], "label": "test", "machineId": HOST["machine_id"], "os": "linux",
            "kind": "linux", "bridgeId": HOST["bridge_id"], "cwdRoots": ["/workspace"], "metadata": {},
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"],
                          "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True}}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
            "machineId": HOST["machine_id"], "bridgeId": HOST["bridge_id"],
            "capabilities": ["managed-run", "resume", "interrupt"], "runtimeConfig": {"channelEnabled": True},
        })
        self.assertEqual(response.status_code, 200, response.text)
        define(self, AGENT, runtime="claude-code", workspace="/workspace", **HOST)

    def _send(self, body):
        response = self.client.post("/api/v1/messages/send", json={
            "from_agent": "dashboard", "trigger": True, "to": AGENT, "type": "request", "subject": body, "body": body,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["messageId"]

    def _inbox(self, **params):
        response = self.client.get(f"/api/v1/messages/inbox/{AGENT}", params={"peek": "1", **params})
        self.assertEqual(response.status_code, 200, response.text)
        return {m["id"]: m for m in response.json()["messages"]}

    def _sql(self, statement, args=()):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(statement, args)
            conn.commit()
        finally:
            conn.close()

    def _runs_of(self, message_id):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, execution_mode, status FROM dispatch_runs WHERE message_id = ?", (message_id,))]
        finally:
            conn.close()

    def _add_run(self, run_id, *, message_id=None, target=AGENT, requested_at, mode="channel", status="queued"):
        self._sql(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, execution_mode, status, requested_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, message_id, "dashboard", target, mode, status, requested_at),
        )

    def test_a_production_queued_channel_run_is_named_until_it_is_claimed(self):
        message_id = self._send("first")
        runs = self._runs_of(message_id)
        self.assertEqual([(r["execution_mode"], r["status"]) for r in runs], [("channel", "queued")],
                         "control: a send to a managed claude makes one queued channel run")

        self.assertIs(self._inbox(queuedChannel="1")[message_id]["queuedForChannel"], True)
        self.assertNotIn("queuedForChannel", self._inbox()[message_id], "a reader that did not ask is unchanged")

        self._sql("UPDATE dispatch_runs SET status = 'claimed' WHERE id = ?", (runs[0]["id"],))
        self.assertIs(self._inbox(queuedChannel="1")[message_id]["queuedForChannel"], False)

    def test_a_production_queued_resident_run_is_named_too(self):
        # A Claude started by hand: its channel producer claims resident runs as the managed one claims channel runs.
        response = self.client.post("/api/v1/agents", json={
            "agentId": "by-hand", "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "machineId": HOST["machine_id"], "bridgeId": "bridge-by-hand", "sessionHandle": "session-by-hand",
            "capabilities": ["resident-run", "resume", "interrupt"], "runtimeConfig": {"channelEnabled": True},
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post("/api/v1/messages/send", json={
            "from_agent": "dashboard", "trigger": True, "to": "by-hand", "type": "request", "subject": "s", "body": "b",
        })
        self.assertEqual(response.status_code, 200, response.text)
        message_id = response.json()["messageId"]
        self.assertEqual([(r["execution_mode"], r["status"]) for r in self._runs_of(message_id)],
                         [("resident", "queued")], "control: a send to a resident claude makes one queued resident run")
        response = self.client.get("/api/v1/messages/inbox/by-hand", params={"peek": "1", "queuedChannel": "1"})
        self.assertIs({m["id"]: m for m in response.json()["messages"]}[message_id]["queuedForChannel"], True)

    def test_runs_are_associated_as_reply_linking_associates_them_and_judged_by_the_newest(self):
        ordinary = self._send("ordinary")
        self._sql("DELETE FROM dispatch_runs WHERE message_id = ?", (ordinary,))
        batched, steered, retried, tied, foreign = (self._send(n) for n in ("batched", "steered", "retried", "tied", "foreign"))
        for message_id in (batched, steered, retried, tied, foreign):
            self._sql("DELETE FROM dispatch_runs WHERE message_id = ?", (message_id,))

        # Batched into another message's run: reached through dispatch_run_items.
        self._add_run("run-batch", requested_at="2026-10-28T01:00:00Z")
        self._sql("INSERT INTO dispatch_run_items (message_id, run_id) VALUES (?, ?)", (batched, "run-batch"))
        # A steer's contract run is written delivered while its control is still to be emitted.
        self._add_run("run-steer", message_id=steered, requested_at="2026-10-28T01:00:00Z", status="delivered")
        # Failed, then queued again: the newer one decides.
        self._add_run("run-old", message_id=retried, requested_at="2026-10-28T01:00:00Z", status="failed")
        self._add_run("run-new", message_id=retried, requested_at="2026-10-28T02:00:00Z")
        # Two runs at one instant that disagree: nobody can say which applies.
        self._add_run("run-tie-a", message_id=tied, requested_at="2026-10-28T03:00:00Z")
        self._add_run("run-tie-b", message_id=tied, requested_at="2026-10-28T03:00:00Z", status="failed")
        # A queued channel run for some other agent says nothing about this reader.
        self._add_run("run-foreign", message_id=foreign, target="someone-else", requested_at="2026-10-28T01:00:00Z")

        view = self._inbox(queuedChannel="1")
        self.assertEqual(
            {m: view[m]["queuedForChannel"] for m in (ordinary, batched, steered, retried, tied, foreign)},
            {ordinary: False, batched: True, steered: False, retried: True, tied: False, foreign: False},
        )
