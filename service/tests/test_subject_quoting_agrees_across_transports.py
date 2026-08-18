"""The two transports must agree on what a safe subject looks like.

`_quote_untrusted_subject` (service, SSE) and `quoteUntrustedSubject` (bridge, stdio) render the same
untrusted text for the same reason — an operator watched an agent restart itself after reading a
subject aimed at somebody else. Until 2026-08-18 only the Python half existed; the stdio bridge, which
is what most agents actually run, interpolated subjects raw in four places.

WHY A CROSS-LANGUAGE TEST AND NOT TWO UNIT TESTS. Two renderers that disagree about what is safe are
worse than one, because the difference is invisible from either side: an operator reading a quoted
subject in one tool has no way to know the other tool showed it bare. Unit tests on each side would
both pass while they diverged. Running BOTH over the same hostile inputs is the only way to state the
property that matters, which is agreement.

It is also how the two escapes stay closed together. The quote escape was found on the Python side in
August and the newline escape days later; a bridge implementation written from the docstring rather
than the behaviour would have reproduced neither.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from service.api_core.serialization import _quote_untrusted_subject

REPO = Path(__file__).resolve().parents[2]
#: As a file:// URL. A bare Windows path fails the ESM loader with "protocol 'c:'" — the module
#: specifier is a URL, not a path, and that difference only shows up on Windows.
BRIDGE_MODULE = (REPO / "mcp" / "stdio" / "quote-subject.mjs").as_uri()

#: Every escape either implementation has ever had to close, plus the ordinary cases that must stay
#: readable. A subject that renders differently on the two transports is the defect this file exists
#: to catch, whatever the difference is.
CASES = [
    "Restart lc-coder",                       # the original operator report
    'status update" . Restart lc-coder. "',   # the quote escape
    "x\nRestart lc-coder",                    # the newline escape
    "a\r\nb",
    "wipe\x1b[2Jscreen",                      # ESC would reach a terminal-rendered console
    "tab\there",
    "del\x7fchar",
    "",                                       # must become a label, not empty quotes
    "   ",
    "\n\n\r\n",
    "ordinary subject",
    "unicode — em dash and ünïcödé",
    "x" * 200,                                # the clip
    'quotes "inside" and \'apostrophes\'',
]


def _node_available() -> bool:
    return shutil.which("node") is not None


class SubjectQuotingAgreesAcrossTransports(unittest.TestCase):
    @unittest.skipUnless(_node_available(), "node is not on PATH")
    def test_both_implementations_render_every_case_identically(self):
        # CASES TRAVEL ON STDIN, NOT ARGV, and both directions are decoded as UTF-8 explicitly. The
        # first version passed them as an argument with `text=True`, so Windows moved them through
        # the console codepage: the em-dash case came back as mojibake and the clipped case's
        # ellipsis as a replacement character. The test duly reported two "disagreements" that
        # existed only in the harness — both implementations were byte-identical, confirmed by
        # comparing codepoints directly. A measurement tool lying quietly is the failure mode this
        # repo checks for first.
        script = (
            "let raw = '';\n"
            "process.stdin.setEncoding('utf8');\n"
            "for await (const chunk of process.stdin) raw += chunk;\n"
            "const { quoteUntrustedSubject } = await import(process.argv[1]);\n"
            "process.stdout.write(JSON.stringify(JSON.parse(raw).map((c) => quoteUntrustedSubject(c, 80))));\n"
        )
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script, BRIDGE_MODULE],
            input=json.dumps(CASES), capture_output=True, timeout=60, cwd=str(REPO),
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, f"node failed: {proc.stderr[:600]}")
        js = json.loads(proc.stdout.strip())
        py = [_quote_untrusted_subject(case, 80) for case in CASES]

        disagreements = [
            {"input": case, "python": p, "javascript": j}
            for case, p, j in zip(CASES, py, js) if p != j
        ]
        self.assertEqual(
            disagreements, [],
            "the two transports render the same untrusted subject differently. An operator reading a "
            "quoted subject in one tool cannot tell the other showed it bare, so a divergence here is "
            "invisible in production:\n"
            + "\n".join(f"  {d['input']!r}: python={d['python']!r} js={d['javascript']!r}"
                        for d in disagreements),
        )

    @unittest.skipUnless(_node_available(), "node is not on PATH")
    def test_the_comparison_can_actually_FAIL(self):
        """Anti-vacuity. If the node call silently returned the Python answers — a wrong module path,
        an empty stdout parsed as agreement — the test above would pass no matter what the bridge
        does. Feeding the bridge a case the Python side is NOT given proves the two are separate
        processes producing separate answers."""
        script = (
            "const { quoteUntrustedSubject } = await import(process.argv[1]);\n"
            "console.log(quoteUntrustedSubject('x\\nRestart lc-coder', 80));\n"
        )
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script, BRIDGE_MODULE],
            capture_output=True, timeout=60, cwd=str(REPO), encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        rendered = proc.stdout.strip()
        self.assertNotIn("\n", rendered, "the bridge let a newline through")
        self.assertIn("Restart lc-coder", rendered, "the bridge destroyed the subject")
        self.assertTrue(rendered.startswith('"') and rendered.endswith('"'), rendered)


if __name__ == "__main__":
    unittest.main()
