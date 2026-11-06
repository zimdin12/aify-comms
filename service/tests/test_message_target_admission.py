"""#53: ambiguous targets fail before storage/wake; blank means absent, not a second selector.

Existing messaging refusals cover missing targets/status codes. These tests add exact recipient
identities, both admission boundaries and absence of *all* downstream effects, with real routes/DB.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from unittest.mock import patch

from service.routers.dispatch_messages import dispatch, messages
from service.tests._base import FastApiTestCase


class MessageTargetAdmission(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent, role, cwd in (("sender", "sender", "/sender"),
                                 ("target-a", "reviewer", "/project-a"),
                                 ("target-b", "reviewer", "/project-b")):
            response = self.client.post("/api/v1/agents", json={"agentId": agent, "role": role, "cwd": cwd})
            self.assertEqual(response.status_code, 200, response.text)

    def _counts(self):
        with closing(sqlite3.connect(self._db_path)) as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("messages", "dispatch_runs", "spawn_requests", "dispatch_controls", "terminal_controls")}

    def _send(self, path, selectors):
        return self.client.post(path, json={"from_agent": "sender", "type": "info", "subject": "selector test",
                                           "body": "fixture", "trigger": True, **selectors})

    def test_mixed_targets_refuse_before_database_message_run_or_wake(self):
        for path, module in (("/api/v1/messages/send", messages), ("/api/v1/dispatch", dispatch)):
            for selectors in ({"to": "target-a", "toRole": "reviewer"},
                              {"to": " target-a ", "toRole": " reviewer "},
                              {"to": "target-a", "toRole": "\u001f"}):
                with self.subTest(path=path, selectors=selectors):
                    before = self._counts()
                    broadcasts, notifications = list(self.ws.broadcasts), list(self.ws.notifications)
                    with patch.object(module, "get_db", wraps=module.get_db) as opened:
                        response = self._send(path, selectors)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(response.json()["detail"], "Use either 'to' or 'toRole', not both")
                    self.assertEqual(opened.await_count, 0, "mixed target reached the database")
                    self.assertEqual(self._counts(), before)
                    self.assertEqual(self.ws.broadcasts, broadcasts)
                    self.assertEqual(self.ws.notifications, notifications)

    def test_direct_and_role_controls_select_exact_ids_with_blank_other_selector(self):
        for path, module in (("/api/v1/messages/send", messages), ("/api/v1/dispatch", dispatch)):
            for blank in (None, "", " \t\r\n", "﻿ "):
                for selectors, expected in (({"to": "target-a", "toRole": blank}, {"target-a"}),
                                            ({"to": blank, "toRole": "reviewer"}, {"target-a", "target-b"})):
                    with self.subTest(path=path, selectors=selectors):
                        with patch.object(module, "get_db", wraps=module.get_db) as opened:
                            response = self._send(path, selectors)
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(set(response.json()["recipients"]), expected)
                        self.assertGreater(opened.await_count, 0, "healthy control never reached real admission")

    def test_whitespace_only_is_still_missing_on_both_routes(self):
        for path in ("/api/v1/messages/send", "/api/v1/dispatch"):
            with self.subTest(path=path):
                before = self._counts()
                response = self._send(path, {"to": " \t", "toRole": "﻿ "})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "Need 'to' or 'toRole'")
                self.assertEqual(self._counts(), before)

    def test_channel_control_keeps_member_fanout(self):
        self.client.post("/api/v1/channels", json={"name": "target-control", "createdBy": "sender"}).raise_for_status()
        for agent in ("target-a", "target-b"):
            self.client.post("/api/v1/channels/target-control/join", json={"agentId": agent}).raise_for_status()
        response = self.client.post("/api/v1/channels/target-control/send", json={
            "from_agent": "sender", "channel": "target-control", "body": "fixture channel control"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()["members"]), {"sender", "target-a", "target-b"})
        with closing(sqlite3.connect(self._db_path)) as db:
            recipients = {row[0] for row in db.execute("SELECT to_agent FROM messages WHERE body = ? AND to_agent IS NOT NULL",
                                                     ("fixture channel control",))}
        self.assertEqual(recipients, {"target-a", "target-b"})
