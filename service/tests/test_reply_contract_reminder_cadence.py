"""How often a reply-contract reminder is the FULL one.

`test_reply_contract_state.py` exercises the contract state and the reminder gate; this covers the
cadence helper underneath it.

`_contract_reminder_is_full` decides FORMAT, never WHETHER: reminders do not back off, they get
cheaper between periodic full nudges. That distinction matters because the obvious misreading — that
this is a backoff — would turn a cadence knob into a silence knob.
"""
from __future__ import annotations

import pytest

from service.api_core.reply_contract import (
    _contract_reminder_full_every,
    _contract_reminder_is_full,
)
from service.api_core.settings import DEFAULT_SETTINGS


def settings(**over):
    merged = dict(DEFAULT_SETTINGS)
    merged.update(over)
    return merged


# ── the reminder cadence ─────────────────────────────────────────────────────────────────────
def test_every_reminder_is_full_when_the_cadence_is_one_or_less():
    for full_every in (1, 0, -5):
        cfg = settings(reply_reminder_full_every=full_every)
        assert all(_contract_reminder_is_full(n, settings=cfg) for n in range(1, 8)), full_every


def test_only_every_nth_reminder_is_full():
    cfg = settings(reply_reminder_full_every=3)
    full = [n for n in range(1, 10) if _contract_reminder_is_full(n, settings=cfg)]
    assert full == [3, 6, 9], "the 1-based ordinal decides, so the FIRST full nudge is the third one"


def test_the_in_between_reminders_still_fire_they_are_just_lighter():
    """FORMAT, not frequency. Reading this as a backoff would turn a cadence knob into a silence
    knob — `_contract_reminder_due` is the only thing that decides WHETHER a reminder is sent."""
    cfg = settings(reply_reminder_full_every=3)
    assert _contract_reminder_is_full(1, settings=cfg) is False
    assert _contract_reminder_is_full(2, settings=cfg) is False
    assert _contract_reminder_is_full(3, settings=cfg) is True


def test_an_unknown_ordinal_fails_safe_to_the_full_format():
    """A reminder whose number could not be determined gets the informative version — the reverse
    would send a one-liner referring to context the reader never received."""
    cfg = settings(reply_reminder_full_every=3)
    for ordinal in (0, -1, -99):
        assert _contract_reminder_is_full(ordinal, settings=cfg) is True, ordinal


@pytest.mark.parametrize("bad", ["", None, [], {}, 0])
def test_a_FALSY_cadence_setting_becomes_zero_which_reads_as_always_full(bad):
    """`... or 0` short-circuits before `int()` is ever called, so EVERY falsy value — including the
    empty list and dict, which look like they would raise — lands on 0 rather than on the except arm.
    Zero then means always-full via the `<= 1` branch. Partitioned by truthiness rather than by a
    hand-listed set, because my first attempt guessed the split and put `[]` on the wrong side."""
    assert _contract_reminder_full_every(settings(reply_reminder_full_every=bad)) == 0
    cfg = settings(reply_reminder_full_every=bad)
    assert all(_contract_reminder_is_full(n, settings=cfg) for n in range(1, 6))


@pytest.mark.parametrize("bad", ["not a number", "3.5", object()])
def test_a_TRUTHY_but_unparseable_cadence_falls_back_to_the_default(bad):
    """Only a truthy value reaches `int()` and can raise, which is the arm that restores the product
    default — a different landing point from the falsy case above, and the reason both are tested."""
    resolved = _contract_reminder_full_every(settings(reply_reminder_full_every=bad))
    assert resolved == int(DEFAULT_SETTINGS["reply_reminder_full_every"])


def test_a_negative_cadence_clamps_to_zero_rather_than_inverting_the_modulo():
    """`max(0, ...)` — without it a negative full_every would reach `n % -3`, whose sign follows the
    divisor in Python and would make the pattern unrecognisable."""
    assert _contract_reminder_full_every(settings(reply_reminder_full_every=-3)) == 0
    cfg = settings(reply_reminder_full_every=-3)
    assert all(_contract_reminder_is_full(n, settings=cfg) for n in range(1, 6))


def test_the_default_cadence_is_read_from_the_shared_settings():
    """Not restated here — a change to the product default must not need this test edited."""
    assert _contract_reminder_full_every({}) == int(DEFAULT_SETTINGS["reply_reminder_full_every"])
