"""The inbox's queued-channel projection (service/api_core/queued_for_channel.py), which the notify hook in a live
channel session reads to leave out bodies the channel will bring (mcp/stdio/notify-notice.mjs).

A message is queued-for-channel only when the newest `requested_at` among its runs for the reader holds exactly one
distinct run, queued, in a mode the session's producer claims. Every other case keeps the body. The production
runs come from real sends; the shapes no single send produces are rows in the real schema, read through the route.

`queuedChannel` is versioned: 1 is the contract the hook installed with 2df43e00 reads (a boolean per message on an
unfiltered page); 2 leaves those messages out of the paged unread population and names the newest in a window.
"""

import sqlite3

from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define

AGENT = "claude-cold"
HOST = {"environment_id": "linux:test-host:default", "machine_id": "linux:test-host", "bridge_id": "bridge-current"}


class QueuedChannelInbox(FastApiTestCase):
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

    def _send(self, body, to=AGENT):
        response = self.client.post("/api/v1/messages/send", json={
            "from_agent": "dashboard", "trigger": True, "to": to, "type": "request", "subject": body, "body": body,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["messageId"]

    def _bare(self, body):
        """A message to AGENT with every run the send gave it removed, so the test writes its runs itself. A send
        while a run is pending merges into that run through dispatch_run_items, so that association goes too."""
        message_id = self._send(body)
        self._sql("DELETE FROM dispatch_runs WHERE message_id = ?", (message_id,))
        self._sql("DELETE FROM dispatch_run_items WHERE message_id = ?", (message_id,))
        return message_id

    def _inbox(self, agent=AGENT, **params):
        response = self.client.get(f"/api/v1/messages/inbox/{agent}", params={"peek": "1", **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _flags(self, **params):
        return {m["id"]: m.get("queuedForChannel") for m in self._inbox(queuedChannel="1", limit=200, **params)["messages"]}

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

    def _add_run(self, run_id, *, message_id=None, target=AGENT, at, mode="channel", status="queued"):
        self._sql(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, execution_mode, status, requested_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, message_id, "dashboard", target, mode, status, at),
        )

    def test_a_production_queued_channel_run_is_named_until_it_is_claimed(self):
        message_id = self._send("first")
        runs = self._runs_of(message_id)
        self.assertEqual([(r["execution_mode"], r["status"]) for r in runs], [("channel", "queued")],
                         "control: a send to a managed claude makes one queued channel run")
        self.assertIs(self._flags()[message_id], True)
        self.assertNotIn("queuedForChannel", self._inbox()["messages"][0], "a reader that did not ask is unchanged")
        self._sql("UPDATE dispatch_runs SET status = 'claimed' WHERE id = ?", (runs[0]["id"],))
        self.assertIs(self._flags()[message_id], False)

    def test_a_message_batched_into_a_queued_run_is_counted_under_that_runs_key(self):
        first, second = self._send("first"), self._send("second")
        self.assertEqual(len(self._runs_of(first)), 1, "control: one run")
        self.assertEqual(self._runs_of(second), [], "control: the second send joined the first run, not a run of its own")
        window = self._inbox(queuedChannel="2")["queuedForChannel"]
        self.assertEqual(window["total"], 2, "the total counts queued MESSAGES")
        self.assertEqual({entry["messageId"] for entry in window["newest"]}, {first, second})
        self.assertEqual(len({(entry["requestedAt"], entry["runId"]) for entry in window["newest"]}), 1,
                         "both are named under one run's key, which is why a run watermark counts the second")

    def test_a_production_queued_resident_run_is_named_too(self):
        # A Claude started by hand: its channel producer claims resident runs as the managed one claims channel runs.
        response = self.client.post("/api/v1/agents", json={
            "agentId": "by-hand", "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "machineId": HOST["machine_id"], "bridgeId": "bridge-by-hand", "sessionHandle": "session-by-hand",
            "capabilities": ["resident-run", "resume", "interrupt"], "runtimeConfig": {"channelEnabled": True},
        })
        self.assertEqual(response.status_code, 200, response.text)
        message_id = self._send("b", to="by-hand")
        self.assertEqual([(r["execution_mode"], r["status"]) for r in self._runs_of(message_id)],
                         [("resident", "queued")], "control: a send to a resident claude makes one queued resident run")
        window = self._inbox("by-hand", queuedChannel="2")["queuedForChannel"]
        self.assertEqual([entry["messageId"] for entry in window["newest"]], [message_id])

    def test_the_rule_judges_the_newest_attempt_and_refuses_what_it_cannot_tell_apart(self):
        cases = {
            # label: ([(at, mode, status), ...] as distinct runs of the message, expected)
            "queued channel": ([("t1", "channel", "queued")], True),
            "queued resident": ([("t1", "resident", "queued")], True),
            "queued managed": ([("t1", "managed", "queued")], False),
            "claimed": ([("t1", "channel", "claimed")], False),
            "delivered steer, control pending": ([("t1", "channel", "delivered")], False),
            "older failed, newer queued": ([("t1", "channel", "failed"), ("t2", "channel", "queued")], True),
            "older queued, newer failed": ([("t1", "channel", "queued"), ("t2", "channel", "failed")], False),
            "tied queued and failed": ([("t2", "channel", "queued"), ("t2", "channel", "failed")], False),
            "two distinct queued attempts tied": ([("t2", "channel", "queued"), ("t2", "channel", "queued")], False),
        }
        ids = {}
        for n, (label, (runs, _)) in enumerate(cases.items()):
            ids[label] = self._bare(label)
            for k, (at, mode, status) in enumerate(runs):
                self._add_run(f"run-{n}-{k}", message_id=ids[label], at=f"2026-10-28T0{at[1]}:00:00Z", mode=mode, status=status)
        no_run = self._bare("no run")
        flags = self._flags()
        self.assertIs(flags[no_run], False, "no run")
        for label, (_, expected) in cases.items():
            with self.subTest(label):
                self.assertIs(flags[ids[label]], expected)

    def test_one_attempt_reached_two_ways_is_one_attempt_and_another_agents_run_is_not_this_one(self):
        both, batched, foreign = self._bare("both"), self._bare("batched"), self._bare("foreign")
        # The run's own message AND a batched item naming the same message: one attempt, read twice.
        self._add_run("run-both", message_id=both, at="2026-10-28T01:00:00Z")
        self._sql("INSERT INTO dispatch_run_items (message_id, run_id) VALUES (?, ?)", (both, "run-both"))
        # Batched into another message's run: reached through dispatch_run_items alone.
        self._add_run("run-batch", at="2026-10-28T01:00:00Z")
        self._sql("INSERT INTO dispatch_run_items (message_id, run_id) VALUES (?, ?)", (batched, "run-batch"))
        self._add_run("run-foreign", message_id=foreign, target="someone-else", at="2026-10-28T01:00:00Z")
        flags = self._flags()
        self.assertEqual({m: flags[m] for m in (both, batched, foreign)}, {both: True, batched: True, foreign: False})

    def test_version_2_pages_only_what_is_not_queued_and_names_the_newest_attempts(self):
        ordinary = self._bare("ordinary")
        queued = []
        for n in range(25):
            message_id = self._bare(f"queued {n}")
            self._add_run(f"run-q{n:02d}", message_id=message_id, at=f"2026-10-28T02:{n:02d}:00Z")
            queued.append(message_id)
        answer = self._inbox(queuedChannel="2", limit=5)
        self.assertEqual([m["id"] for m in answer["messages"]], [ordinary], "a queued message was paged")
        self.assertEqual(answer["total"], 1, "the paged total counts what is paged")
        self.assertEqual(answer["unreadTotal"], 26, "the unread total is the whole inbox")
        window = answer["queuedForChannel"]
        self.assertEqual(window["total"], 25)
        self.assertEqual([entry["messageId"] for entry in window["newest"]], queued[::-1][:20], "newest attempt first, twenty")
        self.assertEqual(window["newest"][0]["runId"], "run-q24")
        self.assertEqual(window["newest"][0]["from"], "dashboard")
        self.assertNotIn("queuedForChannel", answer["messages"][0], "version 2 carries no per-message flag")

        # The hook installed with 2df43e00 sends version 1 and must keep reading the whole page with its flags.
        old = self._inbox(queuedChannel="1", limit=50)
        self.assertEqual(old["total"], 26)
        self.assertNotIn("queuedForChannel", old)
        self.assertEqual(sum(1 for m in old["messages"] if m["queuedForChannel"]), 25)

    def test_version_2_reads_only_the_unread_inbox(self):
        for params in ({"filter": "all"}, {"messageId": "x"}):
            with self.subTest(params):
                response = self.client.get(f"/api/v1/messages/inbox/{AGENT}", params={"peek": "1", "queuedChannel": "2", **params})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "queuedChannel=2 pages the unread inbox only.")
        response = self.client.get(f"/api/v1/messages/inbox/{AGENT}", params={"peek": "1", "queuedChannel": "3"})
        self.assertEqual(response.status_code, 422, "an unknown version is refused")
