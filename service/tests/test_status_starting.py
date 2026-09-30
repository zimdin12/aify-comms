"""`starting` — the boot window, and why it must be able to EXPIRE.

Operator-requested 2026-08-11, from a real false alarm. `ef-manager` was restarted; for 28 seconds
the new worker had not appeared, so the agent read `available`. That is honest — no live worker,
still deliverable — but it is indistinguishable from a genuinely idle agent, and earlier THAT MORNING
an identical-looking `available` was a restart that had silently produced no worker at all. The
operator could not tell the two apart, and asked twice.

THE DANGER IN THE OBVIOUS IMPLEMENTATION, which is why this file exists. "Spawn row says running and
there is no worker → starting" would have rendered the morning's genuinely-broken ef-manager as a
hopeful `starting` FOREVER. That is strictly worse than the `available` it replaced: it converts a
visible problem into a reassuring animation. The same false-green class as a doctor check that
cannot fail.

So the state is bounded at the source: `spawn_starting` means "starting AND still inside the startup
window". Past the window the agent falls back to exactly what it reported before this state existed.
The engine stays pure — the clock lives in the gatherer — so these tests pin the RANKING, and
test_api_v2_regressions pins the window.
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from service.status_engine import VALID_STATUSES, StatusInputs, derive
from service.api_core.managed_env import (
    SPAWN_STARTING_WINDOW_SECONDS,
    _managed_spawn_is_starting,
)


def managed(**over):
    base = dict(
        mode="managed", alive=False, in_turn=False, awaiting_input=False,
        worker_present=False, env_reachable=True, disabled=False,
        bridge_stale=False, has_live_session=False,
    )
    base.update(over)
    return StatusInputs(**base)


class StartingStateTests(unittest.TestCase):
    def test_a_booting_spawn_reads_starting_instead_of_available(self):
        self.assertEqual(derive(managed(spawn_starting=True)), "starting")

    def test_without_the_flag_the_window_reads_exactly_as_before(self):
        """The regression guard for every agent that is NOT starting: unchanged behaviour."""
        self.assertEqual(derive(managed()), "available")

    # ── ranking: the whole safety of a new state is where it sits ────────────────────
    def test_a_live_worker_beats_starting(self):
        """A spawn row can lag reality. If a worker is present the agent is online, full stop."""
        self.assertEqual(derive(managed(alive=True, worker_present=True, spawn_starting=True)), "online")

    def test_an_in_flight_turn_beats_starting(self):
        self.assertEqual(
            derive(managed(in_turn=True, worker_present=True, alive=True, spawn_starting=True)),
            "working",
        )

    def test_an_unreachable_environment_beats_starting(self):
        """Nothing is starting on a bridge that is gone — claiming otherwise invites the operator
        to wait for something that will never arrive."""
        self.assertEqual(derive(managed(env_reachable=False, spawn_starting=True)), "offline")

    def test_stopped_beats_starting(self):
        self.assertEqual(derive(managed(disabled=True, spawn_starting=True)), "stopped")

    def test_misconfigured_beats_starting(self):
        """An agent that can NEVER start is not starting. Reporting a hopeful transient over a
        config defect is the false promise `misconfigured` was added to remove."""
        self.assertEqual(
            derive(managed(spawn_starting=True, config_defect="runtime 'bogus' cannot be launched")),
            "misconfigured",
        )

    def test_console_booting_beats_starting(self):
        """A console that already exists is better evidence than a spawn row, and `online` is what
        that phase has always displayed — this must not silently downgrade it."""
        self.assertEqual(derive(managed(console_booting=True, spawn_starting=True)), "online")

    def test_a_resident_is_never_starting(self):
        """`starting` describes a managed spawn coming up. A resident is operator-launched and has
        no spawn to be inside the window of."""
        resident = StatusInputs(
            mode="resident", alive=False, in_turn=False, awaiting_input=False,
            worker_present=False, env_reachable=True, disabled=False,
            bridge_stale=True, has_live_session=False, spawn_starting=True,
        )
        self.assertEqual(derive(resident), "offline")

    def test_every_derivable_status_is_declared(self):
        """The vocabulary and the function must not drift — a state the engine can return but the
        list does not name renders as a grey `unknown` chip in the dashboard and filters into
        nothing (see test_status_vocabulary_binding)."""
        seen = set()
        for defect in ("", "broken"):
            for flags in range(64):
                seen.add(derive(managed(
                    alive=bool(flags & 1), in_turn=bool(flags & 2), awaiting_input=bool(flags & 4),
                    worker_present=bool(flags & 8), env_reachable=bool(flags & 16),
                    spawn_starting=bool(flags & 32), config_defect=defect,
                )))
        self.assertTrue(seen <= set(VALID_STATUSES), f"underived: {seen - set(VALID_STATUSES)}")
        self.assertIn("starting", seen, "the new state must be reachable from real input combinations")



class EnsureManagedPtyRowAccessTests(unittest.TestCase):
    """`_active_terminal_for_agent` returns a sqlite3.Row, and Row has no `.get()`.

    THE BUG THIS PINS, which was mine and shipped: the restart fix compared sessions with
    `active.get("session_id")`. On a Row that raises AttributeError, and the caller's
    `except Exception: pass` swallowed it — so the eager PTY silently did nothing and the agent came
    back from a restart with no worker and no log line.

    It only fired when an active terminal EXISTED and had not yet flipped to `stopped`, which is a
    race: my first live restart test passed, the second hung. A test that only exercises the
    no-active-terminal path cannot see it, so this one constructs the Row.
    """

    def test_a_real_sqlite_row_has_no_get(self):
        """The property the production code must respect. If sqlite3.Row ever grows `.get()` this
        test tells us the constraint relaxed, rather than leaving a comment that quietly stops
        being true."""
        import sqlite3

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT 'sess_1' AS session_id").fetchone()
        self.assertFalse(hasattr(row, "get"), "sqlite3.Row has no .get() — index it instead")
        self.assertEqual(row["session_id"], "sess_1")

    def test_the_production_comparison_uses_indexing(self):
        """SOURCE-SCANNING because the callable path needs a live DB and a bridge; the defect was
        purely the accessor, and this is the cheapest honest guard against it returning.

        It used to be source-PINNED, which is a different and worse thing — see below. The word is
        corrected here rather than left as an inaccuracy readers have to reconcile with the paragraph
        under it, because a docstring that describes the previous implementation is how ownership prose
        drifts out of true in the first place.

        v0.5.4: this probe FOUND its subject failing rather than passing vacuously — it named
        `control_plane.py` and raised ValueError when `_ensure_managed_pty_for_dispatch` moved to
        `api_core/dispatch_start.py`. That is the correct degradation for a presence assertion and it
        is why the absence-assertion class was the dangerous one. Still, failing on every move is a
        cost with no benefit, so it now SEARCHES the service tree and requires exactly one definition
        — a second copy would let this pick one at random and guard the wrong body.
        """
        from pathlib import Path

        from service.tests._source import code_only

        needle = "async def _ensure_managed_pty_for_dispatch"
        root = Path(__file__).resolve().parents[1]
        owners = [
            path for path in sorted(root.rglob("*.py"))
            if "__pycache__" not in path.parts and "tests" not in path.parts
            and needle in code_only(path.read_text(encoding="utf-8", errors="replace"))
        ]
        self.assertEqual(
            len(owners), 1,
            f"{needle} must have exactly one definition; found {[p.name for p in owners]}",
        )
        src = code_only(owners[0].read_text(encoding="utf-8", errors="replace"))
        at = src.index(needle)
        body = src[at : at + 2500]
        self.assertIn('active["session_id"]', body)
        self.assertNotIn('active.get(', body, "Row does not support .get() — this raises")

    def test_the_eager_pty_failure_is_logged_not_swallowed(self):
        """A best-effort step that fails invisibly is indistinguishable from one with nothing to do.
        This one hid an AttributeError through two live restarts."""
        from pathlib import Path

        from service.tests._source import code_only

        # FINDS THE CODE RATHER THAN NAMING ITS FILE. This comment used to say "this probe follows
        # the code, not the file" and then named a file — which is how it went red on a v0.5.4
        # relocation that changed nothing about the logging. The warning is what matters, wherever
        # it lives, so the product tree is searched for it.
        service_dir = Path(__file__).resolve().parents[1]
        holders = [
            path for path in service_dir.rglob("*.py")
            if "tests" not in path.parts
            and "eager managed PTY for" in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(1, len(holders), f"expected exactly one writer of this warning: {holders}")
        src = code_only(holders[0].read_text(encoding="utf-8", errors="replace"))
        at = src.index("eager managed PTY for")
        window = src[max(0, at - 400) : at + 200]
        self.assertIn("logger.warning", window)
        self.assertIn("except Exception as exc", window)



def _utc_stamp(age_seconds=0):
    """The service clock's shape: whole-second UTC, which is why same-second cases exist."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_seconds))


class SpawnStartingWindowTests(unittest.IsolatedAsyncioTestCase):
    """The BOUND, tested at the gatherer where the clock actually lives.

    `test_status_starting.py`'s header claimed "test_api_v2_regressions pins the window". Review
    checked and it did not — I had described a test that did not exist, which is the same reporting
    fault as calling something verified off a lucky run. This is that test.

    It also pins the constant SHARING that review found: the display window and the in-flight
    suppressor were 180s and 300s independently, so for two minutes the status said `available`
    (which promises a cold-start on the next send) while the dispatcher still refused to start a
    second worker.
    """

    async def asyncSetUp(self):
        import aiosqlite

        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = __import__("sqlite3").Row
        await self.db.execute(
            "CREATE TABLE spawn_requests (id TEXT, agent_id TEXT, status TEXT, "
            "started_at TEXT, updated_at TEXT, created_at TEXT, session_id TEXT DEFAULT 'sess-1', "
            "finished_at TEXT DEFAULT '')"
        )
        await self.db.execute(
            "CREATE TABLE terminal_sessions (id TEXT, session_id TEXT, status TEXT, created_at TEXT)"
        )

    async def asyncTearDown(self):
        await self.db.close()

    async def _add(self, *, status="running", age_seconds=0, started=True, session="sess-1", stamp=None):
        stamp = stamp or _utc_stamp(age_seconds)
        await self.db.execute(
            "INSERT INTO spawn_requests (id, agent_id, status, started_at, updated_at, created_at, session_id) "
            "VALUES (?,?,?,?,?,?,?)",
            ("s1", "a1", status, stamp if started else "", stamp, stamp, session),
        )
        await self.db.commit()

    async def _starting(self):
        return await _managed_spawn_is_starting(self.db, "a1")

    async def test_just_inside_the_window(self):
        await self._add(age_seconds=SPAWN_STARTING_WINDOW_SECONDS - 30)
        self.assertTrue(await self._starting())

    async def test_past_the_window_it_stops_claiming_to_be_starting(self):
        """The safety property: a spawn that never produces a worker must stop looking hopeful and
        fall back to exactly what it reported before this state existed."""

        await self._add(age_seconds=SPAWN_STARTING_WINDOW_SECONDS + 60)
        self.assertFalse(await self._starting())

    async def test_a_queued_unclaimed_spawn_is_not_starting(self):
        """Nothing is starting yet — no bridge has claimed it. Saying `starting` would promise
        motion that has not begun."""
        await self._add(status="queued", age_seconds=5, started=False)
        self.assertFalse(await self._starting())

    async def test_a_running_row_with_no_timestamp_is_not_starting(self):
        """An age we cannot measure must not buy an unbounded `starting`."""
        await self.db.execute(
            "INSERT INTO spawn_requests (id, agent_id, status, started_at, updated_at, created_at) "
            "VALUES ('s2','a1','running','','','')"
        )
        await self.db.commit()
        self.assertFalse(await self._starting())

    async def _terminal(self, status, *, age_seconds=0, term_id="t1", session="sess-1", stamp=None):
        stamp = stamp or _utc_stamp(age_seconds)
        await self.db.execute("INSERT INTO terminal_sessions VALUES (?,?,?,?)", (term_id, session, status, stamp))
        await self.db.commit()

    async def _booting(self):
        from service.api_core.managed_env import _has_pending_or_booting_spawn_request

        return await _has_pending_or_booting_spawn_request(self.db, "a1")

    async def test_a_spawn_whose_own_worker_came_and_went_is_not_starting_or_booting(self):
        """2026-09-30: a stopped worker's spawn stays `running` until the finalizer's grace passes,
        about 90 s, and the agent read `starting` and aify-env refused to start it meanwhile."""
        await self._add(age_seconds=60)
        await self._terminal("stopped", age_seconds=30)
        self.assertFalse(await self._starting())
        self.assertFalse(await self._booting())

    async def test_CONTROL_a_spawn_whose_worker_is_up_still_counts(self):
        await self._add(age_seconds=60)
        await self._terminal("attached", age_seconds=30)
        self.assertTrue(await self._starting())
        self.assertTrue(await self._booting())

    async def test_a_restart_in_the_same_second_is_not_ended_by_the_previous_worker(self):
        """Review of e1095fbc: the previous worker's dead terminal and the restart spawn stamped in
        the SAME second. The restart has its own fresh session and no terminal yet, so it is still
        coming up; counting the old terminal would allow a second start."""
        same = _utc_stamp(5)
        await self._terminal("stopped", term_id="t-old", session="sess-old", stamp=same)
        await self._add(session="sess-new", stamp=same)
        self.assertTrue(await self._starting())
        self.assertTrue(await self._booting())

    async def test_a_spawn_not_yet_running_owns_no_terminal(self):
        """A request that has never reached running has no session yet, and an empty session must
        not match terminals that also carry an empty one."""
        await self._terminal("stopped", term_id="t-blank", session="")
        await self._add(status="starting", session="", age_seconds=5)
        self.assertTrue(await self._booting())

    async def test_CONTROL_its_own_worker_from_the_same_second_that_ended_counts(self):
        """The case a strict `>` on timestamps would miss: spawn and worker born in one second."""
        same = _utc_stamp(5)
        await self._add(session="sess-new", stamp=same)
        await self._terminal("stopped", session="sess-new", stamp=same)
        self.assertFalse(await self._starting())
        self.assertFalse(await self._booting())

    async def test_a_rebound_worker_older_than_its_spawn_that_ended_counts(self):
        """The bridge migration moves a LIVE terminal onto the new spawn's session, so the worker can
        predate its spawn. When it dies, the spawn's worker is gone."""
        await self._add(session="sess-new", age_seconds=5)
        await self._terminal("stopped", session="sess-new", age_seconds=3600)
        self.assertFalse(await self._starting())
        self.assertFalse(await self._booting())

    async def test_no_spawn_at_all(self):
        self.assertFalse(await self._starting())

    async def test_the_display_window_equals_the_inflight_suppressor(self):
        """Review's finding, pinned as a constant relationship rather than two matching literals —
        two numbers that happen to agree today will drift, and the gap between them is a window
        where the display and the dispatcher contradict each other."""
        from service.api_core.managed_env import (
            SPAWN_INFLIGHT_WINDOW_SECONDS,
            SPAWN_STARTING_WINDOW_SECONDS,
        )

        self.assertEqual(
            SPAWN_STARTING_WINDOW_SECONDS, SPAWN_INFLIGHT_WINDOW_SECONDS,
            "the status must not expire before the mechanism that still suppresses a duplicate start",
        )
        # v0.5.4: the suppressor moved out of control_plane.py, and this probe used to read that file
        # BY NAME — so after the move `src.index(...)` would raise, or worse, a future move would
        # leave it scanning a file that no longer contains the function and passing vacuously. It now
        # FINDS the owner instead of assuming one, the same correction the untrusted-subject probe
        # needed for the same reason.
        service_root = Path(__file__).resolve().parents[1]
        needle = "async def _has_pending_or_booting_spawn_request"
        owners = [
            path for path in sorted(service_root.rglob("*.py"))
            if "__pycache__" not in path.parts and "tests" not in path.parts
            and needle in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(
            len(owners), 1,
            f"the suppressor must have exactly one definition; found {[str(o) for o in owners]}",
        )
        src = owners[0].read_text(encoding="utf-8")
        at = src.index(needle)
        body = src[at : at + 1500]
        self.assertIn("SPAWN_INFLIGHT_WINDOW_SECONDS", body,
                      "the suppressor must use the shared constant, not a re-typed 300")


if __name__ == "__main__":
    unittest.main()
