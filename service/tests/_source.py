"""Helpers for tests that assert on SOURCE TEXT.

A lot of this repo's behaviour lives in generated bash, long SQL strings and router code that cannot
be imported in isolation, so source-shape assertions are a legitimate tool here. They have one
recurring failure mode, and it bit me three times in a single day (2026-08-11):

    the file EXPLAINS the thing being asserted, in a comment, and the assertion matches the prose

Each time the test failed — or worse, passed — for a reason that had nothing to do with the code. So
the stripping lives here once instead of being re-improvised per test file.

`code_only` is deliberately crude: whole-line `#` comments only. It does not try to parse, because a
test helper that needs its own tests is the wrong helper. Inline trailing comments are rare in the
files these assertions target, and a false match there is loud rather than silent.
"""

from __future__ import annotations


def code_only(text: str, comment_prefix: str = "#") -> str:
    """The same text with whole-line comments removed."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(comment_prefix)
    )
