"""Plan 6 B3 — pi-aify wrapper rediscovers the real session id.

The Phase-4 watchdog already curls /api/v1/agents/<id>/pi-session-state to
check `bridgeOwned`. The same response carries `sessionId` (per Plan 4).
Reuse that payload to overwrite PI_SESSION_ID / AIFY_SESSION_HANDLE so the
inner aify-comms MCP bridge registers with the truthful session id — not
whatever stale value the operator's shell inherited from a prior pi run.

These are static-text smoke checks on install.sh — no bash exec.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


def _read_install_sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_pi_wrapper_parses_session_id_from_watchdog():
    """The wrapper must parse `"sessionId":"<id>"` out of the watchdog body
    rather than making a second HTTP call."""
    text = _read_install_sh()
    # The pi-aify watchdog block already captures the response into
    # AIFY_WATCHDOG_BODY. Plan 6 B3 reuses that capture.
    assert "AIFY_WATCHDOG_BODY" in text, (
        "Plan 6 B3 (precondition): pi-aify must capture the watchdog body"
    )
    # The new extraction must read sessionId from the captured body.
    assert "sessionId" in text, (
        "Plan 6 B3: pi-aify must extract sessionId from the watchdog response"
    )


def test_pi_wrapper_overwrites_session_env_after_rediscover():
    """After parsing a sessionId, the wrapper must export PI_SESSION_ID
    and AIFY_SESSION_HANDLE from that value."""
    text = _read_install_sh()
    # Find the watchdog-body extraction site (after the body capture).
    body_idx = text.find('AIFY_WATCHDOG_BODY="$(curl')
    assert body_idx > 0
    after_body = text[body_idx:]
    # After the body capture there should be a PI_REDISCOVERED_SESSION_ID
    # extraction and the matching env exports.
    assert "PI_REDISCOVERED_SESSION_ID" in after_body, (
        "Plan 6 B3: wrapper must capture rediscovered session id"
    )
    # Window from the extraction onward should contain both exports.
    redisc_idx = after_body.find("PI_REDISCOVERED_SESSION_ID")
    later = after_body[redisc_idx:]
    assert "export PI_SESSION_ID=" in later, (
        "Plan 6 B3: wrapper must export PI_SESSION_ID from rediscover"
    )
    assert "export AIFY_SESSION_HANDLE=" in later, (
        "Plan 6 B3: wrapper must export AIFY_SESSION_HANDLE from rediscover"
    )


def test_pi_wrapper_rediscover_is_non_fatal():
    """Empty rediscover (no sessionId field, or pi not running) must NOT
    abort — the bridge's discover-first heartbeat (A1) corrects drift."""
    text = _read_install_sh()
    idx = text.find("PI_REDISCOVERED_SESSION_ID")
    assert idx > 0
    window = text[idx : idx + 600]
    assert "if [ -n " in window, (
        "Plan 6 B3: rediscover must be optional — wrapper must gate on "
        "non-empty result, not abort on failure"
    )


def test_pi_wrapper_does_not_make_second_http_call():
    """The rediscover step must REUSE the watchdog body — no second curl."""
    text = _read_install_sh()
    # Count curls inside the pi-aify wrapper heredoc — should be 1
    # (the existing watchdog) for the pi block. We approximate by
    # checking the watchdog block's vicinity doesn't sprout a new curl.
    body_idx = text.find('AIFY_WATCHDOG_BODY="$(curl')
    redisc_idx = text.find("PI_REDISCOVERED_SESSION_ID")
    assert body_idx > 0 and redisc_idx > 0
    # The rediscover extraction should come BETWEEN body capture and the
    # next major block (the OMP-bridge-owned check). Verify no extra
    # curl appears between the body capture and the rediscover.
    assert redisc_idx > body_idx, "rediscover must follow body capture"
    window = text[body_idx:redisc_idx]
    assert "curl" not in window or window.count("curl") <= 1, (
        "Plan 6 B3: rediscover must reuse AIFY_WATCHDOG_BODY — no second HTTP call"
    )
