"""R2c: `_current_agent_session_row` must never return a DEAD session over a live one.

Found in review (2026-07-26) as a docstring/WHERE mismatch, but tracing the ORDER BY showed a
real shadowing defect, not a comment nit:

    WHERE agent_id = ? AND status NOT IN ('ended', 'completed', 'cancelled')
    ORDER BY CASE WHEN status IN ('running','recovering','restarting','cli-takeover')
                  THEN 0 ELSE 1 END,
             last_seen DESC, started_at DESC

`stopped` / `failed` / `lost` pass the WHERE, and the CASE only promotes FOUR statuses — so the
live statuses `attached` / `active` / `idle` / `starting` sit in the SAME tier-1 bucket as the dead
ones and lose the `last_seen DESC` tiebreak to a fresher corpse. The picker then answers "this
agent's CURRENT session" with a dead row.

That is the same shadowing class as the dashboard `find()` bug fixed in `c2f0e38`, and it reaches
five callers:
  * `_has_live_worker_for` (:4716) tests `status in _LIVE_SESSION_STATUSES` on whatever it gets, so
    a shadowing dead row makes `live_session=False` for an agent with a LIVE console — a status lie.
  * the pi and claude idle-reply closers (:5661, :5719) read `terminal_id` off the returned row, so
    they inspect the wrong terminal (or none) and never auto-close their run.
  * the terminal-close requeue path (:5568) compares `current_terminal_id == terminal_id` and
    silently skips the requeue on a mismatch.

The fix aligns the WHERE with the contract the docstring already stated (exclude ALL SIX terminal
statuses). Each caller degrades safely to `None`: `live_session` becomes False exactly as it did
for a dead row, and the two closers plus the requeue path already early-return on a missing
`terminal_id`.
"""
import asyncio
import time

from service.db import get_db
from service.api_core.agent_sessions import _current_agent_session_row
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now
# v0.5.4: the constant moved on to a leaf — the control plane declared it and never read it.
from service.api_core.tuning import _SESSION_DELETE_ALLOWED_STATUSES

from service.tests._base import FastApiTestCase
from service.api_core.tuning import LIVE_SESSION_STATUSES


def _iso_ago(seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


class CurrentSessionPickerPrefersLiveTests(FastApiTestCase):
    DB_NAME = "aify-current-session-picker-test.db"

    def _seed(self, session_id, agent_id, status, *, last_seen_ago, terminal_id=""):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO agent_sessions
                        (id, agent_id, environment_id, runtime, mode, status,
                         started_at, last_seen, terminal_id)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (session_id, agent_id, "env-1", "claude-code", "managed", status,
                     _iso_ago(last_seen_ago + 10), _iso_ago(last_seen_ago), terminal_id),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _pick(self, agent_id):
        async def _run():
            db = await get_db()
            try:
                row = await _current_agent_session_row(db, agent_id)
                return dict(row) if row else None
            finally:
                await db.close()

        return asyncio.run(_run())

    # --- the regression -------------------------------------------------------------------

    def test_fresher_dead_row_does_not_shadow_a_live_attached_session(self):
        """THE DEFECT. `attached` is live but shares tier 1 with `stopped`, so the fresher
        corpse won `last_seen DESC` and became the agent's "current" session."""
        self._seed("s-live", "shadow-1", "attached", last_seen_ago=300, terminal_id="term_live")
        self._seed("s-dead", "shadow-1", "stopped", last_seen_ago=5, terminal_id="term_dead")
        picked = self._pick("shadow-1")
        self.assertIsNotNone(picked, "a live attached session must be found")
        self.assertEqual(
            picked["id"], "s-live",
            "a stopped session must never shadow a live one, however fresh its last_seen",
        )

    def test_every_live_status_outranks_a_fresher_dead_row(self):
        """All six members of LIVE_SESSION_STATUSES, not just the four the CASE promotes."""
        for i, live_status in enumerate(sorted(LIVE_SESSION_STATUSES)):
            agent_id = f"shadow-each-{i}"
            self._seed(f"s-live-{i}", agent_id, live_status, last_seen_ago=300)
            self._seed(f"s-dead-{i}", agent_id, "failed", last_seen_ago=5)
            picked = self._pick(agent_id)
            self.assertIsNotNone(picked, f"{live_status} session went missing")
            self.assertEqual(
                picked["id"], f"s-live-{i}",
                f"a failed session shadowed a live '{live_status}' session",
            )

    def test_all_six_terminal_statuses_are_excluded_as_the_docstring_promises(self):
        """The contract is the complement of LIVE_SESSION_STATUSES. `stopped`/`failed`/`lost`
        used to pass the WHERE, so an agent whose only rows were dead got a dead "current"
        session instead of an honest None."""
        for i, dead_status in enumerate(sorted(_SESSION_DELETE_ALLOWED_STATUSES)):
            agent_id = f"dead-only-{i}"
            self._seed(f"s-{i}", agent_id, dead_status, last_seen_ago=5)
            self.assertIsNone(
                self._pick(agent_id),
                f"'{dead_status}' is a terminal status and must not be returned as CURRENT",
            )

    # --- guard the behaviour that must NOT change ------------------------------------------

    def test_transitional_statuses_still_count_as_current(self):
        """`restarting` / `cli-takeover` are neither live nor terminal — they are mid-flight and
        the picker must keep returning them, or a restart loses track of its own session."""
        for i, status in enumerate(("restarting", "cli-takeover")):
            agent_id = f"transitional-{i}"
            self._seed(f"s-{i}", agent_id, status, last_seen_ago=5)
            picked = self._pick(agent_id)
            self.assertIsNotNone(picked, f"'{status}' must still resolve as the current session")
            self.assertEqual(picked["id"], f"s-{i}")

    def test_running_still_beats_a_fresher_attached_row(self):
        """The CASE tiebreak is a deliberate PRIORITY hint ('prefer a fresh actively-running row
        over a merely-attached one') and its comment says the ordering is kept stable so a
        relaunch does not start picking a different session. Widening the WHERE must not
        disturb it."""
        self._seed("s-running", "prio-1", "running", last_seen_ago=300)
        self._seed("s-attached", "prio-1", "attached", last_seen_ago=5)
        picked = self._pick("prio-1")
        self.assertEqual(
            picked["id"], "s-running",
            "the running-first priority tiebreak must survive the WHERE change",
        )

    def test_newest_live_row_still_wins_among_equals(self):
        self._seed("s-old", "equal-1", "attached", last_seen_ago=300)
        self._seed("s-new", "equal-1", "attached", last_seen_ago=5)
        self.assertEqual(self._pick("equal-1")["id"], "s-new")

    def test_agent_with_no_sessions_gets_none(self):
        self.assertIsNone(self._pick("nobody"))
