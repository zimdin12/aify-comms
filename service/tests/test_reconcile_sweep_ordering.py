"""The periodic sweep's step ORDER is load-bearing, and nothing enforced it.

PRE-MOVE WORK for the reconciler extraction (`docs/ROADMAP.md`). That release moves 43 functions out
of `api_v2.py` in 10 slices with an intentionally empty behaviour changelog. The sweep in `main.py`
imports 28 of them and calls them in a specific order — and several of those orderings are the
*fix* for a past incident, recorded only as a prose comment beside the call:

    "MUST run BEFORE _close_orphaned_managed_runs: that reaper would FAIL the same
     claimed-never-delivered orphan (recovery is preferable to failure)."

A prose comment does not survive a refactor that reorders calls, groups them by new module, or
extracts a helper. The failure would be silent: every function still runs, every test still passes,
and a stranded run gets FAILED instead of recovered — which is the 2026-06-02 incident, where three
hermes runs sat `claimed` for 15+ minutes, the agent looked busy, and the sender never got a reply.

So the constraints become assertions before the move, not after. Each pair below cites the reason the
order exists. Adding a step is free; reordering one of these pairs fails here.

Reading the order from SOURCE is deliberate. Executing the sweep would need a database, 28 live
reconcilers and a way to observe call order — and it would still only prove the order for one run
through one branch. The order is a static property of the code, so a static assertion is the honest
tool for it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from service.tests._source import code_only

MAIN = Path(__file__).resolve().parents[1] / "main.py"

# (earlier, later, why the order is not arbitrary)
ORDERING_CONSTRAINTS = [
    (
        "_finalize_spawns_with_dead_terminals",
        "_fail_running_spawns_superseded_by_current_session",
        "The superseded reaper only clears a dead spawn once a NEWER live session exists. That is "
        "what left a spawn `running` for 97 minutes on 2026-08-07 — the replacement did not arrive "
        "until 15:13. Finalising on the dead terminal needs no successor and must get first refusal.",
    ),
    (
        "_reap_stale_orphan_bridges",
        "_prune_superseded_bridges",
        "A bridge whose process died without a clean supersede must be superseded BEFORE the prune, "
        "or it lingers with superseded_by='' forever and every status/dispatch scan counts it live "
        "(idle-CPU burn + orphan re-accumulation, 2026-07-11 perf report).",
    ),
    (
        "_requeue_orphaned_claimed_runs",
        "_close_orphaned_managed_runs",
        "The reaper would FAIL the same claimed-never-delivered orphan the requeue can RECOVER. "
        "Recovery beats failure: on 2026-06-02 three hermes runs sat `claimed` for 15+ minutes after "
        "a bridge restart, the agent looked busy, and the sender never got a reply.",
    ),
    (
        "_requeue_orphaned_claimed_runs",
        "_reap_undeliverable_queued_runs",
        "A requeued orphan becomes `queued`; a live bridge must get a chance to re-claim it before "
        "anything sweeps queued runs as undeliverable.",
    ),
    (
        "_reroute_orphaned_managed_channel_runs",
        "_reap_undeliverable_queued_runs",
        "The spawn-initial message is created before the target's channel sidecar exists, so the run "
        "is born `managed` and must be re-routed to `channel` before the queued-run reaper decides "
        "it can never be delivered.",
    ),
    (
        "_replay_undelivered_channel_messages_on_env_recovery",
        "_reap_undeliverable_queued_runs",
        "Same reason: the replayed run needs its full deliverability window before anything reaps it.",
    ),
    (
        "_fail_stranded_delivered_reply_runs",
        "_sweep_unmirrored_failed_handoffs",
        "The stranded run must reach a failed state first, so the handoff sweep has something to "
        "mirror back to the sender.",
    ),
]


def _sweep_source() -> str:
    """The periodic reconcile body, comments stripped.

    Comments matter here specifically: this function is dense with prose that NAMES the functions
    whose order is under test ("MUST run BEFORE _close_orphaned_managed_runs"). Matching those would
    make every assertion below pass on the documentation rather than the code — which is the exact
    trap `_source.code_only` exists for, hit four times on 2026-08-11.
    """
    text = code_only(MAIN.read_text(encoding="utf-8", errors="replace"))
    start = text.index("async def _run_dispatch_reconcile_once")
    return text[start : text.index("\nasync def ", start + 10)]


def _call_position(source: str, fn: str) -> int:
    match = re.search(rf"await {re.escape(fn)}\(", source)
    return match.start() if match else -1


class SweepOrderingTests(unittest.TestCase):
    def setUp(self):
        self.src = _sweep_source()

    def test_every_constrained_step_is_actually_called(self):
        """A renamed or dropped step must fail loudly here rather than quietly voiding its
        constraint — an ordering test over functions that no longer exist proves nothing."""
        for earlier, later, _ in ORDERING_CONSTRAINTS:
            for fn in (earlier, later):
                with self.subTest(fn=fn):
                    self.assertGreater(
                        _call_position(self.src, fn), -1,
                        f"{fn} is no longer called in the sweep — if that is intentional, remove its "
                        f"constraint from ORDERING_CONSTRAINTS and say why in the commit",
                    )

    def test_the_load_bearing_orderings_hold(self):
        for earlier, later, why in ORDERING_CONSTRAINTS:
            with self.subTest(f"{earlier} before {later}"):
                a = _call_position(self.src, earlier)
                b = _call_position(self.src, later)
                self.assertLess(a, b, f"{earlier} must run BEFORE {later}.\n{why}")

    def test_every_step_commits(self):
        """The other property the sweep depends on, and the reason the `database is locked` era
        ended: one trailing commit held SQLite's single writer lock across the whole multi-second
        sweep, so every bridge heartbeat 503'd once a minute. Steps commit individually."""
        # Every `await` on the line, not just the leading one: the steps are written as
        # `await _commit_step(await _repair_x(db))`, so anchoring on the first match finds only
        # `_commit_step` and the list comes back empty. The vacuity guard below caught that.
        calls = re.findall(r"await ([_a-z]\w+)\(", self.src)
        reconcilers = [c for c in calls if c.startswith(("_repair", "_reap", "_prune", "_close",
                                                         "_fail", "_finalize", "_requeue",
                                                         "_reroute", "_replay", "_reconcile",
                                                         "_clear", "_sweep", "_run_contract"))]
        self.assertGreater(len(reconcilers), 15, "the sweep body did not parse as expected")
        uncommitted = [
            fn for fn in reconcilers
            if f"_commit_step(await {fn}(" not in self.src and f"await {fn}(" in self.src
        ]
        # `_close_reconcilable_delivered_runs` runs in an explicit batching loop that commits
        # between batches rather than through the helper — a documented exception, not a miss.
        self.assertEqual(
            [f for f in uncommitted if f != "_close_reconcilable_delivered_runs"], [],
            "a reconciler step that does not commit holds the writer lock into the next step",
        )


if __name__ == "__main__":
    unittest.main()
