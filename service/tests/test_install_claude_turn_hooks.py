"""Static guards for the claude-aify turn-lifecycle hooks (install.sh).

Resident/managed claude has no native turn-end RPC (unlike codex turn/completed,
pi agent_end). The dashboard's "working" status is driven by turn_busy, which the
turn-START hook (UserPromptSubmit → /turn-start) sets and the turn-END hook (Stop
→ /turn-end) clears.

proof-based turn signal (2026-06-18): PostToolUse → /turn-start is wired AGAIN,
reversing pure-event #4 (2026-06-02). The re-assert (idempotent /turn-start on every
tool call) is required because (1) UserPromptSubmit does NOT fire for an MCP/channel-
woken managed turn, and (2) the Stop hook is not a clean once-per-turn signal — it
fires prematurely / around rate-limit retries, clearing turn_busy mid-work. Without a
mid-turn re-assert a still-working managed claude fell to `online` until the Console was
opened (task #224). It can NOT pin an idle agent: PostToolUse fires only on a real tool
call, so an idle agent re-asserts nothing and the Stop-hook turn-end stands. So the
generated wrapper must wire UserPromptSubmit AND PostToolUse → /turn-start, and Stop →
/turn-end.
"""
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"


@pytest.fixture(scope="module")
def install_text() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


def test_turn_start_hook_wires_userpromptsubmit_and_posttooluse(install_text: str):
    # proof-based turn signal (2026-06-18, reverses pure-event #4): turn-start fires at
    # turn START (UserPromptSubmit) AND re-asserts on every tool call (PostToolUse), so a
    # managed/channel-woken turn (no UserPromptSubmit) and a turn that survives a premature
    # Stop hook / rate-limit retry both keep reading `working`. The re-assert can't pin an
    # idle agent (no tool calls → no re-assert). See task #224.
    assert "install_claude_turn_start_hook()" in install_text
    assert "wireTurnStart('UserPromptSubmit')" in install_text, "turn-start must wire UserPromptSubmit (turn start)"
    assert "wireTurnStart('PostToolUse')" in install_text, (
        "turn-start must re-assert on PostToolUse (proof-based #224: channel-woken + premature-Stop coverage)"
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
