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

    def test_a_trailing_newline_is_rejected(self):
        """TIGHTENED 2026-08-16. This test used to assert the opposite.

        It was `test_A_TRAILING_NEWLINE_IS_CURRENTLY_ACCEPTED`, a pinned quirk: Python's `$` also
        matches BEFORE a trailing newline, so `"agent\\n"` passed while `"age\\nnt"` was rejected --
        and that difference is what shows the intent. The pin deferred the fix ("changing it is a
        BEHAVIOUR change, and v0.5.x is structural-only") and named the two conditions for making it:
        do it on purpose, and consider existing names.

        ON PURPOSE, by this suite's own stated principle. `test_rejects_non_ascii` rejects homoglyphs
        because "homoglyphs are an impersonation vector when a name is an identity", and this gate
        guards AGENT IDS at twenty-odd call sites. A trailing newline is the perfect homoglyph:
        `coder` and `coder\\n` are two distinct identities that render identically everywhere an
        operator or another agent can see them -- there is no character to notice.

        EXISTING NAMES: nothing in this repo generates one, so such a row could only come from a
        client that sent it. If one exists it now 400s rather than being silently renamed, which is
        the safe direction for an identity gate.

        BOUNDED, and worth saying because I checked the worse story and it is not true: this could
        NOT forge a `From:` line in a merged dispatch body. `dispatch_text.py` writes
        `f"From: {from_agent}"` and `records.py` parses `^From:\\s*(.+)$` back out, but only ONE
        trailing newline was ever admitted and nothing can follow it, so the most that could be
        injected is a blank line -- which the parser's own `if match.group(1).strip()` drops.
        """
        self._rejects("agent\n")
        self.assertIsNone(SAFE_NAME_RE.match("agent\n"))
        self._rejects("age\nnt")  # the same character mid-name was always rejected
        self._rejects("agent\n\n")
        validate_name("agent")  # …and the name without it is still fine

    def test_the_error_names_the_field(self):
        """The label is caller-supplied and appears in the 400, so it must survive the move."""
        with self.assertRaises(HTTPException) as caught:
            validate_name("bad name", "channel")
        self.assertIn("channel", str(caught.exception.detail))

    def test_every_module_that_binds_it_binds_the_owners_object(self):
        """Every reader must hold the OWNER's object, not a second copy of it.

        This read the control plane's binding until v0.7.0 deleted that module. The property was
        never about the control plane: it is that no module can hold a forked validator. So it is
        asked of every service module that binds either name, by identity. A copy would pass `==`
        and still be a fork. Comparing the owner with itself is a tautology, so the owner is left out
        and at least one other reader is required.
        """
        import importlib
        from pathlib import Path

        service = Path(__file__).resolve().parent.parent
        owner = service / "api_core" / "validation.py"
        readers = []
        for path in sorted(service.rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts or path == owner:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "validate_name" not in text and "SAFE_NAME_RE" not in text:
                continue
            dotted = ".".join(path.relative_to(service.parent).with_suffix("").parts)
            module = importlib.import_module(dotted)
            for name, expected in (("validate_name", validate_name), ("SAFE_NAME_RE", SAFE_NAME_RE)):
                if hasattr(module, name):
                    readers.append(f"{dotted}.{name}")
                    self.assertIs(getattr(module, name), expected, f"{dotted}.{name} is not the owner's object")
        self.assertTrue(readers, "no module outside the owner binds the validator; the scan is not reading the tree")


if __name__ == "__main__":
    unittest.main()
