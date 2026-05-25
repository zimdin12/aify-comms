"""Plan 6 B2 — codex-aify wrapper rediscovers the real thread id.

After `codex app-server` is reachable, the wrapper should learn the most
recent codex thread id (the runtime's authoritative session handle) and
export it as CODEX_THREAD_ID / AIFY_SESSION_HANDLE before exec'ing codex.
This guards against stale CODEX_THREAD_ID env vars inherited from prior
sessions — the same drift class Plan 6 A1/A2 fixed on the bridge side.

The actual implementation does a filesystem scan of ~/.codex/sessions —
mirroring the Python adapter's `discover_session_id` (codex's app-server
has no introspection RPC at present; if/when one ships we can swap the
strategy and the env-export contract still holds).

These are static-text smoke checks on install.sh — no bash exec.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


def _read_install_sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_codex_wrapper_defines_rediscover_helper():
    """install.sh codex branch must define rediscover_codex_thread_id."""
    text = _read_install_sh()
    assert "rediscover_codex_thread_id" in text, (
        "Plan 6 B2: install.sh codex branch must define "
        "rediscover_codex_thread_id helper"
    )


def test_codex_wrapper_scans_sessions_dir():
    """The helper must consult ~/.codex/sessions to find the newest thread."""
    text = _read_install_sh()
    # The helper scans for rollout-*.jsonl files. The Python adapter at
    # service/runtimes/codex.py uses the same shape.
    assert ".codex/sessions" in text, (
        "Plan 6 B2: codex rediscover must scan ~/.codex/sessions"
    )


def test_codex_wrapper_overwrites_thread_env_after_rediscover():
    """After rediscover yields a non-empty id, the wrapper must overwrite
    CODEX_THREAD_ID and AIFY_SESSION_HANDLE."""
    text = _read_install_sh()
    idx = text.find("rediscover_codex_thread_id")
    assert idx > 0
    later = text[idx:]
    assert "export CODEX_THREAD_ID=" in later, (
        "Plan 6 B2: wrapper must `export CODEX_THREAD_ID=...` from rediscover"
    )
    assert "export AIFY_SESSION_HANDLE=" in later, (
        "Plan 6 B2: wrapper must `export AIFY_SESSION_HANDLE=...` from rediscover"
    )


def test_codex_wrapper_rediscover_is_non_fatal():
    """Empty rediscover must NOT abort — bridge heartbeat (A1) is the safety net."""
    text = _read_install_sh()
    idx = text.find("CODEX_REDISCOVERED_THREAD_ID")
    assert idx > 0, "Plan 6 B2: wrapper must capture rediscover output"
    window = text[idx : idx + 800]
    assert "if [ -n " in window, (
        "Plan 6 B2: rediscover must be optional — wrapper must gate on "
        "non-empty result, not abort on failure"
    )


def test_codex_wrapper_respects_explicit_resume_handle():
    """When the operator passed --resume <id>, that handle takes priority —
    rediscover should NOT clobber an explicit resume choice."""
    text = _read_install_sh()
    idx = text.find("CODEX_REDISCOVERED_THREAD_ID")
    assert idx > 0
    window = text[idx : idx + 800]
    # The rediscover-export block must inspect CODEX_RESUME_HANDLE — either
    # by skipping export when it's set, or by being placed after the
    # resume-resolution block. We pin: the rediscover override is gated on
    # an empty CODEX_RESUME_HANDLE.
    assert "CODEX_RESUME_HANDLE" in window, (
        "Plan 6 B2: rediscover must respect operator-provided --resume handle"
    )
