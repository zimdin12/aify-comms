"""Hostile-name tests for the API's name admission gate.

`validate_name` guards every agent id, channel name and environment name entering the service. It
had no dedicated test while it lived as one of 236 helpers in the router; v0.5.1f gave it its own
module, and this is the test suite that should have existed with it.

The point of these cases is not coverage-for-coverage's sake. Names reach the filesystem, the
database, shell-adjacent launch paths and the dashboard, so what this regex admits is a security
property, and the way to know what it admits is to enumerate the hostile shapes rather than to read
the pattern and feel reassured.
"""

from __future__ import annotations

import unittest

from fastapi import HTTPException

from service.api_core.validation import SAFE_NAME_RE, validate_name


class NameValidationTests(unittest.TestCase):
    def _rejects(self, name: str) -> None:
        with self.assertRaises(HTTPException) as caught:
            validate_name(name)
        self.assertEqual(caught.exception.status_code, 400)

    def test_accepts_ordinary_names(self):
        for name in ["a", "agent", "mc-senior-dev", "team.lead", "worker_1", "A1", "x" * 128]:
            with self.subTest(name=name):
                validate_name(name)

    def test_rejects_empty(self):
        self._rejects("")

    def test_rejects_a_leading_separator(self):
        """The first character is deliberately narrower than the rest."""
        for name in ["_leading", "-leading", ".leading", "-", ".", "_"]:
            with self.subTest(name=name):
                self._rejects(name)

    def test_rejects_over_128_characters(self):
        validate_name("x" * 128)
        self._rejects("x" * 129)

    def test_rejects_path_traversal_shapes(self):
        """Names reach the filesystem in workspace and log paths."""
        for name in ["../etc", "..", "a/b", "a\\b", "/abs", "C:/win", "a/../b"]:
            with self.subTest(name=name):
                self._rejects(name)

    def test_rejects_shell_metacharacters(self):
        """Names reach launch commands; these are the characters that change their meaning."""
        for name in ["a;rm", "a|b", "a&b", "a$b", "a`b`", "a>b", "a<b", "a(b)", "a{b}",
                     "a'b", 'a"b', "a b", "a*b", "a?b", "a!b"]:
            with self.subTest(name=name):
                self._rejects(name)

    def test_rejects_control_characters_and_nulls(self):
        for name in ["a\x00b", "a\tb", "a\rb", "a\x1bb", "\n", "a\nb"]:
            with self.subTest(name=name):
                self._rejects(name)

    def test_rejects_non_ascii(self):
        """Homoglyphs are an impersonation vector when a name is an identity."""
        for name in ["agenté", "аgent", "agent\u200b", "🙂"]:
            with self.subTest(name=name):
                self._rejects(name)

    def test_A_TRAILING_NEWLINE_IS_CURRENTLY_ACCEPTED(self):
        """DOCUMENTED QUIRK, pinned rather than fixed.

        Python's `$` matches before a trailing newline, so `"agent\\n"` passes admission today. That
        is almost certainly unintended -- the same name with the newline in the middle is rejected,
        which shows the intent -- but changing it is a BEHAVIOUR change, and v0.5.x is
        structural-only with an empty behaviour changelog.

        So it is pinned here, loudly, instead of being quietly fixed in a move commit or quietly
        left undocumented. When someone tightens it (use `\\Z`, or strip first), this test is what
        tells them the behaviour is changing on purpose and makes them consider existing names.
        """
        validate_name("agent\n")  # accepted TODAY
        self.assertTrue(SAFE_NAME_RE.match("agent\n"))
        self._rejects("age\nnt")  # the same character mid-name IS rejected

    def test_the_error_names_the_field(self):
        """The label is caller-supplied and appears in the 400, so it must survive the move."""
        with self.assertRaises(HTTPException) as caught:
            validate_name("bad name", "channel")
        self.assertIn("channel", str(caught.exception.detail))

    def test_the_router_uses_this_owner(self):
        from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

        self.assertIs(validate_name, validate_name)
        self.assertIs(SAFE_NAME_RE, SAFE_NAME_RE)


if __name__ == "__main__":
    unittest.main()
