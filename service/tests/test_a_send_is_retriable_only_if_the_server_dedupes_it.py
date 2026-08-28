"""A send is retriable only where the server collapses the retry. The two halves must agree.

THE PAIR. `/messages/send` deduplicates: `messages.py` looks up `(from_agent, client_nonce)` and
short-circuits a retry to the original message. The bridge's `isRetriableRequest` therefore returns
true for that path -- but ONLY when the body actually carries a nonce, because a nonce-less send
would double-send. `/channels/{name}/send` does NOT deduplicate: `ChannelMessage` has no
`clientNonce` field at all, and the bridge correspondingly refuses to retry it.

Those two facts live in different halves of the repo and nothing tied them together.

WHY THAT MATTERS MORE THAN IT LOOKS. `ChannelMessage` SILENTLY DROPS an unknown `clientNonce` --
measured: constructing one with `clientNonce="n-123"` yields a model whose dumped fields do not
include it, with no error raised. So a caller generalising from `comms_send` to `comms_channel_send`
gets a field that reads as a guarantee and is discarded. If anyone then adds the channel path to the
retriable set on the strength of that field, every retried channel post becomes a duplicate.

MEASURED ON THE LIVE DATABASE 2026-08-28, which is why this is a gate and not a schema change: 667
channel messages, and exactly ONE pair sharing a channel, sender and body. Its two rows are 362,991ms
-- six minutes -- apart, which is a deliberate repost and not a retry. The missing nonce has produced
no observed duplicate, because the bridge does not retry the path. The mitigation IS the refusal to
retry, so the refusal is what needs pinning.

THE LEDGER'S OPEN QUESTION, ANSWERED. `docs/V0_7_WEAK_POINTS.md` records "a DM survives a transient
blip and a channel message does not" and says the honest first move is a counter, since nothing
measures how often a channel send fails. It does not need a counter to make the decision: the send is
never retried, so a blip loses the message rather than duplicating it, and the fix would be to make
the path retriable -- which requires the nonce first, in that order.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import unittest

from service.models import ChannelMessage

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = ROOT / "mcp" / "stdio" / "aify-service-endpoint.mjs"


def _retriable_source() -> str:
    """The bridge's retry predicate, as text. Read rather than executed: it is ESM in another
    runtime, and the property here is which paths the rule admits."""
    text = ENDPOINT.read_text(encoding="utf-8")
    start = text.index("function isRetriableRequest(")
    end = text.index("function isTransientHttpError(", start)
    return text[start:end]


class ASendIsRetriableOnlyIfTheServerDedupesItTests(unittest.TestCase):
    def test_the_predicate_was_actually_read(self) -> None:
        """The control. An empty or missing source makes every assertion below vacuous, and this
        repo has produced that wrong zero repeatedly."""
        source = _retriable_source()
        self.assertGreater(len(source), 400, "the retry predicate is implausibly short")
        self.assertIn("/messages/send", source, "the DM send rule is missing from the predicate")

    def test_the_dm_send_is_retriable_only_with_a_nonce(self) -> None:
        """The half that works, pinned as the control for the half that must not change. The rule is
        conditional on the nonce being PRESENT, not merely on the path."""
        source = _retriable_source()
        rule = [line for line in source.splitlines() if "/messages/send" in line and "return true" in line]
        self.assertEqual(len(rule), 1, f"expected one /messages/send rule, found {len(rule)}")
        self.assertIn("clientNonce", rule[0],
                      "the DM send became retriable regardless of the nonce, so a nonce-less retry "
                      "now double-sends")

    def test_the_channel_send_is_not_retriable(self) -> None:
        """The mitigation. Nothing else prevents a duplicate channel post."""
        source = _retriable_source()
        # BACKSLASHES STRIPPED FIRST. The rule is written as a JS regex literal --
        # `/^\/channels\/[^/]+\/send$/` -- so a pattern expecting a plain `channels/[^/]+/send`
        # matches nothing. The first version of this test did exactly that: adding the channel
        # path to the retriable set left it GREEN, which the mutation run caught. A gate that
        # cannot see the change it exists to stop is decorative.
        flattened = [line.replace(chr(92), '') for line in source.splitlines()]
        channel_rules = [
            line for line in flattened
            if re.search(r'channels/\[\^/\]\+/send', line) and 'return true' in line
        ]
        self.assertEqual(
            channel_rules, [],
            "the channel send was made retriable. It is only safe to retry a send the server "
            "collapses, and the channel path does not deduplicate: ChannelMessage has no "
            "clientNonce field, so a retry posts the message twice.",
        )

    def test_the_channel_model_still_has_no_nonce_and_drops_one_silently(self) -> None:
        """The trap, pinned so the next reader meets it here rather than in production.

        If this ever fails because the field was ADDED, that is the moment to make the channel path
        retriable -- and this file is the reminder that the bridge half has to move with it.
        """
        fields = set(ChannelMessage.model_fields)
        self.assertNotIn(
            "clientNonce", fields,
            "ChannelMessage gained a clientNonce. If the server now deduplicates channel sends, the "
            "bridge's isRetriableRequest must be updated in the same change; if it does not, the "
            "field is a guarantee that is not kept.",
        )
        dropped = ChannelMessage(from_agent="a", channel="c", body="b", clientNonce="n-123")
        self.assertNotIn(
            "clientNonce", dropped.model_dump(),
            "the model now keeps an unknown clientNonce, which is worse than dropping it unless the "
            "route also honours it",
        )


if __name__ == "__main__":
    unittest.main()
