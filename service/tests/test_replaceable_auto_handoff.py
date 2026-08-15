"""May a real reply overwrite the message already sitting on this run — or is that somebody's words?

When a dispatch run ends without an explicit reply, the service writes an AUTO-HANDOFF message on the
agent's behalf so the sender sees something. If a real reply arrives afterwards,
`_is_replaceable_auto_handoff_message` decides whether that placeholder may be replaced.

Both errors destroy information and neither raises:

  * saying YES about a message the service did not write OVERWRITES an agent's actual words;
  * saying NO about a genuine placeholder leaves the placeholder standing, so the sender reads
    "no explicit reply was recorded" while the real reply exists somewhere else.

The guard is a conjunction of FIVE field comparisons — body, subject, sender, recipient, and the
threading id — and its safety comes from ALL of them holding. Any one dropped and a real message
becomes replaceable. Each is therefore broken individually below, which is the only way to show that
none is decorative.

`_auto_handoff_body_for_run` and `_auto_handoff_subject_for_run` are called to BUILD the expected
values rather than hardcoded, so this stays a test of the comparison and not a second copy of the
message format.
"""
from __future__ import annotations

import pytest

from service.api_core.dispatch_text import _auto_handoff_body_for_run, _auto_handoff_subject_for_run
from service.api_core.reply_linking import _is_replaceable_auto_handoff_message


class Row(dict):
    def __getitem__(self, key):
        return dict.get(self, key, "")


def run(**over):
    base = {
        "id": "run-1",
        "status": "completed",
        "subject": "Ship the thing",
        "from_agent": "manager-bot",
        "target_agent": "sc-coder",
        "message_id": "msg-1",
        "summary": "did the thing",
        "error_text": "",
    }
    base.update(over)
    return Row(base)


def placeholder(replied_run, **over):
    """The message the service itself would have written for this run."""
    base = {
        "body": _auto_handoff_body_for_run(replied_run),
        "subject": _auto_handoff_subject_for_run(replied_run),
        "from_agent": replied_run["target_agent"],
        "to_agent": replied_run["from_agent"],
        "in_reply_to": replied_run["message_id"],
    }
    base.update(over)
    return Row(base)


# ── the placeholder IS replaceable ───────────────────────────────────────────────────────────
def test_the_message_the_service_would_have_written_is_replaceable():
    replied = run()
    assert _is_replaceable_auto_handoff_message(placeholder(replied), replied) is True


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_that_holds_for_every_run_outcome(status):
    """The body differs per outcome, so the comparison must be built from the run rather than from
    one remembered string."""
    replied = run(status=status)
    assert _is_replaceable_auto_handoff_message(placeholder(replied), replied) is True


def test_an_auto_mirrored_body_is_replaceable_on_its_prefix_alone():
    """The short circuit: anything the service mirrored is its own to replace, whatever else differs.
    This is what covers mirrored bodies whose exact text has since changed."""
    replied = run()
    mirrored = Row({
        "body": "Auto-mirrored dispatch failure because no explicit reply message was recorded.",
        "subject": "something else entirely",
        "from_agent": "somebody-else",
        "to_agent": "somebody-else",
        "in_reply_to": "unrelated",
    })
    assert _is_replaceable_auto_handoff_message(mirrored, replied) is True


def test_nothing_to_protect_is_replaceable():
    """No existing message, or no run to compare against — there is no one's words at stake."""
    assert _is_replaceable_auto_handoff_message(None, run()) is True
    assert _is_replaceable_auto_handoff_message(placeholder(run()), None) is True
    assert _is_replaceable_auto_handoff_message(None, None) is True


# ── each of the five comparisons is load-bearing ─────────────────────────────────────────────
def test_a_different_body_is_not_replaceable():
    replied = run()
    real = placeholder(replied, body="Actually I finished it, here is what I changed.")
    assert _is_replaceable_auto_handoff_message(real, replied) is False, (
        "an agent's real words must never be overwritten"
    )


def test_a_different_subject_is_not_replaceable():
    replied = run()
    assert _is_replaceable_auto_handoff_message(placeholder(replied, subject="Re: something else"), replied) is False


def test_a_different_sender_is_not_replaceable():
    """The placeholder is written AS the run's target agent. A message from anyone else is theirs."""
    replied = run()
    assert _is_replaceable_auto_handoff_message(placeholder(replied, from_agent="third-party"), replied) is False


def test_a_different_recipient_is_not_replaceable():
    replied = run()
    assert _is_replaceable_auto_handoff_message(placeholder(replied, to_agent="third-party"), replied) is False


def test_a_different_in_reply_to_is_not_replaceable():
    """The threading id ties the placeholder to THIS run. A message threaded elsewhere answers
    something else, even if every other field coincides."""
    replied = run()
    assert _is_replaceable_auto_handoff_message(placeholder(replied, in_reply_to="msg-other"), replied) is False


# ── the comparison's tolerances ──────────────────────────────────────────────────────────────
def test_surrounding_whitespace_on_the_compared_identities_is_ignored():
    """Subject, sender, recipient and threading id are all stripped on both sides, so a padded value
    from the database does not make a genuine placeholder unreplaceable."""
    replied = run()
    padded = placeholder(replied)
    for field in ("subject", "from_agent", "to_agent", "in_reply_to"):
        padded[field] = f"  {padded[field]}  "
    assert _is_replaceable_auto_handoff_message(padded, replied) is True


def test_the_body_is_compared_exactly():
    """Deliberately NOT stripped: the body is the content itself, and treating a body that merely
    resembles the placeholder as identical is how a real reply would get overwritten."""
    replied = run()
    assert _is_replaceable_auto_handoff_message(placeholder(replied, body=f" {_auto_handoff_body_for_run(replied)}"), replied) is False


def test_a_row_with_no_columns_at_all_is_falsy_and_takes_the_nothing_to_protect_branch():
    """An EMPTY row is falsy, so it never reaches the field comparison — it is treated the same as
    no message at all. Correct (there are no words to destroy) but worth pinning, because the
    obvious reading is that it falls through to the five comparisons and fails them."""
    assert _is_replaceable_auto_handoff_message(Row({}), run()) is True


def test_missing_columns_read_as_empty_rather_than_raising():
    """Every field is fetched through a `keys()` guard — a partial row is a real shape here, since
    these messages come from several different queries."""
    replied = run()
    partial = Row({"id": "msg-99"})  # truthy, but carries none of the compared columns
    assert _is_replaceable_auto_handoff_message(partial, replied) is False, (
        "a row that matches nothing is NOT replaceable — the safe direction"
    )
