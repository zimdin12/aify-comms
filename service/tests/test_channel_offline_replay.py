"""Task #238 regression: channel posts to offline-env members are replayed
into a dispatch run when their environment later recovers.

The live send path (`send_channel_message`) drops any member whose managed
environment is effectively offline from `dispatch_recipients` — the canonical
message + the member's inbox copy are stored (dispatch_requested=1) but NO
dispatch_run is created. Nothing ever revisits that stored message, so on
`offline -> online` the member stays silent until it happens to poll (which a
cold managed agent never does). The new reconciler
`_replay_undelivered_channel_messages_on_env_recovery` closes that gap: it runs
in the 60s sweep and, for a stored-but-un-dispatched channel inbox message whose
member's env is now available, creates the dispatch run the send would have made
— idempotently (a member who already has a run, or who read the message, is
never re-dispatched).
"""
import asyncio
from datetime import datetime, timedelta, timezone

from service.db import get_db
from service.routers import api_v2

from service.tests._base import FastApiTestCase


ENV_ID = "linux:test-host:default"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_ago(hours: float) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(hours=hours))


def _hours_ago_ms(hours: float) -> int:
    """`messages.timestamp` is epoch MILLISECONDS in production — verified, 29,854 of 29,854 rows
    are integers and none are ISO.

    These tests used to seed ISO strings here. That is why the replay reconciler's broken
    `datetime(m.timestamp)` predicate passed its own suite for months while matching ZERO rows in
    production: the fixture format and the real format disagreed, so the test validated a shape
    that never occurs. Seeding what the send path actually writes is the point."""
    return int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)


class ChannelOfflineReplayTests(FastApiTestCase):
    DB_NAME = "aify-channel-replay-test.db"

    # ---- harness helpers (mirroring test_bugd_coldstart_selfheal.py) ----

    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _heartbeat_environment(self, **extra):
        payload = {
            "id": ENV_ID,
            "label": "Linux on test-host",
            "machineId": "linux:test-host",
            "os": "linux",
            "kind": "linux",
            "bridgeId": "bridge-current",
            "cwdRoots": ["/workspace"],
            "runtimes": [
                {
                    "runtime": "codex",
                    "modes": ["managed-warm"],
                    "capabilities": {"nativeResume": True, "interrupt": True},
                }
            ],
            "metadata": {},
        }
        payload.update(extra)
        response = self.client.post("/api/v1/environments/heartbeat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def _execute(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _fetchall(self, query: str, params=()):
        async def _run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchall()
            finally:
                await db.close()

        return asyncio.run(_run())

    def _run_replay(self):
        async def _run():
            db = await get_db()
            try:
                replayed = await api_v2._replay_undelivered_channel_messages_on_env_recovery(db)
                await db.commit()
                return replayed
            finally:
                await db.close()

        return asyncio.run(_run())

    def _seed_managed_member(self, agent_id: str):
        """Managed codex agent bound to ENV_ID via a running session."""
        now = api_v2._now()
        self._heartbeat_environment()
        self._register(agent_id, runtime="codex", sessionMode="managed")
        self._execute(
            """
            INSERT INTO agent_sessions (
                id, agent_id, environment_id, runtime, workspace, mode,
                owner_mode, spawn_spec_id, spawn_request_id, status, started_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"sess_{agent_id}", agent_id, ENV_ID, "codex", "/workspace/repo",
                "managed-warm", "managed", None, None, "running", now, now,
            ),
        )

    def _set_env_offline(self):
        # Age the environment heartbeat past the 90s offline threshold.
        self._execute(
            "UPDATE environments SET last_seen = ? WHERE id = ?",
            (_hours_ago(2), ENV_ID),
        )

    def _seed_channel_inbox_message(self, canonical_id: str, agent_id: str, *,
                                    channel: str = "dev", from_agent: str = "poster",
                                    dispatch_requested: int = 1, timestamp=None):
        """Simulate the stored-only inbox copy the send path leaves for an
        offline member: a channel-source message addressed to the member with
        dispatch_requested set and NO dispatch_run."""
        fanout_id = api_v2._channel_fanout_message_id(canonical_id, agent_id)
        self._execute(
            """
            INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, priority, dispatch_requested, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                fanout_id, from_agent, agent_id, channel, "channel", "message",
                "Roll call", "please report status", "normal", dispatch_requested,
                # epoch ms, matching the send path — see _hours_ago_ms.
                timestamp if timestamp is not None else _hours_ago_ms(0),
            ),
        )
        return fanout_id

    def _runs_for(self, message_id: str):
        return self._fetchall(
            "SELECT id, target_agent, status, message_id FROM dispatch_runs WHERE message_id = ?",
            (message_id,),
        )

    # ---- tests ----

    def test_env_offline_stores_only_no_run(self):
        self._seed_managed_member("m-off")
        self._set_env_offline()
        fanout = self._seed_channel_inbox_message("cmsg-1", "m-off")
        replayed = self._run_replay()
        self.assertEqual(replayed, [], "env still offline → no replay")
        self.assertEqual(len(self._runs_for(fanout)), 0, "no dispatch_run while env offline")

    def test_env_recovery_replays_creates_one_run_idempotently(self):
        self._seed_managed_member("m-rec")
        fanout = self._seed_channel_inbox_message("cmsg-2", "m-rec")
        # env is ONLINE (fresh heartbeat from _seed_managed_member) — recovered.
        replayed = self._run_replay()
        self.assertEqual(len(replayed), 1, f"exactly one replay expected, got {replayed}")
        runs = self._runs_for(fanout)
        self.assertEqual(len(runs), 1, "exactly one dispatch_run created")
        self.assertEqual(runs[0]["target_agent"], "m-rec")
        self.assertEqual(runs[0]["status"], "queued")
        # Second pass must NOT create a duplicate.
        replayed2 = self._run_replay()
        self.assertEqual(replayed2, [], "idempotent: no second replay")
        self.assertEqual(len(self._runs_for(fanout)), 1, "still exactly one run")

    def test_replay_with_preexisting_queued_run_records_own_watermark(self):
        # The review's HIGH #238: a cold member already has a queued run R1 from an EARLIER
        # post (message fA). A new post fB arrives while the env is offline (stored, no run).
        # On recovery the replay must NOT merge fB into R1 (a merge keeps R1's message_id=fA,
        # so fB never lands on a run and re-replays every sweep, appending forever). It must
        # insert a DEDICATED run keyed on fB so the watermark records it.
        self._seed_managed_member("m-merge")
        # R1: an existing queued run from the same sender (message_id left NULL — a queued
        # run need not reference a stored message; the merge keeps whatever R1 already has,
        # so the re-replay bug reproduces regardless of R1's message_id value).
        self._execute(
            """
            INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status, require_reply, requested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("run_R1", None, "poster", "m-merge", "start_if_possible", "managed",
             "message", "Earlier", "earlier body", "normal", "queued", 0, api_v2._now()),
        )
        # fB: a new channel post stored while env was offline (same sender 'poster').
        fB = self._seed_channel_inbox_message("cmsg-new", "m-merge", from_agent="poster")

        replayed = self._run_replay()
        self.assertEqual(len(replayed), 1, "fB should replay once")
        # A run must now carry message_id = fB (its own watermark), NOT be merged into R1.
        runs_fB = self._runs_for(fB)
        self.assertEqual(len(runs_fB), 1, "replay must create a dedicated run keyed on fB")
        # Idempotency now holds: a second sweep creates nothing (the watermark is recorded).
        replayed2 = self._run_replay()
        self.assertEqual(replayed2, [], "no re-replay once fB has its own run")
        self.assertEqual(len(self._runs_for(fB)), 1, "still exactly one fB run after a 2nd sweep")

    def test_existing_run_not_double_dispatched(self):
        self._seed_managed_member("m-dup")
        fanout = self._seed_channel_inbox_message("cmsg-3", "m-dup")
        # A run already exists (the member WAS launchable at send) — must be left alone.
        self._execute(
            """
            INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status, require_reply, requested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("run_existing", fanout, "poster", "m-dup", "start_if_possible", "managed",
             "message", "Roll call", "please report status", "normal", "queued", 0, api_v2._now()),
        )
        replayed = self._run_replay()
        self.assertEqual(replayed, [], "member already has a run → no replay")
        self.assertEqual(len(self._runs_for(fanout)), 1, "no duplicate run")

    def test_read_message_not_replayed(self):
        self._seed_managed_member("m-read")
        fanout = self._seed_channel_inbox_message("cmsg-4", "m-read")
        self._execute(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (fanout, "m-read", api_v2._now()),
        )
        replayed = self._run_replay()
        self.assertEqual(replayed, [], "already-read message → no replay")
        self.assertEqual(len(self._runs_for(fanout)), 0)

    def test_message_beyond_horizon_not_replayed(self):
        self._seed_managed_member("m-old")
        fanout = self._seed_channel_inbox_message(
            "cmsg-5", "m-old", timestamp=_hours_ago_ms(72)
        )
        replayed = self._run_replay()
        self.assertEqual(replayed, [], "stale message beyond horizon → no replay")
        self.assertEqual(len(self._runs_for(fanout)), 0)

    def test_dashboard_member_not_replayed(self):
        # A message addressed to the pseudo-agent 'dashboard' must never dispatch.
        self._seed_managed_member("m-dash")
        fanout = self._seed_channel_inbox_message(
            "cmsg-6", "dashboard", dispatch_requested=0
        )
        replayed = self._run_replay()
        self.assertEqual(replayed, [], "dashboard pseudo-member → no replay")
        self.assertEqual(len(self._runs_for(fanout)), 0)
