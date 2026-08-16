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

#: EVERY status a `terminal_sessions.status` column may hold. DERIVED, not retyped — the same rule
#: `new_dashboard/status.js` states for its live subset, and for the same reason: two hand-typed
#: copies of one vocabulary drift.
TERMINAL_SESSION_STATUSES = frozenset(_TERMINAL_ACTIVE_STATUSES | _TERMINAL_MONOTONIC_STATUSES)


def _terminal_status_transition(current_status: str, next_status: str) -> str:
    """Decide the status a terminal row moves to, or "" for "leave it alone".

    AN ALLOWLIST, decided 2026-08-16 after tracing what the alternative actually costs. This is the
    only gate between an HTTP body and `terminal_sessions.status`: its single caller is
    `terminal_output.py`, fed by `POST /terminals/{id}/output`, whose `status` field is
    `Optional[str]` with no validation anywhere.

    AN UNRECOGNISED VALUE IS INVISIBLE TO EVERY REAPER. Not one of them keys on age — every single
    selection is `WHERE status IN (...)`:

        reconcilers/managed_workers.py       IN ('starting','attached','running','active','idle',…)
        reconcilers/terminals.py             IN ('stopped','failed')   and the active list
        reconcilers/terminal_consistency.py  IN (<active list>)

    So a row holding a status nobody declared matches no reaper's WHERE, is not counted live by the
    status engine, is never closed out by `_close_out_terminal_on_end_status`, and reads as "still
    running" to `agent_terminal_ops.py`. Every read puts it on the live side and every cleanup skips
    it — the `lost` incident's shape, where a gate spelled `status NOT IN (...)` treated an unlisted
    status as live and left four sessions permanently unstartable.

    THE ARGUMENT FOR PASSING IT THROUGH DID NOT SURVIVE THE TRACE. It was
    `test_terminal_status_transition.py`'s: "rejecting an unrecognised target would silently drop
    writes from a newer writer" — the bridge is host-side and routinely a different build. But that
    assumes the dropped write carries information this service could use, and it cannot: a service
    that does not recognise the status has no code that acts on it. Both concrete cases favour
    refusing:

      * a bridge invents a status — keeping it strands the row forever; dropping it leaves the last
        KNOWN status, which the reapers still act on.
      * a bridge RENAMES one (`stopped` -> `exited`) — keeping it makes the row invisible to both the
        active and end lists; dropping it leaves the row `running`, and `managed_workers.py` reaps it
        as a ghost once the worker dies.

    So refusing loses a string nothing could have used, and keeping it loses the row. It is a no-op
    today: `test_terminal_status_vocabulary.py` enumerates both writers and every literal either side
    sends is already a member.

    WHICH COST TO PAY IS AN OPEN OPERATOR QUESTION and is not settled here — I changed this to refuse
    unknown statuses, found the ruling above, and reverted. What IS settled:
    `test_terminal_status_vocabulary.py` enumerates both writers and fails if either starts sending a
    status the service does not know, so while the question is open the drift cannot arrive unseen.
    """
    current = str(current_status or "").strip().lower()
    next_value = str(next_status or "").strip().lower()
    if not next_value:
        return ""
    if next_value not in TERMINAL_SESSION_STATUSES:
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
