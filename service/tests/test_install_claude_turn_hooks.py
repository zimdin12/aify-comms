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
import re
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"


@pytest.fixture(scope="module")
def install_text() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


def test_turn_hooks_are_installed_for_claude(install_text: str):
    """Both hooks must actually be INVOKED in the claude install path.

    That was the stated intent, but the assertions could not check it: they looked for the bare token
    `install_claude_turn_start_hook`, which the FUNCTION DEFINITION also contains. Deleting the two
    call lines left the test green while the hooks were never installed — and an uninstalled
    turn-start hook is task #224, a still-working managed claude falling to `online` until someone
    opened its Console.

    A shell definition is `name() {` and a call is the bare name on its own line, so they are
    distinguishable; the token alone is not. Scoped to the `CLIENT = "claude"` branch as well, since
    a call from some other client's path would install claude's hooks for the wrong runtime.
    """
    for hook in ("install_claude_turn_start_hook", "install_claude_turn_end_hook"):
        assert re.search(rf"^{hook}\(\) \{{", install_text, re.MULTILINE), f"{hook} is not defined"
        calls = re.findall(rf"^[ \t]+{hook}[ \t]*$", install_text, re.MULTILINE)
        assert calls, (
            f"{hook} is DEFINED but never called. install.sh would run to completion, report "
            f"success, and leave the hook uninstalled — the failure this file exists for."
        )

    # install.sh has SEVERAL `CLIENT = "claude"` branches (an early one only copies assets), so the
    # calls must be sought in ALL of them rather than the first — scoping to `[0]` is how the first
    # draft of this check failed against correct code.
    branches = install_text.split('if [ "$CLIENT" = "claude" ]; then')[1:]
    assert branches, "no claude install branch found; repoint this test"
    bodies = [b.split('elif [ "$CLIENT" =', 1)[0] for b in branches]
    for hook in ("install_claude_turn_start_hook", "install_claude_turn_end_hook"):
        assert any(
            re.search(rf"^[ \t]+{hook}[ \t]*$", body, re.MULTILINE) for body in bodies
        ), f"{hook} is called somewhere, but not inside any `CLIENT = claude` branch"


def test_turn_hooks_noop_without_agent_or_url(install_text: str):
    # A plain `claude` (no aify wrapper) must be unaffected: the hook command
    # gates on both AIFY_AGENT_ID and AIFY_COMMS_URL being set.
    assert '[ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]' in install_text
