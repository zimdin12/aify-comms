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

    def test_terminal_sessions_are_hidden_by_default(self):
        self._seed("s-live", "a1", "running", last_seen_ago=100, ended=False)
        for i, st in enumerate(("ended", "stopped", "failed", "lost", "cancelled", "completed")):
            self._seed(f"s-{st}", "a1", st, last_seen_ago=10 + i)
        ids = self._ids()
        self.assertIn("s-live", ids, "the live session must be listed")
        for st in ("ended", "stopped", "failed", "lost", "cancelled", "completed"):
            self.assertNotIn(f"s-{st}", ids, f"{st} is history and must not appear by default")

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
            api_v2._SESSION_DELETE_ALLOWED_STATUSES,
            {"stopped", "failed", "lost", "ended", "completed", "cancelled"},
        )
