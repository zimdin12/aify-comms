"""Plan 3 (2026-05-25) — closes #120. Restores the per-config resident gate
that Plan 2 Task 14 dropped. Claude resident agents must have
runtime_config.channelEnabled=True before advertising `resident-run`;
hermes resident agents must have a valid gatewayUrl.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import json

from service.control_plane import _agent_execution_mode, _agent_wake_mode, _default_capabilities_for, _row_capabilities


def test_claude_resident_without_channel_enabled_does_not_advertise_resident_run():
    caps = _default_capabilities_for("claude-code", "resident", "session-x", {})
    assert "resident-run" not in caps, (
        f"claude resident without channelEnabled must not advertise resident-run (#120). caps={caps}"
    )


def test_claude_resident_with_channel_enabled_advertises_resident_run():
    caps = _default_capabilities_for("claude-code", "resident", "session-x", {"channelEnabled": True})
    assert "resident-run" in caps, f"expected resident-run; caps={caps}"


def test_hermes_resident_without_gateway_url_does_not_advertise_resident_run():
    caps = _default_capabilities_for("hermes", "resident", "session-y", {})
    assert "resident-run" not in caps


def test_hermes_resident_with_gateway_url_advertises_resident_run():
    caps = _default_capabilities_for(
        "hermes", "resident", "session-y",
        {"gatewayUrl": "ws://127.0.0.1:9999/api/ws?token=x"},
    )
    assert "resident-run" in caps


def test_codex_resident_always_advertises_resident_run():
    caps = _default_capabilities_for("codex", "resident", "session-z", {})
    assert "resident-run" in caps


def test_pi_resident_never_advertises_resident_run():
    caps = _default_capabilities_for("pi", "resident", "session-q", {})
    assert "resident-run" not in caps


def test_pi_resident_row_backfill_does_not_restore_resident_run():
    row = {
        "id": "pi-presence",
        "runtime": "pi",
        "session_mode": "resident",
        "session_handle": "session-q",
        "runtime_config": "{}",
        "capabilities": json.dumps(["resume", "interrupt", "steer"]),
    }
    caps = _row_capabilities(row)
    assert "resident-run" not in caps


def test_opencode_resident_row_backfill_does_not_restore_resident_run():
    row = {
        "id": "opencode-presence",
        "runtime": "opencode",
        "session_mode": "resident",
        "session_handle": "session-q",
        "runtime_config": "{}",
        "capabilities": json.dumps(["resident-run", "resume", "interrupt", "steer"]),
    }
    caps = _row_capabilities(row)
    assert "resident-run" not in caps


def test_pi_and_opencode_resident_wake_mode_is_presence_only_even_with_stale_caps():
    for runtime in ("pi", "opencode"):
        row = {
            "id": f"{runtime}-presence",
            "runtime": runtime,
            "session_mode": "resident",
            "session_handle": "session-q",
            "launch_mode": "detached",
            "runtime_config": "{}",
            "capabilities": json.dumps(["resident-run", "resume", "interrupt", "steer"]),
        }
        assert _agent_wake_mode(row) == "presence-only"


def test_pi_resident_execution_rejected_even_with_stale_capability():
    mode, reason = _agent_execution_mode({
        "id": "old-pi-presence",
        "runtime": "pi",
        "session_mode": "resident",
        "session_handle": "session-q",
        "launch_mode": "detached",
        "runtime_config": "{}",
        "capabilities": json.dumps(["resident-run", "resume", "interrupt", "steer"]),
    })
    assert mode is None
    assert "not a triggerable resident target" in reason


def test_opencode_resident_execution_rejected_even_with_stale_capability():
    mode, reason = _agent_execution_mode({
        "id": "old-opencode-presence",
        "runtime": "opencode",
        "session_mode": "resident",
        "session_handle": "session-q",
        "launch_mode": "detached",
        "runtime_config": "{}",
        "capabilities": json.dumps(["resident-run", "resume", "interrupt"]),
    })
    assert mode is None
    assert "not a triggerable resident target" in reason
