"""Contract tests for the Python RuntimeAdapter base class. Verifies the
normalizer behavior + Plan 3 stub semantics. Per-adapter capability
assertions live in test_per_adapter.py."""

import asyncio
import os
import pytest

from service.runtimes.base import (
    RuntimeAdapter,
    HANDLE_PLACEHOLDERS,
    MODEL_PLACEHOLDERS,
)


class _TestAdapter(RuntimeAdapter):
    """Concrete fill-in so we can exercise the base class without depending on
    a real runtime. Matches the JS contract.test.js TestAdapter pattern."""
    name = "test-runtime"
    display_name = "Test Runtime"
    session_env_vars = ["TEST_SESSION_ID", "TEST_SESSION_ALT"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed"


def _clear_test_env(monkeypatch):
    monkeypatch.delenv("TEST_SESSION_ID", raising=False)
    monkeypatch.delenv("TEST_SESSION_ALT", raising=False)


def test_get_current_session_id_returns_none_when_unset(monkeypatch):
    _clear_test_env(monkeypatch)
    assert _TestAdapter().get_current_session_id() is None


def test_get_current_session_id_returns_first_var(monkeypatch):
    _clear_test_env(monkeypatch)
    monkeypatch.setenv("TEST_SESSION_ID", "abc-123")
    assert _TestAdapter().get_current_session_id() == "abc-123"


def test_get_current_session_id_falls_back(monkeypatch):
    _clear_test_env(monkeypatch)
    monkeypatch.setenv("TEST_SESSION_ALT", "fallback-id")
    assert _TestAdapter().get_current_session_id() == "fallback-id"


def test_get_current_session_id_rejects_placeholders(monkeypatch):
    _clear_test_env(monkeypatch)
    for placeholder in ("unknown", "default", "none", "null"):
        monkeypatch.setenv("TEST_SESSION_ID", placeholder)
        assert _TestAdapter().get_current_session_id() is None


def test_normalize_session_handle_strips_placeholders():
    a = _TestAdapter()
    assert a.normalize_session_handle("unknown") == ""
    assert a.normalize_session_handle("Default") == ""
    assert a.normalize_session_handle("") == ""
    assert a.normalize_session_handle(None) == ""
    assert a.normalize_session_handle("  real-handle  ") == "real-handle"


def test_resume_args():
    a = _TestAdapter()
    assert a.resume_args("real-handle") == ["--resume", "real-handle"]
    assert a.resume_args("") == []
    assert a.resume_args("unknown") == []
    assert a.resume_args(None) == []


def test_normalize_model_override_strips_placeholders():
    a = _TestAdapter()
    assert a.normalize_model_override("unknown") == ""
    assert a.normalize_model_override("default") == ""
    assert a.normalize_model_override("auto") == ""
    assert a.normalize_model_override("") == ""
    assert a.normalize_model_override("gpt-5.5") == "gpt-5.5"


def test_diagnostic_env(monkeypatch):
    _clear_test_env(monkeypatch)
    monkeypatch.setenv("TEST_SESSION_ALT", "captured")
    env = _TestAdapter().diagnostic_env()
    assert env["TEST_SESSION_ID"] == "(unset)"
    assert env["TEST_SESSION_ALT"] == "captured"


def test_plan_3_methods_raise_not_implemented():
    a = _TestAdapter()
    with pytest.raises(NotImplementedError):
        _ = a.wrapper_name
    with pytest.raises(NotImplementedError):
        a.console_command(agent_id="x", handle="", interactive=True)
    # is_resident_ready is now a concrete default (Plan 3), not a stub.
    # Default impl returns self.supports_resident.
    assert a.is_resident_ready({}) is True  # _TestAdapter.supports_resident=True


def test_plan_3_async_methods_raise_not_implemented():
    # pytest-asyncio is not installed in this environment, so we drive the
    # async coroutines through asyncio.run(...) — matches the existing repo
    # pattern in service/tests/test_api_v2_regressions.py.
    a = _TestAdapter()

    async def _inject():
        await a.inject_message(text="hi")

    async def _interrupt():
        await a.interrupt(reason="x")

    async def _steer():
        await a.steer(text="x")

    with pytest.raises(NotImplementedError):
        asyncio.run(_inject())
    with pytest.raises(NotImplementedError):
        asyncio.run(_interrupt())
    with pytest.raises(NotImplementedError):
        asyncio.run(_steer())


def test_placeholder_sets():
    assert HANDLE_PLACEHOLDERS == {"unknown", "default", "none", "null"}
    assert MODEL_PLACEHOLDERS == {"unknown", "default", "auto"}


def test_subclass_must_define_class_attrs():
    class Bad(RuntimeAdapter):
        pass  # forgets to set name/session_env_vars/etc.

    with pytest.raises(AttributeError):
        _ = Bad().name
