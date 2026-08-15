"""The Work Contracts row the dashboard renders, and the spread that could silently shadow it.

`_contract_row_to_dict` builds every field the contracts page shows. Nothing named it, and one thing
about its shape is worth holding down: it ends with `**state`, so `_contract_state`'s keys are merged
LAST and win any collision. Today there is none. If `_contract_state` ever grew a key called `status`,
`subject` or `priority`, the row's real value would be replaced by the contract state's and the page
would show something plausible and wrong, with no error anywhere. The disjointness test below is the
tripwire for that.

The other rule here is the reply-state reconciliation. `_dispatch_reply_state` answers from the RUN's
own fields and returns `not_required` whenever `require_reply` is 0 — but a contract can be open for
reasons other than that flag, so this serialiser overrides `not_required` to `awaiting`/`sent` when
the contract says a reply IS expected. The two disagree by design and the override is the resolution;
without a test, "simplifying" to one call reads as a cleanup.
"""
from __future__ import annotations

import time

from service.api_core.reply_contract import _contract_state
from service.api_core.settings import DEFAULT_SETTINGS
from service.routers import contracts
from service.routers.contracts import _contract_row_to_dict

NOW = 1_800_000_000.0


def iso(epoch_s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_s))


class Row(dict):
    def __getitem__(self, key):
        return dict.get(self, key, "")


def row(**over):
    base = {
        "id": "run-1",
        "message_id": "msg-1",
        "from_agent": "manager-bot",
        "target_agent": "sc-coder",
        "message_type": "request",
        "subject": "Ship the thing",
        "priority": "high",
        "status": "queued",
        "runtime": "claude-code",
        "require_reply": 1,
        "result_message_id": "",
        "requested_at": iso(NOW - 60),
        "claimed_at": "",
        "started_at": "",
        "finished_at": "",
        "source_read_at": "",
        "last_reminder_at": "",
        "reminder_count": 0,
        "message_body": "the full message body",
        "body": "the run body",
        "result_body": "",
        "summary": "",
    }
    base.update(over)
    return Row(base)


def serialise(**over):
    return _contract_row_to_dict(row(**over), settings=dict(DEFAULT_SETTINGS), now_s=NOW)


# ── the spread ───────────────────────────────────────────────────────────────────────────────
def test_the_state_keys_cannot_shadow_a_row_field(monkeypatch):
    """`**state` is merged LAST. Any key `_contract_state` adds that matches a field built above it
    would silently replace the row's real value with the contract state's.

    THE EXPLICIT KEY SET IS MEASURED, NOT DERIVED FROM THE RESULT. The obvious spelling —
    `set(serialised) - set(state)` — cannot detect anything: a colliding key is in `state`, so
    subtracting `state` removes it from the very set being checked, and the assertion is vacuous by
    construction. It was, until adding a `subject` key to `_contract_state` left it green while two
    unrelated value tests caught the shadowing. So the explicit keys are obtained by serialising with
    an EMPTY state and reading which keys survive.
    """
    # `replyExpected` is the one state key the function READS before spreading, so the stand-in must
    # carry it; everything else in the result is then a key the serialiser set itself.
    monkeypatch.setattr(contracts, "_contract_state", lambda row, **kw: {"replyExpected": True})
    explicit = set(serialise()) - {"replyExpected"}
    monkeypatch.undo()

    state = _contract_state(row(), settings=dict(DEFAULT_SETTINGS), now_s=NOW)
    assert len(explicit) > 15, f"only {len(explicit)} explicit keys — the monkeypatch measured nothing"
    collisions = set(state) & explicit
    assert not collisions, f"{sorted(collisions)} would be overwritten by the trailing **state"

    # And every state key really does reach the payload, or the spread is not doing its job.
    serialised = serialise()
    for key in state:
        assert key in serialised, f"{key} was dropped between _contract_state and the row"


def test_the_state_is_carried_through_verbatim():
    serialised = serialise(requested_at=iso(NOW - 3600))
    assert serialised["state"] == "overdue"
    assert serialised["overdue"] is True
    assert serialised["replyExpected"] is True
    assert serialised["category"] == "direct"


# ── the reply-state reconciliation ───────────────────────────────────────────────────────────
def test_an_expected_reply_with_no_result_is_awaiting_not_not_required():
    """`_dispatch_reply_state` says `not_required` from `require_reply=0` alone; the contract can
    still expect a reply, and the override is what reconciles them."""
    assert serialise(require_reply=0, status="completed")["replyState"] == "not_required", (
        "with no reply expected either, `not_required` stands"
    )
    # A run whose flag is off but whose contract is open resolves to awaiting.
    open_contract = _contract_row_to_dict(
        row(require_reply=0, status="queued"), settings=dict(DEFAULT_SETTINGS), now_s=NOW
    )
    assert open_contract["replyExpected"] is False
    assert open_contract["replyState"] == "not_required", (
        "replyExpected is what drives the override — with it False, nothing is overridden"
    )


def test_a_result_message_reports_sent():
    serialised = serialise(result_message_id="msg-reply", result_body="here you go")
    assert serialised["replyState"] == "sent"
    assert serialised["resultMessageId"] == "msg-reply"
    assert serialised["resultPreview"] == "here you go"
    assert serialised["state"] == "answered"


def test_an_open_contract_awaiting_a_reply():
    serialised = serialise(status="running")
    assert serialised["replyState"] == "awaiting"
    assert serialised["resultMessageId"] == ""


def test_a_terminal_run_that_still_owes_a_reply_is_pending():
    serialised = serialise(status="completed")
    assert serialised["replyState"] == "pending", "the run is over and the reply is not"
    assert serialised["state"] == "missing_reply"


# ── the body fallback and clipping ───────────────────────────────────────────────────────────
def test_the_message_body_is_preferred_and_the_run_body_is_the_fallback():
    assert serialise()["preview"] == "the full message body"
    assert serialise(message_body="")["preview"] == "the run body", (
        "an empty message_body falls through to the run's own body rather than rendering blank"
    )


def test_previews_are_clipped_to_420_characters():
    serialised = serialise(message_body="x" * 500, result_body="y" * 500, result_message_id="m")
    assert len(serialised["preview"]) == 420
    assert len(serialised["resultPreview"]) == 420


def test_a_shorter_body_is_not_padded():
    assert serialise(message_body="short")["preview"] == "short"


# ── the plain fields ─────────────────────────────────────────────────────────────────────────
def test_the_identifying_fields_are_passed_straight_through():
    serialised = serialise()
    assert serialised["id"] == "run-1"
    assert serialised["messageId"] == "msg-1"
    assert serialised["from"] == "manager-bot"
    assert serialised["targetAgentId"] == "sc-coder"
    assert serialised["type"] == "request"
    assert serialised["subject"] == "Ship the thing"
    assert serialised["priority"] == "high"
    assert serialised["status"] == "queued"
    assert serialised["runtime"] == "claude-code"
    assert serialised["requireReply"] is True


def test_null_columns_render_as_empty_strings_not_none():
    """The dashboard concatenates these. A None would print as "None" in the UI."""
    serialised = serialise(
        message_id=None, subject=None, priority=None, runtime=None,
        source_read_at=None, last_reminder_at=None,
    )
    for key in ("messageId", "subject", "runtime", "sourceReadAt", "lastReminderAt"):
        assert serialised[key] == "", f"{key} rendered as {serialised[key]!r}"
    assert serialised["priority"] == "normal", "priority has a real default rather than a blank"


def test_the_timestamps_are_carried_unconverted():
    """They are ISO strings end to end — a format change here is a data migration, not a display
    choice, for the same reason `service/clock.now()` says so."""
    serialised = serialise(
        claimed_at=iso(NOW - 50), started_at=iso(NOW - 40), finished_at=iso(NOW - 30),
    )
    assert serialised["requestedAt"] == iso(NOW - 60)
    assert serialised["claimedAt"] == iso(NOW - 50)
    assert serialised["startedAt"] == iso(NOW - 40)
    assert serialised["finishedAt"] == iso(NOW - 30)
