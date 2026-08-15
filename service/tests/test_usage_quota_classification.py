"""Which quota window is which, whose token is read, and when a percentage must read as unknown.

Three pure functions across the usage path, none named by a test. Every failure here produces a
NUMBER — plausible, precise, and wrong — which is the worst shape for a figure an operator uses to
decide whether a fleet can keep working.

  * `classify_windows` picks the 5-hour and weekly windows BY DURATION, never by position. Its
    docstring records the bug: `primary_window` is not always the 5-hour one, and on a plan whose
    only window is the weekly one (604800s), reading positionally published the weekly figure as
    "5h" — a five-hour window whose reset was six days out.
  * `_blank_elapsed_reset_windows` blanks a window whose own `resets_at` has already passed, because
    its percentage belongs to a cycle that already reset. This is the quota-display instance of the
    standing rule that no evidence must not read as a pass: an em-dash is honest, a stale 12% is not.
  * `_extract_token` walks an auth store for an OpenAI access token and must ignore the others.
    Codex and hermes share these files, so a looser match reads a different provider's credential.

NO REAL CREDENTIALS ANYWHERE HERE. The JWTs below are constructed in the test from a claims dict and
carry no signature — they exist to exercise the issuer check, and none of them authenticates to
anything.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from service.usage_cache import RESET_ELAPSED_GRACE_SECONDS, _blank_elapsed_reset_windows
from service.usage_openai import _extract_token, _is_openai_jwt, classify_windows

FIVE_HOURS = 5 * 3600
WEEK = 604800


def window(seconds, used=10):
    return {"limit_window_seconds": seconds, "used_percent": used}


def fake_jwt(issuer):
    """A syntactically valid, unsigned JWT carrying one claim. Not a credential."""
    payload = base64.urlsafe_b64encode(json.dumps({"iss": issuer}).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJub25lIn0.{payload}.signature-placeholder"


def iso(delta_seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat().replace("+00:00", "Z")


# ── classify_windows ─────────────────────────────────────────────────────────────────────────
def test_windows_are_classified_by_duration_not_position():
    """THE BUG. The weekly window arriving as `primary_window` must still be reported as weekly."""
    five, week = classify_windows({
        "primary_window": window(WEEK),
        "secondary_window": window(FIVE_HOURS),
    })
    assert five["limit_window_seconds"] == FIVE_HOURS
    assert week["limit_window_seconds"] == WEEK


def test_the_conventional_order_works_too():
    five, week = classify_windows({
        "primary_window": window(FIVE_HOURS),
        "secondary_window": window(WEEK),
    })
    assert five["limit_window_seconds"] == FIVE_HOURS
    assert week["limit_window_seconds"] == WEEK


def test_a_weekly_only_plan_reports_no_five_hour_window():
    """The `prolite` case from the docstring: one window, and it is the weekly one. Publishing it as
    "5h" is exactly the failure — a five-hour figure whose reset is six days out."""
    five, week = classify_windows({"primary_window": window(WEEK)})
    assert five is None, "no 5-hour window exists, so none may be reported"
    assert week["limit_window_seconds"] == WEEK


def test_a_five_hour_only_plan_reports_no_weekly_window():
    five, week = classify_windows({"primary_window": window(FIVE_HOURS)})
    assert five["limit_window_seconds"] == FIVE_HOURS
    assert week is None


def test_the_day_boundary_decides_which_bucket():
    """<= 86400 is the short bucket. A daily window is reported in the 5-hour slot rather than
    silently becoming a weekly figure."""
    five, week = classify_windows({"primary_window": window(86400)})
    assert five is not None and week is None
    five, week = classify_windows({"primary_window": window(86401)})
    assert five is None and week is not None


def test_the_first_of_each_duration_wins():
    five, _ = classify_windows({
        "primary_window": window(FIVE_HOURS, used=11),
        "secondary_window": window(3600, used=22),
    })
    assert five["used_percent"] == 11, "two short windows: the first is kept, not the last"


def test_nothing_usable_is_two_nones():
    for rate_limit in ({}, {"primary_window": None}, {"primary_window": "not a dict"}):
        assert classify_windows(rate_limit) == (None, None)


def test_windows_with_no_duration_fall_back_to_positional_order():
    """A LAST RESORT, and deliberately not a silent one: if no window declares a usable duration the
    original positional reading is used, because two unlabelled windows are better than nothing.
    Pinned so the fallback is a recorded decision rather than a surprise."""
    five, week = classify_windows({
        "primary_window": {"used_percent": 1},
        "secondary_window": {"used_percent": 2},
    })
    assert five == {"used_percent": 1}
    assert week == {"used_percent": 2}

    zero, none = classify_windows({"primary_window": window(0), "secondary_window": window(-5)})
    assert zero == window(0) and none == window(-5), "zero and negative durations take the same path"


# ── _blank_elapsed_reset_windows ─────────────────────────────────────────────────────────────
def test_a_window_whose_reset_has_passed_reads_as_unknown():
    out = _blank_elapsed_reset_windows({
        "five_hour": {"used_pct": 12, "resets_at": iso(-RESET_ELAPSED_GRACE_SECONDS - 60), "resets_in": 0},
        "weekly": {"used_pct": 40, "resets_at": iso(3600), "resets_in": 3600},
    })
    assert out["five_hour"]["used_pct"] is None, "a percentage from a cycle that already reset is not a fact"
    assert out["five_hour"]["resets_at"] is None
    assert out["five_hour"]["resets_in"] is None
    assert out["weekly"]["used_pct"] == 40, "the live window is untouched"


def test_the_whole_payload_is_flagged_when_anything_was_blanked():
    out = _blank_elapsed_reset_windows({
        "five_hour": {"used_pct": 12, "resets_at": iso(-RESET_ELAPSED_GRACE_SECONDS - 60)},
    })
    assert out["reset_elapsed"] is True
    assert out["stale"] is True
    assert out["unknown"] is True, "the caller must be able to tell the display it has no answer"


def test_a_live_payload_is_not_flagged():
    out = _blank_elapsed_reset_windows({
        "five_hour": {"used_pct": 12, "resets_at": iso(3600)},
        "weekly": {"used_pct": 40, "resets_at": iso(WEEK)},
    })
    assert "reset_elapsed" not in out and "stale" not in out and "unknown" not in out
    assert out["five_hour"]["used_pct"] == 12


def test_the_grace_window_stops_a_just_expired_reset_flapping():
    """Within the grace period the figure is still shown — a reset that landed seconds ago has not
    yet produced a new number, and blanking on the boundary would flicker the display."""
    out = _blank_elapsed_reset_windows({
        "five_hour": {"used_pct": 12, "resets_at": iso(-(RESET_ELAPSED_GRACE_SECONDS - 60))},
    })
    assert out["five_hour"]["used_pct"] == 12
    assert "reset_elapsed" not in out


def test_an_unparseable_or_missing_reset_is_left_alone():
    """`_age_seconds` returns None for these, and `_reset_elapsed` requires a real age — so an
    unreadable stamp does not blank a figure that may be perfectly good."""
    for stamp in (None, "", "not a date", 12345):
        out = _blank_elapsed_reset_windows({"five_hour": {"used_pct": 12, "resets_at": stamp}})
        assert out["five_hour"]["used_pct"] == 12
        assert "reset_elapsed" not in out


def test_only_pct_and_reset_keys_are_blanked():
    out = _blank_elapsed_reset_windows({
        "five_hour": {
            "used_pct": 12, "remaining_pct": 88, "resets_at": iso(-RESET_ELAPSED_GRACE_SECONDS - 60),
            "resets_in": 5, "plan": "prolite", "limit_window_seconds": FIVE_HOURS,
        },
    })
    band = out["five_hour"]
    assert band["used_pct"] is None and band["remaining_pct"] is None
    assert band["plan"] == "prolite", "identifying fields survive — only the numbers are unknown"
    assert band["limit_window_seconds"] == FIVE_HOURS


def test_a_missing_or_malformed_band_is_skipped():
    out = _blank_elapsed_reset_windows({"five_hour": None, "weekly": "nope", "other": 1})
    assert out["five_hour"] is None and out["weekly"] == "nope"
    assert "reset_elapsed" not in out


# ── _extract_token ───────────────────────────────────────────────────────────────────────────
def test_an_openai_token_is_found_at_any_depth():
    token = fake_jwt("https://auth.openai.com")
    assert _extract_token({"access_token": token}) == token
    assert _extract_token({"tokens": {"nested": {"access_token": token}}}) == token


def test_a_non_openai_token_is_ignored():
    """Codex and hermes share these auth files. A looser match reads another provider's credential."""
    for issuer in ("https://auth.anthropic.com", "https://nous.example", ""):
        found = _extract_token({"access_token": fake_jwt(issuer)})
        assert found == "", f"issuer {issuer!r} must not satisfy the OpenAI check"


def test_the_issuer_test_is_a_SUBSTRING_match_not_a_host_match():
    """CHARACTERIZATION, not endorsement. `_is_openai_jwt` asks whether "openai.com" appears anywhere
    in the `iss` claim, so an issuer that merely CONTAINS it — `openai.com.evil.test` — satisfies the
    check and its token is returned as the OpenAI one.

    Not treated as an exploit and not fixed here: the auth file is local and written by codex/hermes
    themselves, so anyone who can choose its issuer already has local write access, and tightening an
    auth check to a host match risks rejecting a legitimate issuer whose format differs. Recorded
    because the function READS like a host check and is not one — if this ever needs to become one,
    this is the assertion to flip."""
    impostor = fake_jwt("openai.com.evil.test")
    assert _extract_token({"access_token": impostor}) == impostor
    assert _is_openai_jwt(fake_jwt("not-openai.com-really")) is True


def test_the_openai_token_is_found_alongside_others():
    mine = fake_jwt("https://auth.openai.com")
    data = {
        "anthropic": {"access_token": fake_jwt("https://auth.anthropic.com")},
        "openai": {"access_token": mine},
    }
    assert _extract_token(data) == mine


def test_a_value_that_is_not_a_jwt_is_ignored():
    for value in ("", "not-a-jwt", "eyJhbGci", 12345, None, ["ey"]):
        assert _extract_token({"access_token": value}) == ""


def test_a_non_dict_store_yields_nothing():
    for data in (None, [], "string", 5):
        assert _extract_token(data) == ""


def test_the_issuer_check_needs_a_decodable_payload():
    assert _is_openai_jwt("ey.not-base64!.sig") is False
    assert _is_openai_jwt("eyJhbGciOiJub25lIn0") is False, "no payload segment at all"
    assert _is_openai_jwt(fake_jwt("https://auth.openai.com")) is True
