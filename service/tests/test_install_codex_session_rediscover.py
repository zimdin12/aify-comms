"""codex-aify wrapper session-handle contract.

Fresh `codex-aify` launches must not bind themselves to the newest historical
rollout under ~/.codex/sessions. That scan can only tell us "some old Codex
thread existed"; it cannot prove the freshly opened TUI is attached to that
thread. Explicit `--resume <id>` remains authoritative and must be exported so
the aify-comms bridge and Codex CLI agree on the chosen thread.

These are static-text smoke checks on install.sh — no bash exec.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


def _read_install_sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_codex_wrapper_does_not_rediscover_from_historical_sessions():
    """Fresh codex-aify must not export newest historical ~/.codex/sessions id."""
    text = _read_install_sh()
    assert "CODEX_REDISCOVERED_THREAD_ID" not in text
    assert "rediscover_codex_thread_id" not in text
    assert "thread id rediscovered" not in text


def test_codex_wrapper_exports_explicit_resume_handle():
    """When the operator passed --resume <id>, that exact handle is exported."""
    text = _read_install_sh()
    idx = text.find('else\n  # Explicit --resume <id> from operator wins.')
    assert idx > 0
    window = text[idx : idx + 500]
    assert 'export CODEX_THREAD_ID="$CODEX_RESUME_HANDLE"' in window
    assert 'export AIFY_SESSION_HANDLE="$CODEX_RESUME_HANDLE"' in window


def test_codex_wrapper_leaves_fresh_launch_handle_empty():
    """The no-resume branch must leave CODEX_THREAD_ID unset for fresh launches."""
    text = _read_install_sh()
    assert 'if [ -z "${CODEX_RESUME_HANDLE:-}" ]; then' in text
    idx = text.find('if [ -z "${CODEX_RESUME_HANDLE:-}" ]; then')
    window = text[idx : idx + 300]
    assert "Fresh codex-aify launch" in window
    assert "export CODEX_THREAD_ID" not in window
    assert "export AIFY_SESSION_HANDLE" not in window
