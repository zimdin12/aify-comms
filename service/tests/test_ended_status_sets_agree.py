"""Four constants, four owners, one value set — and only one pair had an agreement test.

`{stopped, failed, lost, ended, completed, cancelled}` is declared four times under four names:

    ENDED_AGENT_SESSION_STATUSES      service/api_core/agent_sessions.py   sessions that have ENDED
    _SESSION_DELETE_ALLOWED_STATUSES  service/api_core/tuning.py           sessions that may be DELETED
    _TERMINAL_END_STATUSES            service/api_core/terminal_status.py  terminals that have ENDED
    _TERMINAL_DELETE_ALLOWED_STATUSES service/routers/sessions.py          terminals that may be DELETED

They are two questions ("has it ended?", "may it be deleted?") across two subjects (sessions,
terminals), and today all four answer identically because the rule in force is "ended implies
deletable".

THE REPO ALREADY RULED ON WHAT TO DO ABOUT THIS. From `test_spawn_dead_terminal_finalize.py`:

    N7 was a real bug caused by two sweeps disagreeing about a status literal. The expensive remedy
    is consolidating every copy; the cheap one is a test that the copies AGREE. This pins the ordered
    SQL-binding tuple to the named set, so a future edit to one of them fails the suite instead of
    drifting silently.

The cheap remedy was adopted — and applied to the narrowest possible pair: one set and its own
ordered form, inside a single module. The four-way group across four modules had nothing, which is
the shape N7 actually was: two SWEEPS, in different modules, disagreeing.

AND THE DISCIPLINE IS APPLIED UNEVENLY INSIDE ONE FILE. `service/routers/sessions.py` imports
`_SESSION_DELETE_ALLOWED_STATUSES` from its owner and wraps it in
`_borrowed_session_delete_allowed_statuses()`, whose docstring is "BORROWED constant: one owner,
never a copy — a forked status set is finding N7". Eight lines earlier the same file declares
`_TERMINAL_DELETE_ALLOWED_STATUSES` as a literal copy of `_TERMINAL_END_STATUSES`, and never imports
`terminal_status` at all. One set borrowed, one copied, in the same module, under a docstring
forbidding the copy.

THIS PINS AGREEMENT; IT DOES NOT RULE THEM IDENTICAL. "Ended" and "deletable" could legitimately
diverge — allowing an operator to delete a `lost` terminal without calling it ended would be a
reasonable product decision. If that happens, THIS FILE is where it gets recorded, deliberately,
instead of two sweeps discovering it against each other in production.
"""

from __future__ import annotations

import unittest

from service.api_core.agent_sessions import ENDED_AGENT_SESSION_STATUSES
from service.api_core.terminal_status import _TERMINAL_END_STATUSES
from service.api_core.tuning import _SESSION_DELETE_ALLOWED_STATUSES
from service.routers.sessions import _TERMINAL_DELETE_ALLOWED_STATUSES

#: name -> (value set, owning module). All four must agree until someone decides otherwise HERE.
GROUP = {
    "ENDED_AGENT_SESSION_STATUSES": (ENDED_AGENT_SESSION_STATUSES, "service/api_core/agent_sessions.py"),
    "_SESSION_DELETE_ALLOWED_STATUSES": (_SESSION_DELETE_ALLOWED_STATUSES, "service/api_core/tuning.py"),
    "_TERMINAL_END_STATUSES": (_TERMINAL_END_STATUSES, "service/api_core/terminal_status.py"),
    "_TERMINAL_DELETE_ALLOWED_STATUSES": (_TERMINAL_DELETE_ALLOWED_STATUSES, "service/routers/sessions.py"),
}

EXPECTED = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}


class EndedStatusSetsAgreeTests(unittest.TestCase):
    def test_all_four_hold_the_same_statuses(self):
        for name, (values, owner) in GROUP.items():
            with self.subTest(constant=name):
                self.assertEqual(
                    {s.lower() for s in values}, EXPECTED,
                    f"{name} ({owner}) no longer matches the other three. If that is DELIBERATE — "
                    f"'ended' and 'deletable' parting company is a legitimate product decision — "
                    f"record it here and say which. If it is not, this is finding N7 again: two "
                    f"modules disagreeing about a status literal, which is how a lost worker read "
                    f"as 'already running' forever.",
                )

    def test_the_group_is_pairwise_equal_not_merely_each_equal_to_a_constant(self):
        """Comparing each to EXPECTED would still pass if EXPECTED itself drifted with one of them.
        Compare them to each OTHER as well."""
        sets = [({s.lower() for s in v}, n) for n, (v, _o) in GROUP.items()]
        first, first_name = sets[0]
        for values, name in sets[1:]:
            self.assertEqual(values, first, f"{name} disagrees with {first_name}")

    def test_the_status_vocabulary_is_the_ended_half_not_everything(self):
        """Anti-vacuity: agreement is trivial if the set had grown to contain every status. These
        must NOT be in it — a live status leaking into an 'ended' set makes a running worker
        deletable, which is the opposite failure to N7 and worse."""
        for live in ("running", "starting", "attached", "active", "idle", "recovering", "queued"):
            with self.subTest(status=live):
                for name, (values, _owner) in GROUP.items():
                    self.assertNotIn(
                        live, {s.lower() for s in values},
                        f"{name} now counts {live!r} as ended/deletable",
                    )

    def test_sessions_router_copies_a_set_whose_owner_it_could_import(self):
        """The uneven discipline, pinned as the fact it is rather than fixed in passing.

        `service/routers/sessions.py` BORROWS `_SESSION_DELETE_ALLOWED_STATUSES` from its owner —
        with a docstring saying "one owner, never a copy" — and COPIES the terminal set eight lines
        earlier. Repointing it is a one-line change, but which module should own "a terminal may be
        deleted" is the same reviewer question as the rest of this group, so it is recorded, not
        decided. If the copy is repointed, this test should be deleted with it.
        """
        import service.routers.sessions as sessions_router

        self.assertIs(
            sessions_router._SESSION_DELETE_ALLOWED_STATUSES, _SESSION_DELETE_ALLOWED_STATUSES,
            "the session set is borrowed from tuning.py — that half is right",
        )
        self.assertIsNot(
            sessions_router._TERMINAL_DELETE_ALLOWED_STATUSES, _TERMINAL_END_STATUSES,
            "the terminal set is now BORROWED rather than copied. Good — delete this test and drop "
            "_TERMINAL_DELETE_ALLOWED_STATUSES from the group above.",
        )
