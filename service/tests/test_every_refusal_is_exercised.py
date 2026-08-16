"""Operator-facing refusals, and which of them any test has ever seen.

A refusal is `raise HTTPException(code, message)`. There are 97 with a distinctive message, and most
of them had never been touched by a test — a surface where FOUR live defects were found by reading,
each the same shape: a 4xx NAMING a cause the branch never established. The Start button's "no
environment bridge", the restart 409's "no online environment", the console gate's two-way split, the
cold-start family.

THE FIRST THING THIS GATE CAUGHT WAS ITSELF. It scanned the whole test tree, including
`service/tests/data/` — 36 verbatim pre-split COPIES of product functions — so 56 refusals read as
exercised because the source was duplicated, not because anything asserted them. See FIXTURE_DIR.
Every count below is post-fix; do not compare them to a number written before 2026-08-16.

THE MEASUREMENT WAS IN A SCRATCHPAD, WHICH IS WHY THIS EXISTS. I have driven several slices off that
number and it lived nowhere in the repo — so it could not fail a build, could not be re-derived by
anyone else, and would vanish with the session. Worse, it silently under-reported twice while I was
using it, both times because of how I wrote the TEST rather than anything in the service:

    self.assertEqual(detail, f"Agent '{agent}' has no pending session id to {action}")

reads well and leaves both refusals counted as unexercised, because the scan greps for the
distinctive TEXT and an interpolated message appears nowhere as a literal. Twice in three slices. As
a gate, that mistake is a red test naming the refusal instead of a number quietly one too low.

WHAT "EXERCISED" MEANS, stated because it is generous on purpose: the longest STATIC fragment of the
message appears somewhere in the test tree. It does not prove a test asserted anything useful — a
docstring quoting the message counts. It is a floor of the same kind as
`test_every_route_is_exercised.py` and `every-module-is-imported-by-a-test.test.js`, and it catches
the thing that actually happens: a refusal added with no test at all.

DYNAMIC MESSAGES ARE OUT OF SCOPE. A message built entirely from interpolation has no static fragment
to search for, and one under 12 characters is not distinctive enough to attribute. Both are skipped
rather than guessed at, and the counts below say how many.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv", "tests"}

#: Shortest static fragment worth searching for. Below this a phrase like "not found" matches half
#: the test tree and the attribution is meaningless.
MIN_PHRASE = 12

#: How much of the phrase is searched. The whole thing would fail on a message a test asserts in
#: two pieces or wraps across lines; 40 characters is distinctive without being brittle.
PHRASE_PREFIX = 40

SELF = pathlib.Path(__file__).name


def _literal_chunks(node: ast.AST) -> list[str]:
    """The static text of a message, ignoring interpolated values."""
    out: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
    elif isinstance(node, ast.BinOp):
        out += _literal_chunks(node.left) + _literal_chunks(node.right)
    return out


def _refusals() -> list[tuple[str, int, str]]:
    """(location, status code, searchable phrase) for every distinctive refusal in the service."""
    found = []
    for path in sorted((REPO / "service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            if getattr(node.exc.func, "id", "") != "HTTPException":
                continue
            args = node.exc.args
            if len(args) < 2:
                continue
            code = args[0].value if isinstance(args[0], ast.Constant) else 0
            chunks = _literal_chunks(args[1])
            phrase = max((chunk.strip() for chunk in chunks), key=len, default="")
            phrase = re.sub(r"\s+", " ", phrase).strip()
            if len(phrase) < MIN_PHRASE:
                continue
            found.append((f"{rel.as_posix()}:{node.lineno}", code, phrase))
    return sorted(found)


#: NOT COVERAGE. `service/tests/data/` holds 36 VERBATIM PRE-SPLIT COPIES of product functions —
#: `register_agent_before_split.py` and friends, kept so the v0.5.4 splits could be proven inert. A
#: copy of the source contains every refusal message in the source, so scanning it counted 56
#: refusals as exercised on the strength of the code being duplicated rather than tested. The gate's
#: own anti-vacuity needle was one of them: it picked "; auto re-registration is blocked." as its
#: example of a refusal "the suite genuinely asserts", and the only occurrence anywhere was the
#: fixture.
#:
#: This is the failure this gate was written to prevent, turned on itself — a measurement that
#: reported coverage nobody had written. `test_the_fixture_exclusion_is_checkable` below pins that
#: every file being excluded really is such a copy, so the exclusion cannot quietly grow to swallow a
#: directory where assertions live.
FIXTURE_DIR = "data"
FIXTURE_SUFFIX = "_before_split.py"


def _test_tree_text() -> str:
    """The whole test tree as one blob — never this file, and never a copy of the product code.

    SELF-EXCLUSION, for the reason `test_every_route_is_exercised.py` records after making the same
    mistake: this file names refusals in its own docstring and backlog, so counting itself would
    exonerate exactly what it tracks.

    ONE BLOB is right here, unlike the route gate. A refusal phrase is a long distinctive sentence,
    so a cross-file coincidence is not a realistic risk — whereas a route's segments (`agents`,
    `stop`) collide constantly, which is why that gate matches per file.
    """
    parts = []
    for path in sorted((REPO / "service" / "tests").rglob("*.py")):
        if "__pycache__" in path.parts or path.name == SELF:
            continue
        if FIXTURE_DIR in path.parts:
            continue
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _unexercised() -> list[str]:
    text = _test_tree_text()
    return [
        f"[{code}] {where}  {phrase[:60]}"
        for where, code, phrase in _refusals()
        if phrase[:PHRASE_PREFIX] not in text
    ]


#: MEASURED, NOT CHOSEN — the refusals no test has ever touched, as of the commit that added this
#: gate. Held as a COUNT rather than a list of locations on purpose: every entry is a line number,
#: and a list of 26 line numbers would go stale on the next edit to any of those files and teach the
#: next person to "fix" it by re-running a script.
#:
#: MAY ONLY SHRINK. The test below fails if the real number is higher (a new untested refusal landed)
#: AND if it is lower (some were covered — lower the ceiling in the same commit). That second half is
#: what stops the number rotting upward into a target nobody meets.
#:
#: IT WENT UP ONCE, 15 -> 70, AND THAT IS A CORRECTION RATHER THAN A REGRESSION. Nothing lost a test:
#: the scan was reading `service/tests/data/`, whose 36 files are verbatim copies of the product
#: functions they were kept to prove inert, so 56 refusals counted as exercised because the code was
#: DUPLICATED. See FIXTURE_DIR above. Of the 97 distinctive refusals, 27 are genuinely covered — the
#: number the earlier slices actually earned, against the 82 the gate was reporting.
UNEXERCISED_REFUSAL_CEILING = 66


class EveryRefusalIsExercisedTests(unittest.TestCase):
    def test_no_new_untested_refusal_lands(self):
        """THE ONE THAT MATTERS. A refusal names a cause to an operator; four in this surface named
        one the branch had not established, and all four were found by reading rather than by a
        test."""
        unexercised = _unexercised()
        self.assertLessEqual(
            len(unexercised), UNEXERCISED_REFUSAL_CEILING,
            f"{len(unexercised)} refusals have no test, ceiling is {UNEXERCISED_REFUSAL_CEILING}. "
            "The new ones are in this list — write a test that asserts the message TEXT:\n  "
            + "\n  ".join(unexercised),
        )

    def test_THE_CEILING_MAY_ONLY_SHRINK(self):
        """Covering a refusal must lower the number in the same commit, or the ceiling becomes slack
        that a later untested refusal inherits. Same ratchet as the route gate's backlog and the JS
        side's `UNTESTED_BACKLOG`."""
        unexercised = _unexercised()
        self.assertEqual(
            len(unexercised), UNEXERCISED_REFUSAL_CEILING,
            f"only {len(unexercised)} refusals are now untested — set UNEXERCISED_REFUSAL_CEILING to "
            f"{len(unexercised)} in this commit",
        )

    def test_the_scan_finds_the_surface_it_claims_to_measure(self):
        """Anti-vacuity. A parse that found nothing would report a clean repo, and a search that
        matched everything would report full coverage."""
        refusals = _refusals()
        self.assertGreater(len(refusals), 80, f"only {len(refusals)} refusals parsed")
        text = _test_tree_text()
        self.assertGreater(len(text), 500_000, "the test tree read as almost empty")
        # BOOLEANS, NOT `assertIn`, against a blob this size. `assertIn` prints the haystack on
        # failure: my first version dumped 3.7 MB into the test output, which is a failure nobody
        # would read — and an unreadable failure is a step away from a deleted assertion.
        self.assertFalse(
            "this exact sentence appears in no test anywhere" in text,
            "a phrase no test contains must not be found",
        )
        # THE NEEDLE MUST COME FROM A REAL ASSERTION. This one was "; auto re-registration is
        # blocked." — a phrase whose only occurrence in the whole test tree was
        # `service/tests/data/register_agent_before_split.py`, a copy of the source. The check was
        # itself passing on the artefact it should have been excluding. This phrase is asserted by
        # `test_tombstone_resurrection_gate.py`, so weakening that assertion fails this test too.
        self.assertTrue(
            "; a lingering bridge cannot resurrect it." in text,
            "a refusal the suite genuinely asserts must be found — otherwise the matcher reports "
            "everything as untested and the ceiling is meaningless",
        )

    def test_the_fixture_exclusion_is_checkable(self):
        """The exclusion above skips a whole directory, which is only safe while everything in it is
        a copy of product code. Pinned so it cannot quietly grow into somewhere assertions live.

        Also proves the masking was REAL rather than theoretical: the fixtures do contain refusal
        messages, which is the entire reason they had to stop counting.
        """
        fixtures = sorted((REPO / "service" / "tests" / FIXTURE_DIR).glob("*.py"))
        self.assertGreater(len(fixtures), 20, "the fixture directory read as almost empty")
        for path in fixtures:
            with self.subTest(fixture=path.name):
                self.assertTrue(
                    path.name.endswith(FIXTURE_SUFFIX),
                    f"{path.name} is excluded from the coverage scan but is not a pre-split copy — "
                    "either it is a real test file being skipped, or the exclusion needs narrowing",
                )
        excluded = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in fixtures)
        self.assertTrue(
            "; auto re-registration is blocked." in excluded,
            "the fixtures no longer carry refusal messages — if that is true the exclusion is now "
            "harmless, but check it before trusting this gate's number",
        )

    def test_the_message_parser_reads_the_shapes_the_service_uses(self):
        """All four, because a parser that handled only plain strings would silently skip the
        interpolated ones — which are the majority of the interesting refusals."""
        def phrase(source: str) -> str:
            call = ast.parse(source).body[0].exc
            chunks = _literal_chunks(call.args[1])
            return max((chunk.strip() for chunk in chunks), key=len, default="")

        self.assertEqual(phrase('raise HTTPException(400, "plain message here")'), "plain message here")
        self.assertEqual(
            phrase('raise HTTPException(404, f"Agent {x} was not found anywhere")'),
            "was not found anywhere",
            "an f-string contributes its STATIC fragments",
        )
        self.assertEqual(
            phrase('raise HTTPException(409, "first part " + "and the longer second part")'),
            "and the longer second part",
            "concatenation is walked on both sides",
        )
        self.assertEqual(
            phrase('raise HTTPException(400, f"{a}{b}")'), "",
            "a message with NO static text has nothing to search for and is skipped, not guessed",
        )

    def test_a_message_asserted_only_as_an_f_string_does_NOT_count(self):
        """The mistake this gate exists to make visible, pinned as a property of the matcher.

        Writing `f"Agent '{agent}' has no pending session id to {action}"` in a test asserts the
        right thing and leaves the refusal uncounted, because no literal of that sentence exists
        anywhere. I did it twice in three slices before this gate could say so.
        """
        interpolated_assertion = 'self.assertEqual(detail, f"Agent \'{a}\' has no pending session id to {b}")'
        self.assertNotIn("has no pending session id to confirm", interpolated_assertion)
        spelled_out = 'self.assertEqual(detail, "Agent \'x\' has no pending session id to confirm")'
        self.assertIn("has no pending session id to confirm", spelled_out)
