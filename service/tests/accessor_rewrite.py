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

THE FIX IS TO ASK THE PARSER, TWICE OVER — and it took three rounds of review to get both halves:

  WHICH lines are off-limits: `ast` gives the exact range of every `_borrowed_*` function.
  WHAT may be replaced: `tokenize` gives NAME tokens, so comments and string literals are never
  touched. A constant name appearing inside SQL text or a JSON key is DATA, and rewriting it would
  change behaviour, not just formatting.

And nothing is reconstructed by hand. Untouched spans are copied verbatim out of the original source
by absolute offset — no `ast.unparse` (it would reformat the file) and no synthesised newlines (the
first token-aware attempt invented them and put a blank line between every original line). The
byte-identity this series proves every slice against has to hold for the tool that performs the
slices.
"""

from __future__ import annotations

import ast


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

    So the replacement domain is Python NAME tokens only. `tokenize` decides what is a name.

    And nothing is SYNTHESISED on the way out. The first token-aware version rebuilt the file from
    token strings plus invented gap newlines, which double-emitted line breaks and put a blank line
    between every original line -- a whitespace change across untouched code. Every span is now
    copied verbatim out of the original source by absolute offset, so comments, strings of every
    quoting style, indentation, blank-line runs, tabs and non-ASCII come back byte for byte.
    """
    if not constants:
        return source
    protected = accessor_line_ranges(source)
    targets = set(constants)

    def is_protected(lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in protected)

    import io as _io
    import tokenize as _tokenize

    # ABSOLUTE-OFFSET SLICING. The first token-aware version reconstructed the file from token
    # strings plus synthesised gap newlines, and double-emitted line breaks: it appended "\n" for
    # the row change AND the NEWLINE/NL token itself, so a blank line appeared between every
    # original line. That is a whitespace change across untouched code -- it destroys the
    # byte-identity evidence this series proves each slice with, and it can move line-sensitive
    # source probes.
    #
    # So nothing is synthesised. Every span between tokens is copied verbatim out of the ORIGINAL
    # source, and each token is either replaced (an unprotected matching NAME) or copied verbatim
    # too. Untouched means untouched, byte for byte.
    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def offset(row: int, col: int) -> int:
        return line_starts[row - 1] + col

    out = []
    pos = 0
    for token in _tokenize.generate_tokens(_io.StringIO(source).readline):
        start = offset(*token.start)
        end = offset(*token.end)
        if start < pos:          # tokenize emits some zero-width markers out of order
            continue
        out.append(source[pos:start])
        if (token.type == _tokenize.NAME
                and token.string in targets
                and not is_protected(token.start[0])):
            out.append(f"{accessor_name(token.string)}()")
        else:
            out.append(source[start:end])
        pos = end
    out.append(source[pos:])
    return "".join(out)


def build_accessor(constant: str, owner: str = "service.routers.api_v2") -> str:
    """The accessor itself, written once here so no migration script hand-rolls it again."""
    return (
        f'\n\ndef {accessor_name(constant)}():\n'
        f'    """BORROWED constant: one owner, never a copy (finding N7)."""\n'
        f'    from {owner} import {constant}\n\n'
        f'    return {constant}\n'
    )
