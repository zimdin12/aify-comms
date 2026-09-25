"""The send path and the dispatch path queue console input through ONE function, and say which they were.

`_queue_console_dispatch_inputs` (send_message) and `_queue_console_inputs_for_dispatch`
(create_dispatch) were two near-identical copies, pinned equal by an agreement test. They are one body
now, and the single thing that told them apart -- the `source` recorded on the
`terminal_input_requested` event -- is a parameter. This proves each entry point still records its
own value, and that the send path still gives each fan-out recipient its own message id.

The DB-touching helpers are replaced on the module, so nothing here opens a database.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

import service.api_core.console_input_queue as queue


class ConsoleInputRecordsWhichPathSentIt(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.saved = {}
        fakes = {
            "_describe_sender": self._describe_sender,
            "_append_terminal_control": self._append_terminal_control,
            "_append_terminal_event": self._append_terminal_event,
            "_record_terminal_delivery_contract": self._record_contract,
        }
        for name, fake in fakes.items():
            self.saved[name] = getattr(queue, name)
            setattr(queue, name, fake)

    def tearDown(self):
        for name, original in self.saved.items():
            setattr(queue, name, original)

    async def _describe_sender(self, db, from_agent, origin, machine=""):
        return from_agent

    async def _append_terminal_control(self, db, **kwargs):
        return f"ctl-{kwargs['terminal_id']}"

    async def _append_terminal_event(self, db, terminal_id, kind, body):
        self.events.append((terminal_id, kind, json.loads(body)))

    async def _record_contract(self, db, **kwargs):
        return f"run-{kwargs['recipient_id']}"

    @staticmethod
    def _req():
        return SimpleNamespace(
            trigger=True, from_agent="sender", subject="s", body="b", type="request", priority="normal",
            requireReply=None, origin="", _external_machine="",
        )

    @staticmethod
    def _terminals(*agents):
        return {
            agent: {"terminal_id": f"t-{agent}", "environment_id": "env", "bridge_id": "", "runtime": "codex"}
            for agent in agents
        }

    def test_the_send_path_records_message_send_and_a_per_recipient_id_on_fan_out(self):
        deliveries = []
        asyncio.run(queue._queue_console_dispatch_inputs(
            None, self._req(), "m1", ["a", "b"], self._terminals("a", "b"), deliveries, "",
        ))
        self.assertEqual({e[2]["source"] for e in self.events}, {"message_send"})
        self.assertEqual({e[2]["messageId"] for e in self.events}, {"m1-a", "m1-b"})
        self.assertEqual(len(deliveries), 2)

    def test_a_single_recipient_send_threads_on_the_callers_id(self):
        asyncio.run(queue._queue_console_dispatch_inputs(
            None, self._req(), "m1", ["a"], self._terminals("a"), [], "",
        ))
        self.assertEqual([e[2]["messageId"] for e in self.events], ["m1"])

    def test_the_send_path_queues_nothing_without_a_trigger(self):
        req = self._req()
        req.trigger = False
        deliveries = []
        asyncio.run(queue._queue_console_dispatch_inputs(
            None, req, "m1", ["a"], self._terminals("a"), deliveries, "",
        ))
        self.assertEqual((self.events, deliveries), ([], []))

    def test_the_dispatch_path_records_dispatch(self):
        asyncio.run(queue._queue_console_inputs_for_dispatch(
            None, self._req(), "m2", self._terminals("a"), [], {"a": "m2"}, "",
        ))
        self.assertEqual([(e[2]["source"], e[2]["messageId"]) for e in self.events], [("dispatch", "m2")])


if __name__ == "__main__":
    unittest.main()
