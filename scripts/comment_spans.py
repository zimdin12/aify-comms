"""Where the comments are, in character offsets, for Python and for JavaScript.

WHY IT IS ITS OWN FILE. `deleted-import-census.py` asks whether a deleted module is still named from
CODE, and answering that means knowing which parts of a file are commentary. Those are two
responsibilities, and the census had grown past 400 lines holding both.

OFFSETS, NOT LINES, and that distinction is a defect this replaced. A line holding an import AND a
note about it holds both; a classifier keyed on lines answered PROSE for the import too.

PYTHON IS ANSWERED BY PYTHON'S OWN TOOLS -- `tokenize` for comments, `ast` for docstrings -- so
neither is guessed at. JavaScript has no such tool in the standard library and gets a scanner, which
has been wrong twice: a regex literal whose character class held a quote read as an unterminated
string, and then whole-line classification. It is checked against V8 by its caller rather than
trusted.
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

def _offsets(text: str) -> list[int]:
    """Character offset of the first character of each line, 1-indexed by line."""
    starts = [0, 0]
    for line in text.split(chr(10))[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    return starts


def python_comment_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) character offsets of comments and docstrings, from Python's own tools.

    TWO TOOLS, TWO COLUMN UNITS, and mixing them hid code. `tokenize` reports CHARACTER columns; the
    AST reports UTF-8 BYTE columns. A docstring of forty ASCII characters ends at column 46 either
    way; make those characters non-ASCII and the AST says 86, which read as a character offset
    swallowed the assignment sharing that line and called it PROSE. Review drove both carriers.
    """
    spans: list[tuple[int, int]] = []
    starts = _offsets(text)
    lines = text.split(chr(10))

    def offset(row: int, col: int) -> int:
        return (starts[row] if row < len(starts) else len(text)) + col

    def ast_offset(row: int, byte_col: int) -> int:
        """The AST's byte column, converted to the character column everything else speaks."""
        line = lines[row - 1] if 0 < row <= len(lines) else ""
        prefix = line.encode("utf-8")[:byte_col]
        return offset(row, len(prefix.decode("utf-8", errors="ignore")))

    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                spans.append((offset(*token.start), offset(*token.end)))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return spans
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            spans.append((ast_offset(first.lineno, first.col_offset),
                          ast_offset(first.end_lineno or first.lineno,
                                     first.end_col_offset or first.col_offset)))
    return spans


#: A `/` starts a REGEX rather than a division when the last meaningful thing before it cannot end
#: an expression. The standard JS lexing ambiguity, and the first version of this scanner ignored it
#: entirely: a character class holding a quote opened a string that never closed, and every comment
#: after it read as code. The carriers now include that exact shape.
REGEX_MAY_FOLLOW = set("(,=:[!&|?{};+-*%~^<>") | {""}
REGEX_KEYWORDS = {"return", "typeof", "case", "in", "of", "new", "delete", "void", "throw",
                  "do", "else", "yield", "await", "instanceof"}


def js_comment_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) character offsets of `//` and `/* */` comments, tracking strings and regexes.

    HAND-ROLLED, AND DRIVEN RATHER THAN TRUSTED. This repo's record on hand-rolled JS scanners is
    four of them and four wrong answers, and this one added a fifth and a sixth before its carriers
    caught them: a regex literal read as a string, and then a whole LINE classified from one comment
    on it.

    MISCLASSIFICATION IS SAFE IN ONE DIRECTION ONLY. Reading a comment as CODE over-reports, and an
    over-report is printed for a person to judge. Reading code as a COMMENT hides a real reference.
    So every ambiguity here resolves toward CODE.
    """
    spans: list[tuple[int, int]] = []
    i = 0
    state = "code"          # code | line_comment | block_comment | regex | ' | " | `
    previous = ""           # last meaningful character seen in code
    start = 0
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                state, start, i = "line_comment", i, i + 2
                continue
            if char == "/" and nxt == "*":
                state, start, i = "block_comment", i, i + 2
                continue
            if char == "/" and _regex_may_start(text, i, previous):
                state, i = "regex", i + 1
                continue
            if char in "'\"`":
                state, i = char, i + 1
                continue
            if not char.isspace():
                previous = char
            i += 1
            continue
        if state == "line_comment":
            if char == chr(10):
                spans.append((start, i))
                state = "code"
            i += 1
            continue
        if state == "block_comment":
            if char == "*" and nxt == "/":
                spans.append((start, i + 2))
                state, i = "code", i + 2
                continue
            i += 1
            continue
        if state == "regex":
            if char == "\\":
                i += 2
                continue
            if char == "[":
                # A CHARACTER CLASS SWALLOWS `/`, and this is where the quote in a class like
                # ["'`] lives. Skip to its close rather than ending the regex early.
                close = text.find("]", i + 1)
                i = (close + 1) if close != -1 else i + 1
                continue
            if char == "/":
                state, previous = "code", "/"
            i += 1
            continue
        # inside a string or template literal
        if char == "\\":
            i += 2
            continue
        if char == state:
            state = "code"
            previous = char
        i += 1
    if state in ("line_comment", "block_comment"):
        spans.append((start, len(text)))
    return spans


def _regex_may_start(text: str, index: int, previous: str) -> bool:
    """Could a regex literal begin at this `/`? Ambiguity resolves toward NO, which means CODE."""
    if previous in REGEX_MAY_FOLLOW:
        return True
    before = text[:index].rstrip()
    word = ""
    while before and (before[-1].isalpha() or before[-1] == "_"):
        word = before[-1] + word
        before = before[:-1]
    return word in REGEX_KEYWORDS


def comment_spans(path: Path) -> list[tuple[int, int]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return python_comment_spans(text) if path.suffix == ".py" else js_comment_spans(text)
