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

`live_in_violations()` is the DUAL of that, and the last of the four. If the helper READS a caller
local it was never handed, the split raises NameError — while inline-back reconstructs the original,
where the name is perfectly in scope. Free variables of the helper are therefore computed directly
too. A name only counts if it is genuinely a local (or parameter) of the CALLER: one the caller never
bound is a global or an import, and there was never anything to hand over.

WITH / TRY REGIONS need no separate rule, which was worth PROBING rather than assuming. Hoisting a
call out of a `with` or a `try` changes the reconstructed tree, so the round trip already refuses
exactly the dangerous cases and allows the safe ones. The probe did surface a false REJECTION,
though: a call nested inside a `with` resolved to the enclosing `with` statement, so the inliner
replaced the whole block and the commonest safe shape looked like a behaviour change. `_find_call_site`
is depth-aware for that reason — in a 684-line handler almost every extractable block is nested.

`call_signature_violations()` is the fifth, and it closes the gap the fourth opened: knowing a name
is a PARAMETER says nothing about whether the CALL supplies it. `_w()` against `def _w(x)` raises
TypeError before the body runs, and inline-back never looks at the calling convention because it
splices the body. Checked directly, in a deliberately narrow dialect — no defaults, no `*args`/
`**kwargs`, no positional-only or keyword-only parameters. Those are all expressible and all add ways
to be subtly wrong, and a mechanically-generated extraction needs none of them.

SAME-NAME HANDOFF ONLY is the sixth and subtlest rule. Supplying the right parameter with the WRONG
caller value (`_w(y)` where the parameter is `x`) is legal Python, so there is no TypeError to catch,
and inline-back reconstructs the original exactly while the split computes with a different value.
Because `inline_back` splices the body without substituting arguments, the only handoff it models
correctly is one where the argument NAME matches the parameter name. Expressions, attributes and
differently-named variables are refused rather than guessed at.

AWAIT SHAPE is the seventh. An `async def` helper whose call site does not await it returns a
COROUTINE and its body never runs — and inline-back reconstructs the original perfectly, because
splicing a body says nothing about how the call is invoked. Independent of whether the helper body
contains `await`, which is why the earlier async precondition did not catch it. So: async helper
requires `await` at the call site and an async caller; a sync helper must not be awaited.

A NOTE ON WHAT THIS TOOL IS. Seven separate false PASSES were found in it, five of them by the
reviewer running a shape rather than reading the code. That record is the argument for treating it as
a conservative gate over a narrow extraction dialect, NOT as a general Python equivalence prover. For
the hot handlers it is necessary and not sufficient: the reviewer's standing requirement is this gate
AND characterization tests around the function, because a static proof catches mechanical extraction
mistakes while characterization catches the route/db/side-effect semantics this deliberately does not
model.

This module is test-support, not production code, and is deliberately small enough to be read in one
sitting — the whole point is that the reviewer can verify the verifier.
"""

from __future__ import annotations

import ast
import builtins
import copy
from typing import Optional

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

    THE SAME IS TRUE OF `break`/`continue` INSIDE A LOOP THAT IS ITSELF IN THE BLOCK, which is the
    twelfth hole and was found extracting the analytics agent leaderboard:

        for row in rows:
            if not row["agent"]:
                continue        # bound by the loop above, which is moving WITH it
            ...

    That `continue` targets a loop entirely inside the extracted block, so after the move it still
    targets the same loop and means exactly what it meant. The danger the rule exists for is the
    opposite shape — a `break`/`continue` whose loop stays behind in the caller — and that one is
    still refused, because the enclosing loop is not part of the block being walked.

    `return` and `yield` are NOT loop-scoped and stay unconditional: a `return` anywhere other than
    the tail exits the helper instead of the caller, and inline-back cannot see the difference.
    """
    found: list[str] = []
    LOOPS = (ast.For, ast.AsyncFor, ast.While)

    def walk(node: ast.AST, *, in_loop: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue  # its own scope; its returns are not our escapes
            if isinstance(child, (ast.Break, ast.Continue)):
                if not in_loop:
                    found.append(type(child).__name__)
            elif isinstance(child, ESCAPES):
                found.append(type(child).__name__)
            # A loop's `orelse` runs when the loop finishes; a break inside it belongs to any
            # OUTER loop, not this one, so it is walked with the enclosing flag.
            if isinstance(child, LOOPS):
                for sub in child.body:
                    walk_stmt(sub, in_loop=True)
                for sub in child.orelse:
                    walk_stmt(sub, in_loop=in_loop)
            else:
                walk(child, in_loop=in_loop)

    def walk_stmt(stmt: ast.stmt, *, in_loop: bool) -> None:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(stmt, (ast.Break, ast.Continue)):
            if not in_loop:
                found.append(type(stmt).__name__)
        elif isinstance(stmt, ESCAPES):
            found.append(type(stmt).__name__)
        if isinstance(stmt, LOOPS):
            for sub in stmt.body:
                walk_stmt(sub, in_loop=True)
            for sub in stmt.orelse:
                walk_stmt(sub, in_loop=in_loop)
        else:
            walk(stmt, in_loop=in_loop)

    for stmt in block:
        # A nested def/class at the TOP of the block is its own scope too. The recursive walk
        # already skips them as children; missing them here let `def inner(): return 1` count as
        # an escape and would have rejected a safe extraction.
        walk_stmt(stmt, in_loop=False)
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

    A COMPREHENSION binds its target in its OWN scope, not this one — the dual of hole 11, and the
    same mistake in the other direction. Counting `r` from `[... for r in rows]` as bound here made
    the live-OUT check believe the helper had produced a local the caller still needed, and refused
    a correct extraction for the same reason the live-IN check did.

    A WALRUS inside a comprehension is the exception that has to be kept: PEP 572 binds `y` in
    `[y := x for x in rows]` in the ENCLOSING scope. Blanket-skipping comprehensions would lose it,
    and losing a real binding is the direction that blesses a broken split rather than refusing a
    safe one — so comprehensions are descended into for walrus targets only.
    """
    out: set[str] = set()

    def walrus_targets(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
                out.add(sub.target.id)

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                walrus_targets(child)
                continue
            if _nested_scopes(child):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    out.add(child.name)  # the def itself binds its name HERE
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                out.add(child.id)
            visit(child)

    for stmt in nodes:
        if isinstance(stmt, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            walrus_targets(stmt)
            continue
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

    COMPREHENSION TARGETS ARE THE ONE EXCEPTION, and it is not over-inclusiveness — it is a wrong
    answer. THE ELEVENTH HOLE, and mine: I introduced it fixing the eighth.

        reply_mins = sorted(float(r["mins"]) for r in rows if r["mins"] is not None)

    A flat walk reports `r` as read. It is not read from this scope: a comprehension has its OWN
    scope and `r` is bound in it. When some enclosing function happens to have its own `r` — and in
    `get_analytics` three separate comprehensions do — the live-in check concluded the helper was
    reading a caller local and refused a correct extraction.

    So comprehension bodies are walked with their targets bound. The FIRST generator's iterable is
    deliberately walked in the ENCLOSING scope, because that is where Python evaluates it; the
    remaining iterables, the conditions and the element expression are evaluated inside. Getting
    that boundary wrong in the other direction would hide a genuine read, so it follows the language
    rather than being approximated.
    """
    out: set[str] = set()

    def visit(node: ast.AST, bound: frozenset) -> None:
        # A LAMBDA/DEF PARAMETER IS BOUND IN ITS OWN SCOPE — the same case as a comprehension target
        # below, and it went wrong the same way. `board.sort(key=lambda a: a["id"])` does not read an
        # outer `a`; but `get_analytics_pulse` happens to bind its own `a` in an earlier loop, so the
        # live-in check saw "helper reads a, caller binds a" and refused a correct extraction. Only
        # the PARAMETERS are shadowed — the body's other reads still count, because a closure reading
        # an enclosing local is a genuine read of it, which is what the docstring above is about.
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            params = {a.arg for group in (args.posonlyargs, args.args, args.kwonlyargs) for a in group}
            if args.vararg:
                params.add(args.vararg.arg)
            if args.kwarg:
                params.add(args.kwarg.arg)
            # Defaults are evaluated in the ENCLOSING scope, so they are not shadowed.
            for default in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
                visit(default, bound)
            inner = frozenset(bound | params)
            body = node.body if isinstance(node.body, list) else [node.body]
            for child in body:
                visit(child, inner)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            generators = node.generators
            if generators:
                visit(generators[0].iter, bound)          # evaluated in the ENCLOSING scope
            inner = set(bound)
            for index, generator in enumerate(generators):
                inner |= _assigned_names([generator.target])
                if index > 0:
                    visit(generator.iter, frozenset(inner))
                for condition in generator.ifs:
                    visit(condition, frozenset(inner))
            if isinstance(node, ast.DictComp):
                visit(node.key, frozenset(inner))
                visit(node.value, frozenset(inner))
            else:
                visit(node.elt, frozenset(inner))
            return
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and node.id not in bound:
                out.add(node.id)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, bound)

    for stmt in nodes:
        visit(stmt, frozenset())
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

    AND WITHIN A STATEMENT, ORDER STILL APPLIES — the dual of the eighth hole, found in the same
    run. The first version treated any load anywhere in a compound statement as coming first:

        for i in range(n):     # the loop TARGET binds `i` before the body can read it...
            start = i          # ...so this is not a read of the caller's old `i`

    That is true for `total = total + 1`, where the value really is evaluated before the bind, and
    false for a `for` target, a `with ... as`, and an assignment's own target. Treating them the
    same reported every loop variable in the caller as still-live, which refused the first real
    extraction from `get_analytics` — where the hourly, daily and monthly loops deliberately share
    the names `i` and `start_s`.

    So this walks evaluation order too, and answers "read" whenever a branch is ambiguous: for a
    gate, wrongly refusing costs a manual review, wrongly allowing ships a defect.
    """

    def first_event(stmt: ast.stmt) -> Optional[str]:
        """'read', 'bind', or None — whichever happens FIRST for `name` inside this statement."""
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            if name in _loaded_names([stmt.iter]):
                return "read"
            if name in _assigned_names([stmt.target]):
                return "bind"
            return scan(stmt.body) or scan(stmt.orelse)
        if isinstance(stmt, ast.While):
            if name in _loaded_names([stmt.test]):
                return "read"
            return scan(stmt.body) or scan(stmt.orelse)
        if isinstance(stmt, ast.If):
            if name in _loaded_names([stmt.test]):
                return "read"
            branches = [scan(stmt.body), scan(stmt.orelse)]
            # A read on EITHER path means the caller may read it. Only a bind on BOTH kills it.
            if "read" in branches:
                return "read"
            return "bind" if branches == ["bind", "bind"] else None
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                if name in _loaded_names([item.context_expr]):
                    return "read"
                if item.optional_vars is not None and name in _assigned_names([item.optional_vars]):
                    return "bind"
            return scan(stmt.body)
        if isinstance(stmt, ast.Try):
            # A handler can run after any prefix of the body, so nothing here is guaranteed to bind.
            for part in (stmt.body, [s for h in stmt.handlers for s in h.body], stmt.orelse):
                if scan(part) == "read":
                    return "read"
            return scan(stmt.finalbody)
        if isinstance(stmt, ast.Assign):
            if name in _loaded_names([stmt.value]):
                return "read"
            return "bind" if name in _assigned_names(stmt.targets) else None
        if isinstance(stmt, ast.AnnAssign):
            if stmt.value is not None and name in _loaded_names([stmt.value]):
                return "read"
            return "bind" if name in _assigned_names([stmt.target]) else None
        if name in (_loaded_names([stmt]) | _augmented_reads(stmt)):
            return "read"
        return "bind" if name in _assigned_names([stmt]) else None

    def scan(stmts) -> Optional[str]:
        for stmt in stmts:
            event = first_event(stmt)
            if event:
                return event
        return None

    return scan(after) == "read"


def divergent_call_site_violations(split_fn: ast.AST, helper_fn: ast.AST) -> list[str]:
    """Several call sites are allowed ONLY when the calls are identical.

    Inline-back splices the body into every site, so a helper extracted from two places is provable.
    The other rules here are not: each inspects THE call — its arguments, whether they are bound yet,
    what the statement does with the result — by resolving a single site. Running them against the
    first of several would leave the rest unexamined, which is worse than the refusal it replaced.

    Requiring the calls to be identical makes checking one of them sufficient, and it is exactly the
    shape a deduplication produces: the same block, extracted from places that were already the same.
    Anything else — different arguments, one site assigning and another returning — is refused rather
    than half-checked. Widening this means teaching all four rules to iterate, with its own tests.
    """
    sites = _find_call_sites(split_fn, helper_fn.name)
    if len(sites) < 2:
        return []
    rendered = {ast.dump(block[index], include_attributes=False) for block, index in sites}
    if len(rendered) > 1:
        return [
            f"{helper_fn.name!r} is called from {len(sites)} places and the calls are NOT identical. "
            "Inline-back handles that, but the argument, binding and shape checks each resolve ONE "
            "call site, so the others would go unexamined. Make the calls identical, or extract a "
            "helper per site."
        ]
    return []


def call_site_shape_violations(split_fn: ast.AST, helper_fn: ast.AST) -> list[str]:
    """The call site's shape must agree with whether the helper RETURNS.

    THE SIXTH HOLE, and it leaked in BOTH directions. Inline-back splices the helper's body over the
    call, so it reproduces the original whenever the BODY is right — it never looks at what the call
    statement does with control flow. That leaves two splits whose round trip closes while behaviour
    changed, which is precisely what this gate exists to refuse up front:

      TAIL-CALL WITHOUT A RETURN. `return await _helper(x)` where the helper does NOT end in a
      `return`. The original fell through to the code below; the split returns None right there.
      Inline-back drops the `return` along with the call and reconstructs the original exactly.

      VOID CALL OF A RETURNING HELPER. `await _helper(x)` as a bare statement where the helper DOES
      end in a `return`. The original returned at that point; the split discards the value and keeps
      going. Inline-back splices the `return` back in and the round trip closes.

    Both were found by probing the verifier rather than by a failing extraction — the shapes are
    plausible enough to write by hand, and the second is what you get by extracting an early-exit
    branch and forgetting to move the exit with it.

    THE RULE, stated over the helper's tail:
      helper ends in `return`  -> the call must be `return _helper(...)` (tail-call) or
                                  `x = _helper(...)` (VALUE, where inline-back rewrites the return
                                  into the assignment and execution correctly continues).
      helper does not          -> the call must NOT be a `return` statement.

    A bare `return` with no value counts as returning: `return await _helper(x)` against a helper
    ending in bare `return` yields None on both sides, so it is equivalent and allowed.

    NOT COVERED, DELIBERATELY: `x = _helper(...)` against a helper with no trailing return. That is
    wrong too, but it is not a blind spot — inline-back splices a body that never rebinds `x`, the
    reconstruction lacks the assignment, and the round trip already fails. A rule here would be
    untested belt-and-braces over a case the existing proof catches.
    """
    block, index = _find_call_site(split_fn, helper_fn.name)
    call_stmt = block[index]
    body = _helper_body(helper_fn)
    helper_returns = bool(body) and isinstance(body[-1], ast.Return)

    is_return_site = isinstance(call_stmt, ast.Return)
    is_value_site = isinstance(call_stmt, (ast.Assign, ast.AnnAssign))

    if helper_returns and not (is_return_site or is_value_site):
        return [
            f"{helper_fn.name!r} ends in a `return`, but the call discards it as a bare statement. "
            "In the original that `return` exited the function; in the split it exits only the "
            "helper and the caller keeps going. Write `return "
            f"{helper_fn.name}(...)` if the exit was meant to move, or `x = {helper_fn.name}(...)` "
            "if the value was meant to be used."
        ]
    if is_return_site and not helper_returns:
        return [
            f"the call site is `return {helper_fn.name}(...)` but {helper_fn.name!r} has no trailing "
            "`return`, so the split returns None where the original carried on to the statements "
            "below. Give the helper the `return` that belongs to this path, or make the call a bare "
            "statement."
        ]
    return []


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


def _helper_params(helper_fn: ast.AST) -> set[str]:
    a = helper_fn.args
    names = {p.arg for p in list(getattr(a, "posonlyargs", [])) + list(a.args) + list(a.kwonlyargs)}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def _module_level_names(module: ast.Module) -> set[str]:
    """Names available to the helper without being passed: module globals, imports, defs, classes."""
    out: set[str] = set()
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Assign):
            out |= _assigned_names([node])
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def live_in_violations(helper_fn: ast.AST, module: ast.Module, caller_fn: ast.AST) -> list[str]:
    """Caller locals the helper READS but was never handed.

    THE FOURTH HOLE, and the exact dual of live-outs. The reviewer found it by running it:

        def f():
            x = compute()
            y = _w()        # `x` is never passed
            return y

        def _w():
            y = x + 1       # ...but the helper reads it

    Inline-back closes perfectly — splicing `y = x + 1` back over the call reproduces the original —
    while the split raises NameError, because `x` is a caller local and the helper has no `x` in
    scope. Once again the proof examines the RECONSTRUCTED original rather than the split, so the
    free variables of the helper have to be computed directly.

    A name is a live-in if the helper LOADS it before BINDING it, it is not a parameter, not a
    module-level name, not a builtin — AND it is genuinely a LOCAL OF THE CALLER.

    That last clause is the part I got wrong first, and the test fixtures exposed it: a name the
    helper reads which is never bound in the caller either is not a caller local at all. It is a
    global, an import, or a sibling function that simply is not declared in the snippet. Refusing on
    those made the check reject `n = len(items)`-shaped helpers whose only sin was calling something
    module-level. A live-in has to have been LIVE somewhere — if the caller never bound it, there
    was nothing to hand over.
    """
    import builtins

    params = _helper_params(helper_fn)
    caller_locals = _assigned_names(caller_fn.body) | _helper_params(caller_fn)

    # THE THIRTEENTH HOLE, and the FIRST FALSE PASS in this list — every earlier one was a false
    # rejection, which is annoying but safe. Reported by an external review against origin/main.
    #
    # A module-level name does NOT make a read safe when the CALLER shadows it:
    #
    #     CONFIG = {"a": 1}            # module level
    #     def f():
    #         CONFIG = {"a": 2}        # caller local shadows it
    #         x = CONFIG["a"]          # reads 2
    #
    # Extract that read and the helper resolves CONFIG from MODULE scope — it returns 1, not 2. The
    # gate passed this, and inline-back closes on it too, because the round trip reconstructs the
    # original where the local is in scope. Verified by execution: original 2, split 1, gate green.
    #
    # Python settles it: a name assigned ANYWHERE in a function is local for that function's whole
    # body, so any read of it in the caller was a local read. A helper reading it therefore always
    # resolves a different binding unless the value is handed over. So a caller-local name is
    # unavailable to the helper no matter what module scope or builtins also happen to define —
    # `params` is the only thing that can make it safe.
    shadowed = caller_locals - params
    available = (params | _module_level_names(module) | set(dir(builtins))) - shadowed
    missing: list[str] = []

    def report(names, bound: set[str]) -> None:
        for name in sorted(names):
            if (name in caller_locals and name not in bound and name not in available
                    and name not in missing):
                missing.append(name)

    def walk(stmts, bound: set[str]) -> set[str]:
        """Walk statements in EVALUATION ORDER, returning the names bound afterwards.

        THE EIGHTH HOLE, found running the gate against the first real extraction. `bound` used to
        be updated only AFTER each top-level statement, so a name bound and read within the SAME
        compound statement was reported as a live-in. That is every extracted `for` loop:

            for i in range(24):        # `i` bound by the loop target...
                start = base - i       # ...and read in the body, in the same statement

        Both `i` and `start` came back as "read but never passed", which would have refused nearly
        every real extraction — a gate whose dialect excludes loops is not a usable gate.

        Blanket-subtracting everything a statement assigns would have been the easy fix and a wrong
        one: it would bless `for x in items: items = []`, where the iterable is READ before the
        rebinding happens and the split really does raise NameError. So this walks the actual
        evaluation order instead — iterable before target, value before assignment target, test
        before body — which is the only version that keeps the check honest in both directions.
        """
        for stmt in stmts:
            if isinstance(stmt, (ast.For, ast.AsyncFor)):
                report(_loaded_names([stmt.iter]), bound)          # iterable evaluated FIRST
                bound = bound | _assigned_names([stmt.target])     # then the target binds
                inner = walk(stmt.body, bound)
                # A loop body may not run, so only names bound BEFORE it are guaranteed; and the
                # body can rebind for the next iteration, so its bindings are visible to itself.
                bound = bound | (inner & walk(stmt.orelse, inner) if stmt.orelse else inner)
            elif isinstance(stmt, ast.While):
                report(_loaded_names([stmt.test]), bound)
                bound = bound | walk(stmt.body, bound)
                if stmt.orelse:
                    bound = bound | walk(stmt.orelse, bound)
            elif isinstance(stmt, ast.If):
                report(_loaded_names([stmt.test]), bound)
                then_bound = walk(stmt.body, bound)
                else_bound = walk(stmt.orelse, bound) if stmt.orelse else bound
                # Only what BOTH branches bind is guaranteed bound afterwards. Union would be
                # permissive and could hide a live-in on the path that does not bind it.
                bound = then_bound & else_bound
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    report(_loaded_names([item.context_expr]), bound)
                    if item.optional_vars is not None:
                        bound = bound | _assigned_names([item.optional_vars])
                bound = walk(stmt.body, bound)
            elif isinstance(stmt, ast.Try):
                body_bound = walk(stmt.body, bound)
                for handler in stmt.handlers:
                    walk(handler.body, bound)   # a handler may run after ANY prefix of the body
                # Nothing in `body` is guaranteed to have completed if a handler ran, so only the
                # finally-block's bindings are certain.
                bound = walk(stmt.finalbody, bound) if stmt.finalbody else bound
                if stmt.orelse:
                    walk(stmt.orelse, body_bound)
            elif isinstance(stmt, ast.Assign):
                report(_loaded_names([stmt.value]), bound)          # value evaluated FIRST
                bound = bound | _assigned_names(stmt.targets)
            elif isinstance(stmt, ast.AnnAssign):
                if stmt.value is not None:
                    report(_loaded_names([stmt.value]), bound)
                bound = bound | _assigned_names([stmt.target])
            elif isinstance(stmt, ast.AugAssign):
                report(_loaded_names([stmt.value]) | _augmented_reads(stmt), bound)
                bound = bound | _assigned_names([stmt.target])
            else:
                report(_loaded_names([stmt]) | _augmented_reads(stmt), bound)
                bound = bound | _assigned_names([stmt])
        return bound

    walk(_helper_body(helper_fn), set())

    return [
        f"`{name}` is read by the helper but never passed to it - it was a CALLER local, so after "
        f"the split the helper raises NameError. Pass it as an argument."
        for name in missing
    ]


def _helper_call(stmt: ast.stmt, helper: str) -> Optional[ast.Call]:
    for node in ast.walk(stmt):
        if isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == helper)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == helper)
        ):
            return node
    return None


def conditionally_bound_argument_violations(
    split_fn: ast.AST, helper_fn: ast.AST, module: ast.Module
) -> list[str]:
    """Arguments the CALL reads that the caller may not have bound yet.

    THE FIFTH HOLE, and it shipped a 500 before it was found. `send_message` assigns `prefer_steer`
    and `settings` only inside `if req.trigger:`, and the extracted block's OWN guard was
    `if req.trigger:` — so INSIDE the block those reads were always safe. Hoisting them to the call
    site changed WHEN they are evaluated: on a send without `trigger`, the call now reads an unbound
    local and raises `UnboundLocalError` before the guard it moved into ever runs.

    Every other check passed. Inline-back closed perfectly, because reconstruction puts the reads
    back inside the guard — the round trip cannot see a timing change that only exists in the split.
    `live_in_violations` is the dual and does not catch it either: the name IS passed, so nothing is
    missing; the defect is that passing it is now unconditional.

    MODULE-LEVEL NAMES ARE ALWAYS SAFE, which is not a nicety: the first version of this check
    refused three existing, correct proofs because a helper took `logger` -- a module global that
    no caller ever binds. A check that fires on the whole suite is not a check, it is a rewrite
    request, and it would have been silenced rather than fixed.

    ONE EXHAUSTIVE CASE IS HANDLED rather than refused: an `if` with an `else` binds whatever both
    of its branches bind. That is not scope creep -- the first version refused `register_agent`'s
    shipped, correct split over a two-line if/else, and a gate that refuses correct work gets
    switched off.

    THE RULE, otherwise deliberately conservative: an argument is safe if it is a module-level
    name, or the caller binds it somewhere that DOMINATES the call — a statement at the function's top level before the call, a parameter, or a
    binding in an enclosing block that contains the call. An argument whose only bindings live in
    branches that do NOT contain the call is refused. That will occasionally refuse a safe split
    (two exhaustive branches both binding it, say); a refusal costs a rewrite, and a miss costs a
    500 on a live route.
    """
    block, index = _find_call_site(split_fn, helper_fn.name)
    call = _helper_call(block[index], helper_fn.name)
    if call is None:
        return []
    argument_names = {a.id for a in call.args if isinstance(a, ast.Name)} | {
        kw.value.id for kw in call.keywords if isinstance(kw.value, ast.Name)
    }
    if not argument_names:
        return []

    parameters = {
        a.arg for a in list(split_fn.args.args) + list(split_fn.args.kwonlyargs)
        + list(split_fn.args.posonlyargs)
    }
    for special in (split_fn.args.vararg, split_fn.args.kwarg):
        if special is not None:
            parameters.add(special.arg)

    # Every block on the path from the function body down to the call's own block. A binding in any
    # of them runs before the call does (statement order is checked below for the innermost).
    dominating: list[tuple[list[ast.stmt], int]] = []

    def descend(stmts: list[ast.stmt]) -> bool:
        for position, stmt in enumerate(stmts):
            if stmt is block[index]:
                dominating.append((stmts, position))
                return True
            for field in _BLOCK_FIELDS:
                nested = getattr(stmt, field, None)
                if isinstance(nested, list) and nested and isinstance(nested[0], ast.stmt):
                    if descend(nested):
                        dominating.append((stmts, position))
                        return True
            for handler in getattr(stmt, "handlers", []) or []:
                if descend(handler.body):
                    dominating.append((stmts, position))
                    return True
        return False

    descend(split_fn.body)

    def _unconditionally_assigned(stmts: list[ast.stmt]) -> set[str]:
        """Names these statements ALWAYS bind, running top to bottom.

        `_assigned_names` walks recursively and is wrong for this question: it counts a name
        assigned inside an `if` body as bound, which is exactly the case that shipped a 500. So the
        descent here is by statement kind. `with` bodies always run; a `try` guarantees only its
        `finally`; a bare `if` and every loop guarantee nothing. An `if` WITH an `else` guarantees
        the intersection of its branches, because exactly one of them runs.
        """
        bound: set[str] = set()
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                # AN `if` WITH AN `else` IS EXHAUSTIVE: exactly one branch runs, so a name bound in
                # BOTH is always bound. Not a nicety — the first version skipped every `if` and
                # refused `register_agent`'s shipped, correct split, where `description_value` is
                # assigned in both arms of a two-line if/else. A gate that refuses correct work gets
                # switched off, so the cheap exhaustive case is handled rather than waved at.
                if stmt.orelse:
                    bound |= (_unconditionally_assigned(stmt.body)
                              & _unconditionally_assigned(stmt.orelse))
                continue
            if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                continue
            if isinstance(stmt, ast.Try):
                bound |= _unconditionally_assigned(stmt.finalbody)
                continue
            if isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    if item.optional_vars is not None:
                        bound |= _assigned_names(
                            [ast.Assign(targets=[item.optional_vars], value=ast.Constant(None))])
                bound |= _unconditionally_assigned(stmt.body)
                continue
            bound |= _assigned_names([stmt])
        return bound

    # THE FOURTEENTH HOLE, and the SECOND false pass — the thirteenth's sibling, in the check right
    # next to it. Reported by a reviewer on another instance as "the same shape at ~L850", and
    # CONFIRMED BY EXECUTION rather than by reading, the same way the thirteenth was:
    #
    #     LIMIT = 1                          # module scope
    #     def caller(flag):
    #         if flag:
    #             LIMIT = 2                  # ...so LIMIT is LOCAL for the whole function
    #         if flag:                       # <- extracted block, guard included
    #             return LIMIT + 10
    #         return 0
    #
    # Split it and the call sits at the caller's top level, evaluating LIMIT unconditionally.
    # Measured: flag=False returns 0 before and raises UnboundLocalError after, and this check
    # returned NO VIOLATIONS. The module-level `LIMIT` made a caller-local look always-available —
    # but a name assigned anywhere in a function is local for that function's ENTIRE body, so module
    # scope is unreachable from inside it and cannot make the read safe.
    #
    # The subtraction is deliberately the caller's OWN bindings minus the helper's parameters, which
    # is what keeps the documented `logger` case working: a module global no caller ever binds is
    # still always safe, so the three correct proofs that the first version of this check refused
    # stay green. Verified by the full suite, not by argument.
    caller_bound = (_assigned_names(split_fn.body) | _helper_params(split_fn)) - set(parameters)
    safe = (set(parameters) | _module_level_names(module) | set(dir(builtins))) - caller_bound
    for stmts, position in dominating:
        # Only statements BEFORE the call (or the enclosing statement) in each block have run.
        safe |= _unconditionally_assigned(stmts[:position])
        # A `for` target and a `with ... as` name bind for the body the call sits in.
        enclosing = stmts[position]
        if isinstance(enclosing, (ast.For, ast.AsyncFor)):
            safe |= _assigned_names([ast.Assign(targets=[enclosing.target], value=ast.Constant(None))])
        for item in getattr(enclosing, "items", []) or []:
            if item.optional_vars is not None:
                safe |= _assigned_names(
                    [ast.Assign(targets=[item.optional_vars], value=ast.Constant(None))])

    return [
        f"`{name}` is passed to the helper but the caller binds it only inside a branch that does "
        f"not contain the call. Before the split the read happened INSIDE the extracted block, so it "
        f"ran only when that branch had run; the call now evaluates it unconditionally and raises "
        f"UnboundLocalError on any path that skipped the binding"
        for name in sorted(argument_names - safe)
    ]


def call_signature_violations(split_fn: ast.AST, helper_fn: ast.AST) -> list[str]:
    """Does the CALL actually supply the helper's parameters?

    THE FIFTH FALSE PASS, again found by the reviewer running it rather than reading it. Knowing a
    name is a parameter told the live-in check the helper had it — but said nothing about whether
    the call hands it over:

        y = _w()          # supplies nothing
        def _w(x): ...    # requires x        -> TypeError before the body ever runs

        y = _w(z=x)       # wrong keyword
        def _w(x): ...                        -> TypeError before the body ever runs

    Inline-back reconstructs the original in both cases, because splicing the body ignores the
    calling convention entirely. So the convention is checked directly.

    A DELIBERATELY NARROW DIALECT, on the reviewer's recommendation: no defaults, no *args/**kwargs,
    no positional-only or keyword-only parameters. Those are all expressible and all add ways to be
    subtly wrong; this gate exists for mechanically-generated extractions, where none of them are
    needed. It will false-reject some safe shapes. It will not bless a TypeError.
    """
    block, index = _find_call_site(split_fn, helper_fn.name)
    call = _helper_call(block[index], helper_fn.name)
    if call is None:  # pragma: no cover - _find_call_site already guarantees one
        return [f"no call to {helper_fn.name!r} found at the resolved call site"]

    # AWAIT SHAPE. The seventh false PASS: an `async def` helper whose call site does not await it
    # returns a COROUTINE, and the body never runs at all. Inline-back splices the body and
    # reconstructs the original perfectly, so the round trip is blind to it — and this is independent
    # of whether the helper body itself contains `await`, which is why the existing async
    # precondition did not catch it.
    stmt = block[index]
    awaited = any(
        isinstance(node, ast.Await) and node.value is call for node in ast.walk(stmt)
    )
    helper_is_async = isinstance(helper_fn, ast.AsyncFunctionDef)
    caller_is_async = isinstance(split_fn, ast.AsyncFunctionDef)
    problems_await: list[str] = []
    if helper_is_async and not awaited:
        problems_await.append(
            "helper is `async def` but the call is not awaited - the split returns a coroutine and "
            "the body never runs"
        )
    if helper_is_async and not caller_is_async:
        problems_await.append(
            "helper is `async def` but the caller is sync, so there is no correct call shape"
        )
    if awaited and not helper_is_async:
        problems_await.append("call awaits a helper that is not `async def`")
    if problems_await:
        return problems_await

    # MULTI-OUTPUT TARGET SHAPES the round trip cannot verify (v0.5.4, added with tuple support).
    #
    # `inline_back` proves a multi-output split by comparing target names against returned names.
    # Any shape where that comparison is not meaningful must be REFUSED rather than compared
    # loosely — an unverifiable input silently passing is the false-pass class this gate exists to
    # prevent, and it is exactly how the earlier live-out and await holes behaved.
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Tuple):
        elts = stmt.targets[0].elts
        if any(not isinstance(e, ast.Name) for e in elts):
            return ["multi-output call unpacks into a non-Name target (attribute/subscript/starred); "
                    "the round trip cannot verify element identity"]
        body_tail = _helper_body(helper_fn)
        returned = body_tail[-1].value if body_tail and isinstance(body_tail[-1], ast.Return) else None
        if not isinstance(returned, ast.Tuple):
            return ["multi-output call unpacks a helper whose trailing return is not a TUPLE literal; "
                    "element identity is unverifiable"]
        if any(not isinstance(e, ast.Name) for e in returned.elts):
            return ["helper's returned tuple contains a non-Name element; element identity is unverifiable"]
        if len(returned.elts) != len(elts):
            return [f"multi-output arity mismatch: call unpacks {len(elts)} names, "
                    f"helper returns {len(returned.elts)}"]

    args = helper_fn.args
    if args.defaults or args.kw_defaults:
        return ["helper has DEFAULT parameter values; outside the supported extraction dialect"]
    if args.vararg or args.kwarg:
        return ["helper takes *args/**kwargs; outside the supported extraction dialect"]
    if getattr(args, "posonlyargs", []) or args.kwonlyargs:
        return ["helper has positional-only or keyword-only parameters; outside the dialect"]

    params = [p.arg for p in args.args]
    supplied_positionally = params[: len(call.args)]
    by_keyword = [kw.arg for kw in call.keywords]

    problems: list[str] = []
    if any(kw.arg is None for kw in call.keywords):
        problems.append("call uses `**` unpacking, so the supplied names cannot be checked")
    if len(call.args) > len(params):
        problems.append(
            f"call passes {len(call.args)} positional argument(s) but the helper takes {len(params)}"
        )
    for name in by_keyword:
        if name is not None and name not in params:
            problems.append(f"call passes keyword `{name}=`, which is not a parameter of the helper")
        elif name in supplied_positionally:
            problems.append(f"`{name}` is supplied both positionally and by keyword")
    covered = set(supplied_positionally) | {n for n in by_keyword if n}
    for name in params:
        if name not in covered:
            problems.append(f"required parameter `{name}` is never supplied by the call")

    # SAME-NAME HANDOFF ONLY. The sixth false PASS: supplying the RIGHT parameter with the WRONG
    # caller value.
    #
    #     z = _w(y)      # parameter is `x`, caller value is `y`
    #     z = _w(x=y)    # keyword right, value wrong
    #
    # Both are legal Python, so there is no TypeError to catch, and inline-back splices `z = x + 1`
    # and reconstructs the original perfectly. The split quietly computes with a different value.
    # Silent behaviour drift is the worst thing this gate can miss.
    #
    # `inline_back` does not perform argument SUBSTITUTION -- it splices the body as-is -- so the
    # only handoff it models correctly is one where the argument has the same name as the parameter.
    # Anything else (an expression, an attribute, a differently-named variable) is refused rather
    # than guessed at. That false-rejects safe aliasing; it does not silently swap a value.
    for param, arg in zip(params, call.args):
        if not (isinstance(arg, ast.Name) and arg.id == param):
            rendered = ast.unparse(arg)
            problems.append(
                f"parameter `{param}` is supplied with `{rendered}` rather than the same-name "
                f"caller variable `{param}`. inline-back splices the body without substituting "
                f"arguments, so it cannot see a value swap"
            )
    for kw in call.keywords:
        if kw.arg is None:
            continue
        if not (isinstance(kw.value, ast.Name) and kw.value.id == kw.arg):
            rendered = ast.unparse(kw.value)
            problems.append(
                f"keyword `{kw.arg}=` is supplied with `{rendered}` rather than the same-name "
                f"caller variable `{kw.arg}`"
            )
    return problems


def _closure_captures_nothing(node: ast.AST) -> bool:
    """True when a nested function/lambda/class reads only its own parameters and builtins.

    A closure is dangerous to move because it captures the ENCLOSING frame's locals, and after an
    extraction that frame is the helper's rather than the original's. A closure that captures nothing
    has no such dependency — `lambda a: (a["x"], a["y"])` sorts the same list whichever function it
    was defined in.

    Conservative on purpose. A module-level name (a constant, an imported function) would also be
    safe, but this check is handed the BLOCK, not the module, so it cannot tell a module-level name
    from a caller local. Refusing those costs an extraction; accepting one wrongly ships a helper
    that reads a name from the wrong frame.
    """
    if isinstance(node, ast.ClassDef):
        return False  # a class body can bind and read in ways this does not model
    params: set[str] = set()
    args = getattr(node, "args", None)
    if args is not None:
        for group in (args.posonlyargs, args.args, args.kwonlyargs):
            params.update(a.arg for a in group)
        if args.vararg:
            params.add(args.vararg.arg)
        if args.kwarg:
            params.add(args.kwarg.arg)
    bound = set(params)
    free = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            if isinstance(child.ctx, ast.Store):
                bound.add(child.id)
            elif child.id not in bound and not hasattr(builtins, child.id):
                free.add(child.id)
    return not free


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
        #
        # UNLESS IT CAPTURES NOTHING. `board.sort(key=lambda a: (a["x"], a["y"]))` reads only its own
        # parameter, so which frame encloses it cannot matter — and refusing it blocked a correct
        # extraction whose only sin was sorting a list. The rule is about CAPTURE, so it now asks
        # whether the closure has any free name at all rather than whether a closure exists.
        #
        # Deliberately strict about what counts as "captures nothing": every name the closure reads
        # must be one of its own parameters or a builtin. A module-level name would also be safe in
        # practice, but proving that needs the module and this check only has the block — and a
        # false ACCEPT here is a silently-wrong extraction, while a false refuse is an inconvenience.
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            if not _closure_captures_nothing(sub):
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


def _find_call_sites(fn: ast.AST, helper: str) -> list[tuple[list[ast.stmt], int]]:
    """Every (block, index) whose statement calls `helper`, AT ANY DEPTH.

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
    if not found:
        raise AssertionError(
            f"expected a call to {helper!r} in the split function, found none."
        )
    return found


def _find_call_site(fn: ast.AST, helper: str) -> tuple[list[ast.stmt], int]:
    """The single call site, for the checks that examine one.

    Kept as a separate name because most rules here ask about "the call" and reading them is easier
    when that is literally what they get. A helper with several call sites is checked at EVERY site
    by the callers that matter; see `_find_call_sites`.
    """
    return _find_call_sites(fn, helper)[0]


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
    # EVERY call site, not just the first. One block extracted from SEVERAL places is how a
    # duplicate is removed, and the reconstruction is only exact if the body goes back to all of
    # them — which is also what makes the proof honest: if the original had DIFFERENT code at two
    # sites, splicing the same body into both cannot match it and the round trip fails.
    # Reverse order so an earlier splice does not shift a later index inside the same block.
    sites = _find_call_sites(result, helper_fn.name)
    for block, index in sorted(sites, key=lambda site: site[1], reverse=True):
        _splice_one(block, index, helper_fn)
    return result


def _splice_one(block: list[ast.stmt], index: int, helper_fn: ast.AST) -> None:
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
        # MULTI-OUTPUT (v0.5.4): `a, b, c = _helper(...)` returning `return a, b, c`.
        #
        # Added because a real extraction needed it and the gate refused: the status derivation's
        # decision block has THREE live-outs, and a verifier that can only express one would have
        # forced either a worse split or no gate at all. It is a DIALECT CHANGE, so it landed as its
        # own slice with the refusal probes below, not inside the production extraction.
        #
        # ORDER IS THE WHOLE RISK. `a, b = _h()` returning `(b, a)` is a transposition: same names,
        # same types, silently swapped values. It is treated as NOT self-assigning, so inline-back
        # emits `a, b = b, a`, which does not reconstruct the original and the round trip fails.
        # That is the intended outcome — the one shape most likely to be wrong is the one the gate
        # must not wave through.
        def _names(node):
            """The Name ids of a tuple target/value, or None if any element is not a plain Name."""
            if not isinstance(node, ast.Tuple):
                return None
            ids = []
            for elt in node.elts:
                if not isinstance(elt, ast.Name):
                    return None  # attribute/subscript/starred target: unverifiable, refuse below
                ids.append(elt.id)
            return ids

        target_names = _names(targets[0]) if len(targets) == 1 else None
        returned_names = _names(returned)
        self_assign = (
            len(targets) == 1
            and isinstance(targets[0], ast.Name)
            and isinstance(returned, ast.Name)
            and targets[0].id == returned.id
        ) or (
            target_names is not None
            and returned_names is not None
            and target_names == returned_names  # SAME NAMES IN THE SAME ORDER
        )
        if self_assign:
            body = body[:-1]
        else:
            rebound = ast.Assign(targets=copy.deepcopy(targets), value=copy.deepcopy(returned))
            body = body[:-1] + [rebound]

    block[index:index + 1] = body


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

    For a function that has had SEVERAL blocks extracted, use
    `assert_extractions_preserve_behaviour` — this one models exactly one extraction and will
    report a round-trip failure if the split function also calls a second new helper.
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

    mismatched = call_signature_violations(funcs[original.name], helper)
    if mismatched:
        raise AssertionError(
            "REFUSED: the call does not match the helper's signature:\n  - "
            + "\n  - ".join(mismatched)
            + "\nInline-back ignores the calling convention entirely - it splices the body - so "
            "a TypeError raised before the body ever runs is invisible to it."
        )

    unbound = conditionally_bound_argument_violations(funcs[original.name], helper, module)
    if unbound:
        raise AssertionError(
            "REFUSED: the call reads caller locals that may not be bound yet:\n  - "
            + "\n  - ".join(unbound)
            + "\nInline-back cannot see this: reconstruction puts the read back inside the guard "
            "it moved into, so the round trip closes on a split that raises UnboundLocalError."
        )

    unpassed = live_in_violations(helper, module, funcs[original.name])
    if unpassed:
        raise AssertionError(
            "REFUSED: the helper reads caller locals it was not given:\n  - "
            + "\n  - ".join(unpassed)
            + "\nInline-back CANNOT catch this either - it reconstructs the ORIGINAL, where those "
            "names are in scope. The SPLIT is what raises NameError."
        )

    divergent = divergent_call_site_violations(funcs[original.name], helper)
    if divergent:
        raise AssertionError("REFUSED: " + "; ".join(divergent))

    misshapen = call_site_shape_violations(funcs[original.name], helper)
    if misshapen:
        raise AssertionError(
            "REFUSED: the call site's shape disagrees with the helper's tail:\n  - "
            + "\n  - ".join(misshapen)
            + "\nInline-back splices the BODY over the call and never reads the call statement's "
            "control flow, so both of these reconstruct the original while the split returns from a "
            "different place."
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

    # DECORATORS ARE COMPARED SEPARATELY, and only when the caller supplied them.
    #
    # Every route handler in this repo is decorated, and the natural way to obtain `original_src` is
    # `ast.get_source_segment(src, node)` — which returns the text from the `def` line and therefore
    # DROPS the decorators. Comparing whole nodes then fails on a decorator list that differs only
    # because of how the source was sliced, and reports it as "not behaviour-preserving": the most
    # alarming message this module can emit, for a split that is perfectly fine. That is a false
    # alarm the first real extraction hit immediately.
    #
    # Extraction never touches decorators, so they are not what the round trip is proving. But a
    # CHANGED decorator on a route handler really is a behaviour change, so when the caller did pass
    # them they are still checked — just with their own message, so the two failures cannot be
    # confused for one another.
    if original.decorator_list:
        original_decorators = [normalized(d) for d in original.decorator_list]
        split_decorators = [normalized(d) for d in rebuilt.decorator_list]
        if original_decorators != split_decorators:
            raise AssertionError(
                "REFUSED: the function's decorators changed. Extraction must not touch them — on a "
                "route handler they carry the path, the method and the response model."
            )

    def _bodies_only(node: ast.AST) -> str:
        clone = copy.deepcopy(node)
        clone.decorator_list = []
        return normalized(clone)

    if _bodies_only(rebuilt) != _bodies_only(original):
        raise AssertionError(
            "extraction is NOT behaviour-preserving: inlining the helper back did not reproduce the "
            "original function.\n"
            "This is the whole proof - if the round trip does not close, something other than a "
            "pure block-lift happened (a reordered statement, a changed name, a dropped line)."
        )


def assert_extractions_preserve_behaviour(
    original_src: str, split_src: str, helper_names: "list[str]",
    edited_since: "list[tuple[str, str]] | None" = None,
) -> None:
    """The same proof for a function that has had SEVERAL blocks extracted.

    WHY THIS EXISTS. `assert_extraction_preserves_behaviour` models ONE extraction: it inlines a
    single helper back and requires the result to equal the original. Point it at the second of two
    extractions and it fails — correctly but uselessly — because the split function still calls the
    OTHER new helper, which appears nowhere in the original. That is not a defect in the split.

    The obvious workaround is a chain of pre-split fixtures, one per extraction, each proving one
    step. It works, and it rots: every fixture is a second copy of a function that is still being
    edited, and a stale one proves the wrong thing while staying green.

    So the proof generalises instead of multiplying. Inline ALL the helpers back and require the
    single result to equal the original. One fixture, one comparison, and it stays exact however many
    blocks come out later.

    DIRECT SIBLING EXTRACTIONS ONLY, and that is now ENFORCED rather than documented. An earlier
    draft of this docstring claimed the helpers were inlined "innermost calls first, so a helper
    extracted out of another helper collapses before its parent". They are not: they are inlined in
    the order supplied. The reviewer caught the gap between the sentence and the code.

    Narrowing the claim would have been enough for today — all three analytics helpers are called
    directly by the handler — but a caveat in a docstring is exactly the kind of thing the next
    person does not read. So a helper that calls another helper in the same list is REFUSED. If
    nested extraction is ever wanted, the fix is a real topological order with its own tests, not a
    sentence promising one.

    Every per-helper safety check still runs against every helper individually: escapes,
    preconditions, call signature, live-ins and live-outs. Those examine the SPLIT, which is the
    artifact that can be wrong, and none of them is weakened by there being more than one helper.
    """
    # DECLARED EDITS SINCE THE SPLIT, undone before the comparison.
    #
    # This proof exists to show an extraction was a pure MOVE, so it compares against a captured
    # pre-split fixture. A later behavioural change inside an extracted helper therefore breaks it —
    # correctly: the code has legitimately diverged from the fixture. Re-capturing the fixture would
    # "fix" that by erasing the baseline, after which the gate proves only that the split is inert
    # relative to whatever the code is today, which is not a claim about the extraction at all.
    #
    # `extraction-proof.mjs` met this first on the dashboard side and answered it with `editedSince`:
    # declare the change with BOTH texts and undo it before comparing. The historical proof survives
    # and the deliberate divergence is written down rather than tolerated. Same mechanism, same
    # reason, same verbatim verification — an edit that no longer matches is an error, not a skip.
    for now_text, was_text in (edited_since or []):
        occurrences = split_src.count(now_text)
        if occurrences != 1:
            raise AssertionError(
                f"edited_since entry does not appear exactly once in the split source "
                f"({occurrences} occurrences). It is verified verbatim so a stale declaration cannot "
                f"silently stop undoing anything:\n{now_text}"
            )
        split_src = split_src.replace(now_text, was_text, 1)

    original = ast.parse(original_src).body[0]
    module = ast.parse(split_src)
    funcs = {n.name: n for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if original.name not in funcs:
        raise AssertionError(f"original function {original.name!r} not found in the split source")
    missing = [h for h in helper_names if h not in funcs]
    if missing:
        raise AssertionError(f"extracted helper(s) {missing} not found in the split source")

    caller = funcs[original.name]

    # NESTED EXTRACTIONS, in dependency order. This used to be REFUSED outright, with the refusal
    # message promising "a real topological order with its own tests" as the fix if it were ever
    # wanted. It was wanted the first time a helper was extracted around an existing one:
    # `register_agent`'s console-terminal branch encloses `_adopt_console_terminal_on_register`,
    # which had already been extracted, so the pair could not be proved together at all.
    #
    # The order is what makes it sound. Each helper is fully resolved BEFORE anything inlines it, so
    # a parent is only ever inlined once its own callees have collapsed into it. Supplied order is
    # ignored, which is the property the old refusal was protecting: the verdict must depend on the
    # code, not on how the list happens to be written.
    parents = {
        h: [o for o in helper_names if o != h and _helper_call_anywhere(funcs[h], o)]
        for h in helper_names
    }
    order: list[str] = []
    visiting: set[str] = set()

    def _resolve_order(name: str, trail: tuple[str, ...]) -> None:
        if name in order:
            return
        if name in visiting:
            raise AssertionError(
                "REFUSED: the extraction list contains a call CYCLE "
                f"({' -> '.join(trail + (name,))}). There is no order that inlines each helper after "
                "its callees, so no round trip can be defined."
            )
        visiting.add(name)
        for callee in parents[name]:
            _resolve_order(callee, trail + (name,))
        visiting.discard(name)
        order.append(name)

    for helper_name in helper_names:
        _resolve_order(helper_name, ())

    # A helper's call site lives in whoever calls it — the original function for a direct
    # extraction, another helper for a nested one. Every per-helper check below inspects that call
    # site, so pointing them all at the caller would crash on a nested helper rather than check it.
    site_of = {
        h: next((funcs[o] for o in helper_names if o != h and _helper_call_anywhere(funcs[o], h)),
                caller)
        for h in helper_names
    }

    for helper_name in helper_names:
        helper = funcs[helper_name]
        enclosing = site_of[helper_name]
        bad = escapes(_helper_body(helper))
        if bad and not _returns_only_at_tail(helper):
            raise AssertionError(
                f"REFUSED [{helper_name}]: extracted block contains control-flow escape(s) "
                f"{sorted(set(bad))} that are not a single trailing `return`."
            )
        blocked = preconditions(helper, caller_is_async=isinstance(original, ast.AsyncFunctionDef))
        if blocked:
            raise AssertionError(
                f"REFUSED [{helper_name}]: the extracted block is not structurally movable:\n  - "
                + "\n  - ".join(blocked))
        mismatched = call_signature_violations(enclosing, helper)
        if mismatched:
            raise AssertionError(
                f"REFUSED [{helper_name}]: the call does not match the helper's signature:\n  - "
                + "\n  - ".join(mismatched))
        unbound = conditionally_bound_argument_violations(enclosing, helper, module)
        if unbound:
            raise AssertionError(
                f"REFUSED [{helper_name}]: the call reads caller locals that may not be bound "
                "yet:\n  - " + "\n  - ".join(unbound))
        unpassed = live_in_violations(helper, module, enclosing)
        if unpassed:
            raise AssertionError(
                f"REFUSED [{helper_name}]: the helper reads caller locals it was not given:\n  - "
                + "\n  - ".join(unpassed))
        divergent = divergent_call_site_violations(enclosing, helper)
        if divergent:
            raise AssertionError(f"REFUSED [{helper_name}]: " + "; ".join(divergent))
        misshapen = call_site_shape_violations(enclosing, helper)
        if misshapen:
            raise AssertionError(
                f"REFUSED [{helper_name}]: the call site's shape disagrees with the helper's "
                "tail:\n  - " + "\n  - ".join(misshapen))
        leaked = live_out_violations(enclosing, helper)
        if leaked:
            raise AssertionError(
                f"REFUSED [{helper_name}]: the split does not hand back every local the caller "
                "still needs:\n  - " + "\n  - ".join(leaked))

    # Bottom-up: a parent is inlined only after its own callees have collapsed into it.
    resolved = {h: funcs[h] for h in helper_names}
    for helper_name in order:
        for callee in parents[helper_name]:
            resolved[helper_name] = inline_back(resolved[helper_name], resolved[callee])

    rebuilt = caller
    for helper_name in order:
        if _helper_call_anywhere(caller, helper_name):
            rebuilt = inline_back(rebuilt, resolved[helper_name])

    if original.decorator_list:
        if [normalized(d) for d in original.decorator_list] != [normalized(d) for d in rebuilt.decorator_list]:
            raise AssertionError(
                "REFUSED: the function's decorators changed. Extraction must not touch them.")

    def _bodies_only(node: ast.AST) -> str:
        clone = copy.deepcopy(node)
        clone.decorator_list = []
        return normalized(clone)

    if _bodies_only(rebuilt) != _bodies_only(original):
        raise AssertionError(
            f"extraction is NOT behaviour-preserving: inlining {helper_names} back did not "
            "reproduce the original function.\n"
            "This is the whole proof - if the round trip does not close, something other than a "
            "pure block-lift happened (a reordered statement, a changed name, a dropped line)."
        )


def _helper_call_anywhere(fn: ast.AST, helper: str) -> bool:
    """Does `fn` call `helper` anywhere in its body?

    Used only to refuse nested extractions in `assert_extractions_preserve_behaviour`. Deliberately
    a plain call search rather than the depth-aware single-site resolver: here the question is
    whether a dependency exists at all, not where to splice.
    """
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == helper:
            return True
    return False
