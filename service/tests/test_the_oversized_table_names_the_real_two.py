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

from markdown_it import MarkdownIt

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

#: THE DIALECT, stated. CommonMark plus GFM tables, which is what renders this file.
MARKDOWN = MarkdownIt("commonmark").enable("table")

#: The heading cells that identify the watch-list among any other tables in the document.
TABLE_HEADINGS = ("lines", "file", "headroom")


class HiddenConstruct(Exception):
    """Kept for the tests that assert a document is refused rather than misread.

    A REAL PARSER MAKES MOST REFUSALS UNNECESSARY: content inside a comment, a fence, an indented
    block, `<pre>`, CDATA or `<script>` simply never becomes a table, so there is nothing to refuse
    and nothing to enumerate. What remains worth refusing is a document with NO watch-list table at
    all, because that is indistinguishable from one whose table was hidden.
    """


def _cells(tokens, start):
    """The text of each cell in the row beginning at `start`, and the index after it."""
    cells, i = [], start
    while i < len(tokens) and tokens[i].type not in ("tr_close",):
        if tokens[i].type == "inline":
            cells.append(tokens[i].content.strip())
        i += 1
    return cells, i


def documented_rows(markdown: str) -> list[tuple[int, str, int]]:
    """The watch-list table, parsed as Markdown rather than matched as text.

    THE WHOLE CLASS OF CARRIER MUTATIONS DIES HERE. A parser does not produce a table for text inside
    a comment, a fence, an indented block, an HTML block of any kind, or a `<script>` -- and it does
    not produce one for a pipe-delimited block with no delimiter row either, which is where the
    hand-rolled reader failed last. Nothing needs to enumerate what is forbidden.
    """
    tokens = MARKDOWN.parse(markdown)
    rows: list[tuple[int, str, int]] = []
    i = 0
    while i < len(tokens):
        if tokens[i].type != "table_open":
            i += 1
            continue
        # Read this table's heading row to see whether it is the watch-list.
        j, headings, body = i + 1, [], []
        while j < len(tokens) and tokens[j].type != "table_close":
            if tokens[j].type == "tr_open":
                cells, j = _cells(tokens, j + 1)
                (headings if not headings else body).append(cells)
            j += 1
        i = j + 1
        if not headings or tuple(h.lower() for h in headings[0]) != TABLE_HEADINGS:
            continue
        for cells in body:
            if len(cells) != 3:
                continue
            lines_text, path_text, headroom_text = cells
            path = path_text.strip("`")
            if not lines_text.isdigit() or not headroom_text.isdigit():
                continue
            rows.append((int(lines_text), path, int(headroom_text)))
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

    def test_a_document_with_no_table_reads_as_no_rows(self):
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

    def test_a_table_after_an_unclosed_comment_is_not_a_table(self):
        """FAIL CLOSED. Everything after an unterminated `<!--` is hidden in a real renderer, so a
        reader that carries on is describing a document nobody sees. Refusing is the honest answer,
        and it is what makes the three controls above meaningful rather than best-effort."""
        hidden = "prose\n<!-- someone forgot to close this\n" + TABLE_HEADER + "\n|---|---|---|\n| 996 | `x.js` | 4 |\n"
        self.assertEqual(documented_rows(hidden), [],
                         "text after an unterminated comment was read as a table")

    def test_a_table_inside_a_raw_HTML_BLOCK_is_not_a_table(self):
        """`<pre>` around the table hid it from a reader that modelled fences and comments only.

        CommonMark hands an HTML block's contents to the renderer verbatim, so a table in there is
        preformatted text and not a table. Rather than model every block tag, this reader REFUSES:
        supported grammar, and an explicit stop outside it.
        """
        wrapped = (
            "prose\n\n<pre>\n" + TABLE_HEADER + "\n|---|---|---|\n| 996 | `x.js` | 4 |\n</pre>\n"
        )
        self.assertEqual(documented_rows(wrapped), [],
                         "text inside an HTML block was read as a table")

    def test_a_table_inside_CDATA_is_not_a_table(self):
        """The first version of the refusal matched `</?[a-zA-Z]`, so `<pre>` was caught and
        `<![CDATA[ ... ]]>` sailed through and hid the whole table.

        Adding CDATA to a list of known-bad constructs would be another construct-specific regex, so
        the allowed grammar is stated positively instead: a line does not begin with `<`. Zero lines
        of the real document do.
        """
        wrapped = ("prose\n\n<![CDATA[\n" + TABLE_HEADER
                   + "\n|---|---|---|\n| 996 | `x.js` | 4 |\n]]>\n")
        self.assertEqual(documented_rows(wrapped), [], "text inside CDATA was read as a table")

    def test_an_UNCLOSED_processing_instruction_swallows_the_table(self):
        """CommonMark's HTML block type 3 runs until `?>`. Without one it takes the rest with it."""
        wrapped = ("prose\n\n<?xml version=\"1.0\"\n" + TABLE_HEADER
                   + "\n|---|---|---|\n| 996 | `x.js` | 4 |\n")
        self.assertEqual(documented_rows(wrapped), [],
                         "a table inside an unterminated processing instruction was read as a table")

    def test_a_CLOSED_processing_instruction_hides_nothing(self):
        """AND THE OTHER DIRECTION, which I had wrong until the parser said so.

        `<?xml version="1.0"?>` closes on its own line, so the block ends there and what follows is
        an ordinary table. A reader that refused everything after any `<` would call this hidden and
        be wrong -- which is precisely the failure mode of the hand-rolled version this replaced.
        """
        wrapped = ("prose\n\n<?xml version=\"1.0\"?>\n" + TABLE_HEADER
                   + "\n|---|---|---|\n| 996 | `x.js` | 4 |\n")
        self.assertEqual([path for _, path, _ in documented_rows(wrapped)], ["x.js"],
                         "a closed processing instruction was treated as though it hid the table")

    def test_a_pipe_block_with_no_delimiter_row_is_not_a_table(self):
        """ORDINARY MARKDOWN, not an exotic wrapper, and the hand-rolled reader accepted it.

        A GFM table needs its `|---|---|---|` row. Without one the block is a paragraph containing
        pipe characters, which is what a browser renders and what markdown-it produces.
        """
        no_delimiter = ("prose\n\n" + TABLE_HEADER + "\n| 996 | `x.js` | 4 |\n")
        self.assertEqual(documented_rows(no_delimiter), [],
                         "a pipe-delimited paragraph with no delimiter row was read as a table")

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
