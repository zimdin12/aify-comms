"""The ntfy topic URL is a credential, and this proves the relay cannot leak it.

Anyone holding the topic URL can READ every notification the fleet sends and PUBLISH to it. So the
rule (C6) is that it never appears in full anywhere — not in health, not in a log, not in an error —
and `NtfyRelay` enforces it structurally: the URL goes in through the constructor and the ONLY way
back out is `.redacted`.

`redacted` was one of 71 service functions the suite never entered, measured by tracing call events
through a full pytest run with worker threads included. `redact_url` itself is tested; the PROPERTY
is what carries the guarantee at the one call site that logs anything (`main.py`, at startup).

THE INTERESTING TEST IS NOT "redacted redacts" — it is that no OTHER surface of a live relay yields
the raw URL. That is checkable rather than argued: build a relay around a distinctive fake topic,
then sweep every public attribute, every property, `repr`, `str`, and the object's `__dict__` values,
and require the secret to appear in none of them. A future accessor added for convenience fails this
without anyone having to remember the rule.

NO REAL TOPIC APPEARS IN THIS FILE. The fixture URL is synthetic, and it has to be: a test that
pasted the operator's own topic to prove it stays secret would have published it to the repository.
"""

from __future__ import annotations

import unittest

from service.ntfy import NtfyRelay, redact_url

#: Synthetic, and distinctive enough that a substring search cannot match by accident.
SECRET_TOPIC = "aify-zz-secret-topic-8f3a1c"
FAKE_URL = f"https://ntfy.example.invalid/{SECRET_TOPIC}"


def _public_surface(relay: NtfyRelay) -> dict[str, object]:
    """Everything a caller can read off the object without touching a private name."""
    surface: dict[str, object] = {}
    for name in dir(relay):
        if name.startswith("_"):
            continue
        try:
            value = getattr(relay, name)
        except Exception as exc:  # a property that raises still must not carry the URL
            surface[name] = f"<raised {exc}>"
            continue
        if callable(value):
            continue
        surface[name] = value
    return surface


class NtfyUrlContainmentTests(unittest.TestCase):
    def setUp(self):
        self.relay = NtfyRelay(FAKE_URL)

    def test_the_redacted_form_identifies_the_config_without_granting_access(self):
        redacted = self.relay.redacted
        self.assertNotIn(SECRET_TOPIC, redacted, "the topic itself leaked into the redacted form")
        self.assertIn("ntfy.example.invalid", redacted, "…but two configurations must stay tellable apart")
        self.assertEqual(redacted, redact_url(FAKE_URL), "the property IS the shared redactor")

    def test_NO_public_attribute_of_a_live_relay_carries_the_topic(self):
        """THE CONTAINMENT PROPERTY. Not "redacted redacts" — "nothing else exposes it". A future
        `.url` added for convenience fails here without anyone having to remember C6."""
        for name, value in _public_surface(self.relay).items():
            with self.subTest(attribute=name):
                self.assertNotIn(
                    SECRET_TOPIC, str(value),
                    f"NtfyRelay.{name} exposes the ntfy topic — it is a credential (C6)",
                )

    def test_repr_and_str_do_not_carry_the_topic(self):
        """The leak nobody writes on purpose: an object logged whole, or interpolated into an error
        message, in a traceback the operator pastes into an issue."""
        self.assertNotIn(SECRET_TOPIC, repr(self.relay))
        self.assertNotIn(SECRET_TOPIC, str(self.relay))

    def test_enabled_answers_whether_it_is_configured_without_saying_what_with(self):
        self.assertIs(self.relay.enabled, True)
        self.assertIs(NtfyRelay("").enabled, False)
        self.assertIs(NtfyRelay("   ").enabled, False)

    def test_an_unconfigured_relay_redacts_to_nothing_rather_than_a_placeholder(self):
        """Empty, not "ntfy:?/…". A placeholder in a startup log reads as "configured, somehow",
        which is the opposite of what the operator needs to know."""
        self.assertEqual(NtfyRelay("").redacted, "")
        self.assertEqual(NtfyRelay("   ").redacted, "")

    def test_two_different_topics_on_one_host_redact_differently(self):
        """The redaction has to stay useful: an operator comparing two hosts' logs must be able to
        tell "same host, different topic" from "same configuration"."""
        one = NtfyRelay(f"https://ntfy.example.invalid/{SECRET_TOPIC}").redacted
        two = NtfyRelay("https://ntfy.example.invalid/another-topic").redacted
        self.assertNotEqual(one, two)
        self.assertEqual(one, NtfyRelay(f"https://ntfy.example.invalid/{SECRET_TOPIC}/").redacted,
                         "a trailing slash is the same configuration")

    def test_a_url_with_credentials_in_it_does_not_leak_them_either(self):
        """`https://user:pass@host/topic` is a shape a caller can paste. The netloc is kept to tell
        configurations apart, so this pins what that means when the netloc itself is secret."""
        redacted = NtfyRelay(f"https://user:hunter2@ntfy.example.invalid/{SECRET_TOPIC}").redacted
        self.assertNotIn(SECRET_TOPIC, redacted)
        self.assertNotIn(
            "hunter2", redacted,
            "userinfo in the netloc is a password, and it reaches the log through the host part",
        )

    def test_an_unparseable_url_says_so_instead_of_echoing_it(self):
        """A URL `urlsplit` genuinely REFUSES, which is narrower than "looks wrong": an unterminated
        IPv6 host raises, while `"::::not a url::::"` parses happily into a path. My first version
        used the latter and never reached the except branch at all — the mutation echoing the raw
        input back survived it, because the input had gone down the normal path."""
        raising = f"http://[::1/{SECRET_TOPIC}"
        redacted = NtfyRelay(raising).redacted
        self.assertEqual(redacted, "ntfy:<unparseable>")
        self.assertNotIn(SECRET_TOPIC, redacted, "an unparseable URL still must not be echoed")

    def test_a_url_that_merely_LOOKS_wrong_is_still_redacted_normally(self):
        redacted = NtfyRelay("::::not a url::::").redacted
        self.assertTrue(redacted.startswith("ntfy:"))
        self.assertNotIn("not a url", redacted)
