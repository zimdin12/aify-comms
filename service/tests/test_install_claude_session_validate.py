"""Plan 6 B4 — claude-aify validates env handle against on-disk transcript.

Unlike hermes/codex/pi (which query a live runtime), claude has no probe
endpoint — but its session id maps 1:1 to a JSONL transcript at
~/.claude/projects/<encoded-cwd>/<id>.jsonl. If CLAUDE_SESSION_ID is set
but no matching file exists, the env value is stale (the operator's prior
session was GC'd, or they cd'd into a different project). Unset before
exec'ing claude so the runtime creates a fresh session and the bridge's
discover (Plan 4) picks up the new id on the first heartbeat.

These are static-text smoke checks on install.sh — no bash exec.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


def _read_install_sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_claude_wrapper_defines_validate_helper():
    """install.sh claude branch must define validate_claude_session_id."""
    text = _read_install_sh()
    assert "validate_claude_session_id" in text, (
        "Plan 6 B4: install.sh claude branch must define "
        "validate_claude_session_id helper"
    )


def test_claude_wrapper_checks_projects_directory():
    """The validator must consult ~/.claude/projects/<encoded-cwd>/<id>.jsonl."""
    text = _read_install_sh()
    assert ".claude/projects" in text, (
        "Plan 6 B4: validator must consult ~/.claude/projects/..."
    )


def test_claude_wrapper_unsets_stale_session_id():
    """When the transcript is missing the wrapper must `unset CLAUDE_SESSION_ID`
    (and CLAUDE_RESUME_ID) so claude creates a fresh session."""
    text = _read_install_sh()
    idx = text.find("validate_claude_session_id")
    assert idx > 0
    later = text[idx:]
    assert "unset CLAUDE_SESSION_ID" in later, (
        "Plan 6 B4: wrapper must `unset CLAUDE_SESSION_ID` when transcript is missing"
    )


def test_claude_wrapper_validate_is_non_fatal():
    """A missing transcript must NOT abort — claude will create a fresh
    session on its own, and the bridge heartbeat (A1) catches up."""
    text = _read_install_sh()
    idx = text.find("validate_claude_session_id")
    assert idx > 0
    later = text[idx:]
    # The validate call must be a soft guard — no `exit 1` between the
    # call site and the `unset CLAUDE_SESSION_ID` cleanup.
    call_idx = later.find("validate_claude_session_id \"")
    if call_idx < 0:
        # Alternative spelling without explicit quote — search for the
        # call inside the wrapper body (the helper definition will be
        # the first occurrence, then call sites follow).
        call_idx = later.find("if ", later.find("\n", later.find("validate_claude_session_id() {")))
    assert call_idx > 0, "wrapper must call validate_claude_session_id"
    window = later[call_idx : call_idx + 400]
    assert "exit 1" not in window, (
        "Plan 6 B4: validate must be non-fatal — no `exit 1` near the call"
    )
