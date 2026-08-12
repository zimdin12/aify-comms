"""Rewrite `CONSTANT` to `_borrowed_constant()` in a module — without corrupting the accessors.

WHY THIS EXISTS AS A TESTED UNIT rather than a few lines inside a migration script.

Moving a helper out of `api_v2` while its constants stay behind means every use of those constants
in the moved body has to become a call to a borrowed accessor. Doing that with a plain regex over the
file text has now produced the same defect twice, both times shipped:

    def _borrowed_listen_events():
        from service.routers.api_v2 import _listen_events
        return _borrowed_listen_events()        # <- rewrote its OWN return. RecursionError.

    def _borrowed_channel_claim_runtimes():
        from service.routers.api_v2 import _borrowed_channel_claim_runtimes()   # <- and its import
                                                                                #    SyntaxError.

Both came from the same idea: "apply the regex to everything except the accessor bodies", implemented
by splitting the text on a marker string. Marker-splitting is guesswork about where a function
begins and ends, and it was wrong in two different ways.

THE FIX IS TO ASK THE PARSER. `ast` knows exactly which line ranges belong to a `_borrowed_*`
function; those lines are excluded and every other line is rewritten. Line-based rather than
`ast.unparse`, deliberately: unparsing would reformat the whole file and destroy the byte-identity
this series proves every slice against.
"""

from __future__ import annotations

import ast
import re


def accessor_line_ranges(source: str) -> list[tuple[int, int]]:
    """1-based inclusive line ranges of every `_borrowed_*` function, including its decorators."""
    ranges = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("_borrowed_"):
            continue
        start = node.lineno
        if node.decorator_list:
            start = min(start, min(d.lineno for d in node.decorator_list))
        ranges.append((start, node.end_lineno))
    return sorted(ranges)


def accessor_name(constant: str) -> str:
    return "_borrowed_" + constant.lower().lstrip("_")


def rewrite(source: str, constants: list[str]) -> str:
    """Replace CODE references to each constant with its accessor call.

    TOKEN-AWARE, and that is the whole correctness argument. The first version substituted over raw
    line text outside the protected ranges, which the reviewer showed also rewrites COMMENTS and
    STRING LITERALS:

        # comment _LISTEN_EVENTS          ->  # comment _borrowed_listen_events()
        msg = 'literal _LISTEN_EVENTS'    ->  msg = 'literal _borrowed_listen_events()'

    The comment case is merely an undeclared textual change, which is bad enough in a series that
    proves every slice on byte identity. The string case is BEHAVIOUR-CHANGING: constant names show
    up as data in SQL text, JSON keys, regex sources, telemetry names and diagnostic messages, and
    silently rewriting them changes what the program does.

    So the replacement domain is Python NAME tokens only. `tokenize` decides what is a name; every
    other token -- comments, strings of every quoting style, whitespace, blank-line runs, non-ASCII
    -- is emitted back exactly as it was read.
    """
    if not constants:
        return source
    protected = accessor_line_ranges(source)
    targets = set(constants)

    def is_protected(lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in protected)

    import io as _io
    import tokenize as _tokenize

    out = []
    last_end = (1, 0)
    for token in _tokenize.generate_tokens(_io.StringIO(source).readline):
        start_row, start_col = token.start
        # everything between the previous token and this one, verbatim
        if start_row > last_end[0]:
            out.append("\n" * (start_row - last_end[0]))
            last_end = (start_row, 0)
        if start_col > last_end[1]:
            out.append(source.split("\n")[start_row - 1][last_end[1]:start_col])

        text = token.string
        if (token.type == _tokenize.NAME and text in targets
                and not is_protected(start_row)):
            text = f"{accessor_name(text)}()"
        out.append(text)
        last_end = token.end
    return "".join(out)


def build_accessor(constant: str, owner: str = "service.routers.api_v2") -> str:
    """The accessor itself, written once here so no migration script hand-rolls it again."""
    return (
        f'\n\ndef {accessor_name(constant)}():\n'
        f'    """BORROWED constant: one owner, never a copy (finding N7)."""\n'
        f'    from {owner} import {constant}\n\n'
        f'    return {constant}\n'
    )
