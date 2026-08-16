"""A deliberately removed agent must not be writable by ANY endpoint, and the census that proves it.

Removing an agent writes a tombstone. Twelve handlers consult it and answer 410 "was intentionally
removed" instead of a bare 404, because the two are different facts for a bridge: "I have never heard
of this agent" invites a re-register, while "someone removed it" means stop trying. Ten of those
410s had no test — all of them read as exercised until fe1e22ad, because `service/tests/data/`
holds pre-split copies of the handlers and the coverage scan was reading them.

TESTING THE TWELVE SITES ONE BY ONE WOULD BE A LOCATION PIN. It proves the message exists where it
is written and says nothing about the thirteenth endpoint nobody added it to. So the file is built
the other way round: enumerate every agent-scoped WRITE route from the app itself, and require that
none of them succeeds against a removed agent. A new endpoint is covered the day it is added,
because `test_the_body_map_covers_every_agent_scoped_write_route` fails until someone gives it a
body — which is the moment to notice it needs the guard.

WHY 404 IS ACCEPTABLE AND 200 IS NOT. Removing an agent DELETES its row, so a handler that requires
the row before writing is safe by construction and answers 404. The 410 is the better message and is
asserted individually where it is implemented; the census asserts the SAFETY property, which is that
no write lands. Conflating the two would make the census fail on message quality, and a test that
fails for two unrelated reasons gets its assertion weakened rather than read.
"""

from __future__ import annotations

from fastapi import FastAPI

from service.routers.api_v2 import router
from service.tests._base import FastApiTestCase

AGENT_ID = "lc-removed"

#: One body per agent-scoped write route, keyed by the route's own path template. Bodies exist so a
#: request reaches the HANDLER: a 422 from the request model proves nothing about the tombstone, and
#: a census that counted 422 as "refused" would pass with every guard deleted.
BODIES: dict[str, dict] = {
    "/api/v1/agents/{agent_id}": {"status": "active", "description": "x"},
    "/api/v1/agents/{agent_id}/claimer-lease": {"action": "acquire", "bridgeId": "b1"},
    "/api/v1/agents/{agent_id}/console-working": {},
    "/api/v1/agents/{agent_id}/console/input": {"text": "hello"},
    "/api/v1/agents/{agent_id}/control": {"action": "stop"},
    "/api/v1/agents/{agent_id}/description": {"description": "x"},
    "/api/v1/agents/{agent_id}/environment": {"environmentId": "linux:test-host:default"},
    "/api/v1/agents/{agent_id}/favorite": {"favorited": True},
    "/api/v1/agents/{agent_id}/heartbeat": {"status": "online"},
    "/api/v1/agents/{agent_id}/ready": {"ready": True},
    "/api/v1/agents/{agent_id}/rename": {"newAgentId": "lc-renamed"},
    "/api/v1/agents/{agent_id}/resident-lost": {"reason": "gone"},
    "/api/v1/agents/{agent_id}/runtime-state": {"runtimeState": {"k": "v"}},
    "/api/v1/agents/{agent_id}/session-handle": {"sessionHandle": "sess-1"},
    "/api/v1/agents/{agent_id}/session-mode": {"mode": "managed"},
    "/api/v1/agents/{agent_id}/session/confirm": {"sessionId": "s1"},
    "/api/v1/agents/{agent_id}/session/keep": {"sessionId": "s1"},
    "/api/v1/agents/{agent_id}/status-event": {"kind": "turn_start"},
    "/api/v1/agents/{agent_id}/stop-worker": {},
    "/api/v1/agents/{agent_id}/turn-end": {},
    "/api/v1/agents/{agent_id}/turn-start": {},
    "/api/v1/agents/{agent_id}/usage-source": {"sourceId": "anthropic"},
    "/api/v1/agents/{agent_id}/virtual-terminal/ensure": {"bridgeId": "b1", "runtime": "pi"},
}

#: DELETE is idempotent by design: removing an already-removed agent answers 200 with `ok: false`
#: rather than an error, because a bridge retrying a remove must not be told something went wrong.
#: The recorded exception, not an oversight — and the assertion below checks the `ok: false`, so a
#: DELETE that started reporting success on a tombstoned id would still fail.
IDEMPOTENT_DELETE = "/api/v1/agents/{agent_id}"

#: The endpoints that answer 410 rather than 404 today, with the exact message. A route may move
#: from this list to the plain-404 set only deliberately: the 410 is what tells a bridge to STOP
#: retrying, and downgrading it to 404 invites the re-register loop the tombstone exists to end.
TOMBSTONE_410 = [
    ("PATCH", "/api/v1/agents/{agent_id}/ready"),
    ("POST", "/api/v1/agents/{agent_id}/heartbeat"),
    ("POST", "/api/v1/agents/{agent_id}/claimer-lease"),
    ("PATCH", "/api/v1/agents/{agent_id}/session-handle"),
    ("PATCH", "/api/v1/agents/{agent_id}/session-mode"),
    ("POST", "/api/v1/agents/{agent_id}/session/confirm"),
    ("POST", "/api/v1/agents/{agent_id}/session/keep"),
    ("POST", "/api/v1/agents/{agent_id}/turn-start"),
    ("POST", "/api/v1/agents/{agent_id}/turn-end"),
]


def _agent_write_routes() -> list[tuple[str, str]]:
    """(method, path) for every agent-scoped write route, from the app rather than a hand list."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    found = []
    for route in app.routes:
        methods = (getattr(route, "methods", set()) or set()) & {"POST", "PATCH", "PUT", "DELETE"}
        path = getattr(route, "path", "")
        if "{agent_id}" in path and methods:
            for method in sorted(methods):
                found.append((method, path))
    return sorted(set(found))


class RemovedAgentIsRefusedEverywhereTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        response = self.client.post(
            "/api/v1/agents",
            json={"agentId": AGENT_ID, "role": "coder", "runtime": "pi"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        removed = self.client.delete(f"/api/v1/agents/{AGENT_ID}")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["ok"], "the agent must actually have been removed")

    def _call(self, method: str, path: str):
        url = path.replace("{agent_id}", AGENT_ID)
        body = BODIES[path]
        return self.client.request(method, url, json=body)

    # ── the census ───────────────────────────────────────────────────────────────────────────

    def test_the_body_map_covers_every_agent_scoped_write_route(self):
        """The population half. Without it the invariant below silently stops covering whatever was
        added last — the same false green as a coverage scan reading a directory that no longer
        holds what it thinks."""
        routes = {path for _, path in _agent_write_routes()}
        self.assertGreater(len(routes), 15, "the route scan found almost nothing")
        missing = sorted(routes - set(BODIES))
        self.assertEqual(
            missing, [],
            "these agent-scoped write routes have no request body here, so the removed-agent "
            "census skips them — add one, and check the endpoint refuses a tombstoned agent:\n  "
            + "\n  ".join(missing),
        )
        extra = sorted(set(BODIES) - routes)
        self.assertEqual(extra, [], f"BODIES names routes that no longer exist: {extra}")

    def test_no_write_endpoint_succeeds_against_a_removed_agent(self):
        """THE ONE THAT MATTERS. Not "the message is right" — "nothing was written"."""
        for method, path in _agent_write_routes():
            if path == IDEMPOTENT_DELETE and method == "DELETE":
                continue
            with self.subTest(method=method, path=path):
                response = self._call(method, path)
                self.assertIn(
                    response.status_code, (404, 410),
                    f"{method} {path} answered {response.status_code} for a removed agent: "
                    f"{response.text[:300]}",
                )

    def test_removing_an_already_removed_agent_is_idempotent_and_says_so(self):
        """The recorded exception. A retrying bridge must not be told the remove failed — but the
        answer must still be `ok: false`, or a caller cannot tell a real removal from a repeat."""
        response = self.client.delete(f"/api/v1/agents/{AGENT_ID}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["ok"])

    # ── the message, where the endpoint promises the better one ──────────────────────────────

    def test_the_guarded_endpoints_say_intentionally_removed_not_just_not_found(self):
        """410 and the exact sentence. A bridge reading 404 re-registers; reading 410 it stops,
        which is the whole reason the tombstone is consulted instead of just missing the row."""
        for method, path in TOMBSTONE_410:
            with self.subTest(method=method, path=path):
                response = self._call(method, path)
                self.assertEqual(response.status_code, 410, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    f"Agent '{AGENT_ID}' was intentionally removed",
                )

    def test_reading_a_removed_agent_also_answers_410(self):
        """`GET /agents/{id}` is what `comms_agent_info` calls, so this is the answer an agent sees
        when it asks about a peer that was removed."""
        response = self.client.get(f"/api/v1/agents/{AGENT_ID}")
        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(
            response.json()["detail"], f"Agent '{AGENT_ID}' was intentionally removed",
        )

    def test_claiming_dispatch_as_a_removed_agent_is_410(self):
        """The claim path carries its own copy of the check, and it is the one that matters most:
        a removed agent whose bridge is still running would otherwise keep claiming work."""
        response = self.client.post("/api/v1/dispatch/claim", json={"agentId": AGENT_ID})
        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(
            response.json()["detail"], f"Agent '{AGENT_ID}' was intentionally removed",
        )

    def test_a_heartbeat_for_an_agent_that_never_registered_is_404(self):
        """THE DEFECT THE CENSUS FOUND, in both shapes it took.

        `agent_heartbeat` checked the tombstone and then wrote, without ever checking that the agent
        exists — the only endpoint on this surface that did not. A beat with no `bridgeId` ran an
        UPDATE matching no row and answered `{"ok": true}`, telling a bridge its agent is alive when
        the service has never heard of it. A beat WITH one — what every real bridge sends — reached
        the `bridge_instances` upsert and died on the foreign key, so the caller got a 500 carrying
        the raw "FOREIGN KEY constraint failed" text.

        Neither is a state a bridge can act on. 404 is, and it is what every sibling already says.
        """
        for body in (
            {},
            {"status": "online"},
            {"bridgeId": "bridge-ghost", "bridgeKind": "resident", "liveness": True},
        ):
            with self.subTest(body=sorted(body)):
                response = self.client.post(
                    "/api/v1/agents/lc-never-registered/heartbeat", json=body,
                )
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(
                    response.json()["detail"], "Agent 'lc-never-registered' not found",
                )

    def test_an_agent_that_never_existed_is_404_not_410(self):
        """The distinction the whole file rests on. Every guarded endpoint must tell the two apart,
        or the 410 carries no information."""
        for method, path in TOMBSTONE_410:
            with self.subTest(method=method, path=path):
                url = path.replace("{agent_id}", "lc-never-existed")
                response = self.client.request(method, url, json=BODIES[path])
                self.assertEqual(response.status_code, 404, response.text)
