"""Regression: _default_capabilities_for derives the capability list from the
runtime adapter. Pi no longer claims `resident-run` capability because
PiAdapter.supports_resident == False.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.api_core.capabilities import _default_capabilities_for


def test_pi_resident_no_longer_advertises_resident_run():
    caps = _default_capabilities_for("pi", "resident", "session-x", {})
    assert "resident-run" not in caps, (
        f"pi resident must not advertise resident-run after Plan 2. caps={caps}"
    )


def test_claude_resident_still_has_resident_run():
    # Plan 3 (#120): claude resident needs channelEnabled=True to get resident-run.
    caps = _default_capabilities_for("claude-code", "resident", "session-x", {"channelEnabled": True})
    assert "resident-run" in caps


@pytest.mark.parametrize("runtime", ["codex", "opencode", "pi"])
def test_managed_runtimes_advertise_managed_run_interrupt_and_steer(runtime):
    # OpenCode: the managed controller injects through its promptAsync endpoint.
    caps = _default_capabilities_for(runtime, "managed", "", {})
    assert "managed-run" in caps
    assert "interrupt" in caps
    assert "steer" in caps


def test_hermes_managed_steer_requires_wrapper_gateway_channel():
    assert "steer" not in _default_capabilities_for("hermes", "managed", "", {})
    assert "steer" in _default_capabilities_for(
        "hermes", "managed", "", {"channelEnabled": True},
    )
