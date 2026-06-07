"""A provider rate/usage-limit run failure produces a clear, sender-facing notice.

The auto-handoff already delivers a failure message to the sender on a require_reply run;
this ensures a provider throttle reads as "retry shortly, not your fault" instead of a raw
API error (2026-06-07).
"""

from service.routers.api_v2 import _is_provider_rate_limit_error, _auto_handoff_body_for_run


def test_detects_provider_limit_strings():
    for s in [
        "API Error: Server is temporarily limiting requests (not your usage limit)",
        "You've hit your limit · resets 3:00pm",
        "429 Too Many Requests",
        "Overloaded",
        "rate limit exceeded",
    ]:
        assert _is_provider_rate_limit_error(s), s


def test_ignores_ordinary_errors():
    for s in ["spawn claude ENOENT", "Run timed out", "session not found", ""]:
        assert not _is_provider_rate_limit_error(s), s


def _row(error_text, target="next-senior-dev", frm="cms-manager", status="failed"):
    return {
        "status": status, "from_agent": frm, "target_agent": target,
        "error_text": error_text, "summary": "",
    }


def test_rate_limit_failure_body_is_actionable_and_names_the_agent():
    body = _auto_handoff_body_for_run(_row("API Error: Server is temporarily limiting requests"))
    assert "next-senior-dev" in body
    assert "retry" in body.lower()
    assert "rate-limit" in body.lower() or "usage" in body.lower()


def test_ordinary_failure_body_unchanged():
    body = _auto_handoff_body_for_run(_row("spawn claude ENOENT"))
    assert "Auto-mirrored dispatch failure" in body
    assert "rate-limit" not in body.lower()
