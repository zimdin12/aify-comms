"""Cross-module INLINE literal sets: the population is frozen, not ruled.

`test_no_unruled_constant_coincidences.py` finds two modules holding the same set under different
NAMES. It reads `ast.Assign` only, so it sees named constants and nothing else — and its own docstring
names the gap it cannot cover: "a fork in a module outside that gate's POPULATION". An inline literal
written straight into an `if` is exactly that. Measured BY THIS FILE'S OWN SCAN — an earlier
hand-count said seven, because it included named assignments and single-module repeats — there are
FIVE such sets written out in two or more modules:

    {"claude-code","codex","hermes","opencode","pi"}          x5  — equals LAUNCHABLE_RUNTIMES in the
                                                                    vocabulary contract, read from it
                                                                    at none of the five sites
    {"id","sessionId","session_id"}                           x3  — a handle-field fallback chain
                                                                    (was x4 until 2026-08-17, when the
                                                                    fourth site went with the dead
                                                                    `_query_gateway_most_recent`)
    {"active","attached","idle","running","starting"}         x3  — the live-terminal set
    {"active","attached","idle","recovering","running","starting"} x2
    {"active","idle","recovering","running","starting"}       x2  — and note those last two differ
                                                                    from each other by one member

RELATED BUT NOT GATED HERE: an inline set equal to a NAMED constant in another module.
`claim_block_reason.py:103` writes `{"codex","opencode","pi","hermes"}` inline, which is exactly
`_NATIVE_MANAGED_RUNTIMES` — a constant that IS gated for Python/JS parity, so the inline copy
bypasses that gate. It is not in the ledger below because it appears inline only once, and catching
it needs a different scan (inline-versus-named) that would want its own rulings.

WHY THIS FILE FREEZES RATHER THAN FORBIDS. Every one of those groups needs a RULING, and the sibling
gate says why: "COINCIDENCE IS NOT IDENTITY, AND THIS GATE DOES NOT SAY MERGE." This repo has already
ruled the other way once — `SESSION_CLEAN_HISTORY_STATUSES` is "deliberately NOT the same set" as its
neighbour and the difference is load-bearing. `claim_block_reason.py` is the live example of the
doubt: its inline set equals `_NATIVE_MANAGED_RUNTIMES` exactly, while its own comment points at
`_CHANNEL_CLAIM_RUNTIMES`, which is a DIFFERENT set. I cannot tell from the code which concept it
means, and guessing is how the regression above happened.

Authoring seven rulings to make a gate I wrote go green is the move `oversized-allowlist.json` exists
to prevent. So this pins today's population exactly: a NEW cross-module inline duplicate fails
immediately, and an existing group that GROWS fails immediately, while the seven wait for a reviewer.
Shrinking is free — that is bookkeeping after a real ruling, and the frozen counts are asserted to be
reachable so the list cannot rot into names nobody re-checks.
"""
from __future__ import annotations

import ast
import pathlib
from collections import defaultdict

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "tests", "fixtures", "__pycache__", ".git", ".venv"}
MIN_MEMBERS = 3

# frozenset of members -> number of DISTINCT (file, line) inline occurrences, as measured 2026-08-16.
# UNRULED. This is a debt ledger, not an allowlist of good practice: adding an entry to make a red
# test green is the move this file exists to stop.
FROZEN: dict[frozenset[str], int] = {
    # console_input_queue.py:66 + :104, dispatch_start.py:97, managed_pty_for_dispatch.py:72,
    # reconcilers/undeliverable_queued_runs.py:232
    frozenset({"claude-code", "codex", "hermes", "opencode", "pi"}): 5,
    # runtimes/hermes.py (the active-session file + the sessions-dir scan), runtimes/pi.py.
    # 4 -> 3 on 2026-08-17: the fourth site was `_query_gateway_most_recent`, deleted as dead code
    # (nothing called it, and `discover_session_id` deliberately refuses to). This gate caught the
    # drop, which is what it is for — the ledger shrinks when the duplication does, and only then.
    frozenset({"id", "sessionId", "session_id"}): 3,
    # api_core/claim_gating.py:181, api_core/terminal_ownership.py:100, reconcilers/sessions.py:97
    frozenset({"active", "attached", "idle", "running", "starting"}): 3,
    # api_core/channel_delivery.py:257, routers/session_console.py:87
    frozenset({"active", "attached", "idle", "recovering", "running", "starting"}): 2,
    # routers/agents/config.py:100, routers/session_console.py:133
    frozenset({"active", "idle", "recovering", "running", "starting"}): 2,
}


def _inline_string_sets() -> dict[frozenset, list[str]]:
    """Every literal set/tuple/list of 3+ string constants that is NOT the value of an assignment.

    Assignments are excluded deliberately: a NAMED constant is the sibling gate's population, and
    double-reporting would make one of the two the wrong place to fix anything.
    """
    found: dict[frozenset, list[str]] = defaultdict(list)
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        assigned_values = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Set, ast.Tuple, ast.List)):
                continue
            if id(node) in assigned_values:
                continue
            if len(node.elts) < MIN_MEMBERS:
                continue
            if not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
                continue
            found[frozenset(e.value for e in node.elts)].append(f"{rel.as_posix()}:{node.lineno}")
    return found


def _cross_module_groups() -> dict[frozenset, list[str]]:
    """Only sets appearing in 2+ DISTINCT modules — one module repeating itself is a local style
    choice, not a fork across owners."""
    return {
        members: locations
        for members, locations in _inline_string_sets().items()
        if len({loc.split(":")[0] for loc in locations}) >= 2
    }


def test_no_new_cross_module_inline_duplicate_appears():
    groups = _cross_module_groups()
    new = sorted(
        (sorted(members), groups[members]) for members in groups if members not in FROZEN
    )
    assert not new, (
        "a NEW literal set is now written out by hand in two or more modules.\n"
        "Give it one owner, or — if the two are genuinely different questions with the same answer "
        "today — say so in a comment at each site and add it to FROZEN with the reason.\n  "
        + "\n  ".join(f"{members} at {locs}" for members, locs in new)
    )


def test_no_frozen_group_grows():
    groups = _cross_module_groups()
    grown = []
    for members, ceiling in FROZEN.items():
        actual = len(groups.get(members, []))
        if actual > ceiling:
            grown.append(f"{sorted(members)}: {ceiling} -> {actual} at {groups.get(members)}")
    assert not grown, (
        "an already-known duplicate was copied AGAIN. These are unruled debt, not a pattern to "
        "follow.\n  " + "\n  ".join(grown)
    )


def test_every_frozen_group_is_still_reachable():
    """The ledger shrinks honestly. A group that no longer exists, or whose count has dropped, means
    someone ruled and consolidated — update the number rather than leaving a ceiling nobody meets."""
    groups = _cross_module_groups()
    stale = []
    for members, ceiling in FROZEN.items():
        actual = len(groups.get(members, []))
        if actual == 0:
            stale.append(f"{sorted(members)} no longer appears — remove it from FROZEN")
        elif actual < ceiling:
            stale.append(f"{sorted(members)}: frozen at {ceiling} but only {actual} remain")
    assert not stale, "\n  ".join(stale)


def test_the_scan_sees_inline_literals_and_not_named_constants():
    """Anti-vacuity, both halves. It must find the known groups, and it must NOT report a named
    assignment — that population belongs to test_no_unruled_constant_coincidences.py, and a gate that
    claimed both would make one of them the wrong place to fix anything."""
    groups = _cross_module_groups()
    assert len(groups) == len(FROZEN), f"expected {len(FROZEN)} groups, measured {len(groups)}"

    runtimes = frozenset({"claude-code", "codex", "hermes", "opencode", "pi"})
    assert runtimes in groups, "the launchable-runtime duplication is the clearest case and must be seen"

    # `_NATIVE_MANAGED_RUNTIMES` is a named assignment in runtime.py and db.py; only the INLINE copy
    # in claim_block_reason.py may be counted here.
    native = frozenset({"codex", "hermes", "opencode", "pi"})
    inline_native = _inline_string_sets().get(native, [])
    assert any("claim_block_reason" in loc for loc in inline_native)
    assert not any("api_core/runtime.py" in loc for loc in inline_native), (
        "a named constant leaked into the inline scan — the two gates now overlap"
    )
