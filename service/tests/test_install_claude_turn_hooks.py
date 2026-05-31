"""Static guards for the claude-aify turn-lifecycle hooks (install.sh).

Resident/managed claude has no native turn-end RPC (unlike codex turn/completed,
pi agent_end). The dashboard's "working" status is driven by turn_busy, which
these hooks set/refresh/clear via the claude Stop/UserPromptSubmit/PostToolUse
hooks. Operator-reported (2026-05-31): a working resident claude "shows working
sometimes" — root cause was turn_busy STALING mid-turn (set once at
UserPromptSubmit, never re-pulsed), so turns longer than TURN_BUSY_STALE_SECONDS
(120s) flipped off 'working'. The fix re-pulses /turn-start on PostToolUse.
"""
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"


@pytest.fixture(scope="module")
def install_text() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


def test_turn_start_hook_wires_userpromptsubmit_and_posttooluse(install_text: str):
    # turn-start must fire at turn START (UserPromptSubmit) AND re-pulse on every
    # tool call (PostToolUse) so turn_busy never stales mid-turn (task #134).
    assert "install_claude_turn_start_hook()" in install_text
    assert "wireTurnStart('UserPromptSubmit')" in install_text, "turn-start must wire UserPromptSubmit (turn start)"
    assert "wireTurnStart('PostToolUse')" in install_text, "turn-start must RE-PULSE on PostToolUse (task #134 fix)"
    assert "/api/v1/agents/${AIFY_AGENT_ID}/turn-start" in install_text


def test_turn_end_hook_wires_stop(install_text: str):
    # Stop is the authoritative turn-end → clears turn_busy immediately instead of
    # waiting out the 120s stale window.
    assert "install_claude_turn_end_hook()" in install_text
    assert "/api/v1/agents/${AIFY_AGENT_ID}/turn-end" in install_text


def test_turn_hooks_are_installed_for_claude(install_text: str):
    # Both hooks must actually be invoked in the claude install path.
    assert "install_claude_turn_start_hook" in install_text
    assert "install_claude_turn_end_hook" in install_text


def test_turn_hooks_noop_without_agent_or_url(install_text: str):
    # A plain `claude` (no aify wrapper) must be unaffected: the hook command
    # gates on both AIFY_AGENT_ID and AIFY_COMMS_URL being set.
    assert '[ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]' in install_text
