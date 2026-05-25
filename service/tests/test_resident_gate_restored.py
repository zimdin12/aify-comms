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

from service.routers.api_v2 import _default_capabilities_for


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
