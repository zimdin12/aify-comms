"""A host's lifecycle-request and definition-request claims may be held open with `waitMs`.

A held claim is woken by the COMMIT of a write to its queue (change_feed.CLAIM_SCOPES), so every writer wakes it,
and nothing re-checks on a timer. Each attempt is the whole claim it always was: its own connection and write
transaction, the host proof, the bridge fence, the capability the claim declares and the row's machine. A claim
without `waitMs` answers at once, as before.

Each witness drives the real routes on one event loop, with the service's connection pool enabled as its lifespan
enables it, because the pool is what reports a commit. What each would catch:

* ABSENT `waitMs`: an empty claim that held anyway would delay every host that never asked to wait.
* SAME HOST: a held claim that did not wake would take its host's work only when its wait ran out.
* OTHER HOST: a wake that handed work across machines, or ended another host's wait with nothing.
* NO HOST HOLDING: work queued while nobody waits is handed out on the next claim, without waiting.
* THE RACE: work committed while an empty attempt closes its connection lands before the wait listens.
* PROOF, FENCE, CAPABILITY: an attempt after the wake that skipped one would take work it must not.
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time

import httpx

from service.db import CONNECTION_POOL
from service.tests._base import FastApiTestCase
from service.tests.test_agent_definition_push import A, B, snapshot_digest, valid

API = "/api/v1"
#: Long enough that a claim returning inside WOKEN can only have been woken, short enough to run.
HOLD_MS = 5000
WOKEN = 1.5


def _lifecycle(request_id="life-1"):
    return ("/agents/coder/lifecycle-requests",
            {"requestId": request_id, "action": "stop", "requestedBy": "peer", "expectedLifetime": "worker-1"})


def _definition(request_id=None):
    return ("/agent-definitions/coder/requests", {"patch": {"role": "reviewer"}, "requestedBy": "dashboard"})


#: Each queue: its claim route, how work is enqueued for host A's agent, and the router whose proof an attempt asks.
QUEUES = {
    "lifecycle": ("lifecycle-requests/claim", _lifecycle, "service.routers.agent_lifecycle_requests"),
    "definition": ("definition-requests/claim", _definition, "service.routers.definition_requests"),
}


class _HeldClaims(FastApiTestCase):
    """Two hosts, and an agent host A defines: what each witness below starts from."""

    def setUp(self):
        super().setUp()
        for host in (A, B):
            self.client.post(f"{API}/environments/heartbeat", json={
                "id": host["env"], "machineId": host["machine"], "os": "win32", "kind": "win32",
                "bridgeId": host["bridge"], "cwdRoots": [], "runtimes": [], "metadata": {}}).raise_for_status()
        entries = [valid("coder", incarnation=2, revision=5)]
        self.client.put(f"{API}/environments/{A['env']}/agent-definitions", json={
            "bridgeId": A["bridge"], "machineId": A["machine"], "storeId": "s1", "revision": 1,
            "snapshotDigest": snapshot_digest(entries), "entries": entries}).raise_for_status()

    def against_the_service(self, scenario):
        """`scenario(client)` on one loop, with the pool that reports commits enabled, as the lifespan does."""
        async def go():
            CONNECTION_POOL.enable()
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self._app),
                                             base_url="http://test") as client:
                    return await scenario(client)
            finally:
                await CONNECTION_POOL.aclose()
        return asyncio.run(go())

    def rows(self, table):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(f"SELECT id, machine_id, status FROM {table}")]
        finally:
            conn.close()

    def execute(self, sql, params=()):
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    async def claim(client, route, host=A, **extra):
        started = time.monotonic()
        response = await client.post(f"{API}/environments/{host['env']}/{route}",
                                     json={"bridgeId": host["bridge"], "machineId": host["machine"], **extra})
        return response, time.monotonic() - started

    @staticmethod
    async def enqueue(client, work):
        path, body = work
        response = await client.post(API + path, json=body)
        assert response.status_code == 200, response.text
        return response.json()["request"]["id"]

    @staticmethod
    def table(name):
        return "agent_lifecycle_requests" if name == "lifecycle" else "definition_requests"


class AHeldClaimWakesForItsHostsWork(_HeldClaims):
    DB_NAME = "aify-test-held-claims.db"

    def test_without_waitMs_an_empty_claim_answers_at_once(self):
        for name, (route, _, _) in QUEUES.items():
            for extra in ({}, {"waitMs": 0}):
                with self.subTest(queue=name, body=extra):
                    response, took = self.against_the_service(lambda client: self.claim(client, route, **extra))
                    self.assertEqual((response.status_code, response.json()), (200, {"ok": True, "requests": []}))
                    self.assertLess(took, 0.5, "a claim that asked for no wait was held")

    def test_a_held_claim_wakes_for_work_enqueued_for_its_host(self):
        for name, (route, work, _) in QUEUES.items():
            with self.subTest(queue=name):
                async def scenario(client):
                    held = asyncio.create_task(self.claim(client, route, waitMs=HOLD_MS))
                    await asyncio.sleep(0.3)
                    still_held = not held.done()  # CONTROL: the claim is waiting, not already answered
                    queued_at = time.monotonic()
                    request_id = await self.enqueue(client, work(f"{name}-same"))
                    response, _ = await held
                    return still_held, request_id, response, time.monotonic() - queued_at

                still_held, request_id, response, after_enqueue = self.against_the_service(scenario)
                self.assertTrue(still_held, "the empty claim did not hold")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([r["id"] for r in response.json()["requests"]], [request_id])
                self.assertLess(after_enqueue, WOKEN, "the held claim was not woken by the enqueue's commit")
                self.assertEqual([(r["id"], r["status"]) for r in self.rows(self.table(name))],
                                 [(request_id, "claimed")])
                self.execute(f"DELETE FROM {self.table(name)}")

    def test_another_hosts_held_claim_is_woken_but_handed_nothing_and_keeps_waiting(self):
        # THE WAKE IS PER QUEUE, THE RECEIPT PER HOST. A commit to the queue wakes every claim held on it; each woken
        # attempt reads only its own machine's rows. So host B is woken (it attempts again) and still handed nothing.
        import importlib
        for name, (route, work, module) in QUEUES.items():
            with self.subTest(queue=name):
                router = importlib.import_module(module)
                real = router._proven_host
                attempts = []

                async def counted(db, environment, machine_id, request):
                    attempts.append(machine_id)
                    return await real(db, environment, machine_id, request)

                async def scenario(client):
                    other = asyncio.create_task(self.claim(client, route, host=B, waitMs=1500))
                    await asyncio.sleep(0.3)
                    before = attempts.count(B["machine"])
                    request_id = await self.enqueue(client, work(f"{name}-other"))
                    other_response, other_took = await other
                    after = attempts.count(B["machine"])
                    own, _ = await self.claim(client, route)  # CONTROL: the work was there, for its own host
                    return before, after, request_id, other_response, other_took, own

                router._proven_host = counted
                try:
                    before, after, request_id, other_response, other_took, own = self.against_the_service(scenario)
                finally:
                    router._proven_host = real
                self.assertEqual(before, 1, "control: host B made exactly its first attempt before the enqueue")
                self.assertGreaterEqual(after, 2, "the enqueue's commit did not wake host B's claim on the same queue")
                self.assertEqual((other_response.status_code, other_response.json()), (200, {"ok": True, "requests": []}))
                self.assertGreaterEqual(other_took, 1.4, "another host's work ended this host's wait")
                self.assertEqual([r["id"] for r in own.json()["requests"]], [request_id])
                self.execute(f"DELETE FROM {self.table(name)}")

    def test_work_queued_while_no_claim_is_held_is_handed_out_at_once(self):
        for name, (route, work, _) in QUEUES.items():
            with self.subTest(queue=name):
                async def scenario(client):
                    request_id = await self.enqueue(client, work(f"{name}-absent"))
                    response, took = await self.claim(client, route, waitMs=HOLD_MS)
                    return request_id, response, took

                request_id, response, took = self.against_the_service(scenario)
                self.assertEqual([r["id"] for r in response.json()["requests"]], [request_id])
                self.assertLess(took, 0.5, "a claim with work waiting held anyway")
                self.execute(f"DELETE FROM {self.table(name)}")

    def test_work_committed_while_an_attempt_closes_its_database_still_wakes_the_claim(self):
        # The enqueue commits and notifies inside the first empty attempt's `close()`: after that attempt read the
        # queue, before the wait begins. Waking only once the wait listens slept through it for the whole hold.
        import importlib
        for name, (route, work, module) in QUEUES.items():
            with self.subTest(queue=name):
                router = importlib.import_module(module)
                real_get_db = router.get_db
                fired = {"done": False}

                async def scenario(client):
                    class EnqueueOnClose:
                        def __init__(self, db):
                            self._db = db

                        def __getattr__(self, attribute):
                            return getattr(self._db, attribute)

                        async def close(self):
                            await self._db.close()
                            if not fired["done"]:
                                fired["done"] = True
                                fired["id"] = await self.enqueue(client, work(f"{name}-race"))

                    async def get_db(*args, **kwargs):
                        db = await real_get_db(*args, **kwargs)
                        return EnqueueOnClose(db) if not fired["done"] else db

                    EnqueueOnClose.enqueue = staticmethod(self.enqueue)
                    router.get_db = get_db
                    try:
                        return await self.claim(client, route, waitMs=HOLD_MS)
                    finally:
                        router.get_db = real_get_db

                response, took = self.against_the_service(scenario)
                self.assertTrue(fired["done"], "control: the enqueue did not run inside the attempt's close")
                self.assertEqual([r["id"] for r in response.json()["requests"]], [fired["id"]])
                self.assertLess(took, WOKEN, "work committed as the attempt closed was slept through")
                self.execute(f"DELETE FROM {self.table(name)}")

    def test_each_attempt_proves_its_host_again(self):
        import importlib
        from fastapi import HTTPException
        for name, (route, work, module) in QUEUES.items():
            with self.subTest(queue=name):
                router = importlib.import_module(module)
                real = router._proven_host
                proven = {"holds": True}

                async def proof(db, environment, machine_id, request):
                    if not proven["holds"]:
                        await db.rollback()
                        raise HTTPException(403, "host proof required")
                    return await real(db, environment, machine_id, request)

                async def scenario(client):
                    held = asyncio.create_task(self.claim(client, route, waitMs=HOLD_MS))
                    await asyncio.sleep(0.3)
                    still_held = not held.done()  # CONTROL: the proof stopped holding during the wait
                    proven["holds"] = False
                    await self.enqueue(client, work(f"{name}-proof"))
                    response, _ = await held
                    return still_held, response

                router._proven_host = proof
                try:
                    still_held, response = self.against_the_service(scenario)
                finally:
                    router._proven_host = real
                self.assertTrue(still_held, "the empty claim did not hold")
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual([r["status"] for r in self.rows(self.table(name))], ["pending"],
                                 "work was taken on a proof that no longer held")
                self.execute(f"DELETE FROM {self.table(name)}")

    def test_each_attempt_is_fenced_to_the_environments_current_bridge(self):
        for name, (route, work, _) in QUEUES.items():
            with self.subTest(queue=name):
                async def scenario(client):
                    held = asyncio.create_task(self.claim(client, route, waitMs=HOLD_MS))
                    await asyncio.sleep(0.3)
                    still_held = not held.done()  # CONTROL: the fence moved during the wait, not before it
                    # Another bridge took the environment while this claim waited.
                    self.execute("UPDATE environments SET bridge_id = 'bridge-a-next' WHERE id = ?", (A["env"],))
                    await self.enqueue(client, work(f"{name}-fence"))
                    response, took = await held
                    return still_held, response, took

                try:
                    still_held, response, took = self.against_the_service(scenario)
                finally:
                    self.execute("UPDATE environments SET bridge_id = ? WHERE id = ?", (A["bridge"], A["env"]))
                self.assertTrue(still_held, "the empty claim did not hold")
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("claim refused", response.json()["detail"])
                self.assertLess(took, 0.3 + WOKEN, "the fenced claim was not re-attempted when work arrived")
                self.assertEqual([r["status"] for r in self.rows(self.table(name))], ["pending"])
                self.execute(f"DELETE FROM {self.table(name)}")

    def test_an_automatic_start_wakes_only_a_claim_that_declares_it_can_run_one(self):
        from service.api_core import agent_lifecycle_requests as lifecycle
        from service.db import get_db
        from service.lifecycle_models import AUTOMATIC_START_CAPABILITY, AUTOMATIC_START_INTENT

        async def queue_automatic_start():
            db = await get_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                held = await (await db.execute("SELECT * FROM agent_definitions WHERE agent_id = 'coder'")).fetchone()
                await lifecycle.queue_lifecycle(
                    db, "auto-1", held, action="start", actor="dispatch", expected_lifetime=None,
                    fresh_context=False, intent=json.dumps({"agentId": "coder"}), now="2026-11-21T00:00:00Z",
                    start_intent=AUTOMATIC_START_INTENT)
                await db.commit()
            finally:
                await db.close()

        route = QUEUES["lifecycle"][0]

        async def scenario(client):
            unable = asyncio.create_task(self.claim(client, route, waitMs=1500, capabilities=[]))
            able = asyncio.create_task(self.claim(client, route, waitMs=HOLD_MS,
                                                  capabilities=[AUTOMATIC_START_CAPABILITY]))
            await asyncio.sleep(0.3)
            queued_at = time.monotonic()
            await queue_automatic_start()
            able_response, _ = await able
            able_after = time.monotonic() - queued_at
            return (await unable), able_response, able_after

        (unable, unable_took), able, able_after = self.against_the_service(scenario)
        self.assertEqual((unable.status_code, unable.json()["requests"]), (200, []))
        self.assertGreaterEqual(unable_took, 1.4, "a claim that cannot run an automatic start stopped waiting")
        self.assertEqual([r["id"] for r in able.json()["requests"]], ["auto-1"])
        self.assertLess(able_after, WOKEN, "the capable claim was not woken by the automatic start's commit")


async def _raw_claim(path: str, body: dict, *, disconnect_after):
    """`path` through the production app, middleware included, as a raw ASGI call whose caller leaves after
    `disconnect_after` seconds (never, for None). As in test_a_claim_long_poll_sees_a_caller_that_left.py."""
    from service.main import app

    delivered = {"request": False}

    async def receive():
        if not delivered["request"]:
            delivered["request"] = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        await asyncio.sleep(3600 if disconnect_after is None else disconnect_after)
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [(b"host", b"127.0.0.1:8800"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "root_path": "",
    }
    await app(scope, receive, send)
    statuses = [m.get("status") for m in sent if m["type"] == "http.response.start"]
    assert statuses == [200], f"the route did not answer 200, so nothing below was tested: {sent[:2]}"
    return json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))


class AHeldClaimWhoseCallerLeftTakesNothing(_HeldClaims):
    """A host that timed out or died mid-claim must not have work marked claimed for it."""

    DB_NAME = "aify-test-held-claims-left.db"

    def scenario(self, name, disconnect_after):
        route, work, _ = QUEUES[name]

        async def go(client):
            call = asyncio.create_task(_raw_claim(
                f"{API}/environments/{A['env']}/{route}",
                {"bridgeId": A["bridge"], "machineId": A["machine"], "waitMs": HOLD_MS}, disconnect_after=disconnect_after))
            await asyncio.sleep(0.5)
            finished_early = call.done()
            request_id = await self.enqueue(client, work(f"{name}-left"))
            return finished_early, request_id, await asyncio.wait_for(call, 10)

        return self.against_the_service(go)

    def test_control_a_connected_caller_that_waits_is_handed_the_work(self):
        for name in QUEUES:
            with self.subTest(queue=name):
                finished_early, request_id, answer = self.scenario(name, disconnect_after=None)
                self.assertFalse(finished_early, "control: a connected caller was not held")
                self.assertEqual([r["id"] for r in answer["requests"]], [request_id])
                self.assertEqual([r["status"] for r in self.rows(self.table(name))], ["claimed"])
                self.execute(f"DELETE FROM {self.table(name)}")

    def test_a_caller_that_leaves_while_held_ends_the_wait_and_claims_nothing(self):
        for name in QUEUES:
            with self.subTest(queue=name):
                finished_early, _, answer = self.scenario(name, disconnect_after=0.1)
                self.assertTrue(finished_early, "the wait did not end when the caller left")
                self.assertEqual(answer["requests"], [])
                self.assertEqual([r["status"] for r in self.rows(self.table(name))], ["pending"],
                                 "work was claimed for a caller that had left")
                self.execute(f"DELETE FROM {self.table(name)}")


class AnIdleHeldClaimAttemptsOnlyOnItsFallback(_HeldClaims):
    """EVERY held claim route, idle for its whole wait. A held claim listens from before each attempt, so an empty
    attempt that committed a change to its own queue would wake itself and attempt again at once, for the whole wait:
    a write-locking loop that no single-request test sees. Each route must attempt once, then once per fallback."""

    DB_NAME = "aify-test-held-claims-idle.db"
    HOLD_S = 3.5

    def setUp(self):
        super().setUp()
        self.client.post(f"{API}/agents", json={"agentId": "idle-agent", "role": "coder",
                                                "machineId": A["machine"]}).raise_for_status()

    def test_each_route_attempts_once_and_then_once_per_fallback(self):
        from service import longpoll
        hold = int(self.HOLD_S * 1000)
        env = {"environmentId": A["env"], "bridgeId": A["bridge"], "waitMs": hold}
        host = {"bridgeId": A["bridge"], "machineId": A["machine"], "waitMs": hold}
        routes = {  # path, body, its fallback re-check in seconds (None: none)
            "dispatch": ("/dispatch/claim", {"agentId": "idle-agent", "machineId": A["machine"], "waitMs": hold}, 3.0),
            "dispatch controls": ("/dispatch/controls/claim", {"agentId": "idle-agent", "waitMs": hold}, 3.0),
            "terminal controls": ("/terminals/controls/claim", {k: env[k] for k in ("environmentId", "bridgeId", "waitMs")}, 1.0),
            "environment controls": ("/environments/controls/claim", env, 3.0),
            "spawn requests": ("/spawn-requests/claim", env, 3.0),
            "model reads": (f"/environments/{A['env']}/model-reads/claim", host, None),
            "lifecycle requests": (f"/environments/{A['env']}/lifecycle-requests/claim", host, None),
            "definition requests": (f"/environments/{A['env']}/definition-requests/claim", host, None),
        }
        real = longpoll.longpoll
        for name, (path, body, fallback) in routes.items():
            with self.subTest(route=name):
                attempts = []

                async def counting(wait_ms, attempt, is_empty, **kwargs):
                    async def counted():
                        attempts.append(time.monotonic())
                        return await attempt()
                    return await real(wait_ms, counted, is_empty, **kwargs)

                async def scenario(client):
                    started = time.monotonic()
                    response = await client.post(API + path, json=body)
                    return response, time.monotonic() - started

                longpoll.longpoll = counting
                try:
                    response, took = self.against_the_service(scenario)
                finally:
                    longpoll.longpoll = real
                self.assertEqual(response.status_code, 200, f"control: the route answered {response.text}")
                self.assertGreaterEqual(took, self.HOLD_S - 0.1, "control: the claim was not held, so nothing was measured")
                # The first attempt, one after each fallback sleep, and one when the last sleep ends at the deadline;
                # with no fallback, the first and the one at the deadline. Measured the same before these claims could be held.
                expected = 1 + (math.ceil(self.HOLD_S / fallback) if fallback else 1)
                self.assertLessEqual(len(attempts), expected,
                                     f"{len(attempts)} attempts in {self.HOLD_S}s idle: the claim wakes itself")
