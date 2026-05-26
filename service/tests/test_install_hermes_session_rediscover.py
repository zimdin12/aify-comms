"""hermes-aify wrapper session-handle contract.

Fresh `hermes-aify` launches must not bind themselves to `session.most_recent`
from the dashboard gateway. That method reports historical DB state before the
visible TUI has attached, so using it as the resident handle registers the
agent against a session that cannot be visibly woken. Explicit `--resume <id>`
remains authoritative. Fresh launches rely on the TUI-written active-session
file once the visible session exists.

These are install.sh static-text smoke checks (no bash invocation) — same
pattern as test_install_hermes_prebuild.py's family. We can't easily spin
up a real hermes gateway in tests, so we pin the wrapper's emitted code
shape; the failure path is non-fatal and exercised live by the operator.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


def _read_install_sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_hermes_wrapper_does_not_rediscover_from_gateway_history():
    """Fresh hermes-aify must not export gateway session.most_recent as current."""
    text = _read_install_sh()
    assert "rediscover_hermes_session_id" not in text
    assert "HERMES_REDISCOVERED_SESSION_ID" not in text
    assert "[hermes-aify] session id rediscovered" not in text


def test_hermes_wrapper_exports_only_explicit_resume_handle_before_launch():
    """Only explicit --resume/--session-id should seed HERMES_SESSION_ID."""
    text = _read_install_sh()
    assert 'HERMES_EXPLICIT_SESSION_HANDLE="false"' in text
    idx = text.find('if [ "\\$HERMES_EXPLICIT_SESSION_HANDLE" = "true" ]')
    assert idx > 0
    window = text[idx : idx + 350]
    assert 'export HERMES_SESSION_ID="\\$HERMES_SESSION_HANDLE"' in window
    assert 'export AIFY_SESSION_HANDLE="\\$HERMES_SESSION_HANDLE"' in window
    assert 'if [ -n "$HERMES_SESSION_HANDLE" ]; then' not in text


def test_hermes_installer_patches_visible_session_bind():
    """Hermes resident delivery must bind to the open TUI session, not resume
    a hidden sid."""
    text = _read_install_sh()
    assert "patch_hermes_gateway_visible_bind" in text
    assert "aify.session.bind_transport" in text
    assert "TeeTransport(primary, bridge_transport)" in text


def test_hermes_visible_bind_falls_back_to_single_active_session():
    """If the saved handle is stale but this wrapper gateway has exactly one
    visible session, bind to that session instead of failing or forking hidden."""
    text = _read_install_sh()
    assert "visible session fallback: saved handle not active; using sole active session" in text
    assert "active_candidates" in text


def test_hermes_wrapper_exports_active_session_file():
    """The TUI active-session file lets the bridge repair stale parent env."""
    text = _read_install_sh()
    assert "HERMES_TUI_ACTIVE_SESSION_FILE" in text
    assert "AIFY_HERMES_ACTIVE_SESSION_FILE" in text


def test_hermes_installer_preserves_wrapper_active_session_file():
    """Hermes main.py must not overwrite HERMES_TUI_ACTIVE_SESSION_FILE."""
    text = _read_install_sh()
    assert "patch_hermes_tui_active_session_file" in text
    assert 'env.get("HERMES_TUI_ACTIVE_SESSION_FILE", "").strip()' in text
    assert "created_active_session_file" in text
