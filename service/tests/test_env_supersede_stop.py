"""Regression: a `server:superseded-bridge` stop must never terminate the
environment's CURRENT live owner.

Incident (2026-07-03): the env bridge exited on launch. Root cause was in the
env-control claim, not the reaper. When a bridge registered and became the
current owner, a `server:superseded-bridge` stop targeting that same bridge id
could exist/arrive at-or-after the bridge started. The claim's stale guard only
voided stops whose `requested_at < bridgeStartedAt`, so a supersede-stop created
AFTER the bridge became current slipped through: the current owner claimed its
own stop and self-terminated. These accumulated unbounded (99 pending for one
env) because superseded predecessors never came back to claim them.

Fix (service/routers/api_v2.py):
  * claim voids any `server:superseded-bridge` stop that targets the CURRENT
    owner (self-contradictory: you cannot be both live-current AND superseded),
    independent of timestamps;
  * a TTL drain on the next registration keeps the table from growing
    one-row-per-restart.

These tests pin: the current owner is NOT stopped by a self-targeted
supersede-stop (and it gets voided), a supersede-stop for a DIFFERENT
(non-current) bridge is still delivered, and an OPERATOR env-stop for the
current owner is still delivered.
"""
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from service.tests._base import FastApiTestCase


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class EnvSupersedeStopTests(FastApiTestCase):
    ENV_ID = "wsl:testhost:default"
    MACHINE = "wsl:testhost"

    def _make_current_owner(self, bridge_id, started_at):
        """Create the env row and pin `bridge_id` as the current owner with a
        known bridgeStartedAt (what the claim guard compares against)."""
        # Heartbeat creates the environment row.
        res = self.client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV_ID, "machineId": self.MACHINE, "bridgeId": bridge_id,
            "status": "online",
        })
        self.assertEqual(res.status_code, 200, res.text)
        # Pin the exact owner + start time deterministically.
        con = sqlite3.connect(str(self._db_path))
        con.execute(
            "UPDATE environments SET bridge_id = ?, metadata = ? WHERE id = ?",
            (bridge_id, f'{{"bridgeStartedAt": "{_iso(started_at)}"}}', self.ENV_ID),
        )
        con.commit()
        con.close()

    def _insert_control(self, *, bridge_id, requested_by, requested_at, action="stop"):
        cid = f"envctl-{uuid.uuid4().hex}"
        con = sqlite3.connect(str(self._db_path))
        con.execute(
            "INSERT INTO environment_controls "
            "(id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, self.ENV_ID, bridge_id, self.MACHINE, action, "pending", requested_by, _iso(requested_at)),
        )
        con.commit()
        con.close()
        return cid

    def _claim(self, bridge_id):
        res = self.client.post("/api/v1/environments/controls/claim", json={
            "environmentId": self.ENV_ID, "bridgeId": bridge_id,
            "machineId": self.MACHINE, "waitMs": 0,
        })
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def _control_status(self, cid):
        con = sqlite3.connect(str(self._db_path))
        row = con.execute(
            "SELECT status FROM environment_controls WHERE id = ?", (cid,)
        ).fetchone()
        con.close()
        return row[0] if row else None

    def test_supersede_stop_targeting_current_owner_is_voided(self):
        # The race: a supersede-stop for the CURRENT owner, created AFTER it started.
        now = datetime.now(timezone.utc)
        bridge = "bridge-current"
        self._make_current_owner(bridge, started_at=now - timedelta(seconds=30))
        cid = self._insert_control(
            bridge_id=bridge, requested_by="server:superseded-bridge",
            requested_at=now,  # AFTER bridge start — the old timestamp guard would miss this
        )
        body = self._claim(bridge)
        self.assertIsNone(body.get("control"),
                          "current owner must NOT be handed a supersede-stop for itself")
        self.assertEqual(self._control_status(cid), "failed",
                         "the self-contradictory supersede-stop must be voided")

    def test_supersede_stop_for_a_different_bridge_is_still_delivered(self):
        # Legit supersession: the stop targets a bridge that is NOT current.
        now = datetime.now(timezone.utc)
        self._make_current_owner("bridge-new", started_at=now - timedelta(seconds=5))
        old_bridge = "bridge-old"
        cid = self._insert_control(
            bridge_id=old_bridge, requested_by="server:superseded-bridge",
            requested_at=now - timedelta(seconds=10),
        )
        body = self._claim(old_bridge)
        control = body.get("control")
        self.assertIsNotNone(control, "a genuinely superseded bridge must still be told to stop")
        self.assertEqual(control.get("action"), "stop")
        self.assertEqual(self._control_status(cid), "claimed")

    def test_operator_stop_for_current_owner_is_still_delivered(self):
        # An operator env-stop (not a supersede) must still stop the current owner.
        now = datetime.now(timezone.utc)
        bridge = "bridge-current"
        self._make_current_owner(bridge, started_at=now - timedelta(seconds=30))
        cid = self._insert_control(
            bridge_id=bridge, requested_by="dashboard", requested_at=now,
        )
        body = self._claim(bridge)
        control = body.get("control")
        self.assertIsNotNone(control, "an operator stop must still stop the current owner")
        self.assertEqual(control.get("action"), "stop")
        self.assertEqual(self._control_status(cid), "claimed")
