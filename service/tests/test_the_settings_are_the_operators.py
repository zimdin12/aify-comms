"""Saving the service's settings, and applying the managed defaults to existing agents, are the operator's.

Steven, 2026-10-02: changing an agent's model and data is operator-protected. The per-agent effort and
usage-source routes were gated; the bulk pair beside them was not. comms-senior-dev's whole-range review
of 0.8 executed it behind the real middleware: a caller holding only the service key saved new defaults
and rewrote an undefined managed agent's model and effort, while the same caller's per-agent effort PATCH
got 403. With no OPERATOR_KEY set the API key stays the boundary, as on every operator route.
"""
from __future__ import annotations

import json

from service.tests._base import FastApiTestCase

KEY = "s3cret"


class TheSettingsAreTheOperators(FastApiTestCase):
    DB_NAME = "aify-test-settings-operator.db"

    def setUp(self):
        super().setUp()
        reply = self.client.post("/api/v1/agents", json={
            "agentId": "worker", "role": "coder", "runtime": "hermes", "sessionMode": "managed",
            "model": "old-model", "runtimeConfig": {"thinking": "high"}})
        self.assertEqual(reply.status_code, 200, reply.text)

    def keyed(self):
        self.client.app.state.config.operator_key = KEY
        self.addCleanup(setattr, self.client.app.state.config, "operator_key", "")

    def worker(self):
        agent = self.client.get("/api/v1/agents/worker").json()["agent"]
        return agent.get("model"), json.dumps(agent.get("runtimeConfig"), sort_keys=True)

    def test_CONTROL_with_no_operator_key_both_stay_open(self):
        self.assertEqual(self.client.put("/api/v1/settings", json={"managed_hermes_model": "new-model"}).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/settings/apply-managed-defaults").status_code, 200)
        self.assertEqual(self.worker()[0], "new-model", "the control must show the bulk path does change the agent")

    def test_an_unproven_caller_saves_nothing_and_rewrites_no_agent(self):
        self.keyed()
        before_settings = self.client.get("/api/v1/settings").json()
        before_worker = self.worker()
        for headers in ({}, {"X-Aify-Operator-Key": "wrong"}):
            saved = self.client.put("/api/v1/settings", json={"managed_hermes_model": "new-model"}, headers=headers)
            self.assertEqual(saved.status_code, 403, f"{headers}: {saved.text}")
            applied = self.client.post("/api/v1/settings/apply-managed-defaults", headers=headers)
            self.assertEqual(applied.status_code, 403, f"{headers}: {applied.text}")
        unread = self.client.put("/api/v1/settings", content=b"{not json")
        self.assertEqual(unread.status_code, 403, "refused before the body is read")
        self.assertEqual(self.client.get("/api/v1/settings").json(), before_settings)
        self.assertEqual(self.worker(), before_worker)

    def test_the_operator_saves_and_applies(self):
        self.keyed()
        proof = {"X-Aify-Operator-Key": KEY}
        self.assertEqual(self.client.put("/api/v1/settings", json={"managed_hermes_model": "new-model"}, headers=proof).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/settings/apply-managed-defaults", headers=proof).status_code, 200)
        self.assertEqual(self.worker()[0], "new-model")
