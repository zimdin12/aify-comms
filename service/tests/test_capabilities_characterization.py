"""Freeze the capability decisions that used to branch on runtime names."""
import json

import pytest

from service.api_core.capabilities import (
    _default_capabilities_for, _managed_via_wrapper_for_runtime, _row_capabilities,
)


@pytest.mark.parametrize("runtime, eligible", [
    ("claude-code", False), ("pi", False), ("opencode", False),
    ("codex", True), ("hermes", True), ("unknown-runtime", False),
])
def test_managed_wrapper_setting_still_requires_eligible_runtime(runtime, eligible):
    assert _managed_via_wrapper_for_runtime({"managed_via_wrapper": True}, runtime) is eligible
    assert _managed_via_wrapper_for_runtime({"managed_via_wrapper": [runtime.upper()]}, runtime) is eligible
    assert not _managed_via_wrapper_for_runtime({"managed_via_wrapper": []}, runtime)
    assert not _managed_via_wrapper_for_runtime({"managed_via_wrapper": runtime}, runtime)


@pytest.mark.parametrize("runtime, mode, config, stored, expected", [
    ("pi", "managed", {}, ["resume", "fixture"],
     ["resume", "fixture", "managed-run", "interrupt", "steer", "spawn"]),
    ("opencode", "resident", {}, ["steer", "fixture", "resident-run", "resume", "interrupt"],
     ["fixture", "resume"]),
    ("hermes", "managed", {"channelEnabled": "true"}, ["resume", "fixture"],
     ["resume", "fixture", "managed-run", "interrupt", "spawn", "steer"]),
    ("hermes", "managed", {}, ["steer", "fixture"],
     ["fixture", "managed-run", "resume", "interrupt", "spawn"]),
    ("hermes", "resident", {"gatewayUrl": " WSS://fixture.invalid/ws "}, ["fixture"],
     ["fixture", "resident-run", "resume", "interrupt", "steer"]),
    ("claude-code", "resident", {"channelEnabled": "true"}, ["resident-run", "fixture", "steer"], ["fixture"]),
    ("claude-code", "resident", {"channelEnabled": True}, ["fixture"],
     ["fixture", "resident-run", "interrupt", "steer"]),
])
def test_row_corrections_preserve_exact_capability_order(runtime, mode, config, stored, expected):
    row = {"runtime": runtime, "session_mode": mode, "runtime_config": json.dumps(config),
           "capabilities": json.dumps(stored)}
    assert _row_capabilities(row) == expected


def test_hermes_managed_default_steer_depends_on_channel_not_the_gateway_url():
    assert "steer" not in _default_capabilities_for("hermes", "managed", runtime_config={
        "gatewayUrl": "ws://fixture.invalid/ws",
    })
    assert "steer" in _default_capabilities_for("hermes", "managed", runtime_config={"channelEnabled": "true"})
