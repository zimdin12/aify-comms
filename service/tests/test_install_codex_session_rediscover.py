"""codex-aify wrapper session-handle contract.

Fresh `codex-aify` launches must not bind themselves to the newest historical
rollout under ~/.codex/sessions. That scan can only tell us "some old Codex
thread existed"; it cannot prove the freshly opened TUI is attached to that
thread. Explicit `--resume <id>` remains authoritative and must be exported so
the aify-comms bridge and Codex CLI agree on the chosen thread.

REWRITTEN 2026-08-19 (v0.6 Phase 2). These were "static-text smoke checks on install.sh — no bash
exec": they grepped the INSTALLER SOURCE. When the codex-aify body moved into
wrappers/codex-aify.sh.in they went red while the render was proven byte-identical — a location pin
breaks on a move and stays green on a defect. They now read the RENDERED wrapper, the artifact an
operator installs.

That also repairs the NEGATIVE assertions below, which are the more important half: "this string is
absent" was previously satisfied by reading a file the wrapper no longer lives in, so it would have
held no matter what the wrapper contained.
"""

from __future__ import annotations

from service.tests._launchers import launcher


def _rendered_wrapper() -> str:
    """The RENDERED codex-aify wrapper, the artifact an operator installs."""
    return launcher("codex")


def test_codex_wrapper_does_not_rediscover_from_historical_sessions():
    """Fresh codex-aify must not export newest historical ~/.codex/sessions id."""
    text = _rendered_wrapper()
    assert "CODEX_REDISCOVERED_THREAD_ID" not in text
    assert "rediscover_codex_thread_id" not in text
    assert "thread id rediscovered" not in text


def test_codex_wrapper_exports_explicit_resume_handle():
    """When the operator passed --resume <id>, that exact handle is exported."""
    text = _rendered_wrapper()
    idx = text.find('else\n  # Explicit --resume <id> from operator wins.')
    assert idx > 0
    window = text[idx : idx + 500]
    assert 'export CODEX_THREAD_ID="$CODEX_RESUME_HANDLE"' in window
    assert 'export AIFY_SESSION_HANDLE="$CODEX_RESUME_HANDLE"' in window


def test_codex_wrapper_leaves_fresh_launch_handle_empty():
    """The no-resume branch must leave CODEX_THREAD_ID unset for fresh launches."""
    text = _rendered_wrapper()
    assert 'if [ -z "${CODEX_RESUME_HANDLE:-}" ]; then' in text
    idx = text.find('if [ -z "${CODEX_RESUME_HANDLE:-}" ]; then')
    window = text[idx : idx + 300]
    assert "Fresh codex-aify launch" in window
    assert "export CODEX_THREAD_ID" not in window
    assert "export AIFY_SESSION_HANDLE" not in window
