"""Tests that CALL `_contract_state` and `_contract_reminder_due` — is a reply still owed, and nudge?

These two are pure and take an injectable clock, and until now no test named either. That is the
wrong gap to have here: this module is the CONTRACT, and its own docstring records that the strand
bugs in this subsystem have historically come from delivery paths disagreeing about what closes one.
`_message_satisfies_reply_contract` (what closes a contract) was already covered; whether one is
currently OPEN, what state it is in, and whether a reminder is due were not.

The failure mode is silent in both directions. Report `answered` for a run nobody answered and the
reply is stranded with nothing chasing it. Report `overdue` for a closed one and an idle agent is
nudged forever.

A dict stands in for a sqlite3.Row throughout: every field these functions read goes through
`row["x"]` and `"x" in row.keys()`, both of which a dict satisfies.
"""
from __future__ import annotations

import time

import pytest

from service.api_core.reply_contract import _contract_reminder_due, _contract_state
from service.api_core.settings import DEFAULT_SETTINGS

NOW = 1_800_000_000.0  # a fixed epoch; nothing here may depend on the wall clock


def iso(epoch_s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_s))


def row(**over):
    """A complete contract row. `from_agent`/`target_agent` are ALWAYS supplied -- see the test at the
    bottom, which pins the fact that they are the only two fields read without a `keys()` guard."""
    base = {
        "requested_at": iso(NOW - 60),  # one minute old: not yet overdue at the 10-minute default
        "status": "queued",
        "result_message_id": "",
        "require_reply": 1,
        "reminder_count": 0,
        "source_read_at": "",
        "from_agent": "manager-bot",
        "target_agent": "sc-coder",
        "message_source": "direct",
        "summary": "",
        "last_reminder_at": "",
    }
    base.update(over)
    return base


def settings(**over):
    merged = dict(DEFAULT_SETTINGS)
    merged.update(over)
    return merged


def state(**over):
    return _contract_state(row(**over), settings=settings(), now_s=NOW)


# ── the state ladder, in precedence order ────────────────────────────────────────────────────
def test_a_result_message_means_answered_whatever_the_status_says():
    for status in ("queued", "claimed", "running", "completed", "failed", "cancelled"):
        assert state(status=status, result_message_id="msg-9")["state"] == "answered"


def test_failed_and_cancelled_are_failed_not_missing_reply():
    assert state(status="failed")["state"] == "failed"
    assert state(status="cancelled")["state"] == "failed"


def test_completed_with_a_reply_still_owed_is_missing_reply():
    """THE STRAND SIGNAL. The run finished, nothing was threaded back, and a reply was required."""
    assert state(status="completed", require_reply=1)["state"] == "missing_reply"


def test_completed_with_no_reply_owed_is_closed():
    assert state(status="completed", require_reply=0)["state"] == "closed"


def test_in_flight_and_waiting_states():
    assert state(status="claimed")["state"] == "working"
    assert state(status="running")["state"] == "working"
    assert state(status="queued")["state"] == "queued"
    # `seen` needs a read receipt; without one an unrecognised status falls to `sent`.
    assert state(status="", source_read_at=iso(NOW - 30))["state"] == "seen"
    assert state(status="")["state"] == "sent"


def test_delivered_is_not_a_named_status_and_falls_through_to_seen_or_sent():
    """`delivered` is a real dispatch status but is not one of the branches, so it is decided by the
    read receipt alone. Recorded because it is surprising, not because it is wrong: a delivered run
    awaiting a reply is exactly the case `decideRepulse` on the bridge side also has to special-case."""
    assert state(status="delivered")["state"] == "sent"
    assert state(status="delivered", source_read_at=iso(NOW - 30))["state"] == "seen"


# ── overdue ──────────────────────────────────────────────────────────────────────────────────
def test_overdue_needs_all_four_conditions():
    old = iso(NOW - 3600)
    assert state(requested_at=old)["state"] == "overdue"
    assert state(requested_at=old)["overdue"] is True

    # ...and each condition alone suppresses it.
    assert state(requested_at=old, require_reply=0)["overdue"] is False, "no reply expected"
    assert state(requested_at=old, result_message_id="m")["overdue"] is False, "already answered"
    for terminal in ("completed", "failed", "cancelled"):
        assert state(requested_at=old, status=terminal)["overdue"] is False, terminal
    assert state()["overdue"] is False, "one minute old is not overdue at a 10-minute threshold"


def test_overdue_fires_exactly_at_the_threshold_not_after():
    exactly = _contract_state(
        row(requested_at=iso(NOW - 10 * 60)), settings=settings(reply_reminder_minutes=10), now_s=NOW
    )
    assert exactly["overdue"] is True, "the comparison is >=, so the boundary minute counts"
    just_under = _contract_state(
        row(requested_at=iso(NOW - 10 * 60 + 30)), settings=settings(reply_reminder_minutes=10), now_s=NOW
    )
    assert just_under["overdue"] is False


def test_a_missing_requested_at_is_age_zero_and_never_overdue():
    """An unparseable timestamp must not read as infinitely old -- that would nudge every broken row."""
    for bad in ("", "not a date", None):
        result = _contract_state(row(requested_at=bad), settings=settings(), now_s=NOW)
        assert result["ageMinutes"] == 0.0
        assert result["overdue"] is False


def test_reminder_minutes_degenerate_values():
    old = iso(NOW - 90)
    # 0 and "" are falsy, so the `or DEFAULT` arm restores the 10-minute default rather than making
    # every contract instantly overdue.
    for zeroish in (0, "", None):
        result = _contract_state(row(requested_at=old), settings=settings(reply_reminder_minutes=zeroish), now_s=NOW)
        assert result["overdue"] is False, f"{zeroish!r} must fall back to the default, not to zero"
    # A negative is clamped to 1 minute by `max(1, ...)`, so a 90-second-old contract IS overdue.
    result = _contract_state(row(requested_at=old), settings=settings(reply_reminder_minutes=-5), now_s=NOW)
    assert result["overdue"] is True


# ── category and actionable ──────────────────────────────────────────────────────────────────
def test_a_self_wake_is_never_actionable():
    same = state(from_agent="sc-coder", target_agent="sc-coder", requested_at=iso(NOW - 3600))
    assert same["category"] == "self_wake"
    assert same["replyExpected"] is True
    assert same["overdue"] is True, "it is still overdue -- the state is honest"
    assert same["actionable"] is False, "but nobody is chased about a message they sent themselves"


def test_channel_source_wins_over_self_wake():
    """Order matters: category is set to self_wake/direct first and then OVERWRITTEN by the source."""
    result = state(from_agent="a", target_agent="a", message_source="channel")
    assert result["category"] == "channel"
    assert result["actionable"] is True, "which makes a self-addressed channel message actionable"


def test_direct_with_a_reply_owed_is_actionable():
    assert state()["actionable"] is True
    assert state(require_reply=0)["actionable"] is False
    assert state(result_message_id="m")["actionable"] is False


def test_operator_closed_contract_expects_no_reply():
    """The dashboard's Work Loop close is recognised by all three of status, flag and summary."""
    closed = row(
        status="completed",
        require_reply=0,
        summary="Closed from Work Loop by dashboard operator. Reason: superseded",
        requested_at=iso(NOW - 3600),
    )
    result = _contract_state(closed, settings=settings(), now_s=NOW)
    assert result["replyExpected"] is False
    assert result["overdue"] is False
    assert result["state"] == "closed"


# ── the reminder decision ────────────────────────────────────────────────────────────────────
OVERDUE = dict(requested_at=iso(NOW - 3600))


def due(**over):
    merged = dict(OVERDUE)
    merged.update({k: v for k, v in over.items() if k not in ("settings_over", "ignore_repeat")})
    return _contract_reminder_due(
        row(**merged),
        settings=settings(**over.get("settings_over", {})),
        now_s=NOW,
        ignore_repeat=over.get("ignore_repeat", False),
    )


def test_an_overdue_contract_is_due_and_carries_no_reason():
    assert due() == (True, "")


def test_the_feature_switch_beats_everything():
    ok, why = due(settings_over={"reply_contracts_enabled": False})
    assert ok is False
    assert "disabled" in why


def test_a_contract_that_is_not_overdue_reports_its_state_as_the_reason():
    ok, why = _contract_reminder_due(row(), settings=settings(), now_s=NOW)
    assert ok is False
    assert why == "contract state is queued", "the reason names the state, which is what makes it debuggable"


def test_max_count_stops_the_nudging():
    ok, why = due(reminder_count=3, settings_over={"reply_reminder_max_count": 3})
    assert ok is False
    assert "max reminders reached (3/3)" in why
    assert due(reminder_count=2, settings_over={"reply_reminder_max_count": 3})[0] is True

    # 0 means NO CAP rather than "never remind" -- the opposite reading would silence every reminder.
    assert due(reminder_count=99, settings_over={"reply_reminder_max_count": 0})[0] is True


def test_a_recent_reminder_suppresses_the_next_one():
    ok, why = due(last_reminder_at=iso(NOW - 60))
    assert ok is False
    assert "less than 10 minutes ago" in why
    assert due(last_reminder_at=iso(NOW - 3600))[0] is True, "an old reminder does not suppress"


def test_ignore_repeat_overrides_the_spacing_but_not_the_cap():
    assert due(last_reminder_at=iso(NOW - 60), ignore_repeat=True)[0] is True
    assert due(
        last_reminder_at=iso(NOW - 60),
        ignore_repeat=True,
        reminder_count=3,
        settings_over={"reply_reminder_max_count": 3},
    )[0] is False, "ignore_repeat is about spacing only -- the cap still holds"


def test_an_unparseable_last_reminder_does_not_suppress():
    """A bad timestamp must fail toward sending. Suppressing on it would silence the run forever."""
    assert due(last_reminder_at="not a date")[0] is True


# ── the two unguarded fields ─────────────────────────────────────────────────────────────────
def test_from_agent_and_target_agent_are_read_without_a_keys_guard():
    """Every other field goes through `"x" in row.keys()`; these two do not, so a partial row raises.

    Characterization, not endorsement. In production the row always comes from
    `_contract_list_query`, which selects `r.*` from dispatch_runs, so both columns exist and this
    cannot fire. It is pinned because the inconsistency is invisible when reading the function --
    the line looks like the guarded ones around it -- and because anyone calling this with a
    hand-built row (a future reconciler, a dashboard preview) meets a KeyError, not a default.
    """
    partial = {"requested_at": iso(NOW), "status": "queued", "require_reply": 1}
    with pytest.raises(KeyError):
        _contract_state(partial, settings=settings(), now_s=NOW)


def test_a_missing_row_is_not_a_crash():
    """`None` is handled explicitly at every read, so the no-row case is a shape, not an exception."""
    result = _contract_state(None, settings=settings(), now_s=NOW)
    assert result["replyExpected"] is False
    assert result["overdue"] is False
    assert result["actionable"] is False
