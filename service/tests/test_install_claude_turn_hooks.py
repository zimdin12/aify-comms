"""Static guards for the claude-aify turn-lifecycle hooks (install.sh).

Resident/managed claude has no native turn-end RPC (unlike codex turn/completed,
pi agent_end). The dashboard's "working" status is driven by turn_busy, which the
turn-START hook (UserPromptSubmit → /turn-start) sets and the turn-END hook (Stop
→ /turn-end) clears.

pure-event-status change #4 (2026-06-02): STATUS is now PURE-EVENT. The
PostToolUse turn_busy RE-PULSE was REMOVED — it re-armed turn_busy on every tool
call to hold status past the old short status window, which with a pure-event
model would defeat the turn-END event. turn_busy is now set ONLY at turn START
(UserPromptSubmit) and cleared ONLY by an event (the Stop hook fast-path, or the
bridge transcript turn-END detector — change #1) or the single long ceiling. So
the generated wrapper must wire UserPromptSubmit → /turn-start and Stop →
/turn-end, but must NOT wire PostToolUse → /turn-start.
"""
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"


@pytest.fixture(scope="module")
def install_text() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


def test_turn_start_hook_wires_userpromptsubmit_only_not_posttooluse(install_text: str):
    # pure-event-status #4: turn-start fires at turn START (UserPromptSubmit) ONLY.
    # The PostToolUse re-pulse is REMOVED — re-arming turn_busy on every tool call
    # would defeat the pure-event turn-END transition (status no longer relies on a
    # short window that needed re-pulsing).
    assert "install_claude_turn_start_hook()" in install_text
    assert "wireTurnStart('UserPromptSubmit')" in install_text, "turn-start must wire UserPromptSubmit (turn start)"
    assert "wireTurnStart('PostToolUse')" not in install_text, (
        "turn-start must NOT re-pulse on PostToolUse (pure-event #4 removes the window-defeat re-pulse)"
    )
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
