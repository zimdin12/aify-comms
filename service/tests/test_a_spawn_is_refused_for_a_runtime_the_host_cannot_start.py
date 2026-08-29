r"""The host says a runtime is not launchable, and gives a reason. Both were read by nothing.

MEASURED 2026-08-30, with a positive control on the same instrument: `terminalRuntimes` appears in
three non-test places under `service/`; `unavailableReason` appears in ZERO, and `runtimes[].available`
has no reader either. The spawn gate asked only whether the runtime APPEARED in the environment's
list, so a spawn was accepted for a runtime the host had already refused -- and failed minutes later,
in the tier that runs launchers, as "the agent did not start".

THE REASON IS NOT A PLACEHOLDER. `runtimeLaunchAvailability` builds a paragraph naming the missing
wrapper, the env var that overrides it, the installer flag that fixes it, and a PATH diagnostic. It
was computed, transmitted, stored, and dropped.

EXPLICIT FALSE ONLY. `available` is absent on every environment row written before the field existed,
and "said nothing" is not "said no" -- the same distinction the heartbeat preservation rule turns on.
This mirrors `managed-environment-sync.mjs`, which has always filtered on `available !== false`.
"""

from __future__ import annotations

from service.api_core.runtime import _runtime_unlaunchable_reason
from service.tests._base import FastApiTestCase

REASON = (
    'Runtime "pi" is not launchable from this bridge because the required wrapper "pi-aify" is not '
    "available. Oh My Pi itself IS installed (\"omp\" resolves), so install the wrapper with install.sh"
)


def _environment(**runtime_row):
    return {"id": "windows:Host:default", "runtimes": [{"runtime": "pi", **runtime_row}]}


def test_an_explicit_false_produces_the_hosts_own_reason():
    reason = _runtime_unlaunchable_reason(
        _environment(available=False, unavailableReason=REASON), "pi")
    assert reason == REASON, "the diagnostic the host computed was replaced or dropped"


def test_a_false_with_no_reason_still_refuses_and_says_something():
    # A refusal with an empty string attached would surface as a blank message, which reads like a
    # bug in the service rather than a missing wrapper on the host.
    reason = _runtime_unlaunchable_reason(_environment(available=False, unavailableReason=""), "pi")
    assert reason and "pi" in reason


def test_an_ABSENT_available_is_not_a_refusal():
    """Every environment row written before this field existed is this shape. Refusing on a missing
    key would have made them all unspawnable, which is a far worse failure than the one being fixed."""
    assert _runtime_unlaunchable_reason(_environment(), "pi") is None
    assert _runtime_unlaunchable_reason(_environment(unavailableReason="stale text"), "pi") is None


def test_an_available_runtime_is_not_refused():
    assert _runtime_unlaunchable_reason(_environment(available=True, unavailableReason=""), "pi") is None
    # A stale reason left beside an available:true must not refuse: the boolean is the claim.
    assert _runtime_unlaunchable_reason(
        _environment(available=True, unavailableReason=REASON), "pi") is None


def test_a_runtime_the_environment_never_MENTIONED_is_a_different_refusal():
    """Absent from the list and present-but-refused are two facts with two messages. Merging them
    would report "not launchable" for a runtime the environment never claimed to have, sending an
    operator to install a wrapper on a host that was never the right one."""
    assert _runtime_unlaunchable_reason(_environment(available=False), "hermes") is None


def test_the_runtime_name_is_matched_through_the_shared_vocabulary():
    """`claude` and `claude-code` are one runtime. The contract already says so in both languages, and
    a refusal that missed the alias would be a silent pass for the exact spelling a host sends."""
    environment = {"id": "e", "runtimes": [{"runtime": "claude-code", "available": False,
                                            "unavailableReason": "no claude-aify"}]}
    assert _runtime_unlaunchable_reason(environment, "claude") == "no claude-aify"
    assert _runtime_unlaunchable_reason(environment, "claude-code") == "no claude-aify"


class TheRouteActuallyRefusesTests(FastApiTestCase):
    """The predicate above is pure and proven. This is the part that was missing last time: whether
    anything CALLS it. A helper with six green tests and no call site is a feature that cannot fire."""

    ENV = "windows:spawn-host:default"

    def _environment(self, runtimes):
        response = self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "kind": "windows", "os": "windows",
            "machineId": "win32:spawn-host", "runtimes": runtimes,
            "terminal": True, "pty": True,
            "terminalRuntimes": [r["runtime"] for r in runtimes if r.get("available") is not False],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _spawn(self, runtime):
        return self.client.post("/api/v1/spawn-requests", json={
            "agentId": "spawn-gate-probe", "environmentId": self.ENV,
            "runtime": runtime, "mode": "managed-warm",
        })

    def test_a_spawn_for_an_unlaunchable_runtime_is_refused_WITH_the_reason(self):
        self._environment([{"runtime": "pi", "available": False, "unavailableReason": REASON}])
        response = self._spawn("pi")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("pi-aify", response.text,
                      "the refusal did not carry the host's diagnostic, which is its whole value")

    def test_the_same_spawn_is_ACCEPTED_when_the_host_says_the_runtime_is_there(self):
        # The control. Without it this file passes just as well on a gate that refuses everything,
        # and "spawning is broken" would be indistinguishable from "the gate works".
        self._environment([{"runtime": "pi", "available": True, "unavailableReason": ""}])
        response = self._spawn("pi")
        self.assertIn(response.status_code, (200, 201), response.text)

    def test_a_row_with_no_available_KEY_still_spawns(self):
        """Every environment written before the field existed is this shape, and they must keep
        working. This is the case that would turn a fix into an outage."""
        self._environment([{"runtime": "pi"}])
        response = self._spawn("pi")
        self.assertIn(response.status_code, (200, 201), response.text)

    def test_a_runtime_the_environment_never_advertised_keeps_its_OWN_refusal(self):
        # 400 and 409 are different answers to different questions, and collapsing them would send an
        # operator to install a wrapper on a host that never claimed to have the runtime.
        self._environment([{"runtime": "pi", "available": True}])
        response = self._spawn("hermes")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("does not advertise", response.text)
