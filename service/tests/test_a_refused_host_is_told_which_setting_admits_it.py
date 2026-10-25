"""A client that reaches the service by a hostname it does not trust is told so, and which setting admits it.

A fresh install on a second machine connected to the service as `fi-svc:8800` and was refused with
"Cross-site browser requests are refused ... add its origin to CORS_ORIGINS" (2026-10-25). The caller was
`aify-wrapper-connect`, not a browser, and CORS_ORIGINS would not have admitted it: the refusal was the
Host check, which TRUSTED_HOSTS (or reaching the service by IP) answers. A refusal for any other reason
keeps its browser advice.
"""
from __future__ import annotations

import dataclasses
import unittest
from unittest import mock


def _client(trusted_hosts):
    from fastapi.testclient import TestClient
    from service.config import get_config
    from service.main import create_app
    patched = dataclasses.replace(get_config(), api_key="", trusted_hosts=trusted_hosts)
    with mock.patch("service.main.get_config", return_value=patched):
        return TestClient(create_app())


class ARefusedHostIsToldWhichSettingAdmitsItTests(unittest.TestCase):
    def test_an_untrusted_host_names_itself_and_TRUSTED_HOSTS(self):
        response = _client(["testserver"]).get("/.well-known/aify-service", headers={"Host": "fi-svc:8800"})
        self.assertEqual(response.status_code, 403, response.text)
        error = response.json()["error"]
        self.assertIn('"fi-svc"', error)
        self.assertIn("TRUSTED_HOSTS", error)
        self.assertNotIn("CORS_ORIGINS", error)

    def test_CONTROL_a_hostile_origin_on_a_trusted_host_keeps_the_browser_advice(self):
        response = _client(["testserver"]).get("/api/v1/agents", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("CORS_ORIGINS", response.text)
        self.assertNotIn("TRUSTED_HOSTS", response.text)

    def test_CONTROL_the_same_host_once_listed_is_served(self):
        response = _client(["testserver", "fi-svc"]).get("/.well-known/aify-service", headers={"Host": "fi-svc:8800"})
        self.assertNotEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
