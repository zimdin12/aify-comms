"""Plan 4 status taxonomy: managed agents without a live terminal_session
OR live RPC controller must show `available`, not `online`."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))


def test_has_live_terminal_session_helper_exists():
    from service.routers.api_v2 import _has_live_terminal_session
    assert callable(_has_live_terminal_session)


def test_has_live_rpc_controller_helper_exists():
    from service.routers.api_v2 import _has_live_rpc_controller
    assert callable(_has_live_rpc_controller)


def test_managed_agent_no_worker_returns_available(monkeypatch):
    """When a managed agent has no live terminal_session row and no RPC
    child registration, status must be `available` not `online`."""
    import asyncio
    from unittest import mock
    from service.routers.api_v2 import _compute_agent_status

    row = {
        "id": "test-managed-no-worker",
        "status": "online",
        "session_mode": "managed",
        "runtime": "codex",
        "last_seen": "2026-05-25T00:00:00Z",
    }

    async def fake_terminal(*args, **kwargs):
        return False

    def fake_rpc(*args, **kwargs):
        return False

    with mock.patch("service.routers.api_v2._has_live_terminal_session", side_effect=fake_terminal), \
         mock.patch("service.routers.api_v2._has_live_rpc_controller", side_effect=fake_rpc):
        result = asyncio.run(_compute_agent_status(row, db=None))
        assert result == "available", f"managed-no-worker should be 'available', got {result!r}"


def test_cached_ready_status_serializes_as_online():
    """`ready` is an internal bridge signal, not a public agent status."""
    from service.routers.api_v2 import _agent_record_to_dict

    class Row(dict):
        def keys(self):
            return super().keys()

    row = Row({
        "id": "cached-ready",
        "role": "tester",
        "name": "Cached Ready",
        "cwd": "",
        "model": "",
        "description": "",
        "instructions": "",
        "status": "online",
        "live_status": "ready",
        "live_reason": "",
        "registered_at": "2026-05-27T00:00:00Z",
        "last_seen": "2026-05-27T00:00:00Z",
        "runtime": "codex",
        "machine_id": "linux:test",
        "launch_mode": "detached",
        "session_mode": "managed",
        "session_handle": "",
        "managed_by": "",
        "capabilities": '["managed-run","resume"]',
        "runtime_config": "{}",
        "runtime_state": "{}",
        "favorited": 0,
    })

    payload = _agent_record_to_dict(row, "ready", unread=0)
    assert payload["status"] == "online"
    assert payload["statusRaw"] == "online"
