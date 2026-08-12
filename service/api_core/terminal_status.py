"""The terminal status vocabulary: which statuses are active, which are terminal, and what a
transition is allowed to become.

A NEUTRAL leaf, and deliberately so. `_TERMINAL_ACTIVE_STATUSES` has four consumers — the status
cache, `reconcilers/terminal_runs.py`, `routers/sessions.py` and `routers/terminals.py` — so under the
v0.5.4 constant rule it cannot follow any one function group without making three of those four
modules import a module named after somebody else's job. `routers/sessions.py` asking
`terminal_output.py` what an active status is would be a wrong-looking dependency that happens to
work.

`_TERMINAL_MONOTONIC_STATUSES` is here because `_terminal_status_transition` is its only reader and
the two constants are one vocabulary: monotonic means "already finished, so do not go back to
active", which is only meaningful against the active set. Splitting them puts a rule and its
exception in two files.

`_terminal_status_transition` is 8 lines. It is here rather than beside its caller because it is the
only thing that reads both sets, and a transition rule belongs with the vocabulary it enforces.
"""

from __future__ import annotations

_TERMINAL_ACTIVE_STATUSES = {"starting", "attached", "running", "active", "idle"}
_TERMINAL_MONOTONIC_STATUSES = {"stopping", "stopped", "failed", "lost", "ended", "completed", "cancelled"}


def _terminal_status_transition(current_status: str, next_status: str) -> str:
    current = str(current_status or "").strip().lower()
    next_value = str(next_status or "").strip().lower()
    if not next_value:
        return ""
    if current in _TERMINAL_MONOTONIC_STATUSES and next_value in _TERMINAL_ACTIVE_STATUSES:
        return ""
    return next_value


# v0.5.4: `_TERMINAL_END_STATUSES` and its derived ordered tuple arrived from the control plane. This
# module already owned the ACTIVE and MONOTONIC sets, so the terminal status vocabulary is now in one
# place — which is the point: the last time part of it lived somewhere else, two managed-worker sweeps
# came to disagree about `degraded`.
#
# It is a NEUTRAL owner rather than a follower. Unlike the two cwd regexes in the same release, this set
# had a real carrier reader (the ORDERED tuple below is derived from it at module level) plus three
# accessors in three modules, so nobody's function owns it.
#
# The two MUST stay adjacent. The ordered form exists because a set gives no ordering guarantee across
# builds and an inline literal list in a query is how that `degraded` disagreement happened;
# `test_terminal_status_sets_agree` fails the suite if they ever diverge, and a derivation split across
# a module boundary is one edit away from being re-inlined by someone who cannot see its source.

_TERMINAL_END_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}
# Deterministic, lowercase ordering of the SAME set, for SQL parameter binding. A set
# gives no ordering guarantee across builds and an inline literal list in a query is
# how the two managed-worker sweeps came to disagree about `degraded` (finding N7) —
# `test_terminal_status_sets_agree` fails the suite if these two ever diverge.
_TERMINAL_END_STATUSES_ORDERED = tuple(sorted(s.lower() for s in _TERMINAL_END_STATUSES))
