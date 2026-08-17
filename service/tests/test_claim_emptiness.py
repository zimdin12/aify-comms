""""Is there still nothing to do?" — the five predicates every long-poll claim waits on.

Until v0.5.4 all five were written INLINE at the `longpoll()` call: four one-line lambdas and one
nested `def`. None could be imported, so none had a test, and the nested one was never executed at
all — `longpoll` reads `if wait_ms <= 0 or not is_empty(result)`, `or` short-circuits, and every
test in the suite used the default `waitMs=0`.

BOTH FAILURE MODES ARE SILENT:

* too EAGER — says empty when the result is actionable, so the endpoint holds the request open with
  a claimed run in hand. The bridge reports a claim that timed out and the run sits claimed by
  nobody;
* too RELUCTANT — says non-empty when there is nothing, so `longpoll` returns on the first attempt
  every time. The long poll degrades back into the short poll it replaced, and the only symptom is
  request volume.

Neither raises, and neither shows up in a status. The tests below therefore assert BOTH directions
for every predicate rather than only the "returns True on an empty result" half.

THE FIVE DO NOT AGREE WITH EACH OTHER, and each disagreement is pinned here rather than smoothed
over: `spawn_request` requires its key to be PRESENT, `environment_control` requires a companion key
to be ABSENT, and the two control-list predicates compare against an exact `[]` — so a result with
no `controls` key at all reads as actionable. Making them consistent is a behaviour change and a
reviewer's call; this file's job is to make the current answer visible.

THE LAST CLASS OF TEST IS WIRING. A correct predicate helps nobody if a route passes a different
one, so each handler is called with `longpoll.longpoll` replaced by a recorder, and the predicate it
actually handed over is compared by IDENTITY. That is what the extraction could otherwise break, and
it is asserted by calling the route rather than by reading its source.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from service import longpoll
from service.api_core.claim_emptiness import (
    dispatch_claim_is_empty,
    dispatch_controls_is_empty,
    environment_control_is_empty,
    spawn_request_is_empty,
    terminal_controls_is_empty,
)


class DispatchClaimEmptinessTests(unittest.TestCase):
    """`/dispatch/claim`."""

    def test_a_result_with_no_run_and_no_directive_is_empty(self):
        self.assertIs(dispatch_claim_is_empty({"ok": True, "run": None}), True)

    # The lock-result case is NOT asserted here. A literal written by hand would be the same dict
    # as the test above and would pass whatever the route actually supplies —
    # `test_every_route_calls_its_predicate_EMPTY_on_its_own_lock_result` reads it off the handler.

    def test_a_CLAIMED_RUN_is_not_empty(self):
        self.assertIs(dispatch_claim_is_empty({"ok": True, "run": {"id": "run-1"}}), False)

    def test_an_EMPTY_run_object_is_still_a_claim(self):
        """`is None`, not falsiness. The two differ only for a run that is present and empty — and
        that difference decides which way the endpoint fails. Treating `{}` as nothing holds the
        request open while the run row has already been claimed in the database, so the bridge is
        told nothing about work it now owns; treating it as a claim returns a thin body, which the
        caller can see."""
        self.assertIs(dispatch_claim_is_empty({"ok": True, "run": {}}), False)

    def test_a_STOPPED_directive_is_not_empty(self):
        """`stopped` tells the bridge to tear its worker down. Holding it for the length of a long
        poll delays a shutdown the operator has already asked for."""
        self.assertIs(dispatch_claim_is_empty({"run": None, "stopped": True}), False)

    def test_a_RELEASE_directive_is_not_empty(self):
        self.assertIs(dispatch_claim_is_empty({"run": None, "release": True}), False)

    def test_a_BLOCKED_BY_answer_is_not_empty(self):
        """"You cannot claim, and here is who is holding it" is an answer. Waiting on it would hide
        the reason until the poll expired."""
        self.assertIs(dispatch_claim_is_empty({"run": None, "blockedBy": "other-agent"}), False)

    def test_each_directive_is_read_for_TRUTH_not_presence(self):
        """The handler emits these keys explicitly false on the ordinary empty path. Testing for
        the KEY rather than its value would make every empty poll look actionable."""
        self.assertIs(
            dispatch_claim_is_empty(
                {"run": None, "stopped": False, "release": False, "blockedBy": None},
            ),
            True,
        )

    def test_a_run_present_beats_every_absent_directive(self):
        self.assertIs(
            dispatch_claim_is_empty(
                {"run": {"id": "r"}, "stopped": False, "release": False, "blockedBy": None},
            ),
            False,
        )


class ControlListEmptinessTests(unittest.TestCase):
    """`/dispatch/controls/claim` and `/terminals/controls/claim`."""

    def test_an_empty_control_list_is_empty(self):
        self.assertIs(dispatch_controls_is_empty({"ok": True, "controls": []}), True)
        self.assertIs(terminal_controls_is_empty({"ok": True, "controls": []}), True)

    def test_a_SINGLE_pending_control_is_not_empty(self):
        self.assertIs(dispatch_controls_is_empty({"controls": [{"id": "c1"}]}), False)
        self.assertIs(terminal_controls_is_empty({"controls": [{"id": "c1"}]}), False)

    def test_a_result_with_NO_controls_key_is_treated_as_ACTIONABLE(self):
        """The asymmetry worth knowing about: this is `== []`, not falsiness, so an unrecognised
        result shape ends the wait instead of extending it. Pinned as-is — erring toward returning
        is the safe direction for a predicate whose other failure mode is a request held open with
        work in hand."""
        self.assertIs(dispatch_controls_is_empty({"ok": True}), False)
        self.assertIs(terminal_controls_is_empty({"ok": True}), False)

    def test_a_NULL_controls_value_is_also_actionable(self):
        self.assertIs(dispatch_controls_is_empty({"controls": None}), False)
        self.assertIs(terminal_controls_is_empty({"controls": None}), False)

    def test_the_two_control_predicates_still_AGREE(self):
        """They are separate functions on purpose — two endpoints, two tables, two handlers — and
        one changing shape must not silently redefine emptiness for the other. This is the test that
        turns a divergence into a decision instead of a surprise."""
        for result in ({"controls": []}, {"controls": [{"id": "c"}]}, {"ok": True},
                       {"controls": None}, {}):
            with self.subTest(result=result):
                self.assertEqual(
                    dispatch_controls_is_empty(result), terminal_controls_is_empty(result),
                )


class EnvironmentControlEmptinessTests(unittest.TestCase):
    """`/environments/controls/claim` — the companion key is a NEGATIVE guard."""

    def test_no_control_and_no_control_id_is_empty(self):
        self.assertIs(environment_control_is_empty({"ok": True, "control": None}), True)

    def test_a_control_is_not_empty(self):
        self.assertIs(environment_control_is_empty({"control": {"id": "ec1"}}), False)

    def test_a_CONTROL_ID_makes_a_null_control_actionable(self):
        """The handler reporting on a specific control has answered the request even when the
        control itself came back None. Waiting on that would hold a request open for a question
        already resolved."""
        self.assertIs(
            environment_control_is_empty({"control": None, "controlId": "ec1"}), False,
        )

    def test_a_NULL_control_id_still_counts_as_present(self):
        """`in`, not truthiness — the key being there at all is the signal."""
        self.assertIs(environment_control_is_empty({"control": None, "controlId": None}), False)


class SpawnRequestEmptinessTests(unittest.TestCase):
    """`/spawn-requests/claim` — the companion key is a POSITIVE guard, the opposite shape."""

    def test_a_present_and_null_spawn_request_is_empty(self):
        self.assertIs(spawn_request_is_empty({"ok": True, "spawnRequest": None}), True)

    def test_a_spawn_request_is_not_empty(self):
        self.assertIs(spawn_request_is_empty({"spawnRequest": {"id": "sr1"}}), False)

    def test_a_BLOCKED_BY_answer_is_not_empty(self):
        self.assertIs(
            spawn_request_is_empty({"spawnRequest": None, "blockedBy": "env-busy"}), False,
        )

    def test_a_result_that_NEVER_MENTIONS_the_key_is_actionable(self):
        """The opposite guard to the environment predicate above, and the reason both are worth a
        test: a result of some other shape — an error body, a future field set — has already
        answered, and waiting on it would hold the request open until the client gave up."""
        self.assertIs(spawn_request_is_empty({"ok": True}), False)
        self.assertIs(spawn_request_is_empty({}), False)


class InsideTheRealLongPollLoopTests(unittest.TestCase):
    """The predicate is only ever called by `longpoll()`, so at least one path runs it there."""

    def test_an_actionable_result_ends_the_wait_on_the_FIRST_attempt(self):
        async def run():
            calls = {"n": 0}

            async def attempt():
                calls["n"] += 1
                return {"ok": True, "run": None, "stopped": True}

            result = await longpoll.longpoll(25000, attempt, dispatch_claim_is_empty,
                                             scope="dispatch-test-stopped")
            return result, calls["n"]

        result, attempts = asyncio.run(run())
        self.assertTrue(result["stopped"])
        self.assertEqual(attempts, 1, "a stopped directive was held open by the long poll")

    def test_an_empty_result_waits_and_a_notify_returns_the_work(self):
        """The predicate's whole reason to exist, driven through the real loop rather than called
        directly: empty on the first attempt, awake and claiming on the second."""
        async def run():
            calls = {"n": 0}

            async def attempt():
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"ok": True, "run": None}
                return {"ok": True, "run": {"id": "run-2"}}

            async def wake():
                await asyncio.sleep(0.05)
                longpoll.notify("dispatch-test-wake")

            task = asyncio.ensure_future(wake())
            result = await longpoll.longpoll(25000, attempt, dispatch_claim_is_empty,
                                             scope="dispatch-test-wake")
            await task
            return result, calls["n"]

        result, attempts = asyncio.run(run())
        self.assertEqual(result["run"]["id"], "run-2")
        self.assertEqual(attempts, 2)

    def test_waitMs_zero_never_calls_the_predicate_at_all(self):
        """Why the nested `def` was never entered by the suite. `longpoll` reads
        `if wait_ms <= 0 or not is_empty(result)` and `or` short-circuits, so with the default
        waitMs every test in the service skipped straight past it."""
        calls = []

        def counting(result):
            calls.append(result)
            return dispatch_claim_is_empty(result)

        async def run():
            async def attempt():
                return {"ok": True, "run": None}

            return await longpoll.longpoll(0, attempt, counting)

        asyncio.run(run())
        self.assertEqual(calls, [], "waitMs=0 consulted the emptiness predicate")


class _StubRequest:
    """Only what a claim handler touches before `longpoll()` is called."""

    async def is_disconnected(self) -> bool:
        return False


class TheRoutesPassTheirOwnPredicateTests(unittest.TestCase):
    """Wiring, asserted by CALLING each handler — not by reading its source.

    `longpoll.longpoll` is replaced with a recorder, so the claim attempt inside the lambda is never
    awaited and no database is touched. What is checked is the third positional argument: the
    predicate the route actually handed over, compared by identity.
    """

    def _predicate_passed_by(self, call_handler) -> object:
        captured = {}

        async def recorder(wait_ms, attempt, is_empty, **kwargs):
            captured["is_empty"] = is_empty
            captured["scope"] = kwargs.get("scope")
            captured["lock_result"] = kwargs.get("lock_result")
            return {"recorded": True}

        with mock.patch.object(longpoll, "longpoll", recorder):
            asyncio.run(call_handler())
        return captured

    def test_dispatch_claim(self):
        from service.models import DispatchClaimRequest
        from service.routers.dispatch_messages.dispatch import claim_dispatch

        captured = self._predicate_passed_by(
            lambda: claim_dispatch(DispatchClaimRequest(agentId="a"), _StubRequest()),
        )
        self.assertIs(captured["is_empty"], dispatch_claim_is_empty)
        self.assertEqual(captured["scope"], "dispatch")

    def test_dispatch_controls_claim(self):
        from service.models import DispatchControlClaimRequest
        from service.routers.dispatch_messages.controls import claim_dispatch_controls

        captured = self._predicate_passed_by(
            lambda: claim_dispatch_controls(
                DispatchControlClaimRequest(agentId="a"), _StubRequest(),
            ),
        )
        self.assertIs(captured["is_empty"], dispatch_controls_is_empty)
        self.assertEqual(captured["scope"], "control")

    def test_terminal_controls_claim(self):
        from service.models import TerminalControlClaim
        from service.routers.terminal_controls import claim_terminal_controls

        captured = self._predicate_passed_by(
            lambda: claim_terminal_controls(
                TerminalControlClaim(environmentId="env", bridgeId="bi"),
            ),
        )
        self.assertIs(captured["is_empty"], terminal_controls_is_empty)
        self.assertEqual(captured["scope"], "terminal-control")

    def test_environment_control_claim(self):
        from service.models import EnvironmentControlClaim
        from service.routers.environments import claim_environment_control

        captured = self._predicate_passed_by(
            lambda: claim_environment_control(
                EnvironmentControlClaim(environmentId="env", bridgeId="bi"),
            ),
        )
        self.assertIs(captured["is_empty"], environment_control_is_empty)
        self.assertEqual(captured["scope"], "env-control")

    def test_spawn_request_claim(self):
        from service.models import SpawnRequestClaim
        from service.routers.spawn_requests import claim_spawn_request

        captured = self._predicate_passed_by(
            lambda: claim_spawn_request(
                SpawnRequestClaim(environmentId="env", bridgeId="bi"), _StubRequest(),
            ),
        )
        self.assertIs(captured["is_empty"], spawn_request_is_empty)
        self.assertEqual(captured["scope"], "spawn")

    def test_every_route_calls_its_predicate_EMPTY_on_its_own_lock_result(self):
        """The substituted result a claim returns under SQLite write contention. If a route's lock
        result did not read as empty, a moment of contention would end the long poll and send that
        bridge back to short polling — silently, and exactly under load."""
        from service.models import (
            DispatchClaimRequest,
            DispatchControlClaimRequest,
            EnvironmentControlClaim,
            SpawnRequestClaim,
            TerminalControlClaim,
        )
        from service.routers.dispatch_messages.controls import claim_dispatch_controls
        from service.routers.dispatch_messages.dispatch import claim_dispatch
        from service.routers.environments import claim_environment_control
        from service.routers.spawn_requests import claim_spawn_request
        from service.routers.terminal_controls import claim_terminal_controls

        handlers = {
            "dispatch": lambda: claim_dispatch(DispatchClaimRequest(agentId="a"), _StubRequest()),
            "dispatch-controls": lambda: claim_dispatch_controls(
                DispatchControlClaimRequest(agentId="a"), _StubRequest()),
            "terminal-controls": lambda: claim_terminal_controls(
                TerminalControlClaim(environmentId="e", bridgeId="b")),
            "environment-control": lambda: claim_environment_control(
                EnvironmentControlClaim(environmentId="e", bridgeId="b")),
            "spawn-request": lambda: claim_spawn_request(
                SpawnRequestClaim(environmentId="e", bridgeId="b"), _StubRequest()),
        }
        for name, handler in handlers.items():
            with self.subTest(route=name):
                captured = self._predicate_passed_by(handler)
                lock_result = captured["lock_result"]
                self.assertIsNotNone(lock_result, "no lock_result — contention would raise a 503")
                self.assertIs(captured["is_empty"](lock_result), True)


if __name__ == "__main__":
    unittest.main()
