"""A host's heartbeat names the terminals it holds, and the rest in its environment end.

WHAT WAS MEASURED (2026-09-14, the operator's host). After an aify-env restart the service was never
told which terminals had gone: the new daemon starts with an empty handle book and says nothing about
its predecessor's rows, so they stayed attached until the ghost reaper -- about 180s of sidecar
window plus a 60s sweep -- and a detached hermes gateway's `server.js` child vetoes that reaper for
ever, so a row whose exit was lost could stay live indefinitely.

aify-env now sends `metadata.heldTerminals` on every heartbeat. On an ACCEPTED beat carrying it as a
list, every terminal row in that environment the host had confirmed started, not in the list, and
not touched within a short grace, is closed through the same close-out a terminal-ending output
takes.

WHAT THESE PIN:
  - absent is not empty: an older aify-env sends no list and nothing may end;
  - only the bridge that owns the row after arbitration can end anything -- a superseded or refused
    beat carries another host's view, not this one's;
  - `starting` rows and virtual-rpc consoles are never the host's to hold, so never ended by it;
  - an online beat leaves a row touched inside the grace alone (a start confirmed between building
    the beat and it arriving); an offline beat does not wait, because that host is going.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.reconcilers.host_held_terminals import (
    HELD_TERMINALS_GRACE_SECONDS,
    PEER_SILENCE_SECONDS,
    terminals_the_host_no_longer_holds,
)
from service.tests._base import FastApiTestCase

ENV = "linux:held-host:default"
OTHER_ENV = "linux:other-host:default"
BRIDGE = "bridge-held"
LONG_AGO = "2026-09-03T00:00:00Z"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TheSelectionIsPure(FastApiTestCase):
    """The decision, with no database: rows and a list in, terminal ids out."""

    NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()

    def _row(self, terminal_id, status="attached", updated_at=LONG_AGO, command="claude-aify", bridge_id=BRIDGE):
        return {"id": terminal_id, "status": status, "updated_at": updated_at, "command": command,
                "bridge_id": bridge_id}

    def _select(self, rows, held, grace=HELD_TERMINALS_GRACE_SECONDS):
        return terminals_the_host_no_longer_holds(rows, held, now_epoch=self.NOW, grace_seconds=grace,
                                                  bridge_id=BRIDGE)

    def test_ANOTHER_BRIDGES_terminal_needs_silence_not_just_absence(self):
        """A live peer daemon on the same environment refreshes its terminals every control pass; this
        host's list cannot name them. Only a peer terminal that has gone silent is over."""
        recent = _iso(datetime.fromtimestamp(self.NOW - PEER_SILENCE_SECONDS + 5, timezone.utc))
        silent = _iso(datetime.fromtimestamp(self.NOW - PEER_SILENCE_SECONDS - 5, timezone.utc))
        rows = [self._row("t-peer-live", updated_at=recent, bridge_id="bridge-peer"),
                self._row("t-peer-dead", updated_at=silent, bridge_id="bridge-peer"),
                self._row("t-own", updated_at=recent)]
        self.assertEqual(self._select(rows, []), ["t-peer-dead", "t-own"])
        # AND NO GRACE (an offline beat) DOES NOT WAIVE IT: going offline ends this host's own work only.
        self.assertEqual(self._select(rows, [], grace=0), ["t-peer-dead", "t-own"])

    def test_a_confirmed_terminal_the_host_does_not_name_is_selected(self):
        rows = [self._row("t-attached"), self._row("t-running", "running"), self._row("t-idle", "idle"),
                self._row("t-active", "active")]
        self.assertEqual(self._select(rows, []), ["t-attached", "t-running", "t-idle", "t-active"])

    def test_a_held_terminal_is_not(self):
        self.assertEqual(self._select([self._row("t-1"), self._row("t-2")], ["t-2"]), ["t-1"])

    def test_a_STARTING_terminal_is_not_the_hosts_to_have_confirmed(self):
        self.assertEqual(self._select([self._row("t-1", "starting")], []), [])

    def test_an_already_ended_terminal_is_left_as_it_is(self):
        rows = [self._row(f"t-{status}", status) for status in ("stopped", "failed", "lost", "stopping")]
        self.assertEqual(self._select(rows, []), [])

    def test_a_virtual_rpc_console_has_no_host_process_to_hold(self):
        virtual = sorted(VIRTUAL_RPC_COMMAND_SET)[0]
        rows = [self._row("t-virtual", command=virtual), self._row("vterm_abc")]
        self.assertEqual(self._select(rows, []), [])

    def test_a_row_touched_inside_the_grace_is_left_alone(self):
        fresh = _iso(datetime.fromtimestamp(self.NOW, timezone.utc) - timedelta(seconds=5))
        edge = _iso(datetime.fromtimestamp(self.NOW, timezone.utc)
                    - timedelta(seconds=HELD_TERMINALS_GRACE_SECONDS + 1))
        rows = [self._row("t-fresh", updated_at=fresh), self._row("t-edge", updated_at=edge)]
        self.assertEqual(self._select(rows, []), ["t-edge"])

    def test_no_grace_selects_a_fresh_row_too(self):
        fresh = _iso(datetime.fromtimestamp(self.NOW, timezone.utc) - timedelta(seconds=1))
        self.assertEqual(self._select([self._row("t-fresh", updated_at=fresh)], [], grace=0), ["t-fresh"])

    def test_an_unreadable_timestamp_is_not_evidence_of_age(self):
        self.assertEqual(self._select([self._row("t-1", updated_at="not a time")], []), [])


class AHeartbeatEndsTheTerminalsItNoLongerHolds(FastApiTestCase):
    DB_NAME = "aify-test-host-held-terminals.db"

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _beat(self, *, bridge=BRIDGE, started=LONG_AGO, status=None, env=ENV, **metadata):
        body = {"id": env, "machineId": env.rsplit(":", 1)[0], "os": "linux", "kind": "linux",
                "bridgeId": bridge, "metadata": {"bridgeStartedAt": started, **metadata}}
        if status:
            body["status"] = status
        response = self._client.post("/api/v1/environments/heartbeat", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _seed(self, terminal_id, *, status="attached", updated_at=LONG_AGO, env=ENV,
              command="claude-aify --aify-agent a1", bridge="old-bridge"):
        from service.db import get_db

        session_id = f"sess-{terminal_id}"
        agent_id = f"agent-{terminal_id}"

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agents (id, role, name, runtime, session_mode, registered_at, "
                    "last_seen) VALUES (?,?,?,?,?,?,?)",
                    (agent_id, "coder", agent_id, "claude-code", "managed", LONG_AGO, LONG_AGO),
                )
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                    "owner_mode, terminal_id, terminal_status, started_at, last_seen, "
                    "spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (session_id, agent_id, env, "claude-code", "running", "console",
                     terminal_id, status, LONG_AGO, LONG_AGO, None, None),
                )
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, "
                    "runtime, bridge_id, command, argv, workspace, status, output, "
                    "error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (terminal_id, agent_id, session_id, env, "claude-code", bridge,
                     command, json.dumps(command.split()), "/work", status, "", "",
                     LONG_AGO, updated_at),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _terminal(self, terminal_id) -> dict:
        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT t.status, t.stopped_at, t.error, s.terminal_status AS session_status "
                    "FROM terminal_sessions t JOIN agent_sessions s ON s.id = t.session_id "
                    "WHERE t.id = ?", (terminal_id,))).fetchone()
                events = await (await db.execute(
                    "SELECT event_type FROM terminal_events WHERE terminal_id = ?", (terminal_id,))).fetchall()
                return {**dict(row), "events": [event["event_type"] for event in events]}
            finally:
                await db.close()

        return asyncio.run(go())

    # ── the positive case, and its controls ──────────────────────────────────────────────────

    def test_a_confirmed_terminal_the_host_does_not_hold_ENDS(self):
        self._beat()
        self._seed("t-gone")
        self._seed("t-held")
        self._beat(heldTerminals=["t-held"])

        gone = self._terminal("t-gone")
        self.assertEqual(gone["status"], "stopped", gone)
        self.assertTrue(gone["stopped_at"], "an ended terminal carries no stop time")
        self.assertIn("no longer holds", gone["error"], "the row does not say why it ended")
        self.assertEqual(gone["session_status"], "stopped", "the session still mirrors a live terminal")
        # FOUND BY THE INTEGRATION RUN, not by this file: the event was appended after the close-out's
        # own commit and never committed, so the row said why and the terminal's history did not.
        self.assertIn("host_no_longer_holds_terminal", gone["events"],
                      "the terminal's event history does not record why it ended")
        self.assertEqual(self._terminal("t-held")["status"], "attached", "a HELD terminal was ended")

    def test_ABSENT_is_not_empty__an_older_host_ends_nothing(self):
        self._beat()
        self._seed("t-1")
        self._beat()
        self.assertEqual(self._terminal("t-1")["status"], "attached")

    def test_a_list_that_is_not_a_list_ends_nothing(self):
        self._beat()
        self._seed("t-1")
        self._beat(heldTerminals="t-2")
        self.assertEqual(self._terminal("t-1")["status"], "attached")

    def test_a_starting_terminal_and_another_environments_terminal_are_left_alone(self):
        self._beat()
        self._beat(env=OTHER_ENV, bridge="bridge-other")
        self._seed("t-starting", status="starting")
        self._seed("t-elsewhere", env=OTHER_ENV)
        self._beat(heldTerminals=[])
        self.assertEqual(self._terminal("t-starting")["status"], "starting")
        self.assertEqual(self._terminal("t-elsewhere")["status"], "attached")

    def test_an_online_beat_waits_out_the_grace_and_an_offline_beat_does_not(self):
        self._beat()
        self._seed("t-fresh", updated_at=_iso(datetime.now(timezone.utc)), bridge=BRIDGE)
        self._beat(heldTerminals=[])
        self.assertEqual(self._terminal("t-fresh")["status"], "attached",
                         "a start confirmed just before the beat arrived was ended")
        self._beat(heldTerminals=[], status="offline")
        self.assertEqual(self._terminal("t-fresh")["status"], "stopped",
                         "a host going offline holding nothing left its terminal attached")

    # ── only the owner of the row may end anything ───────────────────────────────────────────

    def test_a_REFUSED_beat_from_an_older_bridge_ends_nothing(self):
        later = _iso(datetime.now(timezone.utc) - timedelta(minutes=1))
        self._beat(bridge="bridge-current", started=later)
        self._seed("t-1")
        refused = self._beat(bridge="bridge-older", started=LONG_AGO, heldTerminals=[])
        self.assertIs((refused.get("claimer") or {}).get("accepted"), False,
                      "the older bridge was not refused, so this proves nothing")
        self.assertEqual(self._terminal("t-1")["status"], "attached")

    def test_an_offline_beat_from_a_bridge_that_does_not_own_the_row_ends_nothing(self):
        later = _iso(datetime.now(timezone.utc) - timedelta(minutes=1))
        self._beat(bridge="bridge-current", started=later)
        self._seed("t-1")
        refused = self._beat(bridge="bridge-gone", started=LONG_AGO, status="offline", heldTerminals=[])
        self.assertIs((refused.get("claimer") or {}).get("accepted"), False)
        self.assertEqual(self._terminal("t-1")["status"], "attached")

    def test_a_NEW_daemon_ends_its_predecessors_terminals_on_its_first_beat(self):
        """The restart: the old daemon's bridge owns the row, a later-started one takes it over."""
        self._beat(bridge="bridge-old", started=LONG_AGO)
        self._seed("t-predecessor")
        new_start = _iso(datetime.now(timezone.utc) - timedelta(seconds=5))
        answer = self._beat(bridge="bridge-new", started=new_start, heldTerminals=[])
        self.assertIs((answer.get("claimer") or {}).get("accepted"), True, answer)
        self.assertEqual(self._terminal("t-predecessor")["status"], "stopped")

    def test_a_LIVE_PEER_daemons_terminal_survives_the_accepted_beat_of_another(self):
        """THE REVIEW'S REPRODUCTION, 2026-09-15: daemon A runs a worker, daemon B starts later on the
        same environment and takes the row. A's terminal is still refreshed by A's liveness frames, so
        B's list -- which cannot name it -- must not end it."""
        self._beat(bridge="bridge-a", started=LONG_AGO)
        self._seed("t-a-live", bridge="bridge-a",
                   updated_at=_iso(datetime.now(timezone.utc) - timedelta(seconds=30)))
        b_start = _iso(datetime.now(timezone.utc) - timedelta(seconds=5))
        answer = self._beat(bridge="bridge-b", started=b_start, heldTerminals=[])
        self.assertIs((answer.get("claimer") or {}).get("accepted"), True, "B did not take the row, so this proves nothing")
        self.assertEqual(self._terminal("t-a-live")["status"], "attached", "a live peer's terminal was ended")
        # AND B GOING OFFLINE DOES NOT REACH IT EITHER.
        self._beat(bridge="bridge-b", started=b_start, heldTerminals=[], status="offline")
        self.assertEqual(self._terminal("t-a-live")["status"], "attached", "an offline beat ended a peer's live terminal")

    def test_the_list_is_not_kept_in_the_environments_stored_metadata(self):
        """An observation of one beat, not configuration: kept, it would read as current long after
        the host that sent it had gone."""
        self._beat(heldTerminals=["t-9"])
        listed = self._client.get("/api/v1/environments").json()["environments"]
        row = next(env for env in listed if env["id"] == ENV)
        self.assertNotIn("heldTerminals", row.get("metadata") or {})
