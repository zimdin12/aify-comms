"""Plan 4 status taxonomy: managed agents without a live terminal_session
OR live RPC controller must show `available`, not `online`."""

import sys
from pathlib import Path
from service.api_core.records import _agent_record_to_dict
from service.api_core.status_refresh import _compute_agent_status

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))


def test_a_terminal_probe_with_no_database_answers_NO():
    """Was `assert callable(_has_live_terminal_session)`, which proved the name imported and
    nothing else. What matters is the db-less answer: this predicate is consulted by callers that
    have no connection, and it must report "no live worker" rather than raise — the whole db-less
    branch below exists to degrade `online` to `available` in exactly that case."""
    import asyncio
    from service.api_core.live_process_probes import _has_live_terminal_session
    assert asyncio.run(_has_live_terminal_session(None, "any-agent")) is False


def test_there_is_no_server_side_rpc_controller_registry_YET():
    """`_has_live_rpc_controller` is a declared NO, not an unfinished function: the bridge owns RPC
    child lifecycle and the server keeps no registry of it, so this answers False for every agent —
    including one that genuinely has a live RPC child.

    That inaccuracy is deliberate and one-directional. The managed gate below reads it as "no proof
    of a worker" and degrades `online` to `available`; it never uses it to claim a worker. Anyone
    adding the registry can delete this test, and should — until then, this is the shape of the
    hole. (Also replaced a `callable(...)` assertion.)"""
    from service.api_core.capabilities import _has_live_rpc_controller
    for agent_id in ("", "sc-coder", "an agent that really does have an rpc child"):
        assert _has_live_rpc_controller(agent_id) is False


def test_managed_agent_no_worker_returns_available(monkeypatch):
    """When a managed agent has no live terminal_session row and no RPC
    child registration, status must be `available` not `online`."""
    import asyncio
    from unittest import mock
    

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

    # PATCH THE CALLER'S NAMESPACE. `_compute_agent_status` lives in `api_core/status_refresh.py` and
    # imports both of these from their owners, so a patch aimed at `service.control_plane` — which
    # merely re-exports them and, since v0.5.4, declares NO FUNCTIONS AT ALL — installs a mock nobody
    # consults. Both patches here were inert, and the assertion below was passing against the real
    # helpers rather than the fakes.
    with mock.patch("service.api_core.status_refresh._has_live_terminal_session", side_effect=fake_terminal), \
         mock.patch("service.api_core.status_refresh._has_live_rpc_controller", side_effect=fake_rpc):
        result = asyncio.run(_compute_agent_status(row, db=None))
        assert result == "available", f"managed-no-worker should be 'available', got {result!r}"


def test_managed_agent_no_worker_returns_available_WITHOUT_any_mocks():
    """The same claim with nothing patched out. Both probes answer False on their own here — one
    because there is no database, the other because there is no registry — so this is the gate
    running as it runs in production, and it is the only test that enters
    `_has_live_rpc_controller` at all.

    Written after noticing the mocked version above cannot fail for the reason it names: the fakes
    return exactly what the real helpers already return, so it would still pass if both were
    deleted.

    Note what this pins alongside it: the managed branch returns BEFORE the stale-heartbeat rule, so
    this row — whose `last_seen` is far outside the liveness window — reads `available` and not
    `offline`, while the resident row in the next test reads `offline` from the same timestamp. That
    asymmetry is the db-less branch being informational by design (the module comment says so, and
    db-backed callers go through `_compute_live_status_cache`, which layers the offline check on
    top). It is recorded here rather than left to be re-discovered as a bug."""
    import asyncio

    row = {
        "id": "test-managed-no-worker-unmocked",
        "status": "online",
        "session_mode": "managed",
        "runtime": "codex",
        "last_seen": "2026-05-25T00:00:00Z",
    }
    assert asyncio.run(_compute_agent_status(row, db=None)) == "available"


def test_a_RESIDENT_agent_is_not_degraded_by_the_managed_gate():
    """The gate is keyed on session_mode for a reason: a resident agent has no worker to look for,
    and applying the managed degrade to it would report every resident agent as `available` while
    it is mid-turn. The stale-heartbeat rule below still applies to it — hence `offline` here, from
    a 2026 last_seen that is far outside the liveness window."""
    import asyncio

    row = {
        "id": "test-resident",
        "status": "online",
        "session_mode": "resident",
        "runtime": "claude-code",
        "last_seen": "2026-05-25T00:00:00Z",
    }
    assert asyncio.run(_compute_agent_status(row, db=None)) == "offline"


def test_cached_ready_status_serializes_as_online():
    """`ready` is an internal bridge signal, not a public agent status."""
    

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
