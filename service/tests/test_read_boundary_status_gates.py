"""The two read-boundary status corrections, which ran on every roster poll and had no tests.

`_enforce_live_worker_gate` and `_enforce_env_reachable_gate` are the last thing that touches an
agent's status before it reaches the dashboard and `comms_agent_info`. Both exist because the cached
status can outlive the thing it describes:

  * live-worker: "the wrapper PTY exits but a parallel heartbeat keeps the agent alive, so
    `refresh_after` stays in the future and `_refresh_expired_agent_live_states` never
    re-validates. Cached `status='online'` then persists indefinitely." Observed on
    graph-senior-dev, 2026-05-25.
  * env-reachable: "a cached LIVE/available status must not outlive its owning ENVIRONMENT" —
    env death is computed-on-read from `last_seen` age, so there is no transition event to
    invalidate dependents.

Each is a correction that makes the difference between an operator seeing a worker that exists and
one that does not, and neither had a test.

TWO THINGS THE TESTS MEASURE RATHER THAN ASSUME.

DEAD SPELLINGS. The live-worker gate opens on `status in {"online", "ready"}` and the env gate on
`{"online", "ready", "idle", "working", "available"}`. `ready` and `idle` are NOT in
`status_engine.VALID_STATUSES` — nothing can produce them — so each gate is narrower than it reads.
That is harmless in itself and is pinned here because it is what made the next point hard to see.

THE SIBLINGS DISAGREE ABOUT `working`. Strip the dead spellings and the env gate covers
{online, working, available} while the live-worker gate covers {online} alone. So a managed
wrapper-backed agent whose worker is gone is corrected within one poll at `online`, and NOT corrected
at `working` — where `turn_busy` holds it for up to `TURN_BUSY_BACKSTOP_SECONDS` (30 minutes). Both
gates ask "does the thing behind this status still exist"; only one of them asks it about a working
agent. Whether that asymmetry is deliberate is not settled here — the tests pin today's answer, name
the difference, and make a change to either side visible.
"""

from __future__ import annotations

import asyncio
import unittest

from service.api_core.registration_gates import _enforce_env_reachable_gate, _enforce_live_worker_gate
from service.status_engine import VALID_STATUSES, is_live_agent_status

#: Runtimes whose managed worker is a wrapper PTY. Read from the real predicate rather than retyped,
#: so this file cannot disagree with the gate about which runtimes it even applies to.
from service.api_core.capabilities import _managed_via_wrapper_for_runtime

LIVE_WORKER_GATE_OPENS_ON = {"online", "ready"}
#: DERIVED SINCE 2026-09-21, and the reason it is: the env gate used to carry a hand-typed set of
#: five, and `shell` -- added to the vocabulary on 2026-09-17 -- was never added to it. A managed
#: agent reading `shell` therefore kept that status after its machine went dark, and
#: `send_preflight` accepted live sends to it. External review, finding 3.
ENV_REACHABLE_GATE_OPENS_ON = {s for s in VALID_STATUSES if is_live_agent_status(s)}


class _Row(dict):
    """Minimal stand-in for an aiosqlite Row: subscriptable and `.keys()`-able."""


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Db:
    """A database with exactly one answer: how many live terminal rows this agent has.

    Deliberately not a real connection. The gate's only DB question is
    `_has_live_terminal_session`, whose whole body is one COUNT query, and a fake makes the two
    answers — worker present, worker gone — the only variable in the test.
    """

    def __init__(self, live_terminals: int):
        self.live_terminals = live_terminals
        self.queries: list[str] = []

    async def execute(self, sql, params=()):
        self.queries.append(sql)
        return _Cursor(_Row(cnt=self.live_terminals))


def _run(coro):
    # `asyncio.run`, not a hand-built loop: `get_event_loop_policy()` is deprecated and slated for
    # removal in 3.16, and a test that emits a DeprecationWarning trains the suite's readers to skip
    # its warning output — which is where a real one would appear.
    return asyncio.run(coro)


def _managed_payload(**overrides):
    payload = {
        "id": "sc-coder",
        "status": "online",
        "statusRaw": "online",
        "sessionMode": "managed",
        "runtime": "codex",
    }
    payload.update(overrides)
    return payload


class LiveWorkerGateTests(unittest.TestCase):
    def test_a_managed_wrapper_agent_with_no_terminal_is_downgraded(self):
        """THE ONE IT WAS WRITTEN FOR: cached `online` for a worker that has exited."""
        payload = _run(_enforce_live_worker_gate(_managed_payload(), _Db(0), {}, "sc-coder"))
        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["statusRaw"], "available", "statusRaw must move with status")
        self.assertIn("no-live-worker", payload["statusNote"])

    def test_a_live_terminal_leaves_the_status_alone(self):
        payload = _run(_enforce_live_worker_gate(_managed_payload(), _Db(1), {}, "sc-coder"))
        self.assertEqual(payload["status"], "online")
        self.assertNotIn("statusNote", payload, "an untouched payload must not gain a note")

    def test_the_gate_asks_the_database_only_when_it_could_act(self):
        """Anti-vacuity AND the reason this runs on the hot path: the DB read is last, after three
        cheap rejections. A gate that queried first would be a per-agent read on every roster poll —
        the shape that starved SQLite's single writer and 503'd the fleet in 2026-06-18."""
        for payload in (
            _managed_payload(status="offline"),
            _managed_payload(sessionMode="resident"),
            _managed_payload(runtime="pi"),
        ):
            db = _Db(0)
            result = _run(_enforce_live_worker_gate(payload, db, {}, "sc-coder"))
            self.assertEqual(result["status"], payload["status"])
            self.assertEqual(db.queries, [], f"{payload} must be rejected before any query")
        db = _Db(0)
        _run(_enforce_live_worker_gate(_managed_payload(), db, {}, "sc-coder"))
        self.assertEqual(len(db.queries), 1, "…and the eligible case DOES query, or this proves nothing")

    def test_only_wrapper_backed_runtimes_are_gated(self):
        """`pi` and `claude-code` are managed but NOT wrapper-backed — their worker is not a PTY, so
        an absent `terminal_sessions` row says nothing about them. Read from the real predicate."""
        for runtime in ("claude-code", "codex", "hermes", "pi", "opencode"):
            with self.subTest(runtime=runtime):
                wrapper_backed = _managed_via_wrapper_for_runtime({}, runtime)
                result = _run(_enforce_live_worker_gate(
                    _managed_payload(runtime=runtime), _Db(0), {}, "sc-coder"))
                expected = "available" if wrapper_backed else "online"
                self.assertEqual(result["status"], expected)
        self.assertTrue(
            _managed_via_wrapper_for_runtime({}, "codex"),
            "if NO runtime were wrapper-backed the loop above would assert nothing",
        )

    def test_a_virtual_terminal_must_not_count_as_a_live_worker(self):
        """Pinned at the SQL, because it is the one exclusion the gate cannot express in Python.

        Pre-Plan-4 `vterm_*` rows persist in operator DBs with `status='running'` and no cleanup
        path; sc-coder and sc-architect kept reading `online` after the Plan 5 deploy because those
        rows hid the dead worker from this gate.
        """
        db = _Db(0)
        _run(_enforce_live_worker_gate(_managed_payload(), db, {}, "sc-coder"))
        self.assertIn("vterm_", db.queries[0], "the vterm exclusion must still be in the query")
        self.assertIn("NOT LIKE", db.queries[0].upper())


class GateStatusVocabularyTests(unittest.TestCase):
    def test_ready_is_a_spelling_no_status_engine_can_produce(self):
        """Measured, not assumed: the live-worker gate is narrower than its literal set reads."""
        self.assertEqual(sorted(LIVE_WORKER_GATE_OPENS_ON - set(VALID_STATUSES)), ["ready"])
        self.assertEqual(sorted(LIVE_WORKER_GATE_OPENS_ON & set(VALID_STATUSES)), ["online"])

    def test_the_env_gate_opens_on_every_status_that_claims_the_agent_is_reachable(self):
        """Including the ones added after it was written, which is the whole point of deriving it.

        `shell` is the one that was missing. The others are pinned so that a status REMOVED from the
        live set -- which would silently narrow this hot-path correction -- shows up here.
        """
        self.assertIn("shell", ENV_REACHABLE_GATE_OPENS_ON)
        self.assertEqual(
            sorted(ENV_REACHABLE_GATE_OPENS_ON),
            ["available", "blocked", "online", "shell", "starting", "working"],
        )
        # And the non-live ones stay out, or the gate would fire on an agent already offline.
        self.assertEqual(ENV_REACHABLE_GATE_OPENS_ON & {"offline", "stopped", "misconfigured"}, set())

    def test_the_live_worker_gate_literal_still_matches_this_file(self):
        """That set is still an inline literal, so there is nothing to import and this reads source.

        Its sibling no longer needs this: the env gate now asks `is_live_agent_status`, so the set
        above IS the gate's own answer rather than a copy of it.
        """
        import pathlib
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "api_core" / "registration_gates.py").read_text(encoding="utf-8")
        self.assertIn('if payload.get("status") not in {"online", "ready"}:', source)


class _EnvDb:
    """Answers the environments lookup, and records whether it was asked at all."""

    def __init__(self, last_seen: str):
        self.last_seen = last_seen
        self.queries: list[str] = []

    async def execute(self, sql, params=()):
        self.queries.append(sql)
        return _Cursor(_Row(id="env-1", status="online", last_seen=self.last_seen))


def _long_ago():
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=400)).isoformat().replace("+00:00", "Z")


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EnvReachableGateTests(unittest.TestCase):
    """The behaviour the vocabulary above is only the doorway to. It had no test before 2026-09-21."""

    def _judge(self, status, last_seen):
        payload = _managed_payload(status=status, statusRaw=status, runtimeState={"environmentId": "env-1"})
        db = _EnvDb(last_seen)
        result = _run(_enforce_env_reachable_gate(payload, db, {}, "sc-coder"))
        return result, db

    def test_a_shell_agent_whose_machine_went_dark_is_corrected(self):
        """THE ONE IT WAS WRITTEN FOR. Before the derivation, this status walked straight through."""
        result, db = self._judge("shell", _long_ago())
        self.assertEqual(result["status"], "offline")
        self.assertEqual(result["statusRaw"], "offline", "statusRaw must move with status")
        self.assertIn("is offline", result["statusNote"])
        self.assertTrue(db.queries, "it must have asked about the environment")

    def test_online_is_corrected_the_same_way(self):
        """The positive control: the status that always worked, in the same run."""
        self.assertEqual(self._judge("online", _long_ago())[0]["status"], "offline")

    def test_a_live_environment_leaves_shell_alone(self):
        """The negative control: the correction must be the ENVIRONMENT's doing, not the status's."""
        result, _ = self._judge("shell", _now())
        self.assertEqual(result["status"], "shell")
        self.assertNotIn("statusNote", result, "an untouched payload must not gain a note")

    def test_an_already_offline_agent_is_rejected_before_any_query(self):
        """Anti-vacuity, and the hot-path guarantee: the cheap rejections come before the DB read."""
        result, db = self._judge("offline", _long_ago())
        self.assertEqual(result["status"], "offline")
        self.assertEqual(db.queries, [], "a non-live status must not cost a query per roster poll")

    def test_a_resident_agent_is_not_gated(self):
        payload = _managed_payload(status="shell", sessionMode="resident",
                                   runtimeState={"environmentId": "env-1"})
        db = _EnvDb(_long_ago())
        result = _run(_enforce_env_reachable_gate(payload, db, {}, "sc-coder"))
        self.assertEqual(result["status"], "shell")
        self.assertEqual(db.queries, [])
