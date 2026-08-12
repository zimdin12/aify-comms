"""Regression: _default_capabilities_for derives the capability list from the
runtime adapter. Pi no longer claims `resident-run` capability because
PiAdapter.supports_resident == False.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.api_core.capabilities import _default_capabilities_for


def test_pi_resident_no_longer_advertises_resident_run():
    caps = _default_capabilities_for("pi", "resident", "session-x", {})
    assert "resident-run" not in caps, (
        f"pi resident must not advertise resident-run after Plan 2. caps={caps}"
    )


def test_pi_managed_still_advertises_managed_run_and_steer():
    caps = _default_capabilities_for("pi", "managed", "", {})
    assert "managed-run" in caps
    assert "steer" in caps
    assert "interrupt" in caps


def test_claude_resident_still_has_resident_run():
    # Plan 3 (#120): claude resident needs channelEnabled=True to get resident-run.
    caps = _default_capabilities_for("claude-code", "resident", "session-x", {"channelEnabled": True})
    assert "resident-run" in caps


def test_codex_managed_has_full_set():
    caps = _default_capabilities_for("codex", "managed", "", {})
    assert "managed-run" in caps
    assert "interrupt" in caps
    assert "steer" in caps


def test_opencode_managed_has_prompt_async_steer():
    # The managed controller injects through OpenCode's promptAsync endpoint.
    caps = _default_capabilities_for("opencode", "managed", "", {})
    assert "managed-run" in caps
    assert "interrupt" in caps
    assert "steer" in caps


def test_hermes_managed_steer_requires_wrapper_gateway_channel():
    assert "steer" not in _default_capabilities_for("hermes", "managed", "", {})
    assert "steer" in _default_capabilities_for(
        "hermes", "managed", "", {"channelEnabled": True},
    )
