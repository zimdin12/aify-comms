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

`live_out_violations()` exists for the SAME reason and is the sharper example, because the reviewer
found it in a test in this repo that asserted a broken split was clean. If the helper binds a local
the caller goes on to read, that name is a helper local after the split and the caller raises
NameError — but inline-back reconstructs the ORIGINAL, which is correct by definition, so the round
trip passes. The proof examines the wrong artifact for this class, so live-outs are computed
directly from the split instead of inferred from the round trip.

WITH / TRY REGIONS need no separate rule, which was worth PROBING rather than assuming. Hoisting a
call out of a `with` or a `try` changes the reconstructed tree, so the round trip already refuses
exactly the dangerous cases and allows the safe ones. The probe did surface a false REJECTION,
though: a call nested inside a `with` resolved to the enclosing `with` statement, so the inliner
replaced the whole block and the commonest safe shape looked like a behaviour change. `_find_call_site`
is depth-aware for that reason — in a 684-line handler almost every extractable block is nested.

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


#: Names whose behaviour depends on the CALL FRAME, so moving code under a new frame changes them.
#: A helper adds a stack frame: tracebacks, `inspect.stack()`, and bare `locals()` all shift.
FRAME_SENSITIVE = {
    "locals", "globals", "vars", "exc_info", "currentframe",
    "stack", "extract_stack", "print_stack", "setprofile", "settrace",
}


def _nested_scopes(node: ast.AST):
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))


def _assigned_names(nodes) -> set[str]:
    """Names BOUND IN THIS SCOPE by these statements.

    Stores inside a nested `def`/`lambda`/`class` bind that inner scope, NOT this one, so they are
    skipped — counting them would let a helper-bound name look rebound by code that cannot rebind
    it. The reviewer caught the earlier version claiming to skip nested scopes in its docstring
    while using a flat `ast.walk` that did not.
    """
    out: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if _nested_scopes(child):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    out.add(child.name)  # the def itself binds its name HERE
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                out.add(child.id)
            visit(child)

    for stmt in nodes:
        if _nested_scopes(stmt):
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(stmt.name)
            continue
        if isinstance(stmt, ast.Name) and isinstance(stmt.ctx, ast.Store):
            out.add(stmt.id)
        visit(stmt)
    return out


def _loaded_names(nodes) -> set[str]:
    """Names READ by these statements, INCLUDING inside nested scopes.

    Deliberately asymmetric with `_assigned_names`. A nested `def` that references the name captures
    it from this scope, which is a genuine read of the outer local — so nested loads count. Being
    over-inclusive here only ever refuses an extraction; being under-inclusive would bless a broken
    one, and this gate's whole job is to fail in the safe direction.
    """
    out: set[str] = set()
    for stmt in nodes:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                out.add(sub.id)
    return out


def _augmented_reads(stmt: ast.stmt) -> set[str]:
    """Names an augmented assignment READS, which the AST marks only as a Store.

    THE THIRD LIVE-OUT HOLE, found by the reviewer and confirmed by running it: in `total += 1` the
    target `total` is a `Name` with `ctx=Store`, so `_loaded_names` never sees a load — but `+=`
    reads the old value before writing the new one. The ordered scan therefore treated it as a clean
    rebind and passed a broken split:

        _w()            # binds `total`, does not hand it back
        total += 1      # reads a name that is now a helper local

    Compound targets (`total[i] += 1`, `obj.total += 1`) already register their base as a Load, but
    they are resolved here too so the rule does not depend on that incidental detail.
    """
    out: set[str] = set()
    for sub in ast.walk(stmt):
        if not isinstance(sub, ast.AugAssign):
            continue
        target = sub.target
        while isinstance(target, (ast.Subscript, ast.Attribute)):
            target = target.value
        if isinstance(target, ast.Name):
            out.add(target.id)
    return out


def _read_before_rebind(after: list[ast.stmt], name: str) -> bool:
    """Walking the post-call statements IN ORDER: is `name` read before it is bound again?

    THE ORDER BUG THE REVIEWER FOUND. The first version subtracted every name assigned anywhere
    after the call, which hid a real violation behind a later assignment:

        _w()            # binds `total`, does not hand it back
        use(total)      # BROKEN -- reads a name that is now a helper local
        total = 0       # ...and this later line made the check ignore the line above

    Liveness is positional. A rebind only kills liveness AFTER the rebind, never before it.
    """
    for stmt in after:
        loads = _loaded_names([stmt]) | _augmented_reads(stmt)
        stores = _assigned_names([stmt])
        # Within one statement a load is evaluated before the bind (`total = total + 1`), so a load
        # in the same statement counts as a read first.
        if name in loads:
            return True
        if name in stores:
            return False
    return False


def live_out_violations(split_fn: ast.AST, helper_fn: ast.AST) -> list[str]:
    """Locals the helper binds that the CALLER still reads afterwards, without being handed back.

    THE DEFECT THIS EXISTS FOR, found by the reviewer in my own "clean extraction" test:

        def handler(...):
            _accumulate(rows)              # helper binds `total`
            label = f"{name}:{total}"      # caller reads `total` -> NameError

    Inline-back CLOSES on this — splicing the helper's body back reproduces the original perfectly —
    so the round trip called it proven while the split was broken. `total` was a caller local before
    the extraction and is a helper local after it. Nothing about the structural proof can see that,
    because the proof compares the RECONSTRUCTED original, not the split.

    So live-outs are computed directly: names the helper binds, that the caller reads after the call
    site, that the call does not rebind. Any such name is a defect, not a warning.
    """
    block, index = _find_call_site(split_fn, helper_fn.name)
    call_stmt = block[index]
    # Only statements in the SAME block after the call are guaranteed to run with those bindings.
    after = block[index + 1:]

    bound_by_helper = _assigned_names(_helper_body(helper_fn))
    # Parameters are the caller's values passed in, not new bindings escaping outward.
    params = {a.arg for a in list(helper_fn.args.args) + list(helper_fn.args.kwonlyargs)}
    bound_by_helper -= params

    rebound_at_call = _assigned_names([call_stmt])
    # Positional, not set-based: a later rebind kills liveness only AFTER it, never before.
    escaped = sorted(
        name for name in (bound_by_helper - rebound_at_call)
        if _read_before_rebind(after, name)
    )
    return [
        f"`{name}` is assigned inside the helper and read by the caller afterwards, but the call "
        f"does not hand it back - after the split it is a HELPER local and the caller raises "
        f"NameError (or silently reads a stale outer binding)"
        for name in escaped
    ]


def preconditions(helper_fn: ast.AST, *, caller_is_async: bool) -> list[str]:
    """Reasons this block must NOT be extracted, beyond the escape rule.

    Every entry is a case where inline-back would still close — the round trip reproduces the
    original — while real behaviour changed. That is the whole reason this list exists: the proof
    is structural, so anything NON-structural has to be refused up front rather than blessed.

    Deliberately conservative. For an empty-behaviour-changelog series, wrongly refusing a safe
    extraction costs a manual review; wrongly allowing an unsafe one ships a silent defect.
    """
    body = _helper_body(helper_fn)
    reasons: list[str] = []

    def walk_stmts(nodes):
        for stmt in nodes:
            for sub in ast.walk(stmt):
                yield sub

    for sub in walk_stmts(body):
        # Rebinding module/enclosing scope from a new function is not the same binding operation.
        if isinstance(sub, ast.Global):
            reasons.append(f"`global {', '.join(sub.names)}` — rebinding module scope from a new frame")
        if isinstance(sub, ast.Nonlocal):
            reasons.append(f"`nonlocal {', '.join(sub.names)}` — the enclosing scope is no longer enclosing")
        # `del x` cannot be expressed through a return value; if x is live after B, silently wrong.
        if isinstance(sub, ast.Delete):
            reasons.append("`del` — deletion cannot travel back through a return value")
        # A closure defined in B captures F's locals; moved into H it captures H's instead.
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            reasons.append(
                f"defines a nested {type(sub).__name__} — it would capture the HELPER's locals, not the original's"
            )
        # Frame-sensitive builtins: a new frame is exactly what breaks them.
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name in FRAME_SENSITIVE:
                reasons.append(f"calls `{name}()` — frame-sensitive, and extraction adds a frame")

    # An `await` in the block forces H to be async and the call site to await it. If the caller is
    # sync there is no correct extraction at all.
    has_await = any(isinstance(s, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for s in walk_stmts(body))
    if has_await and not caller_is_async:
        reasons.append("contains `await`/`async for`/`async with` but the original function is sync")
    if has_await and not isinstance(helper_fn, ast.AsyncFunctionDef):
        reasons.append("contains `await` but the extracted helper is not `async def`")

    return sorted(set(reasons))


#: Statement fields that hold a nested block of statements.
_BLOCK_FIELDS = ("body", "orelse", "finalbody")


def _calls(stmt: ast.stmt, helper: str) -> bool:
    return any(
        isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == helper)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == helper)
        )
        for n in ast.walk(stmt)
    )


def _find_call_site(fn: ast.AST, helper: str) -> tuple[list[ast.stmt], int]:
    """The (block, index) of the single statement calling `helper`, AT ANY DEPTH.

    Depth matters, and getting it wrong made the gate useless on real code. The first version only
    looked at the function's TOP-LEVEL statements, so a call nested inside a `with` resolved to the
    enclosing `with` statement — and the inliner then replaced the whole `with` block with the
    helper's body. That made a perfectly safe extraction (the call staying INSIDE the context
    manager, which is the common shape) fail as if it were a behaviour change. In a 684-line handler
    almost every extractable block is nested inside something.

    Innermost wins: the deepest block containing exactly one calling statement.
    """
    found: list[tuple[list[ast.stmt], int]] = []

    def descend(block: list[ast.stmt]) -> None:
        for index, stmt in enumerate(block):
            if not _calls(stmt, helper):
                continue
            deeper_before = len(found)
            for field in _BLOCK_FIELDS:
                nested = getattr(stmt, field, None)
                if isinstance(nested, list) and nested and isinstance(nested[0], ast.stmt):
                    descend(nested)
            for handler in getattr(stmt, "handlers", []) or []:
                descend(handler.body)
            if len(found) == deeper_before:
                found.append((block, index))  # nothing deeper claimed it

    descend(fn.body)
    if len(found) != 1:
        raise AssertionError(
            f"expected exactly one call to {helper!r} in the split function, found {len(found)}. "
            "Inline-back is only defined for a single call site."
        )
    return found[0]


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
    block, index = _find_call_site(result, helper_fn.name)
    call_stmt = block[index]
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

    block[index:index + 1] = body
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

    blocked = preconditions(helper, caller_is_async=isinstance(original, ast.AsyncFunctionDef))
    if blocked:
        raise AssertionError(
            "REFUSED: the extracted block is not structurally movable:\n  - "
            + "\n  - ".join(blocked)
            + "\nEach of these would still pass inline-back — the round trip closes while behaviour "
            "changed — so they are refused up front instead of being blessed by a proof that cannot "
            "see them."
        )

    leaked = live_out_violations(funcs[original.name], helper)
    if leaked:
        raise AssertionError(
            "REFUSED: the split does not hand back every local the caller still needs:\n  - "
            + "\n  - ".join(leaked)
            + "\nInline-back CANNOT catch this - the round trip reconstructs the ORIGINAL, which is "
            "correct by definition, while the SPLIT is what breaks. Return the value(s) and rebind "
            "them at the call site."
        )

    rebuilt = inline_back(funcs[original.name], helper)
    if normalized(rebuilt) != normalized(original):
        raise AssertionError(
            "extraction is NOT behaviour-preserving: inlining the helper back did not reproduce the "
            "original function.\n"
            "This is the whole proof - if the round trip does not close, something other than a "
            "pure block-lift happened (a reordered statement, a changed name, a dropped line)."
        )
