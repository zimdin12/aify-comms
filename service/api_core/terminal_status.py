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
