"""A run failed after an interrupt says so, instead of listing four possible causes.

`TURN_ENDED_WITHOUT_REPLY` is honest about not knowing -- it names a throttle, a provider refusal, a
mid-turn interrupt and a stall, and says the cause is undetermined. That is right when it is true.

It was not true here. aify-comms issues interrupts itself through `comms_interrupt`, and records each
one in `terminal_controls` with the action, the requester and the time. Observed 2026-08-25: a turn was
interrupted deliberately and the failure that followed still read "Cause NOT DETERMINED", which invites
a reader to go and investigate a model provider for something an operator had just done on purpose.

A cause we can look up must never be reported as a cause we could not determine.
"""
from __future__ import annotations

from service.api_core.authored_failures import (
    TURN_ENDED_WITHOUT_REPLY,
    is_service_authored,
    turn_interrupted,
)


def test_the_interrupted_reason_names_who_and_when():
    text = turn_interrupted("comms-tech-lead", "2026-08-25T02:49:40Z")
    assert "comms-tech-lead" in text
    assert "2026-08-25T02:49:40Z" in text
    assert "INTERRUPTED" in text


def test_it_says_there_is_nothing_upstream_to_chase():
    # The whole cost of the undetermined text: a reader goes looking for a throttle that never existed.
    text = turn_interrupted("dashboard", "now")
    assert "throttle" in text
    assert "nothing went wrong upstream" in text or "no throttle" in text


def test_an_unnamed_requester_still_produces_a_sentence():
    # Never "Turn was INTERRUPTED by  at ." -- a missing field must not leak punctuation.
    text = turn_interrupted("", "")
    assert "an operator" in text
    assert "  " not in text
    assert " at ." not in text


def test_it_is_recognised_as_service_authored():
    """The registry exists so a downstream classifier cannot read our guesses as provider facts.

    A new authored reason that is not registered is exactly the gap the registry was built to close --
    the previous incident had a throttle classifier matching '429' inside our own list of guesses.
    """
    assert is_service_authored(turn_interrupted("someone", "now"))
    assert is_service_authored(TURN_ENDED_WITHOUT_REPLY)


def test_it_survives_truncation_and_prefixing():
    # Consumers prefix a run id and clip for display; both happen on the notification path.
    full = turn_interrupted("comms-tech-lead", "2026-08-25T02:49:40Z")
    assert is_service_authored(f"run_123 failed: {full}")
    assert is_service_authored(full[:60])


def test_a_provider_error_is_still_not_ours():
    # The negative control. If everything looked service-authored the registry would be decoration.
    assert not is_service_authored("Error: 429 Too Many Requests from the model provider")
    assert not is_service_authored("")
