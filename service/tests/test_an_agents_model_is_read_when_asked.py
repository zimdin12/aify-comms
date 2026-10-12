"""0.9.1 3B: what an agent's session actually runs is read when someone asks, by the agent's own host.

Each witness drives the real routes on one event loop: the host's held claim, the looker's GET, the host's answer.
"""
from __future__ import annotations

import asyncio
import sqlite3
import unittest

import httpx

from service.api_core import model_reads
from service.api_core.model_reads import ModelReads, host_answer
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, B

API = "/api/v1"


class AnAgentsModelIsReadWhenAsked(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.reads = ModelReads()
        self._real = model_reads.MODEL_READS
        model_reads.MODEL_READS = self.reads
        for host in (A, B):
            self.client.post(f"{API}/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32", "bridgeId": host["bridge"],
                "cwdRoots": ["/work"], "runtimes": [], "metadata": {}}).raise_for_status()
        self.agent("coder", handle="sess-1")

    def tearDown(self):
        model_reads.MODEL_READS = self._real
        super().tearDown()

    def agent(self, agent_id, *, handle, machine=A["machine"]):
        db = sqlite3.connect(self._db_path)
        try:
            db.execute("INSERT INTO agents (id, role, name, runtime, cwd, session_handle, machine_id, registered_at, last_seen)"
                       " VALUES (?, 'coder', ?, 'claude-code', 'C:/work', ?, ?, '2026-10-12T00:00:00Z', '2026-10-12T00:00:00Z')",
                       (agent_id, agent_id, handle, machine))
            db.commit()
        finally:
            db.close()

    def run_against_the_app(self, scenario):
        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self._app), base_url="http://test") as client:
                return await scenario(client)
        return asyncio.run(go())

    @staticmethod
    async def host_answers(client, host, results):
        """A host holding its claim open, then answering every read it was handed with `results(read)`."""
        claimed = await client.post(f"{API}/environments/{host['env']}/model-reads/claim",
                                    json={"bridgeId": host["bridge"], "machineId": host["machine"], "waitMs": 10_000})
        reads = claimed.json()["reads"]
        for read in reads:
            answered = await client.post(f"{API}/environments/{host['env']}/model-reads/{read['readId']}/answer",
                                         json={"bridgeId": host["bridge"], "machineId": host["machine"], "results": results(read)})
            assert answered.status_code == 200, answered.text
        return reads

    def test_the_agents_own_host_is_woken_and_its_answer_is_the_answer(self):
        async def scenario(client):
            host = asyncio.create_task(self.host_answers(client, A, lambda read: {
                agent["agentId"]: {"model": "claude-opus-5-5", "at": "t1"} for agent in read["agents"]}))
            await asyncio.sleep(0.2)  # the host's claim is held before anyone asks
            looked = await client.get(f"{API}/agents/coder/model")
            return looked, await host
        looked, reads = self.run_against_the_app(scenario)
        self.assertEqual(looked.status_code, 200, looked.text)
        actual = looked.json()["actual"]
        self.assertEqual({k: actual[k] for k in ("model", "at")}, {"model": "claude-opus-5-5", "at": "t1"})
        self.assertNotIn("stale", actual)
        self.assertEqual(reads[0]["agents"], [{"agentId": "coder", "runtime": "claude-code", "workspace": "C:/work",
                                               "sessionHandle": "sess-1"}])

    def test_another_machines_host_is_handed_nothing_and_cannot_answer(self):
        async def scenario(client):
            asking = asyncio.create_task(client.get(f"{API}/agents/coder/model"))
            await asyncio.sleep(0.2)
            other = await client.post(f"{API}/environments/{B['env']}/model-reads/claim",
                                      json={"bridgeId": B["bridge"], "machineId": B["machine"], "waitMs": 0})
            [read_id] = list(self.reads._open)
            forged = await client.post(f"{API}/environments/{B['env']}/model-reads/{read_id}/answer",
                                       json={"bridgeId": B["bridge"], "machineId": B["machine"],
                                             "results": {"coder": {"model": "forged", "at": "t"}}})
            await self.host_answers(client, A, lambda read: {"coder": {"model": "real", "at": "t"}})
            return other.json(), forged.json(), (await asking).json()["actual"]
        other, forged, actual = self.run_against_the_app(scenario)
        self.assertEqual(other["reads"], [])
        self.assertFalse(forged["accepted"])
        self.assertEqual(actual["model"], "real")

    def test_a_held_claim_whose_host_proof_stops_holding_hands_out_nothing(self):
        # Review 0.9.1 F3: proof was checked once before the claim was held, so a claim begun proofless while the host was
        # unenrolled dequeued the read that arrived after the host enrolled.
        from service.routers import model_reads as router
        real = router._proven_host
        proven = {"holds": True}

        async def proof(db, environment, machine_id, request):
            if not proven["holds"]:
                raise router.HTTPException(403, "host proof required")
            return await real(db, environment, machine_id, request)

        router._proven_host = proof
        self.addCleanup(setattr, router, "_proven_host", real)

        async def scenario(client):
            held = asyncio.create_task(client.post(f"{API}/environments/{A['env']}/model-reads/claim",
                                                   json={"bridgeId": A["bridge"], "machineId": A["machine"], "waitMs": 10_000}))
            await asyncio.sleep(0.2)
            proven["holds"] = False
            asking = asyncio.create_task(client.get(f"{API}/agents/coder/model"))
            claimed = await held
            return claimed, await asking
        claimed, asked = self.run_against_the_app(scenario)
        self.assertEqual(claimed.status_code, 403, claimed.text)
        self.assertIn("unknown", asked.json()["actual"])

    def test_work_that_lands_while_an_attempt_closes_its_database_is_not_taken_on_the_old_proof(self):
        # Review of 0.9.1 F3, second pass: the re-proof was followed by `await db.close()` and only then the dequeue, so a
        # host enrolled and a read queued during that close went to the proofless claim.
        from service.routers import model_reads as router
        real_proof, real_get_db = router._proven_host, router.get_db
        proven = {"holds": True}
        closes, parked, release = [], asyncio.Event(), asyncio.Event()

        async def proof(db, environment, machine_id, request):
            if not proven["holds"]:
                raise router.HTTPException(403, "host proof required")
            return await real_proof(db, environment, machine_id, request)

        async def get_db():
            db = await real_get_db()
            close = db.close

            async def parked_close():
                closes.append(1)
                if len(closes) == 2:  # the held claim's first attempt; the first close is its admission proof
                    parked.set()
                    await release.wait()
                await close()
            db.close = parked_close
            return db

        router._proven_host, router.get_db = proof, get_db
        self.addCleanup(setattr, router, "_proven_host", real_proof)
        self.addCleanup(setattr, router, "get_db", real_get_db)

        async def scenario(client):
            held = asyncio.create_task(client.post(f"{API}/environments/{A['env']}/model-reads/claim",
                                                   json={"bridgeId": A["bridge"], "machineId": A["machine"], "waitMs": 1_000}))
            await asyncio.wait_for(parked.wait(), 5)
            proven["holds"] = False  # the host enrolled: its old proof no longer holds
            asking = asyncio.create_task(client.get(f"{API}/agents/coder/model"))
            await asyncio.sleep(0.1)
            release.set()
            return await held, await asking
        claimed, asked = self.run_against_the_app(scenario)
        self.assertFalse(claimed.status_code == 200 and claimed.json().get("reads"),
                         f"the proofless claim was handed the read queued after enrollment: {claimed.text}")
        self.assertIn("unknown", asked.json()["actual"])

    def test_no_handle_or_a_shared_handle_is_unknown_and_no_host_is_asked(self):
        self.agent("idle", handle="")
        self.agent("twin", handle="sess-1")
        for agent_id, why in (("idle", "no session handle"), ("coder", "also claimed by twin")):
            with self.subTest(agent=agent_id):
                looked = self.client.get(f"{API}/agents/{agent_id}/model")
                self.assertEqual(looked.status_code, 200, looked.text)
                self.assertIn(why, looked.json()["actual"]["unknown"])
        self.assertEqual(self.reads._open, {})
        self.assertEqual(self.client.get(f"{API}/agents/nobody/model").status_code, 404)

    def test_a_claim_or_answer_for_an_environment_the_service_does_not_know_is_refused(self):
        for path in ("model-reads/claim", "model-reads/r1/answer"):
            with self.subTest(path=path):
                refused = self.client.post(f"{API}/environments/win32:nowhere:default/{path}",
                                           json={"bridgeId": "b", "machineId": "win32:nowhere"})
                self.assertEqual(refused.status_code, 404, refused.text)
                self.assertIn("Environment 'win32:nowhere:default' not found", refused.text)


class ASilentHostIsSaidNotHidden(unittest.TestCase):
    AGENT = {"agentId": "coder", "runtime": "hermes", "workspace": "/w", "sessionHandle": "h"}

    def test_its_last_answer_comes_back_marked_stale_and_with_none_it_is_unknown(self):
        reads = ModelReads()

        async def go():
            first = await reads.read("m", self.AGENT, wait_s=0.05)
            answering = asyncio.create_task(reads.read("m", self.AGENT, wait_s=1))
            await asyncio.sleep(0)
            [handed] = reads.claim("M")
            self.assertTrue(reads.answer("m", handed["readId"], {"coder": {"model": "gpt-6.1-sol", "at": "t"}}, "r1"))
            fresh = await answering
            silent = await reads.read("m", self.AGENT, wait_s=0.05)
            return first, fresh, silent

        first, fresh, silent = asyncio.run(go())
        self.assertEqual(first, {"unknown": "its host did not answer within 0.05 s"})
        self.assertEqual(fresh, {"model": "gpt-6.1-sol", "at": "t", "readAt": "r1"})
        self.assertEqual(silent["model"], "gpt-6.1-sol")
        self.assertIn("did not answer within 0.05 s; this is its answer from r1", silent["stale"])
        self.assertEqual((reads._open, reads._unclaimed), ({}, {}), "a read outlived its asker")

    def test_a_host_answer_is_a_model_or_a_reason_and_nothing_else(self):
        self.assertEqual(host_answer({"model": "m", "at": "t", "extra": "x"}), {"model": "m", "at": "t"})
        self.assertEqual(host_answer({"unknown": "no transcript"}), {"unknown": "no transcript"})
        for junk in (None, "m", {"model": ""}, {"model": 5}, {}):
            with self.subTest(junk=repr(junk)):
                self.assertEqual(host_answer(junk), {"unknown": "its host answered with no reading"})
