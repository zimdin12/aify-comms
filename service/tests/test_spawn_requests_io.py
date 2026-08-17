"""Claiming a spawn request — the moment a queued agent becomes one bridge's job to start.

`service/api_core/spawn_requests_io.py` is named by no test file. One immediate transaction decides
which bridge starts a queued agent, plus the two serializers that shape what the bridge and the
dashboard read.

TWO BRIDGES MUST NOT CLAIM THE SAME REQUEST, because the outcome is two live processes for one agent
id: both register, each supersedes the other, and the fleet has an agent that keeps reaping itself.
The guard is the environment's CURRENT bridge — an environment that has been taken over by a newer
bridge refuses the old one, and refuses it with an ANSWER rather than silence.

THAT ANSWER MATTERS TO THE LONG POLL. A blocked claim returns `spawnRequest: None` alongside
`blockedBy`, and `spawn_request_is_empty` reads that as NOT empty — so the poll returns at once
instead of holding a superseded bridge open for its full wait. Nothing to claim returns the same
`spawnRequest: None` WITHOUT `blockedBy`, and that one is empty and waits. The two shapes differ by
one key and mean opposite things.

The serializers are tested for their DEFAULTS as much as their names: a spawn request with no mode is
`managed-warm`, no resume policy is `native_first`, no role is `coder`. Those defaults are what a
bridge acts on when a row was written by an older writer.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

import aiosqlite
from fastapi import HTTPException

from service.api_core.spawn_requests_io import (
    _claim_spawn_request_once,
    _spawn_request_to_dict,
    _spawn_spec_to_dict,
)
from service.models import SpawnRequestClaim
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

ENV = "env-1"
BRIDGE = "bridge-current"
OLD_BRIDGE = "bridge-superseded"


class RecordingWs:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def broadcast(self, event: str, payload: dict) -> None:
        self.sent.append((event, payload))


def request_with(ws) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws)))


class SpawnRequestsIoTestCase(FastApiTestCase):
    DB_NAME = "aify-spawn-requests-io-test.db"

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                return [dict(row) for row in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed_env(self, env_id: str = ENV, *, bridge_id: str = "",
                  last_seen: str = "2020-01-01T00:00:00Z") -> None:
        self._write(
            "INSERT INTO environments (id, label, machine_id, os, kind, bridge_id, registered_at,"
            " last_seen) VALUES (?,?,?,?,?,?,?,?)",
            (env_id, "lab", "linux:test", "linux", "wsl", bridge_id,
             "2026-08-17T00:00:00Z", last_seen),
        )

    def _seed_spec(self, spec_id: str, *, agent_id: str = "spawned-1", **overrides) -> None:
        values = {
            "runtime": "claude-code", "workspace": "/w", "model": "", "profile": "",
            "mode": "", "system_prompt": "", "standing_instructions": "",
            "env_vars": "", "channel_ids": "", "budget_policy": "", "context_policy": "",
            "restart_policy": "", "metadata": "",
        }
        values.update(overrides)
        self._write(
            "INSERT INTO spawn_specs (id, agent_id, environment_id, runtime, workspace, model,"
            " profile, mode, system_prompt, standing_instructions, env_vars, channel_ids,"
            " budget_policy, context_policy, restart_policy, metadata, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (spec_id, agent_id, ENV, values["runtime"], values["workspace"], values["model"],
             values["profile"], values["mode"], values["system_prompt"],
             values["standing_instructions"], values["env_vars"], values["channel_ids"],
             values["budget_policy"], values["context_policy"], values["restart_policy"],
             values["metadata"], "2026-08-17T00:00:00Z", "2026-08-17T00:00:00Z"),
        )

    def _seed_request(self, request_id: str, *, spec_id: str = "spec-1", status: str = "queued",
                      env_id: str = ENV, created_at: str = "2026-08-17T00:00:00Z",
                      agent_id: str = "spawned-1", **overrides) -> None:
        values = {"role": "", "name": "", "runtime": "claude-code", "workspace": "",
                  "workspace_root": "", "initial_message": "", "priority": "", "subject": "",
                  "mode": "", "resume_policy": ""}
        values.update(overrides)
        self._write(
            "INSERT INTO spawn_requests (id, spawn_spec_id, created_by, environment_id, agent_id,"
            " role, name, runtime, workspace, workspace_root, initial_message, priority, subject,"
            " mode, resume_policy, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (request_id, spec_id, "dashboard", env_id, agent_id, values["role"], values["name"],
             values["runtime"], values["workspace"], values["workspace_root"],
             values["initial_message"], values["priority"], values["subject"], values["mode"],
             values["resume_policy"], status, created_at, created_at),
        )

    def _claim(self, *, bridge_id: str = BRIDGE, env_id: str = ENV, machine_id=None, ws=None):
        req = SpawnRequestClaim(environmentId=env_id, bridgeId=bridge_id, machineId=machine_id)
        return asyncio.run(_claim_spawn_request_once(req, request_with(ws)))


class ClaimTests(SpawnRequestsIoTestCase):
    def test_a_QUEUED_request_is_claimed_by_the_polling_bridge(self):
        self._seed_env()
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        result = self._claim()
        self.assertEqual(result["spawnRequest"]["id"], "sr-1")
        row = self._rows("SELECT * FROM spawn_requests WHERE id = 'sr-1'")[0]
        self.assertEqual(row["status"], "claimed")
        self.assertEqual(row["claimed_by_bridge_id"], BRIDGE)

    def test_a_request_is_not_claimed_TWICE(self):
        """Two live processes for one agent id: both register, each supersedes the other, and the
        fleet gets an agent that keeps reaping itself.

        What stops it is the SELECT-then-UPDATE running inside one `BEGIN IMMEDIATE`, which holds
        the write lock for the whole claim. The `AND status = 'queued'` on the UPDATE is a second
        belt on the same trousers: removing it survives this suite, and would survive a concurrent
        one too, because the transaction already serialises the two claimers. Recorded rather than
        forced — a test that could distinguish it would have to defeat the lock that makes the
        guarantee."""
        self._seed_env()
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        self.assertIsNotNone(self._claim()["spawnRequest"])
        self.assertIsNone(self._claim()["spawnRequest"])

    def test_only_QUEUED_requests_are_claimable(self):
        """Anything else already belongs to a bridge or is over. Re-claiming a running one starts a
        second process for an agent that is already up."""
        self._seed_env()
        self._seed_spec("spec-1")
        for status in ("claimed", "starting", "running", "failed", "cancelled"):
            with self.subTest(status=status):
                self._seed_request(f"sr-{status}", status=status)
                self.assertIsNone(self._claim()["spawnRequest"])

    def test_the_OLDEST_queued_request_is_claimed_first(self):
        """A queue. Claiming the newest would starve whatever has been waiting longest, and spawn
        requests are how an operator asks for a team."""
        self._seed_env()
        self._seed_spec("spec-1")
        self._seed_request("sr-new", created_at="2026-08-17T10:00:00Z")
        self._seed_request("sr-old", created_at="2026-08-17T09:00:00Z")
        self.assertEqual(self._claim()["spawnRequest"]["id"], "sr-old")

    def test_a_request_for_ANOTHER_ENVIRONMENT_is_not_claimed(self):
        """Environment scope is the whole point: a bridge can only start processes on its own
        host."""
        self._seed_env()
        self._seed_env("env-2")
        self._seed_spec("spec-1")
        self._seed_request("sr-1", env_id="env-2")
        self.assertIsNone(self._claim()["spawnRequest"])

    def test_claiming_REFRESHES_the_environments_last_seen(self):
        """A bridge that just claimed work has proved it is alive. Without this an environment could
        be reaped as offline while it is starting the agent it was asked for."""
        self._seed_env(last_seen="2020-01-01T00:00:00Z")
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        self._claim()
        self.assertNotEqual(
            self._rows("SELECT last_seen FROM environments WHERE id = ?", (ENV,))[0]["last_seen"],
            "2020-01-01T00:00:00Z")

    def test_the_claim_is_BROADCAST(self):
        """The dashboard shows the request moving out of the queue. Without the push it sits at
        queued until the next poll, which is when an operator retries a spawn already in progress."""
        self._seed_env()
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        recorder = RecordingWs()
        self._claim(ws=recorder)
        self.assertEqual([event for event, _ in recorder.sent], ["spawn_request_claimed"])
        self.assertEqual(recorder.sent[0][1],
                         {"spawnRequestId": "sr-1", "environmentId": ENV})

    def test_NO_websocket_does_not_stop_the_claim(self):
        """The push is best-effort; the claim is not. A headless deployment must still spawn."""
        self._seed_env()
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        self.assertEqual(self._claim(ws=None)["spawnRequest"]["id"], "sr-1")


class BlockedByTests(SpawnRequestsIoTestCase):
    def test_a_SUPERSEDED_bridge_is_told_WHY_it_got_nothing(self):
        """Not silence. A bridge that polls forever and is never told it has been replaced looks
        exactly like an environment with no work — and the answer names the bridge that took over,
        so the operator reading the payload can see what happened."""
        self._seed_env(bridge_id=BRIDGE)
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        result = self._claim(bridge_id=OLD_BRIDGE)
        self.assertIsNone(result["spawnRequest"])
        self.assertEqual(result["blockedBy"]["reason"], "bridge_not_current")
        self.assertEqual(result["blockedBy"]["currentBridgeId"], BRIDGE)
        self.assertEqual(result["blockedBy"]["bridgeId"], OLD_BRIDGE)

    def test_a_blocked_claim_LEAVES_THE_REQUEST_QUEUED(self):
        self._seed_env(bridge_id=BRIDGE)
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        self._claim(bridge_id=OLD_BRIDGE)
        self.assertEqual(self._rows("SELECT status FROM spawn_requests")[0]["status"], "queued")

    def test_the_CURRENT_bridge_claims_normally(self):
        self._seed_env(bridge_id=BRIDGE)
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        self.assertEqual(self._claim(bridge_id=BRIDGE)["spawnRequest"]["id"], "sr-1")

    def test_an_environment_with_NO_CURRENT_BRIDGE_accepts_any_claimer(self):
        """A freshly registered environment has not named one yet. Refusing here would leave its
        queue unstartable until something else wrote the column."""
        self._seed_env(bridge_id="")
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        self.assertEqual(self._claim(bridge_id="whoever")["spawnRequest"]["id"], "sr-1")

    def test_the_two_EMPTY_shapes_differ_by_exactly_one_key(self):
        """The distinction the long poll reads. Nothing-to-claim is empty and the poll waits;
        blocked is NOT empty and the poll returns at once, so a superseded bridge is not held open
        for its full wait."""
        from service.api_core.claim_emptiness import spawn_request_is_empty

        self._seed_env(bridge_id=BRIDGE)
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        blocked = self._claim(bridge_id=OLD_BRIDGE)
        self.assertIs(spawn_request_is_empty(blocked), False)

        self._write("DELETE FROM spawn_requests", ())
        nothing = self._claim(bridge_id=BRIDGE)
        self.assertNotIn("blockedBy", nothing)
        self.assertIs(spawn_request_is_empty(nothing), True)

    def test_an_UNKNOWN_environment_is_404(self):
        """Unlike a superseded bridge, this is a misconfiguration: no environment by that id has
        ever registered, so there is nothing to wait for."""
        with self.assertRaises(HTTPException) as caught:
            self._claim(env_id="env-nope")
        self.assertEqual(caught.exception.status_code, 404)


class ClaimPayloadTests(SpawnRequestsIoTestCase):
    def test_the_claimed_request_carries_its_SPEC(self):
        """The spec is the whole brief — model, prompt, env vars. A bridge that got the request
        without it would start an agent with none of what the operator configured."""
        self._seed_env()
        self._seed_spec("spec-1", model="opus", system_prompt="be terse")
        self._seed_request("sr-1")
        payload = self._claim()["spawnRequest"]
        self.assertEqual(payload["spawnSpec"]["model"], "opus")
        self.assertEqual(payload["spawnSpec"]["systemPrompt"], "be terse")

    def test_a_request_whose_SPEC_IS_MISSING_omits_the_key_entirely(self):
        """Absent, not None. A bridge checking `"spawnSpec" in payload` and one checking its
        truthiness must reach the same conclusion, and a null under that key reads as "a spec that
        is empty" rather than "no spec"."""
        self._seed_env()
        self._seed_request("sr-1", spec_id="spec-that-does-not-exist")
        payload = self._claim()["spawnRequest"]
        self.assertNotIn("spawnSpec", payload)

    def test_the_payload_reflects_the_CLAIM_that_just_happened(self):
        """It is re-read after the update rather than serialized from the pre-claim row — otherwise
        the bridge is handed a request that still says queued and claimed by nobody."""
        self._seed_env()
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        payload = self._claim()["spawnRequest"]
        self.assertEqual(payload["status"], "claimed")
        self.assertEqual(payload["claimedByBridgeId"], BRIDGE)
        self.assertTrue(payload["claimedAt"])

    def test_the_claiming_MACHINE_is_recorded(self):
        self._seed_env()
        self._seed_spec("spec-1")
        self._seed_request("sr-1")
        payload = self._claim(machine_id="linux:builder")["spawnRequest"]
        self.assertEqual(payload["claimMachineId"], "linux:builder")


class SerializerTests(SpawnRequestsIoTestCase):
    def _request_row(self, request_id: str = "sr-1"):
        return self._rows("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))[0]

    def _spec_row(self, spec_id: str = "spec-1"):
        return self._rows("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))[0]

    def test_a_request_with_nothing_set_gets_the_documented_DEFAULTS(self):
        """These are what a bridge acts on when a row was written by an older writer that did not
        know the column. A blank mode is not "no mode" — it is `managed-warm`."""
        self._seed_request("sr-1")
        payload = _spawn_request_to_dict(self._request_row())
        self.assertEqual(payload["role"], "coder")
        self.assertEqual(payload["mode"], "managed-warm")
        self.assertEqual(payload["resumePolicy"], "native_first")
        self.assertEqual(payload["priority"], "normal")
        self.assertEqual(payload["status"], "queued")

    def test_set_values_are_not_overridden_by_the_defaults(self):
        self._seed_request("sr-1", role="tester", mode="managed-cold",
                           resume_policy="fresh", priority="high")
        payload = _spawn_request_to_dict(self._request_row())
        self.assertEqual(payload["role"], "tester")
        self.assertEqual(payload["mode"], "managed-cold")
        self.assertEqual(payload["resumePolicy"], "fresh")
        self.assertEqual(payload["priority"], "high")

    def test_the_spec_key_is_absent_unless_a_spec_is_PASSED(self):
        self._seed_request("sr-1")
        self.assertNotIn("spawnSpec", _spawn_request_to_dict(self._request_row()))
        self.assertIn("spawnSpec", _spawn_request_to_dict(self._request_row(), {"id": "spec-1"}))

    def test_an_EMPTY_spec_dict_is_still_attached(self):
        """`if spec is not None`, not truthiness. An empty spec is a spec that was found and had
        nothing in it, which is a different answer from one that was never looked up."""
        self._seed_request("sr-1")
        self.assertEqual(_spawn_request_to_dict(self._request_row(), {})["spawnSpec"], {})

    def test_the_SPEC_also_defaults_its_mode(self):
        """The same default, written a second time in the second serializer. Tested separately
        because they are separate literals: a change to one is not a change to the other, and the
        spec's mode is what the bridge actually launches with."""
        self._seed_spec("spec-1", mode="")
        self.assertEqual(_spawn_spec_to_dict(self._spec_row())["mode"], "managed-warm")

    def test_the_specs_JSON_columns_are_PARSED_not_passed_through(self):
        """They cross into a bridge that reads them as objects. Handing over the raw text would make
        every consumer parse it again, and the ones that do not would silently see a string."""
        self._seed_spec("spec-1", env_vars='{"FOO": "bar"}', channel_ids='["general"]',
                        metadata='{"team": "alpha"}')
        payload = _spawn_spec_to_dict(self._spec_row())
        self.assertEqual(payload["envVars"], {"FOO": "bar"})
        self.assertEqual(payload["channelIds"], ["general"])
        self.assertEqual(payload["metadata"], {"team": "alpha"})

    def test_UNPARSEABLE_json_falls_back_to_the_right_EMPTY_SHAPE(self):
        """A dict for the mappings and a list for the ids — not None for both. A bridge iterating
        `channelIds` would crash on None where an empty list is simply nothing to join."""
        self._seed_spec("spec-1", env_vars="{not json", channel_ids="[oops")
        payload = _spawn_spec_to_dict(self._spec_row())
        self.assertEqual(payload["envVars"], {})
        self.assertEqual(payload["channelIds"], [])

    def test_the_specs_instructions_are_renamed_on_the_wire(self):
        """`standing_instructions` becomes `instructions`. Pinned because the two names differ and a
        silent rename leaves a bridge starting agents with no standing instructions."""
        self._seed_spec("spec-1", standing_instructions="always run the suite")
        payload = _spawn_spec_to_dict(self._spec_row())
        self.assertEqual(payload["instructions"], "always run the suite")
        self.assertNotIn("standingInstructions", payload)


if __name__ == "__main__":
    unittest.main()
