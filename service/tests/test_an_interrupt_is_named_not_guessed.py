"""A run failed after an interrupt says so, instead of listing four possible causes.

`TURN_ENDED_WITHOUT_REPLY` is honest about not knowing -- it names a throttle, a provider refusal, a
mid-turn interrupt and a stall, and says the cause is undetermined. That is right when it is true.

It was not true here. aify-comms issues interrupts itself through `comms_interrupt`, and records each
one in `dispatch_controls` with the action, the requester and the time. Observed 2026-08-25: a turn was
interrupted deliberately and the failure that followed still read "Cause NOT DETERMINED", which invites
a reader to go and investigate a model provider for something an operator had just done on purpose.

A cause we can look up must never be reported as a cause we could not determine.

WHAT THIS FILE DOES NOT PROVE, stated because for one afternoon it read as though it did. Every test
here exercises the pure reason-builder. The reconciler that CALLS it queried a table-and-action
combination nothing writes, so the feature could never fire -- and this file was green throughout,
because a builder works identically whether or not anything reaches it. Proving a producer and
calling the feature proven is the gap; `test_the_interrupt_reader_matches_a_writer.py` covers the
join, and the two are only meaningful together.
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


def test_the_undetermined_reason_says_whose_failure_it_is():
    """A reader supplied the subject themselves and got it wrong, at real cost.

    Reported 2026-08-27: an operator read `TURN_ENDED_WITHOUT_REPLY` and concluded the agent had
    died. It had not -- the worker was mid-way through a large rebuild and still going. They sent two
    dispatches asking where it had got to, interrupting exactly the work that needed uninterrupted
    attention.

    Nothing in the sentence was false. It described a RUN and never said so, and "the worker turn did
    not finish" invites exactly one inference about the worker. This module already refuses to state
    a CAUSE it cannot determine; the same discipline applies to the SUBJECT.

    Asserted as properties rather than as the exact sentence, so a rewrite that keeps the meaning
    does not have to edit a test -- and one that drops it does.
    """
    text = TURN_ENDED_WITHOUT_REPLY.lower()
    assert "run" in text, "the reason does not name what actually failed"
    assert "may still be" in text or "still be alive" in text, (
        "the reason does not say the worker may still be running, which is the inference a reader "
        "supplies for themselves when it is missing"
    )
    # And it names the cheap check, because "do not conclude that" without "check this instead" is
    # how a reader ends up guessing again.
    assert "comms_console_tail" in TURN_ENDED_WITHOUT_REPLY or "comms_agent_info" in TURN_ENDED_WITHOUT_REPLY, (
        "the reason does not name a way to find out whether the agent is alive"
    )


def test_it_is_still_service_authored_and_still_not_provider_evidence():
    """CONTROL on the rewrite: the two properties other code keys on must survive it.

    `is_service_authored` is provenance, not wording, so this holds by construction -- but the string
    grew a sentence and the classifier is what stops a service-authored guess being read as a
    provider's own error. Cheap to assert, and the failure would be silent.
    """
    assert is_service_authored(TURN_ENDED_WITHOUT_REPLY)
