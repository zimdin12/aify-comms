r"""Every bounded list the dashboard fetches truthfully says whether it is the whole answer.

THE SAME DEFECT SIX TIMES, so it stops being a bug and becomes a rule. `/contracts` and `/terminals`
reported truncation from the start; `/sessions`, `/dispatch/runs`, `/messages/recent` and
`/spawn-requests` did not, and each produced the identical failure: the page renders exactly what it
got, an operator searches for something not on it, and the empty state blames the filter.

THE FIRST VERSION OF THIS GATE HAD TWO FALSE GREENS. A reviewer found both and I reproduced both here
before fixing them:

  * `/dispatch/runs` was NOT IN THE POPULATION. The dashboard calls `api(runQueryPath())`, and the scan
    read only string literals handed straight to `api(...)` -- so the gate written BECAUSE Runs dropped
    its truncation flag did not govern Runs. Executed mutant: delete `truncated` from that endpoint,
    gate passes 5/5.
  * The honesty check was named for every endpoint and seeded ONE. Executed mutant: hardcode
    `/spawn-requests` to `"truncated": false`, gate passes 5/5.

A gate with a false green is worse than no gate: it is a green light bolted over the thing it was built
to watch. Both are closed by construction below rather than by remembering.

THREE THINGS IT NOW DOES.

  1. POPULATION, DERIVED TWO WAYS. String literals, AND paths built by a function handed to `api(...)`
     -- those producers are located by name, evaluated with node, and joined to the set.
     `/dispatch/runs` is pinned as a positive control because it is the known missed class.
  2. CONTRACT, CLASSIFIED. A lone `total` does NOT qualify: that is exactly the shape just removed from
     `/messages/recent`, where `total` was `len(page)` and answered nothing. What qualifies is a boolean
     `truncated`, or `showing` + `total` where the returned page length AGREES with `showing`, or
     `totalMessages` beside its page.
  3. AN OVER-LIMIT WITNESS FOR EVERY ROUTE. Each governed route is seeded past its limit and requested
     with `limit=1`; a route that cannot then say it is truncated fails ON ITS OWN, so hardcoding one
     endpoint's flag cannot hide behind another's. The route-to-seeder map is closed in both
     directions: a governed route with no seeder fails, and a seeder for no governed route fails.

WHAT IT DOES NOT CLAIM. This is the PRODUCER contract: the response CAN answer the question. Whether a
page renders the answer is asserted per page in the dashboard suite, and `/messages/inbox/{agent}` is a
known gap -- it reports `showing` and `total` and the Chat surface renders neither. That is named
product debt, not something this file pretends to cover.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from service.tests._base import FastApiTestCase

REPO = Path(__file__).resolve().parents[2]
DASHBOARD = REPO / "service" / "new_dashboard"

#: `api('/path?...limit=N...')` and its template-literal form.
LITERAL_CALL = re.compile(r"""api\(\s*['"`](?P<path>/[^'"`]*\blimit=[^'"`]*)['"`]""")

#: `api(someProducer(...))` -- a path BUILT rather than written. This is the form that hid
#: `/dispatch/runs`: `runQueryPath` assembles its limit with URLSearchParams, so no literal exists.
PRODUCER_CALL = re.compile(r"""api\(\s*(?P<name>[A-Za-z_$][\w$]*)\s*\(""")

#: Placeholders the dashboard interpolates into literal paths.
SUBSTITUTIONS = {
    "${encodeURIComponent(name)}": "gate-channel",
    "${encodeURIComponent(state.chat.identity)}": "gate-agent",
}


def _source_files() -> list[Path]:
    return [
        p for p in sorted(DASHBOARD.glob("*.mjs")) + sorted(DASHBOARD.glob("*.js"))
        if not p.name.endswith((".test.mjs", ".test.js"))
    ]


def _literal_paths() -> set[str]:
    paths = set()
    for source in _source_files():
        for match in LITERAL_CALL.finditer(source.read_text(encoding="utf-8")):
            path = match.group("path")
            for placeholder, value in SUBSTITUTIONS.items():
                path = path.replace(placeholder, value)
            if "${" in path:
                # Requested literally it would 404, which reads as a missing endpoint rather than as a
                # gap in this scan. Refusing is the honest answer.
                raise AssertionError(
                    f"{source.name} fetches a capped list with an unresolved placeholder: {path}. "
                    "Add it to SUBSTITUTIONS, or this endpoint is ungoverned."
                )
            paths.add(path)
    return paths


def _producer_paths() -> set[str]:
    """Paths built by a function handed to `api(...)`, obtained by RUNNING the function.

    Located by name, matched to the module that exports it, then evaluated. Evaluating beats parsing:
    the whole point of these producers is that the path is assembled at runtime, so anything short of
    running them is a second implementation of their logic that can disagree with them.
    """
    node = shutil.which("node")
    if not node:
        # NOT A SKIP. Without node the population is incomplete, and an incomplete population reports
        # green exactly like a complete one -- the false green this file was rewritten to close.
        raise AssertionError(
            "node is not on PATH, so paths built by a producer function cannot be derived and this "
            "gate would govern only the literal ones. That is the hole it exists to close."
        )

    names: set[str] = set()
    for source in _source_files():
        for match in PRODUCER_CALL.finditer(source.read_text(encoding="utf-8")):
            names.add(match.group("name"))

    exporters: dict[str, Path] = {}
    for source in _source_files():
        text = source.read_text(encoding="utf-8")
        for name in names:
            if re.search(rf"^export (?:async )?function {re.escape(name)}\b", text, re.M):
                exporters[name] = source

    paths = set()
    for name, module in sorted(exporters.items()):
        # A `file://` URL, not a bare Windows path: node's ESM loader refuses `c:/...` outright
        # ("Only URLs with a scheme in: file, data, and node"), and the refusal is swallowed by the
        # try/catch below -- so the producer would silently contribute nothing and the population
        # would be short by exactly the paths this arm exists to find.
        script = (
            f"import {{ {name} }} from {json.dumps(module.as_uri())};"
            f"try {{ const v = {name}(); if (typeof v === 'string') console.log(v); }} catch {{}}"
        )
        result = subprocess.run(
            [node, "--input-type=module", "-e", script],
            capture_output=True, text=True, cwd=str(DASHBOARD), timeout=60,
        )
        for line in result.stdout.splitlines():
            candidate = line.strip()
            if candidate.startswith("/") and "limit=" in candidate:
                paths.add(candidate)
    return paths


def dashboard_capped_paths() -> list[str]:
    """Every capped list request the dashboard makes, literal or constructed."""
    return sorted(_literal_paths() | _producer_paths())


def route_of(path: str) -> str:
    """The route a path addresses, without its query or its parameters.

    Route identity and invocation identity are different counts: one route may be reached by several
    paths, and reporting one number as the other overstates coverage.
    """
    head = path.split("?", 1)[0]
    if head.startswith("/channels/"):
        return "/channels/{name}"
    if head.startswith("/messages/inbox/"):
        return "/messages/inbox/{agent}"
    return head


#: The arrays these responses carry their page in. Used to check `showing` against reality.
PAGE_KEYS = ("runs", "sessions", "messages", "contracts", "spawnRequests", "terminals")


def answers_completeness(body: object) -> tuple[bool, str]:
    """Can a caller tell this page from the complete list, and by which contract?

    A LONE `total` DOES NOT QUALIFY. That is the shape just removed from `/messages/recent`, where
    `total` was `len(messages)` -- equal to the page by construction, under a name promising the whole.
    Paired with `showing`, and with the page length agreeing, it is a real answer.
    """
    if not isinstance(body, dict):
        return False, "not an object"
    if isinstance(body.get("truncated"), bool):
        return True, "truncated"
    page = next((body[k] for k in PAGE_KEYS if isinstance(body.get(k), list)), None)
    if "showing" in body and "total" in body:
        if page is not None and len(page) != body["showing"]:
            return False, f"showing={body['showing']} disagrees with {len(page)} rows"
        return True, "showing+total"
    if "totalMessages" in body and page is not None:
        return True, "totalMessages"
    if "total" in body:
        return False, "a lone `total` says nothing about the whole"
    return False, "no completeness signal"


class CappedListSaysItIsCappedTests(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        for agent_id in ("gate-agent", "gate-peer"):
            registered = self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code",
                "sessionMode": "resident",
            })
            self.assertEqual(registered.status_code, 200, registered.text)
        created = self.client.post("/api/v1/channels", json={
            "name": "gate-channel", "createdBy": "gate-agent", "description": "capped-list gate",
        })
        self.assertIn(created.status_code, (200, 201, 409), created.text)

    # ---- seeders: one per governed route, each producing MORE than one row -----------------------

    def _seed_messages(self) -> None:
        for index in range(3):
            sent = self.client.post("/api/v1/messages/send", json={
                "from_agent": "gate-peer", "to": "gate-agent", "type": "info",
                "subject": f"s{index}", "body": "b",
            })
            self.assertEqual(sent.status_code, 200, sent.text)

    def _seed_dashboard_inbox(self) -> None:
        for index in range(3):
            sent = self.client.post("/api/v1/messages/send", json={
                "from_agent": "gate-agent", "to": "dashboard", "type": "info",
                "subject": f"d{index}", "body": "b",
            })
            self.assertEqual(sent.status_code, 200, sent.text)

    def _seed_channel(self) -> None:
        for index in range(3):
            sent = self.client.post("/api/v1/channels/gate-channel/send", json={
                "from_agent": "gate-agent", "channel": "gate-channel", "body": f"c{index}",
            })
            self.assertEqual(sent.status_code, 200, sent.text)

    def _seed_rows(self, sql: str, rows: list[tuple]) -> None:
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                for row in rows:
                    await db.execute(sql, row)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def _seed_runs(self) -> None:
        """Runs that are ALSO contracts, so one seeder serves both routes.

        A contract is derived, not stored. The rows are `queued` rather than `completed`, and that is
        the whole of why the first attempt seeded three runs and zero contracts: the dashboard fetches
        `/contracts?limit=80` with no `state`, and the default view is
        `status NOT IN ('completed','failed','cancelled')`. A `completed` run derives `missing_reply`,
        which only appears once a state is asked for -- so a witness seeded that way is invisible to
        the very request the gate probes, and a witness that seeds nothing proves nothing.

        The message comes first because `dispatch_runs.message_id` is a FOREIGN KEY and the contracts
        query LEFT JOINs it, and the columns are shaped after
        `test_a_contract_filter_does_not_lose_rows_to_the_limit.py`, where this repo already worked out
        what a contract row has to look like.
        """
        self._seed_rows(
            "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, "
            "priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
            [(f"gate-msg-{i}", "gate-peer", "gate-agent", "direct", "request", f"s{i}", "b",
              "normal", 1787000000000 + i) for i in range(3)],
        )
        self._seed_rows(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, "
            "message_type, subject, body, priority, status, require_reply, requested_at, "
            "finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(f"gate-run-{i}", f"gate-msg-{i}", "gate-peer", "gate-agent", "request", f"s{i}", "b",
              "normal", "queued", 1, f"2026-08-0{1 + i}T00:00:00Z", None) for i in range(3)],
        )

    def _seed_sessions(self) -> None:
        self._seed_rows(
            "INSERT OR IGNORE INTO environments (id, label, machine_id, registered_at, last_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            [("gate-env", "gate", "gate-host", "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z")],
        )
        self._seed_rows(
            "INSERT OR IGNORE INTO agent_sessions (id, agent_id, environment_id, status, runtime, "
            "started_at, last_seen, spawn_spec_id, spawn_request_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            [(f"gate-sess-{i}", "gate-agent", "gate-env", "stopped", "claude-code",
              "2026-08-01T00:00:00Z", f"2026-08-0{1 + i}T00:00:00Z") for i in range(3)],
        )

    def _seed_spawn_requests(self) -> None:
        # `environmentId` is required by the model: a spawn request names the environment that will
        # host it, and the row is a FOREIGN KEY into `environments`. Seeded through the same helper the
        # sessions witness uses, so both witnesses agree about which environment exists.
        self._seed_rows(
            "INSERT OR IGNORE INTO environments (id, label, machine_id, registered_at, last_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            [("gate-env", "gate", "gate-host", "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z")],
        )
        # SEEDED THROUGH THE TABLE, not the route. The route refuses an offline environment -- "restart
        # its bridge before spawning" -- and it is right to: a spawn request against a dead environment
        # is a request nothing will claim. This witness needs ROWS in the list, not a live spawn path,
        # and faking an environment as online to get them would seed a state the service never writes.
        # A SPEC PER REQUEST: `spawn_spec_id` is NOT NULL and a FOREIGN KEY, so a request without one
        # is refused outright -- the schema saying that a spawn request is a request to run a
        # particular spec, not a free-floating row.
        self._seed_rows(
            "INSERT OR IGNORE INTO spawn_specs (id, agent_id, environment_id, runtime, workspace, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(f"gate-spec-{i}", f"gate-spawn-agent-{i}", "gate-env", "claude-code", "C:/gate",
              "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z") for i in range(3)],
        )
        self._seed_rows(
            "INSERT OR IGNORE INTO spawn_requests (id, spawn_spec_id, created_by, environment_id, "
            "agent_id, role, runtime, workspace, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(f"gate-spawn-{i}", f"gate-spec-{i}", "gate-agent", "gate-env",
              f"gate-spawn-agent-{i}", "coder", "claude-code", "C:/gate", "queued",
              f"2026-08-0{1 + i}T00:00:00Z", f"2026-08-0{1 + i}T00:00:00Z") for i in range(3)],
        )

    def seeders(self) -> dict:
        """ROUTE -> the seeder that puts more than one row behind it.

        Closed in both directions by a test below: a governed route with no seeder would be probed
        against an empty table, where "not truncated" is the honest answer and proves nothing.
        """
        return {
            "/contracts": self._seed_runs,
            "/dispatch/runs": self._seed_runs,
            "/sessions": self._seed_sessions,
            "/messages/recent": self._seed_messages,
            "/spawn-requests": self._seed_spawn_requests,
            "/channels/{name}": self._seed_channel,
            "/messages/inbox/{agent}": self._seed_dashboard_inbox,
        }

    # ---- the population ---------------------------------------------------------------------------

    def test_THE_POPULATION_INCLUDES_CONSTRUCTED_PATHS(self):
        """THE POSITIVE CONTROL, and it is the defect this gate was rewritten for.

        `/dispatch/runs` is fetched as `api(runQueryPath())` -- no literal exists. The first version
        read literals only, so the gate written BECAUSE Runs dropped its truncation flag did not govern
        Runs, and deleting that flag left it passing 5/5.
        """
        paths = dashboard_capped_paths()
        self.assertTrue(
            any(p.startswith("/dispatch/runs") for p in paths),
            f"the scan cannot see the path it was built for: {paths}",
        )
        for expected in ("/contracts", "/sessions", "/messages/recent", "/spawn-requests"):
            self.assertTrue(any(p.startswith(expected) for p in paths), f"{expected} missing: {paths}")

    def test_the_scan_does_not_match_an_uncapped_request(self):
        """NEGATIVE CONTROL. A pattern loose enough to match every `api(...)` would drag in endpoints
        returning one record, and the gate would be widened away rather than obeyed."""
        self.assertIsNone(LITERAL_CALL.search("api('/agents')"))
        self.assertIsNone(LITERAL_CALL.search("api(`/dispatch/runs/${id}`)"))
        self.assertIsNotNone(LITERAL_CALL.search("api('/sessions?limit=80')"))

    def test_every_governed_route_has_a_witness_and_every_witness_a_route(self):
        """BIDIRECTIONAL CLOSURE. A route with no seeder would be checked against an empty table, where
        "not truncated" is true and proves nothing. A seeder for no route is a coverage claim that has
        quietly stopped holding."""
        routes = {route_of(p) for p in dashboard_capped_paths()}
        seeded = set(self.seeders())
        self.assertEqual(routes - seeded, set(), "governed routes with no over-limit witness")
        self.assertEqual(seeded - routes, set(), "witnesses for routes the dashboard no longer fetches")

    # ---- the contract -----------------------------------------------------------------------------

    def test_a_lone_total_is_not_an_answer(self):
        """NEGATIVE CONTROL on the classifier, and it is the exact shape removed from
        `/messages/recent`: `total` was `len(messages)`, equal to the page by construction."""
        self.assertFalse(answers_completeness({"ok": True, "runs": []})[0])
        self.assertFalse(answers_completeness({"messages": [1, 2], "total": 2})[0])
        self.assertFalse(answers_completeness([])[0])
        self.assertTrue(answers_completeness({"runs": [], "truncated": False})[0])
        self.assertTrue(answers_completeness({"messages": [1], "showing": 1, "total": 3189})[0])
        self.assertTrue(answers_completeness({"messages": [1], "totalMessages": 12})[0])

    def test_showing_must_agree_with_the_page_it_describes(self):
        """`showing` that disagrees with the rows returned is a third number, not an answer."""
        ok, why = answers_completeness({"messages": [1, 2, 3], "showing": 80, "total": 3189})
        self.assertFalse(ok, why)

    def test_EVERY_CAPPED_LIST_ANSWERS_COMPLETENESS(self):
        missing = []
        for path in dashboard_capped_paths():
            response = self.client.get(f"/api/v1{path}")
            self.assertEqual(response.status_code, 200, f"{path}: {response.text[:200]}")
            ok, why = answers_completeness(response.json())
            if not ok:
                missing.append((path, why))
        self.assertEqual(missing, [], (
            "these bounded lists cannot say whether they are the whole answer, so a page rendering "
            "exactly what it got cannot tell a short list from a window. Read one row wider than the "
            f"limit and return `truncated`:\n{missing}"
        ))

    def test_EVERY_ROUTE_REPORTS_TRUNCATION_WHEN_IT_IS_TRUNCATED(self):
        """THE HONESTY CHECK, per route rather than per file.

        The first version seeded ONE endpoint while its name claimed all of them, so hardcoding
        `/spawn-requests` to `truncated: false` left the gate green. Each route is now seeded past its
        limit and asked for one row, and fails on its own.
        """
        seeders = self.seeders()
        by_route: dict[str, str] = {}
        for path in dashboard_capped_paths():
            by_route.setdefault(route_of(path), path)

        silent = []
        # RUN EACH SEEDER ONCE. `/contracts` and `/dispatch/runs` deliberately share one -- a contract
        # is a derived view of a run -- and calling it twice hits `UNIQUE constraint failed:
        # messages.id`. An `INSERT OR IGNORE` would swallow that, and would equally swallow a seeder
        # that had stopped inserting anything, which is the failure that makes a witness worthless.
        already: set = set()
        for route, path in sorted(by_route.items()):
            seeder = seeders[route]
            if seeder not in already:
                seeder()
                already.add(seeder)
            base, _, raw_query = path.partition("?")
            query = [p for p in raw_query.split("&") if p and not p.startswith("limit=")]
            probe = f"/api/v1{base}?" + "&".join([*query, "limit=1"])
            body = self.client.get(probe).json()
            ok, why = answers_completeness(body)
            self.assertTrue(ok, f"{route}: {why}")
            if body.get("truncated") is True:
                continue
            if "showing" in body and "total" in body and body["total"] > body["showing"]:
                continue
            page = next((body[k] for k in PAGE_KEYS if isinstance(body.get(k), list)), [])
            if body.get("totalMessages", 0) > len(page):
                continue
            silent.append((route, {k: v for k, v in body.items() if not isinstance(v, list)}))
        self.assertEqual(silent, [], (
            "these routes were given more rows than they were asked for and still did not say the page "
            f"was partial, so their completeness signal is not measuring anything:\n{silent}"
        ))
