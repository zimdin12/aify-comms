"""N7 — a queued STOP in a DEGRADED environment was still cancelled, so the PTY survived.

Reviewer finding, 2026-07-26. This is the same worker-duplication chain v0.1 claimed to fix AT THE
ROOT, reached through `degraded` instead of through a changed `bridge_id`. The two halves of one
feature disagreed about the same question:

    api_v2.stop_console — the REQUEST path (~:12391-12405)
        env_status = _environment_effective_status(env_row, offline_seconds=max(30, setting))
        bridge_can_claim = ... and env_status in {"online", "degraded"}
        -> a degraded env's bridge CAN claim, so the control is left PENDING for it

    db._reconcile_terminal_controls — the SWEEP
        AND environments.status = 'online'
        -> fails that very control, because 'degraded' != 'online'

Result: the stop is failed, the PTY is never killed, `stop_agent_worker` has already written the
session `'ended'`, and Start is then free to spawn a SECOND worker for the same agent.

The sweep now mirrors the request path's definition of "a bridge on this environment can act" —
effective status in {online, degraded} — which closes BOTH divergences:
  * `degraded` was excluded, though eleven reachability gates in api_v2 accept it and
    `_ENVIRONMENT_HEARTBEAT_STATUSES` keeps it heartbeating;
  * staleness was ignored, because raw `status = 'online'` never ages. A silent ONLINE bridge kept
    its controls pending indefinitely while api_v2 already considered that environment offline.

REACHABILITY, measured rather than assumed (slip 15 discipline): `environments.status` holds only
{online, offline, forgotten} on the live fleet, and no bridge reports `degraded` — it arrives only
from an explicit registration or a status write. So this was latent-but-reachable, not firing today.
It is fixed anyway because a disagreement between eleven gates and the sweeps is reached eventually,
and silently, and its symptom is a leaked PTY plus a duplicate worker.
"""
import asyncio

from service.db import get_db
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

from service.tests._base import FastApiTestCase


class StopControlInDegradedEnvironmentTests(FastApiTestCase):
    DB_NAME = "aify-stop-degraded-env-test.db"

    # Well past any plausible `environment_offline_seconds` (default 90, floor 30).
    STALE_LAST_SEEN = "2020-01-01T00:00:00Z"

    def _seed(self, terminal_id, *, env_id, control_bridge, terminal_status="stopping",
              action="stop", control_status="pending"):
        """Seed a terminal + one control. The terminal is `stopping`, as stop_agent_worker leaves it."""
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
                    (terminal_id, "sess-" + terminal_id, "agent-1", env_id, control_bridge,
                     "claude-code", terminal_status, api_v2._now(), api_v2._now()),
                )
                await db.execute(
                    """
                    INSERT INTO terminal_controls
                        (id, terminal_id, environment_id, bridge_id, action, body, status,
                         requested_by, requested_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    ("ctl-" + terminal_id, terminal_id, env_id, control_bridge, action, "",
                     control_status, "dashboard", api_v2._now()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())
        return "ctl-" + terminal_id

    def _seed_env(self, env_id, bridge_id, status, last_seen=None):
        async def _run():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR REPLACE INTO environments (id, status, bridge_id, registered_at, last_seen) "
                    "VALUES (?,?,?,?,?)",
                    (env_id, status, bridge_id, api_v2._now(),
                     api_v2._now() if last_seen is None else last_seen),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _sweep(self, control_id):
        from service import db as db_module

        async def _run():
            db = await get_db()
            try:
                await db_module._reconcile_terminal_controls(db)
                await db.commit()
                row = await (await db.execute(
                    "SELECT status, bridge_id, error FROM terminal_controls WHERE id = ?",
                    (control_id,),
                )).fetchone()
                return dict(row)
            finally:
                await db.close()

        return asyncio.run(_run())

    # --- the whole environment-status domain, enumerated ------------------------------------
    #
    # Slip 15 — three consecutive rounds of fixing only the value named in the bug report
    # (turn_busy: missed FUTURE; stop control: missed CLAIMED; re-target: missed DEGRADED). When a
    # fix guards on an ENUMERATED value, enumerate the whole domain and pin what happens for each.
    # `ENV_KNOWN_STATES` (mcp/stdio/doctor-predicates.js) is the vocabulary:
    #     online | degraded | offline | forgotten | disabled

    def test_a_fresh_stop_survives_in_online_AND_degraded_environments(self):
        for status in ("online", "degraded"):
            with self.subTest(env_status=status):
                tid = "term_ok_" + status
                ctl = self._seed(tid, env_id="env-" + status, control_bridge="bridge-1")
                self._seed_env("env-" + status, "bridge-1", status)
                row = self._sweep(ctl)
                self.assertEqual(
                    row["status"], "pending",
                    "a bridge on a fresh '" + status + "' env CAN act — api_v2's bridge_can_claim "
                    "accepts both — so its stop must stay actionable; got " + repr(row),
                )

    def test_a_stop_is_still_failed_in_offline_forgotten_and_disabled_environments(self):
        for status in ("offline", "forgotten", "disabled"):
            with self.subTest(env_status=status):
                tid = "term_dead_" + status
                ctl = self._seed(tid, env_id="env-" + status, control_bridge="bridge-1")
                self._seed_env("env-" + status, "bridge-1", status)
                row = self._sweep(ctl)
                self.assertEqual(
                    row["status"], "failed",
                    "'" + status + "' is a DECISION, not an observation — nothing can act on it, so "
                    "the accumulation bound must still fail the stop; got " + repr(row),
                )

    def test_a_stop_in_a_DEGRADED_env_is_RETARGETED_when_the_bridge_restarted(self):
        """The re-target must reach degraded too. Otherwise server.js's orphan-pid fallback stays
        unreachable for exactly the environments that are already struggling."""
        ctl = self._seed("term_degraded_restart", env_id="env-dg", control_bridge="bridge-OLD")
        self._seed_env("env-dg", "bridge-NEW", "degraded")
        row = self._sweep(ctl)
        self.assertEqual(row["status"], "pending", "must stay actionable; got " + repr(row))
        self.assertEqual(
            row["bridge_id"], "bridge-NEW",
            "must be re-pointed at the degraded env's CURRENT bridge; got " + repr(row),
        )

    # --- staleness: the accumulation bound must survive the widening ------------------------

    def test_a_STALE_environment_fails_the_stop_for_BOTH_heartbeat_statuses(self):
        """Widening to `degraded` without ageing would strand stops forever on a dead bridge.

        `_environment_effective_status` ages BOTH `online` and `degraded` to `offline` once
        `last_seen` passes `environment_offline_seconds`, and the request path uses that derivation.
        Raw `status='online'` never ages, so pinning this also closes a PRE-EXISTING divergence: a
        silent online env kept its controls pending indefinitely while api_v2 called it offline."""
        for status in ("online", "degraded"):
            with self.subTest(env_status=status):
                tid = "term_stale_" + status
                ctl = self._seed(tid, env_id="env-stale-" + status, control_bridge="bridge-1")
                self._seed_env("env-stale-" + status, "bridge-1", status,
                               last_seen=self.STALE_LAST_SEEN)
                row = self._sweep(ctl)
                self.assertEqual(
                    row["status"], "failed",
                    "a '" + status + "' env silent since " + self.STALE_LAST_SEEN + " is effectively "
                    "offline; leaving its stop pending forever would break the accumulation bound; "
                    "got " + repr(row),
                )

    def test_an_UNDATABLE_last_seen_trusts_the_served_status(self):
        """R3a's lesson applied to the other direction: do NOT invent a failure from a bad timestamp.

        `_environment_effective_status` swallows the parse error and returns the stored status
        untouched. The sweep must agree, so an empty or malformed `last_seen` on a live-status env
        leaves its stop actionable instead of silently cancelling it."""
        for label, last_seen in (("empty", ""), ("garbage", "not-a-timestamp")):
            with self.subTest(last_seen=label):
                tid = "term_undatable_" + label
                ctl = self._seed(tid, env_id="env-und-" + label, control_bridge="bridge-1")
                self._seed_env("env-und-" + label, "bridge-1", "degraded", last_seen=last_seen)
                row = self._sweep(ctl)
                self.assertEqual(
                    row["status"], "pending",
                    "last_seen=" + repr(last_seen) + " is undatable — trust the status, do not "
                    "invent a failure; got " + repr(row),
                )

    # --- the widening must not leak past `stop` --------------------------------------------

    def test_a_NON_STOP_control_is_still_failed_on_bridge_mismatch_in_a_degraded_env(self):
        """Re-targeting stays stop-only. Replaying a queued keystroke at a different bridge would
        inject it into whatever that bridge now owns."""
        ctl = self._seed("term_dg_input", env_id="env-dg2", control_bridge="bridge-OLD",
                         action="input")
        self._seed_env("env-dg2", "bridge-NEW", "degraded")
        row = self._sweep(ctl)
        self.assertEqual(row["status"], "failed",
                         "a non-stop control must still fail on bridge mismatch; got " + repr(row))

    def test_a_CLAIMED_stop_in_a_degraded_env_is_released_to_pending(self):
        """A claim held by a bridge that no longer exists is not a claim — and that must hold for
        degraded environments too, since it is the state most likely to exist mid-stop."""
        ctl = self._seed("term_dg_claimed", env_id="env-dg3", control_bridge="bridge-OLD",
                         control_status="claimed")
        self._seed_env("env-dg3", "bridge-NEW", "degraded")
        row = self._sweep(ctl)
        self.assertEqual(row["status"], "pending", "the claim must be released; got " + repr(row))
        self.assertEqual(row["bridge_id"], "bridge-NEW", "and re-pointed; got " + repr(row))
