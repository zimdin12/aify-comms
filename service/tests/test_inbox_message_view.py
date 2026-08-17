"""The shape every inbox message arrives in — including the two fields that are absent on purpose.

`service/api_core/message_view.py` is named by no test file. It is one view-model function, and three
of its decisions are load-bearing in a way its size hides.

`preview` IS ALWAYS PRESENT AND `body` IS NOT. A headers-mode inbox exists so an agent can scan a
hundred messages without pulling a hundred bodies into its context; if `body` leaked into that mode
the saving disappears, and if `preview` were dropped when the body IS included, every caller would
need two code paths to show a list.

`to` LOOKS REDUNDANT AND IS NOT. Every row in an inbox is addressed to the agent that asked, so the
field carries no information — except that the dashboard's unread/mark-read logic filters on it and
falls back to inbox data when `/messages/recent` blips. Without the field that fallback silently
matched nothing, which is a review finding rather than a theory.

`parentContext` IS THE THREE-STATE ONE. Absent means "not a reply". Present-and-null means "a reply
whose parent could not be found" — the serializer seeds the null and the handler overwrites it only
when the parent row exists, so a reply to a deleted message is distinguishable from a message that
was never threaded. Collapsing those two would make an orphaned reply look like a root.
"""

from __future__ import annotations

import unittest

from service.api_core.message_view import _serialize_inbox_message


def row(**overrides) -> dict:
    base = {
        "id": "m-1",
        "from_agent": "sender",
        "to_agent": "recipient",
        "type": "info",
        "source": "direct",
        "channel": "",
        "subject": "the subject",
        "body": "the body",
        "priority": "normal",
        "timestamp": 1_700_000_000_000,
        "in_reply_to": None,
        "dispatch_requested": 0,
        "read_at": None,
    }
    base.update(overrides)
    return base


class FieldNameTests(unittest.TestCase):
    def test_the_sender_is_reported_as_FROM(self):
        """The wire name differs from the column name in both directions across this API; a silent
        rename leaves every consumer reading nothing for who sent it."""
        message = _serialize_inbox_message(row(), include_body=True)
        self.assertEqual(message["from"], "sender")
        self.assertNotIn("from_agent", message)

    def test_the_RECIPIENT_is_reported_even_though_the_inbox_implies_it(self):
        """The review finding: the dashboard filters on `to` when falling back to inbox data, and
        without the field that fallback matched nothing at all."""
        message = _serialize_inbox_message(row(), include_body=True)
        self.assertEqual(message["to"], "recipient")

    def test_a_row_with_NO_to_agent_column_reports_None_rather_than_raising(self):
        """Not every query selects it. The guard is `in row.keys()`, so a narrower SELECT degrades
        to a null instead of failing the whole inbox."""
        narrow = row()
        del narrow["to_agent"]
        self.assertIsNone(_serialize_inbox_message(narrow, include_body=True)["to"])

    def test_the_reply_parent_is_reported_as_IN_REPLY_TO(self):
        message = _serialize_inbox_message(row(in_reply_to="m-parent"), include_body=True)
        self.assertEqual(message["inReplyTo"], "m-parent")

    def test_the_carried_through_fields_keep_their_values(self):
        message = _serialize_inbox_message(
            row(type="request", source="channel", channel="general", priority="high"),
            include_body=True)
        self.assertEqual(message["type"], "request")
        self.assertEqual(message["source"], "channel")
        self.assertEqual(message["channel"], "general")
        self.assertEqual(message["priority"], "high")
        self.assertEqual(message["subject"], "the subject")
        self.assertEqual(message["timestamp"], 1_700_000_000_000)


class PreviewAndBodyTests(unittest.TestCase):
    def test_HEADERS_mode_omits_the_body(self):
        """The whole point of the mode: an agent scanning a hundred messages must not pull a hundred
        bodies into its context."""
        message = _serialize_inbox_message(row(), include_body=False)
        self.assertNotIn("body", message)

    def test_FULL_mode_includes_the_body(self):
        self.assertEqual(
            _serialize_inbox_message(row(), include_body=True)["body"], "the body")

    def test_the_PREVIEW_is_present_in_BOTH_modes(self):
        """A caller rendering a list uses `preview` whichever mode it asked for. Dropping it when the
        body is included would force two code paths for one list."""
        for include_body in (True, False):
            with self.subTest(include_body=include_body):
                message = _serialize_inbox_message(row(), include_body=include_body)
                self.assertEqual(message["preview"], "the body")

    def test_a_long_body_is_CLIPPED_in_the_preview_but_not_in_the_body(self):
        """The clip is what bounds a headers response. A full-mode caller asked for the whole thing
        and must get it — the preview being short is not a reason to truncate the body."""
        long_body = "x" * 500
        message = _serialize_inbox_message(row(body=long_body), include_body=True)
        self.assertLessEqual(len(message["preview"]), 240)
        self.assertEqual(len(message["body"]), 500)

    def test_a_NULL_body_previews_as_an_empty_string(self):
        """Messages are written with no body — a bare subject, a control acknowledgement. `None`
        would render as "null" in a list an operator reads."""
        message = _serialize_inbox_message(row(body=None), include_body=True)
        self.assertEqual(message["preview"], "")


class ReadStateTests(unittest.TestCase):
    def test_a_message_with_NO_RECEIPT_is_unread(self):
        """`read_at` is NULL from the LEFT JOIN when this agent has no receipt. That is the whole
        definition of unread in this schema."""
        message = _serialize_inbox_message(row(read_at=None), include_body=True)
        self.assertIs(message["read"], False)
        self.assertIsNone(message["readAt"])

    def test_a_message_WITH_a_receipt_is_read_and_carries_when(self):
        message = _serialize_inbox_message(
            row(read_at="2026-08-17T09:00:00Z"), include_body=True)
        self.assertIs(message["read"], True)
        self.assertEqual(message["readAt"], "2026-08-17T09:00:00Z")

    def test_read_is_derived_from_PRESENCE_not_truthiness(self):
        """`is not None`, so an empty-string receipt still counts as read. That is the right
        direction: a receipt row exists, and treating it as unread would re-deliver a message the
        agent has already seen — the infinite-redelivery shape. Recorded because the two readings
        differ only for a value nothing writes today."""
        message = _serialize_inbox_message(row(read_at=""), include_body=True)
        self.assertIs(message["read"], True)


class DispatchRequestedTests(unittest.TestCase):
    def test_the_flag_is_a_BOOLEAN_not_the_stored_integer(self):
        """SQLite stores 0/1. A caller doing `if msg.dispatchRequested` works either way; one
        comparing to `true` does not, so the boundary picks one shape."""
        self.assertIs(
            _serialize_inbox_message(row(dispatch_requested=1), include_body=True)["dispatchRequested"],
            True)
        self.assertIs(
            _serialize_inbox_message(row(dispatch_requested=0), include_body=True)["dispatchRequested"],
            False)

    def test_a_row_without_the_column_reports_FALSE(self):
        """Absent means "nothing asked for a dispatch", which is the safe reading — a null here would
        make a caller treat an ordinary message as one that may have started work."""
        narrow = row()
        del narrow["dispatch_requested"]
        self.assertIs(
            _serialize_inbox_message(narrow, include_body=True)["dispatchRequested"], False)


class ParentContextTests(unittest.TestCase):
    def test_a_message_that_is_NOT_a_reply_has_NO_parentContext_key(self):
        """Absent, not null. The three states are distinguishable only if this one is missing
        entirely."""
        self.assertNotIn(
            "parentContext", _serialize_inbox_message(row(in_reply_to=None), include_body=True))

    def test_a_REPLY_is_seeded_with_a_NULL_parentContext(self):
        """The seed is what makes "a reply whose parent is gone" a reportable state: the handler
        overwrites this only when the parent row exists, so a reply to a deleted message keeps the
        null and does not read as a root message."""
        message = _serialize_inbox_message(row(in_reply_to="m-parent"), include_body=True)
        self.assertIn("parentContext", message)
        self.assertIsNone(message["parentContext"])

    def test_an_EMPTY_in_reply_to_is_not_a_reply(self):
        """Threading writes `''` as readily as NULL. Seeding the key for it would put a null parent
        on every unthreaded message in the inbox."""
        for value in (None, ""):
            with self.subTest(in_reply_to=value):
                self.assertNotIn(
                    "parentContext",
                    _serialize_inbox_message(row(in_reply_to=value), include_body=True))


if __name__ == "__main__":
    unittest.main()
