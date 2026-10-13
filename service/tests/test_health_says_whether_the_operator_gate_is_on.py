"""`/health` says whether the operator gate is on, and never what the key is (2026-10-14 audit, D14).

The dashboard enables an irreversible action (agent Delete) only when it can prove the operator. With no
OPERATOR_KEY there is nothing to prove, and the only way it had to know was mirroring this service's .env.
"""

import asyncio
from types import SimpleNamespace
from unittest import mock

from service.routers import health as health_module

SECRET = "SENTINELoperatorKey0123456789"


def _health_with(operator_key: str) -> dict:
    config = SimpleNamespace(version="0.0.0-test", build_short="test", stamp_overrides=(), operator_key=operator_key)
    with mock.patch.object(health_module, "get_config", return_value=config):
        return asyncio.run(health_module.health())


def test_the_gate_is_reported_off_when_no_key_is_set():
    assert _health_with("")["operatorGate"] is False


def test_the_gate_is_reported_on_and_the_key_never_appears():
    payload = _health_with(SECRET)
    assert payload["operatorGate"] is True
    assert SECRET not in repr(payload), "the operator key leaked into /health"
    assert payload["status"] == "healthy", "control: this is the health payload"
