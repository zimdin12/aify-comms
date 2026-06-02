import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.db import init_db
from service.routers.api_v2 import router


from service.tests._base import FastApiTestCase


class ResidentBridgeLivenessTests(FastApiTestCase):
    LEGACY_SETTINGS = {"resident_lease_seconds": 150}

    def _register_resident_hermes(self, agent_id: str, **extra):
        payload = {
            "agentId": agent_id,
            "role": "tester",
            "runtime": "hermes",
            "sessionMode": "resident",
            "sessionHandle": f"session-{agent_id}",
            "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9119/api/ws?token=t"},
        }
        payload.update(extra)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_raw_resident_registration_without_bridge_is_reported_stale_and_not_dispatchable(self):
        self._register_resident_hermes("target")
        self._register_resident_hermes("sender", bridgeId="bridge-sender", machineId="win32:test-host")

        info = self.client.get("/api/v1/agents/target")
        self.assertEqual(info.status_code, 200, info.text)
        self.assertEqual(info.json()["agent"]["status"], "stale")

        send = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "sender",
                "to": "target",
                "type": "request",
                "subject": "ping",
                "body": "hello",
                "trigger": True,
            },
        )
        self.assertEqual(send.status_code, 200, send.text)
        body = send.json()
        self.assertFalse(body["ok"], body)
        self.assertEqual(body.get("dispatchRuns") or [], [])
        not_started = (body.get("notStarted") or [{}])[0]
        self.assertEqual(not_started.get("recipientStatus"), "stale")
        self.assertIn("Restart the visible resident wrapper", not_started.get("fix", ""))

    def test_resident_registration_with_current_bridge_remains_dispatchable(self):
        self._register_resident_hermes("target", bridgeId="bridge-target", machineId="win32:test-host")

        info = self.client.get("/api/v1/agents/target")
        self.assertEqual(info.status_code, 200, info.text)
        self.assertNotEqual(info.json()["agent"]["status"], "stale")

        send = self.client.post(
            "/api/v1/messages/send",
            json={
                "from_agent": "dashboard",
                "to": "target",
                "type": "request",
                "subject": "ping",
                "body": "hello",
                "trigger": True,
            },
        )
        self.assertEqual(send.status_code, 200, send.text)
        body = send.json()
        self.assertTrue(body["ok"], body)
        self.assertTrue(body.get("dispatchRuns"), body)
