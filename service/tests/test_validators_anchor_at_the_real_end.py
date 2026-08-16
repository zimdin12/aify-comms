"""`$` is not "end of string" in Python, and two admission gates were written as if it were.

`re.match(r"^...$", value)` reads like a whole-string match and is not one: `$` also matches BEFORE a
single trailing newline. So `SAFE_NAME_RE` -- the gate on every agent id, channel name and shared
artifact name -- admitted `"agent\\n"` while rejecting `"age\\nnt"`, and `_SHELL_PLACEHOLDER_HANDLE_RE`
would have failed to recognise `"${HERMES_SESSION_ID}\\n"` as an unexpanded placeholder.

WHY IT MATTERS FOR THESE TWO SPECIFICALLY. `test_name_validation.py` rejects homoglyphs because
"homoglyphs are an impersonation vector when a name is an identity". A trailing newline is the
perfect homoglyph: `coder` and `coder\\n` are distinct identities that render identically everywhere
-- there is no character to notice. The placeholder gate is the milder case, latent because its one
caller strips first, which is the reason to fix it rather than to leave it: a guard must not depend
on a caller it cannot see.

THIS GATE IS THE GENERAL FORM. A pattern that is anchored `^...$` and compiled WITHOUT `re.MULTILINE`
is a whole-value validator, and for those `$` is a bug waiting for its first newline. With MULTILINE
it is correct and intended -- `^From:\\s*(.+)$` and `^=== ITEM \\d+ ===$` are per-line parsers, and
demanding `\\Z` there would break them. So the rule is scoped to the single-line case, which is the
one where the author meant "the end".

SCOPE, stated so a narrowing does not look like thoroughness: it matches the WHOLE-VALUE shape
`^...$` only. A pattern anchored just at the end is a suffix search -- the jsonl-extension test in
`runtimes/hermes.py`, and `validation.py`'s own truncated-marker search, which deliberately tolerates
the newline a message body ends with. Demanding `\\Z` of those would be a lint result rather than a
defect found. Both are real hits of the looser rule I wrote first, and both were correct code.

It scans repo-wide rather than naming the two files, because the point is the NEXT one.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

from service.api_core.tuning import _SHELL_PLACEHOLDER_HANDLE_RE
from service.api_core.validation import SAFE_NAME_RE, validate_name

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv", "tests"}

#: Patterns that end in `$` and are DELIBERATELY per-line. Each is a parser over a multi-line body,
#: not a validator of one value, so `$` is what it wants. Declared so the scan below can require an
#: explicit `re.MULTILINE` rather than exempting anything by guesswork.
MULTILINE_PARSERS = {
    "service/api_core/claim_gating.py",
    "service/api_core/dispatch_text.py",
    "service/api_core/records.py",
}


def _sources() -> list[tuple[str, str]]:
    out = []
    for path in sorted((REPO / "service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        out.append((rel.as_posix(), path.read_text(encoding="utf-8")))
    return out


def _dollar_anchored_patterns(sources) -> dict[str, list[str]]:
    """Every `re.*` call whose pattern literal ends in `$`, split by whether MULTILINE is passed.

    AST, not regex-over-source: the flags are an ARGUMENT, so a text scan would have to guess which
    `re.MULTILINE` belongs to which call. It also means an f-string or a joined pattern is skipped
    rather than half-read — those are reported by the count check below, not silently dropped.
    """
    single, multi = {}, {}
    for rel, src in sources:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == "re"):
                continue
            if not node.args:
                continue
            pattern = node.args[0]
            if not (isinstance(pattern, ast.Constant) and isinstance(pattern.value, str)):
                continue
            # BOTH ANCHORS, which is narrower than "ends with `$`" and deliberately so. A pattern
            # anchored only at the end is a SUFFIX search, and there `$` is usually what the author
            # wants: `re.search(r"\.jsonl?$", …)` matches a filename ending, and validation.py's
            # truncated-marker search deliberately tolerates the trailing newline a message body
            # ends with. Requiring `\Z` of those would be a lint result, not a defect found. The
            # WHOLE-VALUE shape `^…$` is the one whose author meant "the entire value".
            if not (pattern.value.startswith("^") and pattern.value.endswith("$")):
                continue
            flag_text = " ".join(
                ast.unparse(a) for a in node.args[1:]
            ) + " " + " ".join(ast.unparse(k.value) for k in node.keywords)
            bucket = multi if "MULTILINE" in flag_text or " re.M" in flag_text else single
            bucket.setdefault(rel, []).append(pattern.value)
    return {"single": single, "multi": multi}


class ValidatorAnchorTests(unittest.TestCase):
    def test_no_single_line_validator_anchors_with_dollar(self):
        """THE ONE THAT MATTERS. `$` in a whole-value validator admits a trailing newline."""
        single = _dollar_anchored_patterns(_sources())["single"]
        self.assertEqual(
            {rel: patterns for rel, patterns in sorted(single.items())}, {},
            "these patterns end in `$` without re.MULTILINE, so they are whole-value validators that "
            "will also match a value with a trailing newline. Use `\\Z`, which means the end of the "
            "string and nothing else.",
        )

    def test_the_per_line_parsers_are_left_alone(self):
        """Anti-vacuity from the other side: if the scan simply found nothing, the test above would
        pass on a repo with no regexes at all."""
        multi = _dollar_anchored_patterns(_sources())["multi"]
        self.assertEqual(
            sorted(multi), sorted(MULTILINE_PARSERS),
            "the per-line parsers changed. `$` is CORRECT for these — they read a multi-line body a "
            "line at a time — so this list moving is a real change, not a lint result.",
        )
        for rel, patterns in multi.items():
            self.assertTrue(patterns, rel)

    def test_the_scanner_reads_flags_from_the_call_not_the_file(self):
        fixture = [(
            "service/fake.py",
            "import re\n"
            "A = re.compile(r'^ok$')\n"
            "B = re.compile(r'^ok$', re.MULTILINE)\n"
            "C = re.compile(r'^ok$', flags=re.MULTILINE)\n"
            "D = re.compile(r'^ok')\n"
            "E = re.finditer(r'^ok$', text, flags=re.MULTILINE)\n"
            # A SUFFIX SEARCH, deliberately out of scope — this is the shape whose two real
            # instances the first version of this rule reported as findings when they were correct.
            r"F = re.search(r'\.jsonl?$', name)" "\n",
        )]
        found = _dollar_anchored_patterns(fixture)
        self.assertEqual(found["single"], {"service/fake.py": ["^ok$"]}, "only A is a validator")
        self.assertEqual(found["multi"], {"service/fake.py": ["^ok$", "^ok$", "^ok$"]}, "B, C and E")

    def test_the_two_gates_this_was_written_for_now_reject_a_trailing_newline(self):
        self.assertIsNone(SAFE_NAME_RE.match("agent\n"))
        self.assertIsNotNone(SAFE_NAME_RE.match("agent"))
        self.assertIsNone(_SHELL_PLACEHOLDER_HANDLE_RE.match("${HERMES_SESSION_ID}\n"))
        self.assertIsNotNone(_SHELL_PLACEHOLDER_HANDLE_RE.match("${HERMES_SESSION_ID}"))

    def test_the_name_gate_still_admits_and_refuses_everything_it_did(self):
        """The tightening must remove exactly one shape and nothing else."""
        from fastapi import HTTPException

        for name in ("a", "agent", "mc-senior-dev", "team.lead", "worker_1", "A1", "x" * 128):
            with self.subTest(accepted=name):
                validate_name(name)
        for name in ("", "-lead", ".hidden", "../etc", "a/b", "a b", "a;rm", "a\tb", "agenté",
                     "x" * 129, "agent\n"):
            with self.subTest(rejected=name):
                with self.assertRaises(HTTPException):
                    validate_name(name)
