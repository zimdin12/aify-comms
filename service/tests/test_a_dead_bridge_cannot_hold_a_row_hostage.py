"""Supersession compared start times and never asked whether the holder was alive.

MEASURED 2026-09-03, on the operator's own host, and it blocked the whole fleet. An aify-env was
restarted; the new instance claimed the environment row and then exited; the surviving daemon beat
every 30 seconds for twenty minutes and was refused every time with "an existing bridge started
later than this one". `/spawn` answered 409 telling the operator to start a claimer -- which was
already running, beating, and being turned away.

THE HOLE. Arbitration asked only "who started later". A bridge that started later and then died kept
the row for ever: nothing older could take it, and only something started even LATER could. There is
no way out from inside: every restart of the surviving host produces a bridge whose start time is
newer, but the operator has no reason to know that is what is needed, and the message told them the
opposite.

WHY LIVENESS IS THE RIGHT TEST AND NOT A NEW ONE. `bridgeLastSeen` is the liveness fact this service
already keeps, and `/spawn` already gates on it through `environment_has_live_bridge`. Arbitration
ignoring it was an inconsistency between two questions about the same row: the endpoint would not
have let the stale incumbent claim anything either. Using the SAME window means the two answers
cannot disagree.

IT CAN ONLY ADMIT, NEVER REFUSE. The check skips the refusal branch; it never adds one. By the time
it applies, the incumbent has been silent longer than any spawn would have waited for it.
"""

from __future__ import annotations

from service.tests._base import FastApiTestCase


class ADeadBridgeCannotHoldARowHostageTests(FastApiTestCase):
    DB_NAME = "aify-test-bridge-arbitration.db"
    ENV = "windows:arbitration-host:default"

    def _beat(self, bridge_id, started_at, **overrides):
        body = {
            "id": self.ENV, "kind": "windows", "os": "windows",
            "machineId": "win32:arbitration-host",
            "runtimes": [{"runtime": "pi", "available": True}],
            "bridgeId": bridge_id,
            "metadata": {"bridgeStartedAt": started_at},
        }
        body.update(overrides)
        return self._client.post("/api/v1/environments/heartbeat", json=body)

    def _row(self):
        listed = self._client.get("/api/v1/environments").json()["environments"]
        return [item for item in listed if item["id"] == self.ENV][0]

    def _backdate_last_seen(self, stamp):
        """Time passing, expressed the only way a test can. A caller cannot set `bridgeLastSeen` --
        the handler stamps it -- so a bridge beats (stamping now) and the stored value is moved
        back."""
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                import json as _json
                row = await (await db.execute(
                    "SELECT metadata FROM environments WHERE id = ?", (self.ENV,),
                )).fetchone()
                metadata = _json.loads(row["metadata"] or "{}")
                metadata["bridgeLastSeen"] = stamp
                await db.execute(
                    "UPDATE environments SET metadata = ? WHERE id = ?",
                    (_json.dumps(metadata), self.ENV),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def test_A_LIVE_BRIDGE_TAKES_THE_ROW_FROM_A_DEAD_ONE_THAT_STARTED_LATER(self):
        """THE DEFECT, verbatim. The incumbent started later and has been silent for a day."""
        self._beat("bridge-dead", "2026-09-03T04:01:43Z")
        self._backdate_last_seen("2026-09-02T04:01:43Z")

        answer = self._beat("bridge-live", "2026-09-03T03:43:48Z").json()
        self.assertTrue(answer["claimer"]["accepted"],
                        "a live bridge was refused by one that has been gone for a day")
        self.assertEqual(self._row()["bridgeId"], "bridge-live")

    def test_A_LIVE_INCUMBENT_STILL_WINS_ON_START_TIME(self):
        """THE CONTROL, and the more important half. Without it the fix reads as "the last beat
        always wins", which would let two live bridges flap the row between them for ever -- the
        collision the environment tier exists to end."""
        self._beat("bridge-newer", "2026-09-03T04:01:43Z")

        answer = self._beat("bridge-older", "2026-09-03T03:43:48Z").json()
        self.assertFalse(answer["claimer"]["accepted"],
                         "an older bridge took the row from a live, newer one")
        self.assertEqual(answer["claimer"]["bridgeId"], "bridge-newer")
        self.assertEqual(self._row()["bridgeId"], "bridge-newer")

    def test_a_genuinely_newer_bridge_still_supersedes_a_live_older_one(self):
        """Restarting a host must still take the row. This is the ordinary path and the fix must not
        touch it."""
        self._beat("bridge-first", "2026-09-03T03:00:00Z")
        answer = self._beat("bridge-second", "2026-09-03T04:00:00Z").json()
        self.assertTrue(answer["claimer"]["accepted"])
        self.assertEqual(self._row()["bridgeId"], "bridge-second")

    def test_the_stale_incumbent_and_the_spawn_gate_AGREE(self):
        """The reason liveness is the right test rather than a new rule of its own: `/spawn` already
        refuses on behalf of a stale bridge. Arbitration keeping it as the holder meant one endpoint
        treated the row as claimed while the other treated it as unclaimable -- two answers about one
        row, and the operator was reading both."""
        self._beat("bridge-dead", "2026-09-03T04:01:43Z")
        self._backdate_last_seen("2026-09-02T04:01:43Z")
        self.assertFalse(self._row()["spawnClaim"]["canClaim"],
                         "the spawn gate considered the stale incumbent live, so the premise is wrong")

        self._beat("bridge-live", "2026-09-03T03:43:48Z")
        self.assertTrue(self._row()["spawnClaim"]["canClaim"],
                        "the row is claimed by a live bridge and spawns must now be accepted")

    def test_a_beat_with_no_start_time_still_cannot_take_a_LIVE_row(self):
        """The 2026-09-02 shape, which must stay refused: a caller whose start time is missing has
        nothing to arbitrate with, and letting it win would make an unparseable beat the strongest
        possible claim."""
        self._beat("bridge-live-incumbent", "2026-09-03T04:00:00Z")
        answer = self._client.post("/api/v1/environments/heartbeat", json={
            "id": self.ENV, "kind": "windows", "os": "windows",
            "machineId": "win32:arbitration-host",
            "runtimes": [{"runtime": "pi", "available": True}],
            "bridgeId": "bridge-no-start",
        }).json()
        self.assertFalse(answer["claimer"]["accepted"])
        self.assertEqual(self._row()["bridgeId"], "bridge-live-incumbent")
