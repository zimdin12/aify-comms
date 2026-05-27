"""Plan 4 discover_session_id contract — per-adapter runtime-native discovery
for fresh managed launches."""

import asyncio
import pytest
from service.runtimes.base import RuntimeAdapter


class _TestAdapter(RuntimeAdapter):
    name = "test-runtime"
    display_name = "Test"
    session_env_vars = ["TEST_SESSION_ID"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"


def test_base_discover_session_id_returns_none():
    a = _TestAdapter()
    assert asyncio.run(a.discover_session_id()) is None
