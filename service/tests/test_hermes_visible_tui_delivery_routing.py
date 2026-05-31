"""Managed-hermes visible-TUI delivery — routing + bridge coexistence.

The visible-TUI managed model (plan 2026-05-31) runs THREE things per agent:
  1. a hidden `hermes dashboard --tui` gateway host,
  2. the VISIBLE `hermes --tui` in the bridge PTY (whose in-session aify-comms
     MCP registers a `managed-wrapper-child` bridge so the agent self-replies),
  3. a standalone background delivery loop (hermes-managed-host.js `run`) that
     registers a `channel-sidecar` bridge and claims execution_mode='channel'
     runs to deliver them via WS prompt.submit.

Two bugs broke live delivery (observed on agent `gov-tui`, 2026-05-30):

  A. ROUTING: a managed hermes run only routed to execution_mode='channel'
     when runtime_config.channelEnabled was truthy. The visible-TUI model
     never sets that flag (the TUI is a thin WS client; its inner MCP
     auto-register does not re-flag an already-managed agent), so dispatched
     runs stayed execution_mode='managed' and the channel-sidecar loop (which
     claims only channel/resident) never matched them → stuck queued.
     ROBUST FIX: route a managed hermes run to 'channel' when a LIVE
     channel-sidecar bridge exists for the agent — the live sidecar IS the
     channel mechanism, regardless of the flag.

  B. SUPERSESSION: the visible TUI's `managed-wrapper-child` bridge registration
     superseded the standalone `channel-sidecar` bridge (same agent+machine).
     A superseded bridge is blocked from claiming, so even a correctly
     channel-routed run could never be claimed by the delivery loop. The two
     bridges play COMPLEMENTARY roles for one managed agent and must coexist.
"""

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.db import init_db  # noqa: E402
from service.routers import api_v2  # noqa: E402
from service.routers.api_v2 import (  # noqa: E402
    router,
    _record_bridge_registration,
    _has_live_channel_sidecar,
    _apply_channel_routing_to_claude_runs,
    _now,
)


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


def _run(coro):
    return asyncio.run(coro)


class HermesVisibleTuiDeliveryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "aify-test.db"
        asyncio.run(init_db(self._db_path))

        app = FastAPI()
        app.state.ws_manager = _DummyWS()
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.state.testing = True
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    # -- low-level DB helpers ------------------------------------------------

    def _conn(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _insert_managed_hermes_agent(self, agent_id="gov-tui", channel_enabled=False):
        rc = {"channelEnabled": True} if channel_enabled else {}
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO agents (id, name, role, runtime, session_mode,
                    session_handle, launch_mode, machine_id, capabilities,
                    runtime_config, status, registered_at, last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    agent_id,
                    agent_id,
                    "coder",
                    "hermes",
                    "managed",
                    "",
                    "detached",
                    "win32:test-host",
                    json.dumps(["resume", "interrupt"]),
                    json.dumps(rc),
                    "active",
                    _now(),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # -- Fix B: channel-sidecar survives wrapper-child supersession ----------

    def test_wrapper_child_registration_does_not_supersede_channel_sidecar(self):
        agent_id = "gov-tui"
        self._insert_managed_hermes_agent(agent_id)

        async def scenario():
            db = await api_v2.get_db()
            try:
                now = _now()
                # 1. The delivery loop's standalone channel-sidecar bridge.
                await db.execute(
                    """
                    INSERT INTO bridge_instances (id, agent_id, machine_id, runtime,
                        session_mode, session_handle, terminal_id, bridge_kind,
                        registered_at, last_seen, superseded_by, superseded_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "hermes-managed-host-win32:test-host",
                        agent_id,
                        "win32:test-host",
                        "hermes",
                        "managed",
                        "",
                        "",
                        "channel-sidecar",
                        now,
                        now,
                        "",
                        None,
                    ),
                )
                await db.commit()
                # 2. The visible TUI's in-session MCP registers a wrapper-child
                #    bridge for the SAME agent+machine.
                await _record_bridge_registration(
                    db,
                    bridge_id="wrapper-child-1",
                    agent_id=agent_id,
                    machine_id="win32:test-host",
                    runtime="hermes",
                    session_mode="managed",
                    session_handle="",
                    terminal_id="term-1",
                    managed_wrapper_child=True,
                    now=_now(),
                )
                await db.commit()
                # The channel-sidecar bridge must still be alive (not superseded).
                live = await _has_live_channel_sidecar(db, agent_id)
                row = await (
                    await db.execute(
                        "SELECT superseded_by FROM bridge_instances WHERE id = ?",
                        ("hermes-managed-host-win32:test-host",),
                    )
                ).fetchone()
                return live, (row["superseded_by"] if row else "MISSING")
            finally:
                await db.close()

        live, sup = _run(scenario())
        self.assertIn(
            sup,
            ("", None),
            f"channel-sidecar must NOT be superseded by the wrapper-child; superseded_by={sup!r}",
        )
        self.assertTrue(
            live, "channel-sidecar must remain a live deliverability signal after wrapper-child registers"
        )

    def test_channel_sidecar_claim_heartbeat_does_not_supersede_wrapper_child(self):
        """Order-independence: the standalone channel-sidecar's bridge row is
        created/refreshed ONLY via its /dispatch/claim poll
        (_record_channel_sidecar_heartbeat), which is a lightweight liveness
        upsert that must never supersede other bridges — so a later sidecar poll
        must NOT kill an existing live wrapper-child bridge."""
        agent_id = "gov-tui"
        self._insert_managed_hermes_agent(agent_id)

        async def scenario():
            db = await api_v2.get_db()
            try:
                now = _now()
                await db.execute(
                    """
                    INSERT INTO bridge_instances (id, agent_id, machine_id, runtime,
                        session_mode, session_handle, terminal_id, bridge_kind,
                        registered_at, last_seen, superseded_by, superseded_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "wrapper-child-1",
                        agent_id,
                        "win32:test-host",
                        "hermes",
                        "managed",
                        "",
                        "term-1",
                        "managed-wrapper-child",
                        now,
                        now,
                        "",
                        None,
                    ),
                )
                await db.commit()
                await api_v2._record_channel_sidecar_heartbeat(
                    db,
                    bridge_id="hermes-managed-host-win32:test-host",
                    agent_id=agent_id,
                    machine_id="win32:test-host",
                    runtime="hermes",
                    now=_now(),
                )
                await db.commit()
                row = await (
                    await db.execute(
                        "SELECT superseded_by FROM bridge_instances WHERE id = ?",
                        ("wrapper-child-1",),
                    )
                ).fetchone()
                live = await _has_live_channel_sidecar(db, agent_id)
                return (row["superseded_by"] if row else "MISSING"), live
            finally:
                await db.close()

        sup, live = _run(scenario())
        self.assertIn(
            sup,
            ("", None),
            f"wrapper-child must NOT be superseded by a channel-sidecar claim heartbeat; got {sup!r}",
        )
        self.assertTrue(live, "the sidecar heartbeat must register a live deliverability signal")

    def test_wrapper_child_does_not_supersede_a_STALE_channel_sidecar(self):
        """Regression (operator-reported 2026-05-31, sc-claude): the
        complementary-pair protection must be ABSOLUTE — a managed agent's
        channel-sidecar and its visible-TUI managed-wrapper-child coexist and
        must never supersede each other, EVEN when the sidecar's heartbeat is
        briefly stale during managed-PTY churn. Previously the 5-min-stale clause
        was OR'd BEFORE the carve-out, so a stale sidecar got superseded by the
        wrapper-child registration -> the still-live sidecar's claims were then
        permanently blocked and delivery silently stalled."""
        agent_id = "gov-tui"
        self._insert_managed_hermes_agent(agent_id)

        async def scenario():
            db = await api_v2.get_db()
            try:
                # Channel-sidecar bridge whose last_seen is far past the 5-min
                # stale window (simulating PTY churn / a brief sidecar gap).
                await db.execute(
                    """
                    INSERT INTO bridge_instances (id, agent_id, machine_id, runtime,
                        session_mode, session_handle, terminal_id, bridge_kind,
                        registered_at, last_seen, superseded_by, superseded_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "hermes-managed-host-win32:test-host",
                        agent_id,
                        "win32:test-host",
                        "hermes",
                        "managed",
                        "",
                        "",
                        "channel-sidecar",
                        "2020-01-01T00:00:00Z",
                        "2020-01-01T00:00:00Z",
                        "",
                        None,
                    ),
                )
                await db.commit()
                await _record_bridge_registration(
                    db,
                    bridge_id="wrapper-child-1",
                    agent_id=agent_id,
                    machine_id="win32:test-host",
                    runtime="hermes",
                    session_mode="managed",
                    session_handle="",
                    terminal_id="term-1",
                    managed_wrapper_child=True,
                    now=_now(),
                )
                await db.commit()
                row = await (
                    await db.execute(
                        "SELECT superseded_by FROM bridge_instances WHERE id = ?",
                        ("hermes-managed-host-win32:test-host",),
                    )
                ).fetchone()
                return row["superseded_by"] if row else "MISSING"
            finally:
                await db.close()

        sup = _run(scenario())
        self.assertIn(
            sup,
            ("", None),
            f"a STALE complementary channel-sidecar must NOT be superseded by the "
            f"wrapper-child registration; superseded_by={sup!r}",
        )

    # -- Fix A: routing on a live channel-sidecar (flag-independent) ---------

    def test_managed_hermes_routes_to_channel_when_live_sidecar_exists_without_flag(self):
        agent_id = "gov-tui"
        # No channelEnabled flag — the visible-TUI model never sets it.
        self._insert_managed_hermes_agent(agent_id, channel_enabled=False)

        async def scenario():
            db = await api_v2.get_db()
            try:
                now = _now()
                # A live channel-sidecar bridge (delivery loop polling).
                await db.execute(
                    """
                    INSERT INTO bridge_instances (id, agent_id, machine_id, runtime,
                        session_mode, session_handle, terminal_id, bridge_kind,
                        registered_at, last_seen, superseded_by, superseded_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "hermes-managed-host-win32:test-host",
                        agent_id,
                        "win32:test-host",
                        "hermes",
                        "managed",
                        "",
                        "",
                        "channel-sidecar",
                        now,
                        now,
                        "",
                        None,
                    ),
                )
                # A queued managed run for the agent.
                await db.execute(
                    """
                    INSERT INTO dispatch_runs (id, from_agent, target_agent, runtime, status,
                        execution_mode, dispatch_mode, requested_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    ("run-A", "comms-tech-lead", agent_id, "hermes", "queued", "managed", "start_if_possible", now),
                )
                await db.commit()

                await _apply_channel_routing_to_claude_runs(
                    db, [{"runId": "run-A"}], settings={"managed_via_wrapper": ["codex"]}
                )
                await db.commit()
                row = await (
                    await db.execute(
                        "SELECT execution_mode FROM dispatch_runs WHERE id = ?", ("run-A",)
                    )
                ).fetchone()
                return row["execution_mode"] if row else "MISSING"
            finally:
                await db.close()

        mode = _run(scenario())
        self.assertEqual(
            mode,
            "channel",
            "a managed hermes run must route to 'channel' when a live channel-sidecar exists (flag-independent)",
        )

    def test_managed_hermes_stays_managed_without_flag_and_without_live_sidecar(self):
        """Guard: no false channel route when there is neither the flag nor a
        live sidecar — preserves the prior 'no silent channel claim' contract."""
        agent_id = "gov-tui"
        self._insert_managed_hermes_agent(agent_id, channel_enabled=False)

        async def scenario():
            db = await api_v2.get_db()
            try:
                now = _now()
                await db.execute(
                    """
                    INSERT INTO dispatch_runs (id, from_agent, target_agent, runtime, status,
                        execution_mode, dispatch_mode, requested_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    ("run-B", "comms-tech-lead", agent_id, "hermes", "queued", "managed", "start_if_possible", now),
                )
                await db.commit()
                await _apply_channel_routing_to_claude_runs(
                    db, [{"runId": "run-B"}], settings={"managed_via_wrapper": ["codex"]}
                )
                await db.commit()
                row = await (
                    await db.execute(
                        "SELECT execution_mode FROM dispatch_runs WHERE id = ?", ("run-B",)
                    )
                ).fetchone()
                return row["execution_mode"] if row else "MISSING"
            finally:
                await db.close()

        mode = _run(scenario())
        self.assertEqual(mode, "managed", "no flag + no live sidecar must NOT route to channel")


if __name__ == "__main__":
    unittest.main()
