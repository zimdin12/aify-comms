"""Prove an extract-method refactor preserved behaviour, by INLINING IT BACK.

The operator's ruling, and it is correct: *"splitting methods is also structural. big functions are
also sign of bad architecture"*. Extract-method is behaviour-preserving by construction, so it
belongs in the v0.5.x empty-behaviour-changelog series.

The reason we had excluded it was verification convenience, not correctness: an AST body-compare —
the proof standard every v0.5 slice met — cannot check a split, because the body deliberately
changed. That is an argument for a better gate, not for leaving a 684-line `register_agent` alone.

THE PROOF: if extracting block B out of F into H is behaviour-preserving, then substituting H's body
back over the call to H must reproduce F EXACTLY. Inline it back and AST-compare against the
original. Structural equality of the round trip is the evidence.

    original F                      split F'                    inline_back(F', H)
    ----------                      --------                    ------------------
    stmt1                           stmt1                       stmt1
    <block B>            ==>        H(...)          ==>         <block B>          == original F
    stmt3                           stmt3                       stmt3

TWO SHAPES ARE ACCEPTED, because refusing the second would refuse nearly every real extraction:

    VOID     `_helper(args)`         block spliced back in place.
    VALUE    `x = _helper(args)`     block spliced back, and the helper's single TRAILING `return v`
                                     rewritten to `x = v` (dropped entirely when it is `x = x`,
                                     which is a no-op and the usual case).

WHERE THIS GATE CAN BE FOOLED, which is the part that matters:

`escapes()` exists because inline-back is NOT sufficient on its own. A MID-BLOCK `return` does not
mean the same thing after extraction — it returns from H, not from F, so F carries on where it
previously stopped — yet inlining H's body back into F's call site reproduces F perfectly, so the
round trip PASSES while the behaviour changed. Same for `break`/`continue` (they would escape a loop
that is no longer around them) and `yield` (which turns H into a generator and silently makes F stop
yielding). A gate with a known blind spot is only honest if it refuses the inputs it cannot judge,
so any escape OTHER than a single trailing `return` is REJECTED rather than passed.

This module is test-support, not production code, and is deliberately small enough to be read in one
sitting — the whole point is that the reviewer can verify the verifier.
"""

from __future__ import annotations

import ast
import copy

ESCAPES = (ast.Return, ast.Break, ast.Continue, ast.Yield, ast.YieldFrom)


def _strip(node: ast.AST) -> ast.AST:
    """Normalise away everything that is not behaviour: docstrings positions, line numbers."""
    clone = copy.deepcopy(node)
    for sub in ast.walk(clone):
        for attr in ("lineno", "col_offset", "end_lineno", "end_col_offset", "type_comment"):
            if hasattr(sub, attr):
                try:
                    setattr(sub, attr, None)
                except (AttributeError, ValueError):
                    pass
    return clone


def normalized(node: ast.AST) -> str:
    return ast.dump(_strip(node), annotate_fields=True, include_attributes=False)


def escapes(block: list[ast.stmt]) -> list[str]:
    """Control-flow escapes that make extraction unsafe, ignoring nested function/class scopes.

    A `return` inside a nested `def` within the block is that inner function's own return and is
    not an escape from the block — walking blindly would reject safe extractions, so nested scopes
    are skipped explicitly.
    """
    found: list[str] = []

    def walk(node: ast.AST, *, top: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue  # its own scope; its returns are not our escapes
            if isinstance(child, ESCAPES):
                found.append(type(child).__name__)
            walk(child, top=False)

    for stmt in block:
        # A nested def/class at the TOP of the block is its own scope too. The recursive walk
        # already skips them as children; missing them here let `def inner(): return 1` count as
        # an escape and would have rejected a safe extraction.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ESCAPES):
            found.append(type(stmt).__name__)
        walk(stmt, top=True)
    return found


def _find_call_stmt(body: list[ast.stmt], helper: str) -> int:
    """Index of the single statement in `body` that calls `helper`. Exactly one must exist."""
    hits = [
        i
        for i, stmt in enumerate(body)
        if any(
            isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == helper)
                or (isinstance(n.func, ast.Attribute) and n.func.attr == helper)
            )
            for n in ast.walk(stmt)
        )
    ]
    if len(hits) != 1:
        raise AssertionError(
            f"expected exactly one call to {helper!r} in the split function, found {len(hits)}. "
            "Inline-back is only defined for a single call site."
        )
    return hits[0]


def _helper_body(helper_fn: ast.AST) -> list[ast.stmt]:
    body = copy.deepcopy(helper_fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # the helper's new docstring is not part of the behaviour
    return body


def inline_back(split_fn: ast.AST, helper_fn: ast.AST) -> ast.AST:
    """Substitute `helper_fn`'s body over the call to it inside `split_fn`.

    Handles the two shapes an extract-method actually takes:

      VOID    `_helper(args)`            -> body spliced in place.
      VALUE   `x = _helper(args)`        -> body spliced in place, and its single trailing
                                            `return v` rewritten back to `x = v`.

    The VALUE shape is the common one — most useful extractions compute something — so a gate that
    could not express it would refuse nearly every real split and be quietly abandoned. It is
    handled explicitly rather than waved through: only ONE trailing return, at the very end, with
    no other escape anywhere in the helper (`_returns_only_at_tail`).
    """
    result = copy.deepcopy(split_fn)
    index = _find_call_stmt(result.body, helper_fn.name)
    call_stmt = result.body[index]
    body = _helper_body(helper_fn)

    if body and isinstance(body[-1], ast.Return) and isinstance(call_stmt, (ast.Assign, ast.AnnAssign)):
        returned = body[-1].value
        targets = call_stmt.targets if isinstance(call_stmt, ast.Assign) else [call_stmt.target]
        # `total = _sum_weights(rows)` inlines to `... ; total = total` when the helper returns the
        # very variable the caller rebinds — the overwhelmingly common case, since the extracted
        # block already computed it under that name. A self-assignment is a no-op, so dropping it
        # is a normalization, not a concession: keeping it would fail every correct extraction of
        # this shape and make the gate useless.
        self_assign = (
            len(targets) == 1
            and isinstance(targets[0], ast.Name)
            and isinstance(returned, ast.Name)
            and targets[0].id == returned.id
        )
        if self_assign:
            body = body[:-1]
        else:
            rebound = ast.Assign(targets=copy.deepcopy(targets), value=copy.deepcopy(returned))
            body = body[:-1] + [rebound]

    result.body[index:index + 1] = body
    return result


def _returns_only_at_tail(helper_fn: ast.AST) -> bool:
    """True when the helper's ONLY escape is a single `return` as its final statement."""
    body = _helper_body(helper_fn)
    if not body or not isinstance(body[-1], ast.Return):
        return False
    return not escapes(body[:-1])


def assert_extraction_preserves_behaviour(original_src: str, split_src: str, helper_name: str) -> None:
    """The gate. Raises AssertionError with a readable reason if the split is not provably inert.

    `original_src` is the function BEFORE the split; `split_src` is the module text containing both
    the split function and the extracted helper.
    """
    original = ast.parse(original_src).body[0]
    module = ast.parse(split_src)
    funcs = {n.name: n for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if helper_name not in funcs:
        raise AssertionError(f"extracted helper {helper_name!r} not found in the split source")
    if original.name not in funcs:
        raise AssertionError(f"original function {original.name!r} not found in the split source")

    helper = funcs[helper_name]
    bad = escapes(_helper_body(helper))
    # A single trailing `return` is the VALUE-shape extraction, which inline_back models exactly by
    # rewriting it back to the caller's assignment. Any OTHER escape is the blind spot.
    if bad and not _returns_only_at_tail(helper):
        raise AssertionError(
            f"REFUSED: extracted block contains control-flow escape(s) {sorted(set(bad))} that are "
            "not a single trailing `return`. A mid-block `return` exits the HELPER, not the "
            "original function, so behaviour changed — and inline-back would still reproduce the "
            "original, meaning this gate CANNOT see it. Restructure the split (hoist the early "
            "exit into the caller) or leave the block in place."
        )

    rebuilt = inline_back(funcs[original.name], helper)
    if normalized(rebuilt) != normalized(original):
        raise AssertionError(
            "extraction is NOT behaviour-preserving: inlining the helper back did not reproduce the "
            "original function.\n"
            "This is the whole proof - if the round trip does not close, something other than a "
            "pure block-lift happened (a reordered statement, a changed name, a dropped line)."
        )
