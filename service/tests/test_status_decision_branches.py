"""Branch characterization for `_decide_effective_status` — the status engine's actual decision.

WHY THIS FILE EXISTS. Until v0.5.4 this decision lived inside the 551-line
`_compute_live_status_cache` and was reachable ONLY through a database and a route. Six test files
touch that function; every one of them does so incidentally, through integration. So the block that
decides whether an agent reads as offline, blocked, working or online — twelve assignment sites behind
eighteen conditions — had no direct branch coverage at all. Extracting it (87e953fa) was the
prerequisite for this; this is the point of that extraction.

CHARACTERIZATION, NOT SPECIFICATION. These tests record what the code DOES today, branch by branch,
so a later reshape (a facts object, a purity change, a mode split) has something to be checked
against. Where a branch's behaviour looks surprising, the test says so and pins it anyway — that is
the job. Two are load-bearing regressions rather than mere behaviour, and are marked REGRESSION.

INPUT SHAPE. The decision takes plain values plus three row-likes (`agent_row`, `active_run`,
`channel_pending_reply_run`) that it indexes with `[...]`, and a `db` used for exactly one call. Plain
dicts satisfy the row protocol; `db` is only ever passed through to `_managed_console_is_booting`,
which is patched where it is used.

ORDER IS THE CONTRACT. This is an if/elif chain, so a branch's meaning is "these conditions AND none
of the earlier ones". Several tests therefore assert PRECEDENCE explicitly — that a later condition
does not win when an earlier one is also true — because a reshape that preserves each branch in
isolation while reordering them would pass a naive test suite and change the fleet's reported state.
"""

from __future__ import annotations

import unittest
from unittest import mock

from service.api_core import status_decision
from service.api_core.status_decision import _decide_effective_status


def _row(**kw):
    """A dict satisfying the `row["key"]` protocol the decision uses."""
    base = {"id": "run-1", "subject": "", "runtime": "", "dispatch_mode": ""}
    base.update(kw)
    return base


def _call(**over):
    """Invoke the decision with all-neutral inputs, overridden per test.

    Neutral means: every guard false, no runs, no hints — the `else` tail. Each test then flips the
    ONE fact its branch keys on, so a test failing tells you which input changed the outcome.
    """
    kw = dict(
        active_run=None,
        active_run_terminal_missing=False,
        agent_row=_row(id="agent-1"),
        agent_session_mode="managed",
        channel_managed_no_console=False,
        channel_managed_no_sidecar=False,
        channel_pending_reply_run=None,
        db=object(),
        env_bridge_id="",
        env_status="",
        environment_id="",
        has_live_worker=True,
        live_session=True,
        managed_env_bridge_offline=False,
        resident_bridge_stale=False,
        session_bridge_id="",
        session_status="",
        terminal_input_hint="",
        terminal_status="",
        turn_busy=False,
        turn_runtime="",
        effective_status="available",
        reason="",
        awaiting_reply=False,
    )
    kw.update(over)

    async def run():
        return await _decide_effective_status(**kw)

    import asyncio

    return asyncio.run(run())


class OfflineBranches(unittest.TestCase):
    def test_managed_env_bridge_offline_wins_over_everything(self):
        status, reason, _ = _call(managed_env_bridge_offline=True, environment_id="env-1",
                                  env_status="offline")
        self.assertEqual("offline", status)
        self.assertIn("env-1", reason)

    def test_env_bridge_offline_outranks_an_active_run(self):
        """PRECEDENCE. Only the env bridge can host the worker, so a surviving run is moot."""
        status, _, _ = _call(managed_env_bridge_offline=True, environment_id="env-1",
                             active_run=_row(subject="still going"), turn_busy=True)
        self.assertEqual("offline", status, "a live-looking run must not outrank a dead env bridge")

    def test_unreachable_environment_is_offline(self):
        status, reason, _ = _call(environment_id="env-9", env_status="degraded")
        self.assertNotEqual("offline", status, "degraded is a REACHABLE state")
        status, reason, _ = _call(environment_id="env-9", env_status="dead")
        self.assertEqual("offline", status)
        self.assertIn("env-9", reason)

    def test_a_fresh_resident_survives_an_unreachable_environment(self):
        """A resident's liveness is its own bridge, not the environment's."""
        status, _, _ = _call(environment_id="env-9", env_status="dead",
                             agent_session_mode="resident", resident_bridge_stale=False)
        self.assertNotEqual("offline", status)
        status, _, _ = _call(environment_id="env-9", env_status="dead",
                             agent_session_mode="resident", resident_bridge_stale=True)
        self.assertEqual("offline", status, "a STALE resident bridge does not get the carve-out")

    def test_an_orphaned_session_bridge_is_offline_for_non_managed_only(self):
        common = dict(session_bridge_id="bridge-old", env_bridge_id="bridge-new",
                      live_session=False, active_run=None)
        status, reason, _ = _call(agent_session_mode="resident", **common)
        self.assertEqual("offline", status)
        self.assertIn("no longer owns", reason)
        status, _, _ = _call(agent_session_mode="managed", **common)
        self.assertNotEqual(
            "offline", status,
            "a MANAGED agent with a reachable env stays lazily autostartable, not offline",
        )

    def test_a_stale_resident_bridge_is_offline(self):
        status, reason, _ = _call(agent_session_mode="resident", resident_bridge_stale=True)
        self.assertEqual("offline", status)
        self.assertIn("restart the resident wrapper", reason)

    def test_a_stale_resident_bridge_beats_turn_busy(self):
        """REGRESSION (pure-event-status change #2). A dead resident with a lingering turn_busy=1
        must NOT reach the turn_busy branch and show working forever."""
        status, _, _ = _call(agent_session_mode="resident", resident_bridge_stale=True,
                             turn_busy=True)
        self.assertEqual("offline", status, "liveness must win over a stale turn_busy")


class BlockedBranches(unittest.TestCase):
    def test_a_terminal_backed_run_with_no_terminal_is_blocked(self):
        status, reason, _ = _call(active_run_terminal_missing=True,
                                  active_run=_row(subject="deploy"))
        self.assertEqual("blocked", status)
        self.assertIn("deploy", reason)

    def test_an_active_run_with_an_input_hint_is_blocked(self):
        status, reason, _ = _call(active_run=_row(subject="build"),
                                  terminal_input_hint="Waiting for approval.")
        self.assertEqual("blocked", status)
        self.assertIn("Waiting for approval.", reason)
        self.assertIn("build", reason)

    def test_a_managed_worker_awaiting_input_with_no_run_is_blocked(self):
        status, reason, _ = _call(agent_session_mode="managed", has_live_worker=True,
                                  terminal_input_hint="Approve?", terminal_status="active")
        self.assertEqual("blocked", status)
        self.assertEqual("Approve?", reason)

    def test_that_branch_needs_an_ACTIVE_terminal(self):
        status, _, _ = _call(agent_session_mode="managed", has_live_worker=True,
                             terminal_input_hint="Approve?", terminal_status="stopped")
        self.assertNotEqual("blocked", status)


class WorkingBranches(unittest.TestCase):
    def test_an_active_run_is_working(self):
        status, reason, _ = _call(active_run=_row(subject="compile"))
        self.assertEqual("working", status)
        self.assertIn("compile", reason)

    def test_turn_busy_is_working_and_names_the_runtime(self):
        status, reason, _ = _call(turn_busy=True, turn_runtime="hermes")
        self.assertEqual("working", status)
        self.assertIn("hermes", reason)
        _, reason_bare, _ = _call(turn_busy=True, turn_runtime="")
        self.assertEqual("Executing turn.", reason_bare)

    def test_a_transitioning_session_is_working(self):
        for session_status in ("recovering", "restarting"):
            status, reason, _ = _call(session_status=session_status)
            self.assertEqual("working", status, session_status)
            self.assertEqual(session_status, reason)
        status, _, _ = _call(terminal_status="stopping")
        self.assertEqual("working", status)


class AwaitingReplyBranch(unittest.TestCase):
    def test_an_idle_agent_owing_a_reply_is_ONLINE_not_working(self):
        """The operator-reported 'blink when not working': idle-owes-reply is not `working`."""
        status, reason, awaiting = _call(channel_pending_reply_run=_row(subject="question"),
                                         has_live_worker=True)
        self.assertEqual("online", status)
        self.assertTrue(awaiting)
        self.assertIn("question", reason)

    def test_a_DEAD_managed_worker_owing_a_reply_is_NOT_online(self):
        """REGRESSION (FIX-3). This branch once manufactured `online` for a dead agent.

        Visible-TUI truthfulness: a managed worker with no live console/sidecar must fall through so
        the available/offline derivation stands, rather than being upgraded because it owes a reply.
        """
        status, _, awaiting = _call(channel_pending_reply_run=_row(subject="q"),
                                    agent_session_mode="managed", has_live_worker=False)
        self.assertNotEqual("online", status)
        self.assertFalse(awaiting, "a dead worker must not be flagged as awaiting a reply")

    def test_a_stale_resident_owing_a_reply_is_NOT_online(self):
        """Right outcome — but reached by an EARLIER branch than the name suggests, so say so.

        My first version of this test claimed to exercise the `worker_is_dead` guard. It does not:
        with `resident_bridge_stale=True` the stale-resident branch fires first and returns offline.
        The assertion below is still worth keeping (a stale resident owing a reply must not read
        online), it is simply satisfied upstream. Mutation testing is what exposed the mislabel — the
        test passed while the guard it named was removed.
        """
        status, _, awaiting = _call(channel_pending_reply_run=_row(subject="q"),
                                    agent_session_mode="resident", resident_bridge_stale=True)
        self.assertEqual("offline", status, "satisfied by the stale-resident branch, not worker_is_dead")
        self.assertFalse(awaiting)

    def test_the_resident_term_in_worker_is_dead_is_UNREACHABLE(self):
        """A measured finding, pinned so a reshape cannot quietly rely on it.

        `worker_is_dead` is `(managed and not has_live_worker) or resident_bridge_stale`. The second
        term can never be True where it is evaluated: with a stale resident bridge, either there is no
        active run (the stale-resident branch returns offline) or there is one (the active-run branches
        claim it). Exhaustive search over active_run x hint x mode x turn_busy finds ZERO inputs that
        reach the awaiting-reply branch with `resident_bridge_stale=True`.

        NOT removed. It is defensive and documents intent, and deleting it would be a behaviour-adjacent
        change to the status engine on the strength of an ordering that a later reshape might alter —
        which is exactly when the term would start mattering. Recorded here instead so that reshape has
        to confront it deliberately.
        """
        import itertools

        reached = [
            combo
            for combo in itertools.product([None, _row()], ["", "hint"], ["resident", "managed"],
                                           [False, True])
            if _call(active_run=combo[0], terminal_input_hint=combo[1], agent_session_mode=combo[2],
                     turn_busy=combo[3], resident_bridge_stale=True,
                     channel_pending_reply_run=_row(subject="q"))[2]
        ]
        self.assertEqual([], reached, "the awaiting-reply branch became reachable with a stale resident")

    def test_a_live_resident_with_no_tracked_terminal_keeps_the_online_state(self):
        """has_live_worker=False is not death for a resident whose bridge is fresh."""
        status, _, awaiting = _call(channel_pending_reply_run=_row(subject="q"),
                                    agent_session_mode="resident",
                                    resident_bridge_stale=False, has_live_worker=False)
        self.assertEqual("online", status)
        self.assertTrue(awaiting)


class AvailableTailBranch(unittest.TestCase):
    def test_no_visible_console_annotates_available_without_changing_it(self):
        status, reason, _ = _call(effective_status="available", channel_managed_no_console=True)
        self.assertEqual("available", status, "the annotation must not change the status")
        self.assertIn("no visible console", reason)

    def test_an_existing_reason_is_not_overwritten(self):
        _, reason, _ = _call(effective_status="available", channel_managed_no_console=True,
                             reason="already explained")
        self.assertEqual("already explained", reason)

    def test_a_BOOTING_console_displays_online(self):
        """Display-only: routing is untouched, so a send during boot still queues."""
        with mock.patch.object(status_decision, "_managed_console_is_booting",
                               new=mock.AsyncMock(return_value=True)):
            status, reason, _ = _call(effective_status="available", channel_managed_no_sidecar=True)
        self.assertEqual("online", status)
        self.assertIn("booting", reason.lower())

    def test_a_sidecar_that_registered_then_DIED_stays_available(self):
        with mock.patch.object(status_decision, "_managed_console_is_booting",
                               new=mock.AsyncMock(return_value=False)):
            status, reason, _ = _call(effective_status="available", channel_managed_no_sidecar=True)
        self.assertEqual("available", status)
        self.assertIn("not deliverable", reason)

    def test_the_neutral_case_changes_nothing(self):
        """No guard true, nothing to annotate: the incoming status and reason pass through."""
        status, reason, awaiting = _call(effective_status="available")
        self.assertEqual(("available", "", False), (status, reason, awaiting))


class PrecedenceInvariants(unittest.TestCase):
    def test_blocked_outranks_working(self):
        """An active run with an input hint is BLOCKED, not WORKING — the hint wins."""
        status, _, _ = _call(active_run=_row(subject="x"), terminal_input_hint="Approve?")
        self.assertEqual("blocked", status)

    def test_an_active_run_outranks_turn_busy(self):
        status, reason, _ = _call(active_run=_row(subject="named-run"), turn_busy=True,
                                  turn_runtime="codex")
        self.assertEqual("working", status)
        self.assertIn("named-run", reason, "the run's reason must win over the turn's")

    def test_working_outranks_the_awaiting_reply_branch(self):
        status, _, awaiting = _call(active_run=_row(subject="x"),
                                    channel_pending_reply_run=_row(subject="q"))
        self.assertEqual("working", status)
        self.assertFalse(awaiting)


class TheHotPathQueryBoundary(unittest.TestCase):
    """`_managed_console_is_booting` is the decision's ONLY database call. Where it runs is a contract.

    The reviewer asked for this explicitly, and it is the reason the decision was left async rather than
    made pure: hoisting that call to compute a fact up front would add a query to EVERY status
    computation, on a path the dashboard polls. Today it runs only on the last branch, and only when a
    managed agent's channel sidecar is missing.

    These cases assert the BOUNDARY, not just the branch: every earlier outcome must reach its answer
    with ZERO database calls. A reshape that made the decision pure by pre-computing this fact would
    keep every other test in this file green while quietly adding a query per poll.
    """

    def _counting_probe(self, **over):
        """Run the decision with the booting query counted rather than merely stubbed."""
        calls = []
        booting = over.pop("_booting", False)

        async def _counted(db, agent_id):
            calls.append(agent_id)
            return booting

        with mock.patch.object(status_decision, "_managed_console_is_booting", new=_counted):
            result = _call(**over)
        return result, calls

    def test_the_booting_query_runs_ONLY_for_a_managed_agent_missing_its_sidecar(self):
        (status, reason, _), calls = self._counting_probe(
            effective_status="available", channel_managed_no_sidecar=True, _booting=True)
        self.assertEqual(["agent-1"], calls, "the query must run exactly once on this branch")
        self.assertEqual("online", status)

    def test_no_database_call_on_the_offline_branches(self):
        for label, over in (
            ("env bridge offline", dict(managed_env_bridge_offline=True, environment_id="e")),
            ("unreachable env", dict(environment_id="e", env_status="dead")),
            ("stale resident", dict(agent_session_mode="resident", resident_bridge_stale=True)),
        ):
            (status, _, _), calls = self._counting_probe(**over)
            self.assertEqual("offline", status, label)
            self.assertEqual([], calls, f"{label}: reached its answer with a database query")

    def test_no_database_call_on_the_blocked_branches(self):
        for label, over in (
            ("terminal missing", dict(active_run_terminal_missing=True, active_run=_row())),
            ("run with hint", dict(active_run=_row(), terminal_input_hint="Approve?")),
            ("managed awaiting input", dict(agent_session_mode="managed", has_live_worker=True,
                                            terminal_input_hint="Approve?", terminal_status="active")),
        ):
            (status, _, _), calls = self._counting_probe(**over)
            self.assertEqual("blocked", status, label)
            self.assertEqual([], calls, f"{label}: reached its answer with a database query")

    def test_no_database_call_on_the_working_branches(self):
        for label, over in (
            ("active run", dict(active_run=_row())),
            ("turn busy", dict(turn_busy=True)),
            ("transitioning", dict(session_status="restarting")),
        ):
            (status, _, _), calls = self._counting_probe(**over)
            self.assertEqual("working", status, label)
            self.assertEqual([], calls, f"{label}: reached its answer with a database query")

    def test_no_database_call_on_the_awaiting_reply_branch(self):
        (status, _, awaiting), calls = self._counting_probe(
            channel_pending_reply_run=_row(subject="q"), has_live_worker=True)
        self.assertEqual("online", status)
        self.assertTrue(awaiting)
        self.assertEqual([], calls)

    def test_no_database_call_when_the_tail_has_nothing_to_annotate(self):
        """The neutral pass-through must not query either — this is the commonest case of all."""
        (status, _, _), calls = self._counting_probe(effective_status="available")
        self.assertEqual("available", status)
        self.assertEqual([], calls)

    def test_no_database_call_for_the_no_console_annotation(self):
        (status, reason, _), calls = self._counting_probe(
            effective_status="available", channel_managed_no_console=True)
        self.assertEqual("available", status)
        self.assertIn("no visible console", reason)
        self.assertEqual([], calls, "the no-console annotation must not trigger the booting query")


if __name__ == "__main__":
    unittest.main()
