"""What counts as an operator closing a contract, and how often a reminder is the FULL one.

Two rules that complete `reply_contract.py`'s coverage. `test_reply_contract_state.py` exercises the
contract state and the reminder gate; these are the two helpers underneath that nothing named.

`_is_operator_closed_contract` recognises the dashboard's Work Loop close, and it is a CONJUNCTION of
three independent signals — status, the require_reply flag, and a summary prefix. Its answer feeds
`_contract_reply_expected`, so a false positive silently stops chasing a reply that is genuinely owed,
and a false negative nags an operator about work they explicitly closed. Each of the three is broken
separately below, because the safety comes from all of them holding and a conjunction where one term
is decorative is a conjunction that will lose it in the next edit.

`_contract_reminder_is_full` decides FORMAT, never WHETHER: reminders do not back off, they get
cheaper between periodic full nudges. That distinction matters because the obvious misreading — that
this is a backoff — would turn a cadence knob into a silence knob.
"""
from __future__ import annotations

import pytest

from service.api_core.reply_contract import (
    _contract_reminder_full_every,
    _contract_reminder_is_full,
    _is_operator_closed_contract,
)
from service.api_core.settings import DEFAULT_SETTINGS

CLOSE_PREFIX = "Closed from Work Loop by dashboard operator."


class Row(dict):
    def __getitem__(self, key):
        return dict.get(self, key, "")


def closed_row(**over):
    base = {"status": "completed", "require_reply": 0, "summary": f"{CLOSE_PREFIX} Reason: superseded"}
    base.update(over)
    return Row(base)


def settings(**over):
    merged = dict(DEFAULT_SETTINGS)
    merged.update(over)
    return merged


# ── the operator close, and its three terms ──────────────────────────────────────────────────
def test_the_dashboard_close_is_recognised():
    assert _is_operator_closed_contract(closed_row()) is True


def test_a_bare_prefix_with_no_reason_still_counts():
    """The summary is matched by PREFIX, so the operator's free-text reason is theirs to write."""
    assert _is_operator_closed_contract(closed_row(summary=CLOSE_PREFIX)) is True
    assert _is_operator_closed_contract(closed_row(summary=f"  {CLOSE_PREFIX} anything at all  ")) is True


def test_the_status_term_is_load_bearing():
    """A run still in flight has not been closed by anyone, whatever its summary says."""
    for status in ("queued", "claimed", "running", "failed", "cancelled", ""):
        assert _is_operator_closed_contract(closed_row(status=status)) is False, status


def test_the_require_reply_term_is_load_bearing():
    """An operator close clears the reply requirement. A row that still demands one was not closed
    this way — treating it as closed would abandon a reply somebody is waiting for."""
    assert _is_operator_closed_contract(closed_row(require_reply=1)) is False


def test_the_summary_term_is_load_bearing():
    """This is the term that distinguishes an OPERATOR close from an ordinary completed run. Without
    it, every completed run with no reply owed would read as operator-closed."""
    for summary in ("", "Run completed.", "closed from work loop by dashboard operator.", "Done."):
        assert _is_operator_closed_contract(closed_row(summary=summary)) is False, repr(summary)


def test_an_ordinary_completed_run_is_not_an_operator_close():
    """The case the summary term exists to separate — and the commonest row this ever sees."""
    assert _is_operator_closed_contract(Row({"status": "completed", "require_reply": 0, "summary": ""})) is False


def test_status_is_compared_case_insensitively_and_trimmed():
    assert _is_operator_closed_contract(closed_row(status="  COMPLETED  ")) is True


def test_no_row_is_not_a_close():
    assert _is_operator_closed_contract(None) is False
    assert _is_operator_closed_contract(Row({})) is False


def test_missing_columns_read_as_absent_rather_than_raising():
    """These rows come from several different queries; a partial one must answer, not explode."""
    assert _is_operator_closed_contract(Row({"status": "completed"})) is False


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
