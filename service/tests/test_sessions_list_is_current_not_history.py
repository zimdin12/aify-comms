"""GET /sessions lists CURRENT sessions. History lives in spawn-requests, not here.

The query used to filter only on agentId/environmentId, so it returned every historical row and
the dashboard's Sessions rail became an accidental history dump — one entry per worker process ever
started. mc-senior-dev showed 79 entries that were all the SAME native conversation, resumed by 75
successive boots, which is precisely why the operator read them as duplicates.

Two latent bugs rode along, and they are the reason this is a correctness fix:
  * the dashboard resolves an agent's session with `find()` — the FIRST match in `last_seen DESC`
    order — so a dead row with a newer last_seen could shadow the live one.
  * the dashboard asks for limit=80; one agent's dead history could evict another agent's LIVE
    session from the window entirely.
"""
import asyncio
import time

from service.db import get_db
from service.routers import api_v2
# v0.5.2i: the clean-history set moved with the sessions domain. The delete-allowed set it is
# compared against is still router-owned, so this test deliberately reads each from ITS OWNER
# -- the assertion is about the RELATIONSHIP between the two sets, not about one file.
from service.routers import sessions as sessions_router

from service.tests._base import FastApiTestCase


def _iso_ago(seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


class SessionsListIsCurrentNotHistoryTests(FastApiTestCase):
    DB_NAME = "aify-sessions-current-test.db"

    def _seed(self, session_id, agent_id, status, *, last_seen_ago=0, ended=True):
        async def _run():
            db = await get_db()
            try:
                await db.execute("PRAGMA foreign_keys=OFF")
                await db.execute(
                    """
                    INSERT INTO agent_sessions
                        (id, agent_id, environment_id, runtime, mode, status,
                         started_at, ended_at, last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (session_id, agent_id, "env-1", "hermes", "managed-warm", status,
                     _iso_ago(last_seen_ago + 10),
                     _iso_ago(last_seen_ago) if ended else "",
                     _iso_ago(last_seen_ago)),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _ids(self, query=""):
        r = self.client.get(f"/api/v1/sessions{query}")
        self.assertEqual(r.status_code, 200, r.text)
        return [s.get("id") for s in r.json().get("sessions", [])]

    def test_cleanly_finished_sessions_are_hidden_by_default(self):
        self._seed("s-live", "a1", "running", last_seen_ago=100, ended=False)
        for i, st in enumerate(("ended", "cancelled", "completed")):
            self._seed(f"s-{st}", "a1", st, last_seen_ago=10 + i)
        ids = self._ids()
        self.assertIn("s-live", ids, "the live session must be listed")
        for st in ("ended", "cancelled", "completed"):
            self.assertNotIn(f"s-{st}", ids, f"{st} is clean history and must not appear by default")

    def test_ACTIONABLE_terminal_sessions_stay_visible(self):
        """CRITICAL REGRESSION GUARD (review 2026-07-26).

        The first cut hid every status in `_SESSION_DELETE_ALLOWED_STATUSES` — a DELETION allowlist
        — which also hid `stopped`/`failed`/`lost`. Those are the states you act on: Restart, Reset
        and Compact exist precisely for them, and `comms_restart` deliberately falls back to
        `sessions[0]` so a non-live session can be restarted. Hiding them made comms_restart and
        comms_compact answer "no session" for a stopped agent. Safe-to-delete is NOT
        not-worth-showing.
        """
        for st in ("stopped", "failed", "lost"):
            self._seed(f"s-act-{st}", f"actionable-{st}", st, last_seen_ago=5)
            ids = self._ids(f"?agentId=actionable-{st}")
            self.assertIn(
                f"s-act-{st}", ids,
                f"a {st} session is actionable (restart/reset/compact) and must stay listed",
            )

    def test_comms_restart_style_lookup_still_finds_a_stopped_session(self):
        """Reproduce the consumer contract: comms_restart prefers a live status, else sessions[0]."""
        self._seed("s-stopped-only", "restartable", "stopped", last_seen_ago=5)
        sessions = self.client.get("/api/v1/sessions?agentId=restartable").json()["sessions"]
        live = {"starting", "running", "recovering", "restarting", "cli-takeover"}
        target = next((s for s in sessions if str(s.get("status", "")).lower() in live), None)             or (sessions[0] if sessions else None)
        self.assertIsNotNone(target, "comms_restart must still find a session to restart")
        self.assertEqual(target["id"], "s-stopped-only")

    def test_hidden_set_is_narrower_than_the_delete_set(self):
        """Pin the distinction so the two sets cannot be collapsed again."""
        self.assertTrue(
            sessions_router.SESSION_CLEAN_HISTORY_STATUSES < api_v2._SESSION_DELETE_ALLOWED_STATUSES,
            "the hidden set must be a strict subset of the deletable set",
        )
        for actionable in ("stopped", "failed", "lost"):
            self.assertNotIn(actionable, sessions_router.SESSION_CLEAN_HISTORY_STATUSES)

    def test_include_ended_restores_history(self):
        self._seed("s2-live", "a2", "running", last_seen_ago=100, ended=False)
        self._seed("s2-ended", "a2", "ended", last_seen_ago=10)
        ids = self._ids("?includeEnded=true")
        self.assertIn("s2-live", ids)
        self.assertIn("s2-ended", ids, "history must still be reachable explicitly")

    def test_a_dead_row_cannot_shadow_the_live_one(self):
        """THE `find()` BUG. A dead row with a NEWER last_seen sorted ahead of the live session,
        so the dashboard's first-match lookup bound the console to an ended session."""
        self._seed("s3-live", "a3", "running", last_seen_ago=500, ended=False)
        self._seed("s3-dead", "a3", "ended", last_seen_ago=1)  # newer last_seen than the live row
        ids = self._ids("?agentId=a3")
        self.assertEqual(
            ids, ["s3-live"],
            "the only session listed for the agent must be the live one, whatever last_seen says",
        )

    def test_history_cannot_evict_a_live_session_from_the_limit_window(self):
        """THE STARVATION BUG. One agent's dead history filled the dashboard's limit=80 window and
        pushed another agent's LIVE session out of the response entirely."""
        for i in range(120):
            self._seed(f"s4-hist-{i:03d}", "noisy-agent", "ended", last_seen_ago=1 + i)
        self._seed("s4-quiet-live", "quiet-agent", "running", last_seen_ago=900, ended=False)
        ids = self._ids("?limit=80")
        self.assertIn(
            "s4-quiet-live", ids,
            "a live session must not be evicted from the window by another agent's history",
        )

    def test_agent_filter_still_applies(self):
        self._seed("s5-a", "agent-a", "running", last_seen_ago=5, ended=False)
        self._seed("s5-b", "agent-b", "running", last_seen_ago=5, ended=False)
        ids = self._ids("?agentId=agent-a")
        self.assertEqual(ids, ["s5-a"])

    def test_default_matches_the_prune_helpers_terminal_set(self):
        """Both the list filter and the history prune must mean the same thing by "terminal"; if
        they drift, the list would hide rows the prune keeps (or vice versa)."""
        self.assertEqual(
            sessions_router.SESSION_CLEAN_HISTORY_STATUSES, {"ended", "completed", "cancelled"},
        )


class EnvironmentDegradedAgesOfflineTests(FastApiTestCase):
    """A `degraded` environment must age to `offline` like an `online` one.

    Found in review 2026-07-26. `_environment_effective_status` gated its staleness check on
    `status == "online"`, so a `degraded` row NEVER aged out — it stayed "degraded" forever after
    the bridge died. Because callers (including aify-doctor's env-bridge check) treat degraded as
    still-connected, that resurrected the exact false-green class the check exists to prevent: a
    dead bridge reported as live. `degraded` means reduced capability, not dead.
    """

    DB_NAME = "aify-env-degraded-test.db"

    def _row(self, status, last_seen_ago):
        return {
            "status": status,
            "last_seen": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - last_seen_ago)
            ),
        }

    def test_fresh_degraded_stays_degraded(self):
        self.assertEqual(
            api_v2._environment_effective_status(self._row("degraded", 5), offline_seconds=90),
            "degraded",
            "a heartbeating degraded bridge is still usable",
        )

    def test_stale_degraded_ages_to_offline(self):
        self.assertEqual(
            api_v2._environment_effective_status(self._row("degraded", 3600), offline_seconds=90),
            "offline",
            "a degraded bridge that stopped heartbeating is offline, not degraded forever",
        )

    def test_stale_online_still_ages_to_offline(self):
        self.assertEqual(
            api_v2._environment_effective_status(self._row("online", 3600), offline_seconds=90),
            "offline",
        )

    def test_fresh_online_stays_online(self):
        self.assertEqual(
            api_v2._environment_effective_status(self._row("online", 5), offline_seconds=90),
            "online",
        )

    def test_decisions_are_never_overridden_by_a_timestamp(self):
        """offline/forgotten/disabled are operator or server DECISIONS — ageing must not touch
        them, and a fresh heartbeat must not resurrect them."""
        for decided in ("offline", "forgotten", "disabled"):
            for age in (5, 3600):
                self.assertEqual(
                    api_v2._environment_effective_status(
                        self._row(decided, age), offline_seconds=90
                    ),
                    decided,
                    f"{decided} is a decision, not an observation",
                )
