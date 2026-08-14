"""A queued STOP must survive the ended-controls reconcile, or the process is never killed.

Review finding on `35cc646`, and a regression I introduced. `stop_agent_worker` marks a real
terminal `'stopping'` (correctly — the host has not acknowledged yet) and queues an
`action='stop'` terminal control for the bridge to execute. But:

    _reconcile_ended_terminal_controls (api_v2.py):
        WHERE terminal.status NOT IN ('starting','attached','running','active','idle')
          AND control.status IN ('pending','claimed')
        -> UPDATE terminal_controls SET status='failed', error='terminal is not active'

`'stopping'` is not in that active set. The reconcile loop runs on a timer and the bridge polls
every ~3s, so whenever the reconcile wins the race it FAILS the stop control. The bridge never
receives it, the PTY keeps running, and 900s later the stuck-stopping reaper force-writes
`'stopped'` — a row asserting a process death that never happened. Strictly worse than the state
lie it replaced, because now the process actually survives.

This is not hypothetical: the live DB has 158 `action='input'` controls failed with exactly
`terminal is not active`, so the sweep demonstrably fires against real traffic.

The same exposure applied to the PRE-EXISTING virtual-terminal path, which marks `'stopped'` and
queues a stop control in the same transaction — `'stopped'` is not in the active set either. So the
fix cannot just add `'stopping'` to the set; a stop control must never be failed for this reason.
Killing a process is idempotent and remains desirable on a dead-looking row — server.js has an
orphan-pid fallback for precisely that case.
"""
import asyncio

from service.db import get_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

from service.tests._base import FastApiTestCase
from service.api_core import terminal_ownership  # v0.5.4: patched on its OWNER, not the carrier
from service.clock import now as _now
from service.reconcilers.terminal_runs import _reconcile_ended_terminal_controls


class StopControlSurvivesReconcileTests(FastApiTestCase):
    DB_NAME = "aify-stop-control-reconcile-test.db"

    def _seed(self, terminal_id, *, terminal_status, action, control_status="pending"):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO terminal_sessions
                        (id, session_id, agent_id, environment_id, bridge_id, runtime, status,
                         created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (terminal_id, f"sess-{terminal_id}", "agent-1", "env-1", "bridge-1",
                     "claude-code", terminal_status, _now(), _now()),
                )
                await db.execute(
                    """
                    INSERT INTO terminal_controls
                        (id, terminal_id, environment_id, bridge_id, action, body, status,
                         requested_by, requested_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (f"ctl-{terminal_id}-{action}", terminal_id, "env-1", "bridge-1", action, "",
                     control_status, "dashboard", _now()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _reconcile_then_status(self, terminal_id, action):
        async def _run():
            db = await get_db()
            try:
                await _reconcile_ended_terminal_controls(db)
                await db.commit()
                row = await (await db.execute(
                    "SELECT status, error FROM terminal_controls WHERE id = ?",
                    (f"ctl-{terminal_id}-{action}",),
                )).fetchone()
                return dict(row)
            finally:
                await db.close()

        return asyncio.run(_run())

    # --- the regression -------------------------------------------------------------------

    def test_stop_control_on_a_STOPPING_terminal_is_not_failed(self):
        """THE REGRESSION. stop_agent_worker's own control was being cancelled before the bridge
        could poll it, so the wrapper PTY survived a "successful" Stop worker."""
        self._seed("term_stopping", terminal_status="stopping", action="stop")
        row = self._reconcile_then_status("term_stopping", "stop")
        self.assertEqual(
            row["status"], "pending",
            f"the stop must stay actionable for the bridge; got {row}",
        )

    def test_stop_control_on_an_already_STOPPED_row_is_not_failed_either(self):
        """The pre-existing virtual-terminal path marks 'stopped' and queues the stop in the SAME
        transaction, so it had the same exposure. Killing a process is idempotent and still wanted
        on a dead-looking row — server.js keeps an orphan-pid fallback for exactly this."""
        self._seed("term_stopped", terminal_status="stopped", action="stop")
        row = self._reconcile_then_status("term_stopped", "stop")
        self.assertEqual(row["status"], "pending", f"a stop must not be cancelled; got {row}")

    def test_a_claimed_stop_is_also_preserved(self):
        self._seed("term_claimed", terminal_status="stopping", action="stop",
                   control_status="claimed")
        row = self._reconcile_then_status("term_claimed", "stop")
        self.assertEqual(row["status"], "claimed", f"an in-flight stop must not be failed; got {row}")

    # --- the SECOND copy of the same rule -------------------------------------------------

    def test_the_OTHER_sweep_also_spares_a_stop(self):
        """SELF-REVIEW FIND. The rule is implemented TWICE: _reconcile_ended_terminal_controls
        and reconcilers/terminal_controls._reconcile_terminal_controls, with the same predicate and the same
        'terminal is not active' error text. Exempting the stop in only one of them fixes nothing —
        the other sweep still cancels it. This test drives THAT sweep specifically, so the two
        implementations cannot drift apart again without a failure.

        It lived in `service/db.py` until v0.5.4 and the name said so; the sweep moved to the
        reconcilers package, which is where a reconciler belongs, and a test named after a file is a
        location pin waiting to go red."""
        from service.reconcilers import terminal_controls as sweep

        self._seed("term_dbpy", terminal_status="stopping", action="stop")

        async def _run():
            db = await get_db()
            try:
                # Keep the env-currency sweep in the same function from failing the row for an
                # unrelated reason — this test is about the LIVENESS predicate only.
                await db.execute(
                    "INSERT OR REPLACE INTO environments (id, status, bridge_id, registered_at, last_seen) "
                    "VALUES (?,?,?,?,?)",
                    ("env-1", "online", "bridge-1", _now(), _now()),
                )
                await sweep._reconcile_terminal_controls(db)
                await db.commit()
                row = await (await db.execute(
                    "SELECT status, error FROM terminal_controls WHERE id = ?",
                    ("ctl-term_dbpy-stop",),
                )).fetchone()
                return dict(row)
            finally:
                await db.close()

        row = asyncio.run(_run())
        self.assertEqual(
            row["status"], "pending",
            f"the terminal-controls sweep must spare a stop exactly as api_v2's does; got {row}",
        )

    def test_the_OTHER_sweep_still_fails_a_doomed_input(self):
        """That copy must keep its fail-fast behaviour too — the exemption is stop-only.

        Named for the sweep rather than the file it used to live in; it moved to
        `service/reconcilers/terminal_controls.py` in v0.5.4."""
        from service.reconcilers import terminal_controls as sweep

        self._seed("term_dbpy_input", terminal_status="stopped", action="input")

        async def _run():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR REPLACE INTO environments (id, status, bridge_id, registered_at, last_seen) "
                    "VALUES (?,?,?,?,?)",
                    ("env-1", "online", "bridge-1", _now(), _now()),
                )
                await sweep._reconcile_terminal_controls(db)
                await db.commit()
                row = await (await db.execute(
                    "SELECT status FROM terminal_controls WHERE id = ?",
                    ("ctl-term_dbpy_input-input",),
                )).fetchone()
                return dict(row)
            finally:
                await db.close()

        self.assertEqual(asyncio.run(_run())["status"], "failed")

    # --- bridge restart: the stop must REACH the new bridge, not be cancelled --------------

    def _seed_env(self, env_id, bridge_id, status="online"):
        async def _run():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR REPLACE INTO environments (id, status, bridge_id, registered_at, last_seen) "
                    "VALUES (?,?,?,?,?)",
                    (env_id, status, bridge_id, _now(), _now()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _run_db_reconcile_and_read(self, control_id):
        from service.reconcilers import terminal_controls as sweep

        async def _run():
            db = await get_db()
            try:
                await sweep._reconcile_terminal_controls(db)
                await db.commit()
                row = await (await db.execute(
                    "SELECT status, bridge_id, error FROM terminal_controls WHERE id = ?",
                    (control_id,),
                )).fetchone()
                return dict(row)
            finally:
                await db.close()

        return asyncio.run(_run())

    def test_a_stop_is_RETARGETED_when_the_owning_bridge_restarted(self):
        """THE COMPOSED DEFECT the reviewer identified, and its root cause.

        `server.js` carries an orphan-pid fallback for precisely "the owning bridge restarted/died
        and orphaned a still-live console" — it kills the persisted PTY root by pid when the stop
        arrives at a bridge that never owned the terminal in memory. That fallback could NEVER RUN,
        because the env-currency sweep fails the control the moment `bridge_id` stops matching a
        current online environment. So the machinery built for bridge restart was unreachable in the
        exact scenario it names, the PTY survived, and — since stop_agent_worker writes the session
        `'ended'` — Start was then free to spawn a SECOND worker for the same agent.

        Re-target instead of cancel: a live bridge on that environment is machine-local and CAN reap
        the orphan. This is also why the fix is not a Start gate — a gate would only hide the
        duplicate, and a too-strict Start gate is what made the whole ef- team unstartable."""
        self._seed("term_restart", terminal_status="stopping", action="stop")
        self._seed_env("env-1", "bridge-NEW", status="online")
        row = self._run_db_reconcile_and_read("ctl-term_restart-stop")
        self.assertEqual(row["status"], "pending", f"the stop must stay actionable; got {row}")
        self.assertEqual(
            row["bridge_id"], "bridge-NEW",
            "the stop must be re-pointed at the environment's CURRENT bridge so the orphan-pid "
            f"fallback can reach it; got {row}",
        )

    def test_a_CLAIMED_stop_is_released_back_to_pending_when_its_bridge_restarted(self):
        """REVIEW FIND on `530ee71` — a real defect in my own re-target fix.

        The re-target rewrote `bridge_id` for `status IN ('pending','claimed')`, but a bridge only
        ever claims PENDING work: `api_v2.py:12675` is
        `SET status='claimed' ... WHERE id = ? AND status = 'pending'`. So a stop the OLD bridge had
        already claimed kept `status='claimed'`, was re-pointed at the new bridge, and the new bridge
        never touched it — stranded forever. Re-targeting without releasing the claim is a no-op for
        exactly the controls most likely to exist when a bridge dies mid-stop.

        A claim held by a bridge that no longer exists is not a claim. Release it."""
        self._seed("term_claimed_restart", terminal_status="stopping", action="stop",
                   control_status="claimed")

        async def _stamp_claimed_at():
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE terminal_controls SET claimed_at = ? WHERE id = ?",
                    (_now(), "ctl-term_claimed_restart-stop"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_stamp_claimed_at())
        self._seed_env("env-1", "bridge-NEW", status="online")

        from service.reconcilers import terminal_controls as sweep

        async def _run():
            db = await get_db()
            try:
                await sweep._reconcile_terminal_controls(db)
                await db.commit()
                row = await (await db.execute(
                    "SELECT status, bridge_id, claimed_at FROM terminal_controls WHERE id = ?",
                    ("ctl-term_claimed_restart-stop",),
                )).fetchone()
                return dict(row)
            finally:
                await db.close()

        row = asyncio.run(_run())
        self.assertEqual(row["bridge_id"], "bridge-NEW", f"must be re-pointed; got {row}")
        self.assertEqual(
            row["status"], "pending",
            "a claim held by a bridge that no longer exists must be RELEASED, or the replacement "
            f"bridge (which claims only 'pending') can never pick the stop up; got {row}",
        )
        self.assertFalse(
            str(row["claimed_at"] or "").strip(),
            f"the stale claim timestamp must be cleared with the claim; got {row}",
        )

    def test_a_CLAIMED_non_stop_control_is_NOT_released(self):
        """Releasing is stop-only, same reasoning as re-targeting: re-running a keystroke that a
        previous bridge may already have delivered would double-type it."""
        self._seed("term_claimed_input", terminal_status="attached", action="input",
                   control_status="claimed")
        self._seed_env("env-1", "bridge-NEW", status="online")

        from service.reconcilers import terminal_controls as sweep

        async def _run():
            db = await get_db()
            try:
                await sweep._reconcile_terminal_controls(db)
                await db.commit()
                row = await (await db.execute(
                    "SELECT status FROM terminal_controls WHERE id = ?",
                    ("ctl-term_claimed_input-input",),
                )).fetchone()
                return dict(row)
            finally:
                await db.close()

        self.assertNotEqual(asyncio.run(_run())["status"], "pending",
                            "a claimed input must never be silently re-queued for replay")

    def test_a_stop_IS_failed_when_the_environment_has_no_live_bridge(self):
        """The bound on accumulation. If nothing can act on it, cancelling is correct — otherwise a
        control for a dead environment would sit pending forever."""
        self._seed("term_noenv", terminal_status="stopping", action="stop")
        self._seed_env("env-1", "bridge-OLD", status="offline")
        row = self._run_db_reconcile_and_read("ctl-term_noenv-stop")
        self.assertEqual(row["status"], "failed",
                         f"an unreachable stop must not accumulate; got {row}")

    def test_a_non_stop_control_is_still_failed_on_bridge_mismatch(self):
        """Re-targeting is stop-only. Replaying a keystroke at a different bridge would inject it
        into whatever that bridge now owns — the exemption must not widen."""
        self._seed("term_mismatch_input", terminal_status="attached", action="input")
        self._seed_env("env-1", "bridge-NEW", status="online")
        row = self._run_db_reconcile_and_read("ctl-term_mismatch_input-input")
        self.assertEqual(row["status"], "failed", f"got {row}")

    def test_start_never_ADOPTS_a_stopping_terminal(self):
        """The invariant the Start-before-ack concern rests on. `_active_terminal_for_agent`'s query
        carries NO status filter — it takes the newest session's terminal — and the guard lives in
        the Python that follows it. If that guard ever widened, Start would reuse a terminal the
        operator had just asked to die, which is far worse than briefly running two: the new worker
        would inherit a PTY being killed underneath it. Pinned here because the SQL alone does not
        express it."""
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO terminal_sessions
                        (id, session_id, agent_id, environment_id, bridge_id, runtime, status,
                         created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    ("term_adopt", "sess_adopt", "adopt-agent", "env-1", "bridge-1", "claude-code",
                     "stopping", _now(), _now()),
                )
                await db.execute(
                    """
                    INSERT INTO agent_sessions
                        (id, agent_id, environment_id, runtime, mode, status, started_at, last_seen,
                         terminal_id, terminal_status)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("sess_adopt", "adopt-agent", "env-1", "claude-code", "managed", "running",
                     _now(), _now(), "term_adopt", "stopping"),
                )
                await db.commit()
                return await terminal_ownership._active_terminal_for_agent(db, "adopt-agent")
            finally:
                await db.close()

        self.assertIsNone(
            asyncio.run(_run()),
            "a 'stopping' terminal must never be offered up as an agent's active console",
        )

    # --- what must KEEP working -----------------------------------------------------------

    def test_input_on_a_dead_terminal_is_still_failed(self):
        """The reconcile exists so a caller is not left waiting on a control nobody will run.
        Keystrokes into a dead PTY are genuinely undeliverable and must still fail fast — this is
        the behaviour that produced the 158 real 'terminal is not active' rows."""
        self._seed("term_dead_input", terminal_status="stopped", action="input")
        row = self._reconcile_then_status("term_dead_input", "input")
        self.assertEqual(row["status"], "failed", f"input to a dead terminal must fail; got {row}")
        self.assertIn("not active", str(row["error"] or ""))

    def test_input_on_a_STOPPING_terminal_is_still_failed(self):
        """'stopping' is transitional but the console is going away — typing into it cannot be
        honoured, so the caller should learn that immediately rather than hang."""
        self._seed("term_stopping_input", terminal_status="stopping", action="input")
        row = self._reconcile_then_status("term_stopping_input", "input")
        self.assertEqual(row["status"], "failed", f"got {row}")

    def test_a_stop_survives_even_when_the_SAME_terminal_also_has_a_doomed_input(self):
        """The case the exclude_actions parameter exists for. The outer WHERE stops selecting a
        terminal whose ONLY outstanding control is a stop — but a terminal with an input AND a stop
        is still selected (on account of the input), and the helper would then fail every pending
        row for it, taking the stop down as collateral."""
        async def _seed_both():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO terminal_sessions
                        (id, session_id, agent_id, environment_id, bridge_id, runtime, status,
                         created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    ("term_mixed", "sess-mixed", "agent-1", "env-1", "bridge-1", "claude-code",
                     "stopping", _now(), _now()),
                )
                for action in ("input", "stop"):
                    await db.execute(
                        """
                        INSERT INTO terminal_controls
                            (id, terminal_id, environment_id, bridge_id, action, body, status,
                             requested_by, requested_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (f"ctl-term_mixed-{action}", "term_mixed", "env-1", "bridge-1", action, "",
                         "pending", "dashboard", _now()),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_seed_both())
        stop_row = self._reconcile_then_status("term_mixed", "stop")
        self.assertEqual(stop_row["status"], "pending",
                         f"the stop must survive alongside a doomed input; got {stop_row}")

        async def _read_input():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT status FROM terminal_controls WHERE id = ?", ("ctl-term_mixed-input",)
                )).fetchone()
                return dict(row)
            finally:
                await db.close()

        self.assertEqual(asyncio.run(_read_input())["status"], "failed",
                         "the input must still fail fast — only the stop is exempt")

    def test_controls_on_a_LIVE_terminal_are_untouched(self):
        for status in ("starting", "attached", "running", "active", "idle"):
            tid = f"term_live_{status}"
            self._seed(tid, terminal_status=status, action="input")
            row = self._reconcile_then_status(tid, "input")
            self.assertEqual(row["status"], "pending",
                             f"a live '{status}' terminal's control must not be failed; got {row}")
