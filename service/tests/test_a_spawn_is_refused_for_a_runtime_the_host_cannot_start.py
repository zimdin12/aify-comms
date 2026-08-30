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

import unittest

from service.api_core.dispatch_start import _why_no_environment_can_start
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

    def _backdate_bridge_last_seen(self, stamp: str) -> None:
        """Age the stored anchor, simulating elapsed time rather than a caller writing it."""
        import asyncio, json
        from service.db import get_db

        async def _run():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT metadata FROM environments WHERE id = ?", (self.ENV,))).fetchone()
                metadata = json.loads(row["metadata"] or "{}") if row else {}
                metadata["bridgeLastSeen"] = stamp
                await db.execute(
                    "UPDATE environments SET metadata = ? WHERE id = ?",
                    (json.dumps(metadata), self.ENV))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _environment(self, runtimes):
        # BEATS AS A BRIDGE, which is what a host with a claimer actually looks like. These tests
        # are about the OTHER refusals -- an unlaunchable runtime, an unadvertised one -- and they
        # used to reach them through an environment with no bridge at all, which only worked while
        # a missing `bridgeLastSeen` was read as "assume one is there". Now that an absent stamp is
        # resolved against `bridge_instances` instead of assumed, an environment with no bridge is
        # correctly refused before those checks are reached, so the fixture has to describe a host
        # that could actually run something.
        response = self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "kind": "windows", "os": "windows",
            "machineId": "win32:spawn-host", "runtimes": runtimes,
            "terminal": True, "pty": True,
            "bridgeId": "bridge-spawn-host",
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
        # The phrase as well as the code: `test_every_refusal_is_exercised.py` requires each refusal's
        # TEXT to be asserted somewhere, because a status alone does not say the operator was told
        # anything useful -- and the text is the entire product of this particular refusal.
        self.assertIn(" cannot launch runtime ", response.text)
        self.assertIn("pi-aify", response.text,
                      "the refusal did not carry the host's diagnostic, which is its whole value")

    def _legacy_environment_with_a_live_bridge(self, bridge_id="bridge-legacy"):
        """A row from BEFORE `bridgeLastSeen` existed, whose bridge is genuinely alive.

        The only shape that exercises the authority lookup: no stamp to classify, and a real
        `bridge_instances` row to resolve the absence against. Every other fixture here beats WITH a
        bridgeId, which stamps the row fresh and never consults the authority at all -- a mutation
        removing the lookup left all of them green.
        """
        import asyncio, json
        from service.clock import now as _now
        from service.db import get_db

        self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "kind": "windows", "os": "windows", "machineId": "win32:spawn-host",
            "runtimes": [{"runtime": "pi", "available": True}],
            "terminal": True, "pty": True, "terminalRuntimes": ["pi"],
        })

        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    "INSERT INTO bridge_instances (id, agent_id, last_seen, registered_at) "
                    "VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen",
                    (bridge_id, "legacy-owner", _now(), _now()))
                # The row names its bridge, exactly as a registration would; the METADATA stamp is
                # what a legacy row lacks.
                await db.execute(
                    "UPDATE environments SET bridge_id = ?, metadata = ? WHERE id = ?",
                    (bridge_id, json.dumps({}), self.ENV))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def test_a_LEGACY_row_with_a_LIVE_bridge_is_accepted_on_the_authority(self):
        """The whole reason absence is resolved rather than assumed. Refusing every unstamped row
        would refuse every environment registered before the field existed, on a host that is
        perfectly capable of claiming the spawn."""
        self._legacy_environment_with_a_live_bridge()
        response = self._spawn("pi")
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_LEGACY_row_whose_bridge_is_GONE_is_refused(self):
        """The other direction, and the one the fail-open got wrong: no stamp and no live bridge is
        no claimer, however fresh aify-env keeps `last_seen`."""
        import asyncio
        from service.db import get_db

        self._legacy_environment_with_a_live_bridge(bridge_id="bridge-departed")

        async def _kill():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ? WHERE id = ?",
                    ("2020-01-01T00:00:00Z", "bridge-departed"))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_kill())
        response = self._spawn("pi")
        self.assertEqual(response.status_code, 409, response.text)

    def test_a_LEGACY_row_whose_bridge_is_WILDLY_FUTURE_DATED_is_refused(self):
        """A stamp hours ahead satisfies `> now - stale` for ever, so one bad write would let a dead
        bridge authorize spawns permanently."""
        import asyncio
        from service.db import get_db

        self._legacy_environment_with_a_live_bridge(bridge_id="bridge-future")

        async def _skew():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ? WHERE id = ?",
                    ("2099-01-01T00:00:00Z", "bridge-future"))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_skew())
        self.assertEqual(self._spawn("pi").status_code, 409, "a future-dated bridge authorized a spawn")

    def test_a_LEGACY_row_whose_bridge_was_SUPERSEDED_is_refused(self):
        """A superseded bridge keeps heartbeating -- supersession is a server-side fact it is never
        told about -- so freshness alone would accept a replaced owner."""
        import asyncio
        from service.db import get_db

        self._legacy_environment_with_a_live_bridge(bridge_id="bridge-replaced")

        async def _supersede():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE bridge_instances SET superseded_by = ? WHERE id = ?",
                    ("bridge-newer", "bridge-replaced"))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_supersede())
        response = self._spawn("pi")
        self.assertEqual(response.status_code, 409, response.text)

    def test_a_spawn_with_no_live_BRIDGE_is_refused_rather_than_queued_for_ever(self):
        """FOUND ON THE DEPLOYED SYSTEM, not by reading. `comms_spawn` was accepted, the request sat
        `queued`, and nothing claimed it -- for as long as anyone cared to wait, with no error. It went
        `running` the instant an environment bridge was started.

        aify-env heartbeats this row to describe the host, which keeps `last_seen` fresh and the
        status `online`; the thing that CLAIMS a spawn is the bridge. Before the cutover only a bridge
        wrote `last_seen`, so `online` implied a claimer. Now it does not, and the gate has to ask the
        question it actually depends on."""
        # ESTABLISHED THE WAY PRODUCTION ESTABLISHES IT, which is not how this test first did it.
        #
        # It used to POST `metadata: {"bridgeLastSeen": stale}` on a beat carrying NO `bridgeId`.
        # That state cannot occur: the handler writes `bridgeLastSeen` only for a beat that carries
        # a bridgeId, and since 2026-08-30 it STRIPS the whole `bridge*` namespace from any beat
        # that does not -- because a host advertiser writing bridge authority is a forgery, not a
        # fixture. The test was constructing exactly the shape the guard now refuses, so it started
        # passing its own setup through a hole instead of through the product.
        #
        # A real bridge beats (which stamps the anchor to NOW), and then time passes. Backdating the
        # stored value is how "time passed" is expressed in a test; it is not how a caller could
        # ever set it.
        stale = "2026-08-30T00:00:00Z"
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "kind": "windows", "os": "windows", "machineId": "win32:spawn-host",
            "runtimes": [{"runtime": "pi", "available": True}],
            "bridgeId": "bridge-that-has-since-gone",
        })
        self._backdate_bridge_last_seen(stale)
        response = self._spawn("pi")
        self.assertEqual(response.status_code, 409, response.text)
        # The phrase in full, because `test_every_refusal_is_exercised.py` matches a refusal's whole
        # message against the test tree -- a fragment leaves it counted as untested.
        self.assertIn('" is described by aify-env but has no live environment bridge, and only a '
                      'bridge claims a spawn. Run `aify-comms` on that host, then retry.', response.text)
        self.assertIn("aify-comms", response.text, "the refusal must say how to start one")

    def test_a_row_with_no_bridgeLastSeen_at_all_still_spawns(self):
        """Every environment registered before that field existed is this shape. Reading absent as
        'no bridge' would refuse every spawn on every host until each one's bridge restarted -- a
        far worse failure than the one being fixed."""
        self._environment([{"runtime": "pi", "available": True}])
        self.assertIn(self._spawn("pi").status_code, (200, 201))

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


class TheColdStartRefusalCarriesTheReasonTests(unittest.TestCase):
    """The path two real agents are on.

    `graph-tester-pi` and `comms-senior-dev-pi` are managed, offline and bound to no environment, so a
    send to either falls back to "pick any online environment that advertises the runtime". That
    fallback used to pick the Windows environment -- which claimed pi was available while `pi-aify` is
    not installed -- and the cold start then failed somewhere else entirely. The environment is now
    correctly skipped, which leaves the operator with "could not be resolved": true, and useless.

    The host already explained itself. This is the moment somebody wants to read it.
    """

    @staticmethod
    def _environment(runtimes, status="online", env_id="windows:cold-host:default"):
        return {"id": env_id, "status": status, "runtimes": runtimes}

    def test_the_hosts_diagnostic_reaches_the_refusal(self):
        reason = _why_no_environment_can_start(
            [self._environment([{"runtime": "pi", "available": False, "unavailableReason": REASON}])],
            "pi")
        self.assertIn("pi-aify", reason, "the wrapper the operator must install was not named")
        self.assertIn("windows:cold-host:default", reason, "the refusal did not say WHICH host said it")

    def test_nothing_is_invented_when_no_host_explained_itself(self):
        """A different case with a different answer: no environment advertises this runtime AT ALL.
        Filler here would overwrite the caller's own correct wording with a guess."""
        self.assertEqual(_why_no_environment_can_start(
            [self._environment([{"runtime": "pi", "available": False, "unavailableReason": REASON}])],
            "hermes"), "")
        self.assertEqual(_why_no_environment_can_start([], "pi"), "")

    def test_an_available_runtime_produces_no_refusal_text(self):
        self.assertEqual(_why_no_environment_can_start(
            [self._environment([{"runtime": "pi", "available": True, "unavailableReason": ""}])],
            "pi"), "")

    def test_an_OFFLINE_hosts_opinion_is_not_quoted(self):
        """A host that is not running has a stale reading of its own wrappers, and quoting it would
        send an operator to fix a machine whose only problem is that it is off."""
        self.assertEqual(_why_no_environment_can_start(
            [self._environment([{"runtime": "pi", "available": False, "unavailableReason": REASON}],
                               status="offline")],
            "pi"), "")

    def test_the_FIRST_online_host_that_explains_itself_is_the_one_quoted(self):
        # Rows arrive most-recently-seen first, and one sentence an operator can act on beats three
        # they have to compare.
        reason = _why_no_environment_can_start([
            self._environment([{"runtime": "pi", "available": True}], env_id="a"),
            self._environment([{"runtime": "pi", "available": False, "unavailableReason": "first reason"}],
                              env_id="b"),
            self._environment([{"runtime": "pi", "available": False, "unavailableReason": "second reason"}],
                              env_id="c"),
        ], "pi")
        self.assertIn("first reason", reason)
        self.assertNotIn("second reason", reason)


class AnAbsentStampIsResolvedAgainstTheAuthorityTests(unittest.TestCase):
    """Q1. `bridgeLastSeen` missing is a QUESTION, not an answer, and the first version answered yes.

    THE FAIL-OPEN. `environment_has_live_bridge` read ABSENT as "unknown, and unknown means yes".
    Every environment registered before that field existed has no stamp, so each was treated as
    having a live bridge FOR EVER -- and an aify-env advertisement keeps such a row `online`
    indefinitely with nothing able to claim a spawn. That is the queued-for-ever strand this gate
    was added to prevent, reintroduced through the gate itself. Unparseable was read as absent too,
    so invalid data became authorization.

    THE FIX IS EVIDENCE, NOT A GRACE PERIOD. `bridge_instances` is the authority on whether a bridge
    is alive, and it is the same table the turn lease consults. Asking it means there is no window
    to expire and no doctor row to add for one.
    """

    def _env(self, **metadata):
        return {"id": "windows:h:default", "bridgeId": "bridge-1", "metadata": dict(metadata)}

    def test_a_FRESH_stamp_is_live(self):
        from service.clock import now as _now
        from service.env_status import environment_has_live_bridge
        self.assertTrue(environment_has_live_bridge(self._env(bridgeLastSeen=_now())))

    def test_a_STALE_stamp_is_NOT_live_however_the_authority_answers(self):
        """A bridge that stopped beating is gone. The authority is not consulted for a stamped row,
        so a stale stamp cannot be overridden by a stray row."""
        from service.env_status import environment_has_live_bridge
        env = self._env(bridgeLastSeen="2020-01-01T00:00:00Z")
        self.assertFalse(environment_has_live_bridge(env))
        self.assertFalse(environment_has_live_bridge(env, bridge_rows_say_live=True))

    def test_an_INVALID_stamp_is_NEVER_live(self):
        """Invalid data must not become authorization. The previous reading -- 'unparseable is the
        same as absent' -- meant a corrupt write authorized a spawn."""
        from service.env_status import environment_has_live_bridge
        for junk in ("not-a-timestamp", "2026-08-30T", "", "   "):
            with self.subTest(stamp=junk):
                env = self._env(bridgeLastSeen=junk)
                # blank/whitespace are ABSENT rather than INVALID, and absent without an authority
                # answer is also not live -- both directions are covered here.
                self.assertFalse(environment_has_live_bridge(env))

    def test_a_FAR_FUTURE_stamp_is_invalid_rather_than_permanently_fresh(self):
        """`age <= window` is satisfied for ever by a stamp hours ahead."""
        from service.env_status import environment_has_live_bridge
        self.assertFalse(environment_has_live_bridge(self._env(bridgeLastSeen="2099-01-01T00:00:00Z")))

    def test_ORDINARY_CLOCK_SKEW_is_still_fresh(self):
        """The control against reproducing doctor's false red: it once called every environment dead
        because the container clock ran 4.1s ahead of the host."""
        from datetime import datetime, timedelta, timezone
        from service.env_status import environment_has_live_bridge
        soon = (datetime.now(timezone.utc) + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertTrue(environment_has_live_bridge(self._env(bridgeLastSeen=soon)))

    def test_an_ABSENT_stamp_follows_the_AUTHORITY_in_both_directions(self):
        from service.env_status import environment_has_live_bridge
        env = self._env()
        self.assertTrue(environment_has_live_bridge(env, bridge_rows_say_live=True))
        self.assertFalse(environment_has_live_bridge(env, bridge_rows_say_live=False))

    def test_NOT_ASKING_is_not_evidence(self):
        """THE CORRECTION ITSELF. `None` means the caller did not consult the authority, and the old
        code returned True there -- making 'we never checked' indistinguishable from 'yes'."""
        from service.env_status import environment_has_live_bridge
        self.assertFalse(environment_has_live_bridge(self._env(), bridge_rows_say_live=None))

    def test_the_four_states_are_distinguishable(self):
        """The boolean collapsed them and got the collapse backwards. Each is named now, so a caller
        can tell 'no bridge' from 'corrupt data' and report the difference."""
        from service.clock import now as _now
        from service.env_status import (
            BRIDGE_STAMP_ABSENT, BRIDGE_STAMP_FRESH, BRIDGE_STAMP_INVALID, BRIDGE_STAMP_STALE,
            bridge_stamp_state,
        )
        self.assertEqual(bridge_stamp_state(self._env(bridgeLastSeen=_now())), BRIDGE_STAMP_FRESH)
        self.assertEqual(bridge_stamp_state(self._env(bridgeLastSeen="2020-01-01T00:00:00Z")), BRIDGE_STAMP_STALE)
        self.assertEqual(bridge_stamp_state(self._env()), BRIDGE_STAMP_ABSENT)
        self.assertEqual(bridge_stamp_state(self._env(bridgeLastSeen="nonsense")), BRIDGE_STAMP_INVALID)
