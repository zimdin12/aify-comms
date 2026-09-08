"""CLAUDE.md's watch-list must name the files that are actually closest to the 1000-line limit.

WHY THIS EXISTS, and it is a measurement rather than a preference. CLAUDE.md carries a ranked table
of the files nearest the limit, and it has now been wrong FOUR times:

    2026-08-25  named only two files; `pi-session.js` at 993 was on nobody's list
    2026-08-25  the correction claimed to be the whole population and skipped ranks three and four
    2026-08-29  recorded `doctor-predicates.js` at 844 when it was 914 -- 70 lines, in the direction
                that matters, because somebody budgeting a refactor would have budgeted for room
                that was not there
    2026-09-08  `control_plane.py` read 893 and was 901, and `mcp/stdio/doctor.js` at 794 was absent
                while ranking SIXTH -- the middle omitted for the second time

The file already tells the next person to re-run the walk rather than amend a row. Four repeats is a
mechanism, not carelessness, so the instruction gets an instrument.

WHAT IS PINNED IS THE CLAIM THAT CHANGES BEHAVIOUR, not the whole table. "The next edit to `app.js`
goes red" is what makes somebody slice before they type; the exact line count of the eighth row
changes nothing and would turn this gate red on every ordinary commit.

THE TABLE IS FOUND, NOT GREPPED, and that is this file's own correction. Its first version matched
`| 996 | \\`path\\` | 4 |` anywhere in the document with a MULTILINE regex, and review showed three
carrier changes it stayed green through: wrapping the `doctor.js` row in an HTML comment, wrapping
ALL NINE rows in one, and moving a row into a fenced example.

A SECOND ROUND FOUND THREE MORE, because the first fix modelled too little Markdown: a `~~~text`
fence, four-space indentation, and an UNCLOSED `<!--`. So the reader now models fenced blocks of
either character, indented code blocks, and comments -- and REFUSES the document outright when a
construct is left open, because everything after it is hidden in a real renderer and carrying on
would describe a document nobody sees. A claim about what the document SHOWS cannot be read from
text the document hides.

THE POPULATION COMES FROM THE GATE THAT OWNS IT. `_source_files` and `_line_count` are imported from
`test_no_new_oversized_source_file.py`, and the JS half replicates its sibling's skip set and
filename filters. A second walk written from scratch here would agree until one of them was fixed.
"""

from __future__ import annotations

import io
import os
import re
import unittest
from pathlib import Path

from service.tests.test_no_new_oversized_source_file import (
    LIMIT,
    _line_count,
    _source_files,
)

REPO = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO / "CLAUDE.md"

#: The JS gate's own parameters, from `mcp/stdio/tests/no-new-oversized-source-file.test.js`.
JS_SKIP = frozenset(
    {"node_modules", "tests", "fixtures", "__pycache__", ".git", ".pytest_cache", ".venv", "venv"}
)
JS_NAME = re.compile(r"\.m?js$")
JS_TEST = re.compile(r"\.test\.m?js$")

#: The watch-list's own header, which is how the table is located rather than guessed at.
TABLE_HEADER = "| lines | file | headroom |"
ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*$")

#: A fenced block opens with three or more backticks OR tildes, per CommonMark, and closes with at
#: least as many of the SAME character. Knowing only about backticks let a `~~~text` fence hide the
#: whole table in plain sight.
FENCE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})(.*)$")
COMMENT_OPEN = "<!--"
COMMENT_CLOSE = "-->"
#: Four spaces (or a tab) makes an indented code block. The first version STRIPPED indentation before
#: matching, so an indented copy of the table read as the table itself.
INDENTED_CODE = re.compile(r"^(\s{4,}|\t)\S")
#: A line opening with a raw HTML tag starts an HTML BLOCK, whose contents CommonMark passes to the
#: renderer verbatim. Wrapping the whole table in `<pre>` hid it from a reader that modelled fences
#: and comments only. INLINE html mid-line is not this and does not hide anything.
HTML_BLOCK = re.compile(r"^\s{0,3}</?[a-zA-Z][a-zA-Z0-9-]*")


class HiddenConstruct(Exception):
    """The document uses something this reader cannot judge, so it refuses to judge the document."""


def visible_text(markdown: str) -> str:
    """The document with everything a reader does not see removed.

    THREE CONSTRUCTS HIDE CONTENT and review demonstrated all three: an HTML comment removes it, a
    fence shows it as an EXAMPLE, and four spaces of indentation does the same. The first two are
    worse than removal, because they look like the table while claiming nothing.

    FAILS CLOSED on an unclosed comment. Treating the rest of the file as visible is the assumption
    that let an unterminated `<!--` leave the table apparently intact; refusing is the only honest
    answer, because everything after it is hidden in a real renderer.
    """
    lines = markdown.split("\n")
    out = []
    fence = None            # the exact fence marker that opened the current block
    in_comment = False
    for line in lines:
        if in_comment:
            if COMMENT_CLOSE in line:
                in_comment = False
                # Anything after the close on the same line is visible again.
                out.append(line.split(COMMENT_CLOSE, 1)[1])
            continue
        if fence is not None:
            opener = FENCE.match(line)
            if opener and opener.group(2)[0] == fence[0] and len(opener.group(2)) >= len(fence):
                fence = None
            continue
        opener = FENCE.match(line)
        if opener:
            fence = opener.group(2)
            continue
        if COMMENT_OPEN in line:
            before, rest = line.split(COMMENT_OPEN, 1)
            out.append(before)
            if COMMENT_CLOSE in rest:
                out.append(rest.split(COMMENT_CLOSE, 1)[1])
            else:
                in_comment = True
            continue
        if INDENTED_CODE.match(line):
            continue
        # RAW HTML AT THE START OF A LINE OPENS AN HTML BLOCK, and CommonMark hands its contents to
        # the renderer verbatim -- so a table inside `<pre>` is shown as preformatted text and is not
        # a table at all. Rather than model every block tag and its closing rules, this REFUSES:
        # supported grammar, and an explicit stop outside it. CLAUDE.md contains no raw block HTML
        # today, so the refusal costs nothing until somebody adds some, which is when a human should
        # decide what the gate ought to do about it.
        if HTML_BLOCK.match(line):
            raise HiddenConstruct(
                f"CLAUDE.md opens a raw HTML block ({line.strip()[:40]!r}). This reader models "
                "Markdown only, and content inside an HTML block is not what it appears to be, so "
                "it refuses rather than guessing."
            )
        out.append(line)
    if in_comment:
        raise HiddenConstruct(
            "CLAUDE.md contains an unclosed HTML comment, so everything after it is hidden from a "
            "reader. This gate refuses to judge a document it cannot see."
        )
    if fence is not None:
        raise HiddenConstruct(
            f"CLAUDE.md contains an unclosed {fence!r} fence, so everything after it renders as code."
        )
    return "\n".join(out)


def documented_rows(markdown: str) -> list[tuple[int, str, int]]:
    """The watch-list table, read as a TABLE: located by its header, ended by its first non-row.

    PURE, over text, so the tests below can feed it a document that hides its rows. A reader that has
    only ever seen the real file has never been shown to miss anything.
    """
    lines = visible_text(markdown).split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == TABLE_HEADER)
    except StopIteration:
        return []
    rows = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            break
        if set(stripped) <= set("|-: "):     # the header separator
            continue
        match = ROW.match(stripped)
        if not match:
            break
        rows.append((int(match.group(1)), match.group(2), int(match.group(3))))
    return rows


def js_source_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in JS_SKIP)
        for name in sorted(filenames):
            if JS_NAME.search(name) and not JS_TEST.search(name):
                yield Path(dirpath) / name


def measured_ranking() -> list[tuple[int, str]]:
    """Every file both gates judge, longest first."""
    rows = [(_line_count(p), p.relative_to(REPO).as_posix()) for p in _source_files(REPO)]
    rows += [(_line_count(p), p.relative_to(REPO).as_posix()) for p in js_source_files(REPO)]
    rows.sort(key=lambda row: (-row[0], row[1]))
    return rows


class TheWatchListNamesTheRealFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markdown = io.open(CLAUDE_MD, encoding="utf-8").read()
        cls.measured = measured_ranking()
        cls.documented = documented_rows(cls.markdown)

    def test_both_readers_found_something(self):
        """POSITIVE CONTROL. Either side returning empty makes every assertion below vacuous, and a
        renamed heading or a reformatted table is exactly how that happens quietly."""
        self.assertGreater(len(self.measured), 400, "the walk found almost no source files")
        self.assertGreaterEqual(
            len(self.documented), 8,
            "CLAUDE.md's watch-list table did not parse; if its shape changed, change this reader",
        )

    def test_the_two_files_named_closest_to_the_limit_are_the_closest_two(self):
        """THE CLAIM THAT CHANGES BEHAVIOUR. Everything else in the table is a snapshot."""
        measured_top = {path for _, path in self.measured[:2]}
        documented_top = {path for _, path, _ in self.documented[:2]}
        self.assertEqual(
            documented_top, measured_top,
            "CLAUDE.md names {} as closest to the {}-line limit; the closest two are {}. "
            "Re-run the walk and rewrite the table -- do not amend one row.".format(
                sorted(documented_top), LIMIT, sorted(measured_top),
            ),
        )

    def test_every_file_the_table_names_still_exists(self):
        """A row naming a deleted file is worse than no row: somebody budgets a refactor against it.
        `terminal-runtime.js` sat in this table at rank three after being deleted."""
        known = {path for _, path in self.measured}
        missing = sorted({path for _, path, _ in self.documented} - known)
        self.assertEqual(missing, [], f"the watch-list names files the gates no longer see: {missing}")

    def test_the_table_does_not_skip_a_file_larger_than_one_it_lists(self):
        """THE FAILURE IT HAS HAD TWICE. A ranked list that omits its own middle reads as complete.
        `doctor.js` at 794 was absent while `environments.py` at 780 was listed."""
        named = {path for _, path, _ in self.documented}
        smallest_listed = min((n for n, path in self.measured if path in named), default=0)
        skipped = [
            f"{n} {path}" for n, path in self.measured
            if path not in named and n > smallest_listed
        ]
        self.assertEqual(
            skipped, [],
            "the watch-list skips files longer than ones it lists, so it reads as complete when it "
            f"is not: {skipped}",
        )

    # ── the reader sees only what the document SHOWS ──────────────────────────────────────────
    #
    # Each of these is a carrier change review demonstrated the first version staying green through.

    def _document_with(self, body: str) -> str:
        return f"prose before\n\n{TABLE_HEADER}\n|---|---|---|\n{body}\n\nprose after\n"

    def test_a_row_hidden_in_an_html_comment_is_not_read_as_a_row(self):
        hidden = self._document_with(
            "| 996 | `a.js` | 4 |\n<!--\n| 993 | `b.js` | 7 |\n-->\n| 500 | `c.js` | 500 |"
        )
        self.assertEqual(
            [path for _, path, _ in documented_rows(hidden)], ["a.js"],
            "a commented-out row was read as visible, and the table would claim what it hides",
        )

    def test_a_row_inside_a_fenced_example_is_not_read_as_a_row(self):
        fenced = "prose\n\n```\n" + TABLE_HEADER + "\n|---|---|---|\n| 996 | `x.js` | 4 |\n```\n"
        self.assertEqual(
            documented_rows(fenced), [],
            "a fenced example was read as the real table",
        )

    def test_a_table_that_is_not_there_reads_as_no_rows_and_fails_loudly(self):
        """NEGATIVE CONTROL for the positive control above: an absent table must produce an empty
        list, which `test_both_readers_found_something` then reports rather than passing."""
        self.assertEqual(documented_rows("no table here at all\n"), [])

    def test_positive_control_the_reader_does_read_a_visible_table(self):
        """Without this, a reader that returned nothing for everything would satisfy all three
        controls above while proving the document says nothing at all."""
        visible = self._document_with("| 996 | `a.js` | 4 |\n| 993 | `b.js` | 7 |")
        self.assertEqual(documented_rows(visible), [(996, "a.js", 4), (993, "b.js", 7)])

    def test_a_table_inside_a_TILDE_fence_is_not_read_as_a_table(self):
        """CommonMark fences open with backticks OR tildes. Knowing only about backticks let a
        `~~~text` fence hide the whole table in plain sight."""
        fenced = "prose\n\n~~~text\n" + TABLE_HEADER + "\n|---|---|---|\n| 996 | `x.js` | 4 |\n~~~\n"
        self.assertEqual(documented_rows(fenced), [], "a tilde-fenced example was read as the table")

    def test_a_table_indented_as_a_code_block_is_not_read_as_a_table(self):
        """Four spaces makes a code block. The first reader STRIPPED indentation before matching, so
        an indented copy read as the real thing."""
        indented = (
            "prose\n\n    " + TABLE_HEADER + "\n    |---|---|---|\n    | 996 | `x.js` | 4 |\n"
        )
        self.assertEqual(documented_rows(indented), [], "an indented code block was read as the table")

    def test_an_unclosed_comment_refuses_the_document_rather_than_reading_past_it(self):
        """FAIL CLOSED. Everything after an unterminated `<!--` is hidden in a real renderer, so a
        reader that carries on is describing a document nobody sees. Refusing is the honest answer,
        and it is what makes the three controls above meaningful rather than best-effort."""
        hidden = "prose\n<!-- someone forgot to close this\n" + TABLE_HEADER + "\n|---|---|---|\n| 996 | `x.js` | 4 |\n"
        with self.assertRaises(HiddenConstruct):
            documented_rows(hidden)

    def test_a_table_inside_a_raw_HTML_BLOCK_refuses_rather_than_reading_it(self):
        """`<pre>` around the table hid it from a reader that modelled fences and comments only.

        CommonMark hands an HTML block's contents to the renderer verbatim, so a table in there is
        preformatted text and not a table. Rather than model every block tag, this reader REFUSES:
        supported grammar, and an explicit stop outside it.
        """
        wrapped = (
            "prose\n\n<pre>\n" + TABLE_HEADER + "\n|---|---|---|\n| 996 | `x.js` | 4 |\n</pre>\n"
        )
        with self.assertRaises(HiddenConstruct):
            documented_rows(wrapped)

    def test_inline_html_mid_line_is_not_treated_as_a_block(self):
        """POSITIVE CONTROL for the refusal. Inline markup hides nothing, and a reader that refused
        every document containing a `<` would pass the test above while judging nothing at all."""
        inline = ("some <b>bold</b> prose\n\n" + TABLE_HEADER
                  + "\n|---|---|---|\n| 996 | `a.js` | 4 |\n")
        self.assertEqual([path for _, path, _ in documented_rows(inline)], ["a.js"])

    def test_a_closed_comment_does_not_hide_what_follows_it(self):
        """POSITIVE CONTROL for the refusal above: a NORMAL comment must not swallow the document.
        A reader that refused every file containing `<!--` would pass the test above for the wrong
        reason and never judge anything again."""
        normal = "prose <!-- an aside --> more prose\n\n" + TABLE_HEADER + "\n|---|---|---|\n| 996 | `a.js` | 4 |\n"
        self.assertEqual([path for _, path, _ in documented_rows(normal)], ["a.js"])

    def test_the_reader_stops_at_the_end_of_the_table(self):
        """Prose after the table must not be scavenged for anything that looks like a row."""
        trailing = self._document_with("| 996 | `a.js` | 4 |") + "\n| 12 | `stray.js` | 988 |\n"
        self.assertEqual([path for _, path, _ in documented_rows(trailing)], ["a.js"])


if __name__ == "__main__":
    unittest.main()
