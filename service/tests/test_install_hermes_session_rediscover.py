"""Plan 6 B1 — hermes-aify wrapper rediscovers the real session id.

After the dashboard probe succeeds and the gateway token is captured, the
hermes-aify wrapper should query the gateway's `session.most_recent` JSON-RPC
to learn the actual current hermes session id. It must then overwrite
HERMES_SESSION_ID and AIFY_SESSION_HANDLE before exec'ing hermes — so the
inner aify-comms MCP bridge registers with the truthful id, not whatever
stale value the operator's shell inherited (e.g. an HERMES_SESSION_ID left
over from a prior hermes session that has long since been cycled).

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


def test_hermes_wrapper_defines_rediscover_helper():
    """install.sh hermes branch must define rediscover_hermes_session_id."""
    text = _read_install_sh()
    assert "rediscover_hermes_session_id" in text, (
        "Plan 6 B1: install.sh hermes branch must define "
        "rediscover_hermes_session_id helper"
    )


def test_hermes_wrapper_calls_session_most_recent_rpc():
    """The helper must call the gateway's session.most_recent JSON-RPC."""
    text = _read_install_sh()
    assert "session.most_recent" in text, (
        "Plan 6 B1: hermes wrapper must call gateway's session.most_recent RPC"
    )


def test_hermes_wrapper_overwrites_session_env_after_rediscover():
    """After rediscover yields a non-empty id, the wrapper must overwrite
    HERMES_SESSION_ID and AIFY_SESSION_HANDLE BEFORE exec'ing hermes."""
    text = _read_install_sh()
    rediscover_idx = text.find("rediscover_hermes_session_id")
    assert rediscover_idx > 0, "helper definition must appear in install.sh"
    # Find the place in the wrapper where rediscover is CALLED (after the
    # token-capture site), and verify HERMES_SESSION_ID is exported after.
    later = text[rediscover_idx:]
    assert "export HERMES_SESSION_ID=" in later, (
        "Plan 6 B1: wrapper must `export HERMES_SESSION_ID=...` from the "
        "rediscover result"
    )
    assert "export AIFY_SESSION_HANDLE=" in later, (
        "Plan 6 B1: wrapper must `export AIFY_SESSION_HANDLE=...` from the "
        "rediscover result"
    )


def test_hermes_wrapper_rediscover_is_non_fatal():
    """If rediscover returns empty, the wrapper must NOT abort — the
    bridge's discover-first heartbeat (Plan 6 A1) corrects drift within 60s.
    Smoke: the rediscover branch is gated on `if [ -n ...` rather than
    `|| exit`."""
    text = _read_install_sh()
    # The line that consumes the rediscover result must be gated on
    # non-empty; an unconditional `|| exit 1` would be the failure shape.
    idx = text.find("HERMES_REDISCOVERED_SESSION_ID")
    assert idx > 0, "Plan 6 B1: wrapper must capture rediscover output"
    window = text[idx : idx + 600]
    assert "if [ -n " in window, (
        "Plan 6 B1: rediscover must be optional — wrapper must gate on "
        "non-empty result, not abort on failure"
    )


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
