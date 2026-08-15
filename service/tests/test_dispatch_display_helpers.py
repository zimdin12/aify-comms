"""How a run is described back to a human: its dispatch state, its handoff subject, and clipping.

Three helpers on the display path, none named by a test. `_format_dispatch_state` in particular is
read on every `comms_agent_info` call — the first thing the troubleshooting skill tells an agent to
look at — so a wrong field here starts every diagnosis from a wrong picture.

None of them can raise in a way anyone would notice. They produce strings and dicts that look
right.
"""
from __future__ import annotations

from service.api_core.dispatch_text import _auto_handoff_subject_for_run, _format_dispatch_state
from service.api_core.serialization import _clip_text


class Row(dict):
    def __getitem__(self, key):
        return dict.get(self, key, "")


def run_row(**over):
    base = {
        "id": "run-1",
        "status": "running",
        "subject": "Ship the thing",
        "from_agent": "manager-bot",
        "dispatch_mode": "active",
        "execution_mode": "channel",
        "runtime": "claude-code",
        "claim_bridge_id": "bridge-9",
        "requested_at": "2026-08-16T00:00:00Z",
        "claimed_at": "2026-08-16T00:00:05Z",
        "started_at": "2026-08-16T00:00:07Z",
    }
    base.update(over)
    return Row(base)


# ── _format_dispatch_state ───────────────────────────────────────────────────────────────────
def test_no_active_run_is_a_complete_shape_not_a_missing_key():
    """Callers read `hasActiveRun` and `activeRun` unconditionally; an absent key would be a
    KeyError on the quietest path — an idle agent."""
    state = _format_dispatch_state(None, 0)
    assert state == {"hasActiveRun": False, "activeRun": None, "queuedRuns": 0}


def test_an_active_run_is_fully_described():
    state = _format_dispatch_state(run_row(), 2)
    assert state["hasActiveRun"] is True
    assert state["queuedRuns"] == 2
    assert state["activeRun"] == {
        "runId": "run-1",
        "status": "running",
        "subject": "Ship the thing",
        "from": "manager-bot",
        "dispatchMode": "active",
        "executionMode": "channel",
        "runtime": "claude-code",
        "claimBridgeId": "bridge-9",
        "requestedAt": "2026-08-16T00:00:00Z",
        "startedAt": "2026-08-16T00:00:07Z",
    }


def test_started_at_falls_back_to_the_claim_time():
    """A claimed-but-not-yet-started run reports WHEN IT WAS CLAIMED rather than an empty string —
    otherwise a run that is visibly in flight shows no start time at all."""
    state = _format_dispatch_state(run_row(started_at=""), 0)
    assert state["activeRun"]["startedAt"] == "2026-08-16T00:00:05Z"


def test_neither_timestamp_is_an_empty_string_not_none():
    """These are concatenated into UI text; a None would render as "None"."""
    state = _format_dispatch_state(run_row(started_at="", claimed_at="", requested_at=None), 0)
    assert state["activeRun"]["startedAt"] == ""
    assert state["activeRun"]["requestedAt"] == ""


def test_an_absent_execution_mode_defaults_to_managed():
    """A ROUTING DEFAULT, not a cosmetic one — this value tells a reader how the run is delivered, so
    the blank case must name the mode the system actually falls back to."""
    assert _format_dispatch_state(run_row(execution_mode=""), 0)["activeRun"]["executionMode"] == "managed"
    assert _format_dispatch_state(run_row(execution_mode=None), 0)["activeRun"]["executionMode"] == "managed"


def test_the_other_optional_fields_blank_rather_than_defaulting():
    state = _format_dispatch_state(run_row(dispatch_mode="", runtime=None, claim_bridge_id=None), 0)
    assert state["activeRun"]["dispatchMode"] == ""
    assert state["activeRun"]["runtime"] == ""
    assert state["activeRun"]["claimBridgeId"] == ""


def test_the_queued_count_is_clamped_and_coerced():
    """A negative count would render as "-1 queued". The clamp is the reason it cannot."""
    for value, expected in ((0, 0), (5, 5), (-1, 0), (-99, 0), (None, 0), ("", 0), ("3", 3), (2.9, 2)):
        assert _format_dispatch_state(None, value)["queuedRuns"] == expected, value


# ── _auto_handoff_subject_for_run ────────────────────────────────────────────────────────────
def test_a_completed_run_is_a_reply_subject():
    assert _auto_handoff_subject_for_run(run_row(status="completed")) == "Re: Ship the thing"


def test_failure_and_cancellation_are_flagged_in_the_subject():
    """The sender sees this line in their inbox; a failed run that reads like a normal reply hides
    the failure until the body is opened."""
    assert _auto_handoff_subject_for_run(run_row(status="failed")) == "[FAILED] Ship the thing"
    assert _auto_handoff_subject_for_run(run_row(status="cancelled")) == "[CANCELLED] Ship the thing"
    assert _auto_handoff_subject_for_run(run_row(status="FAILED")) == "[FAILED] Ship the thing"
    assert _auto_handoff_subject_for_run(run_row(status="  failed  ")) == "[FAILED] Ship the thing"


def test_a_missing_subject_falls_back_to_the_run_id_then_to_a_constant():
    """Never an empty subject line: the run id is at least identifying, and the constant is at least
    a sentence."""
    assert _auto_handoff_subject_for_run(run_row(subject="")) == "Re: run-1"
    assert _auto_handoff_subject_for_run(run_row(subject="", id="")) == "Re: dispatch result"
    assert _auto_handoff_subject_for_run(run_row(subject="   ")) == "Re: ", (
        "CHARACTERIZATION: a whitespace-only subject is TRUTHY, so it wins the `or` chain ahead of "
        "both fallbacks and only then strips to nothing — yielding 'Re: ' with a dangling space "
        "instead of 'Re: run-1'. Recorded, not endorsed; stripping before the chain would fix it."
    )


def test_no_row_at_all_still_produces_a_subject():
    assert _auto_handoff_subject_for_run(None) == "Re: dispatch result"


# ── _clip_text ───────────────────────────────────────────────────────────────────────────────
def test_text_within_the_limit_is_returned_stripped_and_unchanged():
    assert _clip_text("hello") == "hello"
    assert _clip_text("  hello  ") == "hello"
    assert _clip_text("") == ""
    assert _clip_text(None) == ""


def test_longer_text_is_clipped_to_exactly_the_limit_including_the_ellipsis():
    clipped = _clip_text("x" * 100, 10)
    assert len(clipped) == 10, "the ellipsis is INSIDE the budget, not added to it"
    assert clipped.endswith("…")
    assert clipped == "x" * 9 + "…"


def test_the_boundary_does_not_clip():
    assert _clip_text("x" * 10, 10) == "x" * 10, "exactly at the limit is not too long"
    assert _clip_text("x" * 11, 10).endswith("…")


def test_trailing_space_before_the_ellipsis_is_removed():
    assert _clip_text("abcdefgh ij", 10) == "abcdefgh…", "no 'abcdefgh …' with a floating space"


def test_a_zero_limit_still_yields_one_character():
    """CHARACTERIZATION. `max(limit - 1, 0)` protects the slice, but the ellipsis is appended
    unconditionally, so clipping to 0 returns "…" — one character over a limit of none. Harmless at
    every call site (no caller passes 0) and recorded so it is a known oddity rather than a
    surprise."""
    assert _clip_text("hello", 0) == "…"
    assert _clip_text("hello", 1) == "…"


def test_a_non_string_is_coerced_rather_than_raising():
    assert _clip_text(12345) == "12345"
    assert _clip_text(0) == "", "0 is falsy, so it renders as empty rather than as \"0\""
