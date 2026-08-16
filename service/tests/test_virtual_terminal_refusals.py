"""Provisioning a virtual terminal — five refusals in one endpoint, none of them tested.

`ensure_virtual_terminal` is how a bridge asks the service to create the synthesized
`terminal_sessions` row that stands in for a PTY a native managed worker does not have. It is a
FUNNEL: five gates in a fixed order, each refusing a different thing.

    404 Agent "<id>" not found
    400 bridgeId is required
    409 Virtual terminal is available for runtimes [...] only (got runtime="<r>")
    404 No environment registered for bridgeId "<id>"
    409 No active agent_session for "<a>" on environment "<e>". The bridge should dispatch at least
        once before requesting a virtual terminal.
    409 Synth terminal creation skipped for wrapper-backed runtime "<r>" (Plan 4 deprecation …)

ALL SIX WERE COUNTED AS EXERCISED UNTIL TODAY, because `service/tests/data/` holds a verbatim
pre-split copy of this function and the coverage scan was reading it. Nothing asserted any of them.

TWO INDEPENDENT LISTS DECIDE WHICH RUNTIMES GET ONE, and they disagree on purpose:

    VIRTUAL_RPC_COMMANDS_BY_RUNTIME   pi, hermes, codex, opencode   — has a sentinel command
    settings["managed_via_wrapper"]   codex, hermes  (default)      — the wrapper PTY IS the terminal

So of the four runtimes that CAN have a virtual terminal, two are refused at the last gate under
default settings — and it is the settings list, not the map, that decides which. Asserted as the
intersection of both rather than as one example each, because the interesting failure is the two
lists drifting: a runtime added to the map and never considered for the deprecation would silently
get a second terminal beside its wrapper PTY, which is the duplicate this module's docstring names as
its own failure mode.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMANDS_BY_RUNTIME
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT_ID = "lc-pi"
BRIDGE_ID = "bridge-one"
ENVIRONMENT_ID = "linux:test-host:default"

#: The statuses the endpoint's own SELECT accepts, spelled here so a change to that inline list
#: fails a test instead of quietly narrowing which sessions can be given a terminal.
ACTIVE_SESSION_STATUSES = ("running", "recovering", "starting", "managed-warm")
INACTIVE_SESSION_STATUSES = ("ended", "stopped", "failed", "dead", "")

#: Wrapper-backed by default — the wrapper PTY is their terminal, so the last gate refuses them.
WRAPPER_BACKED = ("codex", "hermes")


class VirtualTerminalRefusalTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self._register_agent(AGENT_ID, runtime="pi")
        self._heartbeat_environment()

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _register_agent(self, agent_id: str, runtime: str = "pi") -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={"agentId": agent_id, "role": "coder", "runtime": runtime},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _heartbeat_environment(self, bridge_id: str = BRIDGE_ID) -> None:
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": ENVIRONMENT_ID,
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": bridge_id,
                "cwdRoots": ["/workspace"],
                "runtimes": [],
                "status": "online",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _seed_session(self, status: str = "running", agent_id: str = AGENT_ID) -> str:
        """Insert the agent_session the endpoint looks for. Direct, because driving a real dispatch
        to each of nine statuses would be testing the dispatch path instead of this gate."""
        session_id = f"sess-{agent_id}-{status or 'blank'}"

        async def write():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status,"
                    " started_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                    (session_id, agent_id, ENVIRONMENT_ID, "pi", status,
                     "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
                )
                await db.commit()

        asyncio.run(write())
        return session_id

    def _ensure(self, **overrides):
        body = {"bridgeId": BRIDGE_ID}
        body.update(overrides)
        agent_id = body.pop("agentId", AGENT_ID)
        return self.client.post(f"/api/v1/agents/{agent_id}/virtual-terminal/ensure", json=body)

    # ── the funnel, gate by gate ─────────────────────────────────────────────────────────────

    def test_an_unknown_agent_is_404_before_anything_else_is_judged(self):
        """Order, pinned. A request with NOTHING else right still answers about the agent, because
        every later gate reads a row it does not have."""
        response = self._ensure(agentId="no-such-agent", bridgeId="", runtime="nonsense")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], 'Agent "no-such-agent" not found')

    def test_a_missing_bridge_id_is_refused_in_every_empty_spelling(self):
        """The bridge id is what the environment lookup keys on, so an empty one would 404 later
        with a message about the environment — blaming the wrong thing."""
        for bridge_id in ("", "   ", "\t"):
            with self.subTest(bridgeId=bridge_id):
                response = self._ensure(bridgeId=bridge_id)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "bridgeId is required")

    def test_an_absent_bridge_id_is_the_models_job_not_the_gates(self):
        """`bridgeId: str` is REQUIRED, so omitting it is a 422 before the handler runs and the 400
        above is unreachable for that case. Pinned because the two look like the same failure to a
        caller and are refused by different layers — and because making the field optional would
        move a 422 into the handler's 400 without anyone noticing."""
        response = self.client.post(
            f"/api/v1/agents/{AGENT_ID}/virtual-terminal/ensure", json={},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_the_runtime_allowlist_is_exactly_the_virtual_rpc_map(self):
        """As a SET, from the map itself. A runtime with no sentinel command has nothing to write
        into `terminal_sessions.command`, which is what makes a row recognisable as virtual."""
        for runtime in sorted(VIRTUAL_RPC_COMMANDS_BY_RUNTIME):
            with self.subTest(accepted=runtime):
                response = self._ensure(bridgeId="bridge-nope", runtime=runtime)
                self.assertEqual(
                    response.status_code, 404,
                    f"{runtime} is in the map and must reach the environment gate — got "
                    f"{response.status_code} {response.text}",
                )
        for runtime in ("claude-code", "aider", "nonsense", "PI-typo"):
            with self.subTest(refused=runtime):
                response = self._ensure(runtime=runtime)
                self.assertEqual(response.status_code, 409, response.text)
                detail = response.json()["detail"]
                self.assertIn("Virtual terminal is available for runtimes", detail)
                self.assertIn(str(sorted(VIRTUAL_RPC_COMMANDS_BY_RUNTIME)), detail)

    def test_the_runtime_falls_back_to_the_agents_own_when_none_is_sent(self):
        """THE MIDDLE STEP OF THE FALLBACK, which was dead until this commit.

        `req.runtime or agent["runtime"] or "pi"` reads as three steps, and the model's own
        `runtime = "pi"` default meant an omitted runtime never reached step two. It is not
        cosmetic: the runtime picks the sentinel command written into `terminal_sessions.command`,
        so a claude-code agent was admitted as PI — given `aify://virtual-rpc/pi` and waved past the
        wrapper-deprecation gate, because pi is never wrapper-backed and claude-code is.
        """
        self._register_agent("lc-claude", runtime="claude-code")
        response = self._ensure(agentId="lc-claude")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('(got runtime="claude-code")', response.json()["detail"])

    def test_an_agent_that_declared_no_runtime_is_refused_rather_than_called_pi(self):
        """THE THIRD STEP IS UNREACHABLE FOR A REGISTERED AGENT, and this pins why.

        Registration normalises a blank runtime to `generic`, so `agent["runtime"]` is never falsy
        and the endpoint's final `or "pi"` cannot fire. That makes the honest answer for a generic
        agent a 409 naming the four supported runtimes — not a pi terminal for something that is not
        pi. Before the model default was removed this case silently created
        `aify://virtual-rpc/pi`, which every subsystem that recognises virtual rows would have read
        as a pi worker.
        """
        self._register_agent("lc-blank", runtime="")
        self._seed_session(agent_id="lc-blank")
        response = self._ensure(agentId="lc-blank")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('(got runtime="generic")', response.json()["detail"])

    def test_an_unregistered_bridge_id_is_404_and_names_it(self):
        response = self._ensure(bridgeId="bridge-never-registered")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            response.json()["detail"],
            'No environment registered for bridgeId "bridge-never-registered"',
        )

    # ── the session gate, over the whole status vocabulary ───────────────────────────────────

    def test_no_active_session_names_the_agent_the_environment_and_the_remedy(self):
        response = self._ensure()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            f'No active agent_session for "{AGENT_ID}" on environment "{ENVIRONMENT_ID}". '
            "The bridge should dispatch at least once before requesting a virtual terminal.",
        )

    def test_every_ACTIVE_session_status_gets_past_the_session_gate(self):
        """The four the SELECT names, each one. `managed-warm` and `starting` are the ones a reader
        would drop: a bridge asks for the terminal while the worker is still coming up, which is
        exactly when the row is needed."""
        for status in ACTIVE_SESSION_STATUSES:
            with self.subTest(status=status):
                agent_id = f"lc-{status}"
                self._register_agent(agent_id, runtime="pi")
                self._seed_session(status=status, agent_id=agent_id)
                response = self._ensure(agentId=agent_id)
                self.assertEqual(response.status_code, 200, response.text)

    def test_a_finished_session_does_not_count_as_active(self):
        for status in INACTIVE_SESSION_STATUSES:
            with self.subTest(status=status):
                agent_id = f"lc-x-{status or 'blank'}"
                self._register_agent(agent_id, runtime="pi")
                self._seed_session(status=status, agent_id=agent_id)
                response = self._ensure(agentId=agent_id)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("No active agent_session", response.json()["detail"])

    # ── the deprecation gate, and the two lists that decide it ───────────────────────────────

    def test_a_wrapper_backed_runtime_is_refused_at_the_last_gate(self):
        """The wrapper PTY IS the terminal; a synth row beside it is the duplicate this module
        exists to avoid. Under default settings this is codex and hermes."""
        for runtime in WRAPPER_BACKED:
            with self.subTest(runtime=runtime):
                agent_id = f"lc-{runtime}"
                self._register_agent(agent_id, runtime=runtime)
                self._seed_session(agent_id=agent_id)
                response = self._ensure(agentId=agent_id, runtime=runtime)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    f'Synth terminal creation skipped for wrapper-backed runtime "{runtime}" '
                    "(Plan 4 deprecation — the wrapper PTY is the terminal).",
                )

    def test_the_two_lists_are_cross_checked_rather_than_assumed(self):
        """THE DRIFT TEST. Every runtime in the virtual-rpc map is either given a terminal or refused
        by the deprecation gate — never both, never neither. A runtime added to the map without
        being considered for the deprecation would show up here as an extra acceptance, which is the
        silent duplicate-terminal case."""
        accepted, deprecated = set(), set()
        for runtime in sorted(VIRTUAL_RPC_COMMANDS_BY_RUNTIME):
            agent_id = f"lc-cross-{runtime}"
            self._register_agent(agent_id, runtime=runtime)
            self._seed_session(agent_id=agent_id)
            response = self._ensure(agentId=agent_id, runtime=runtime)
            if response.status_code == 200:
                accepted.add(runtime)
            else:
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("wrapper-backed runtime", response.json()["detail"])
                deprecated.add(runtime)
        self.assertEqual(
            accepted | deprecated, set(VIRTUAL_RPC_COMMANDS_BY_RUNTIME),
            "a runtime in the map neither got a terminal nor was refused for having a wrapper",
        )
        self.assertEqual(deprecated, set(WRAPPER_BACKED))
        self.assertEqual(accepted, {"pi", "opencode"}, "the natively-managed runtimes")

    # ── the accepting side, so the refusals are not the only thing pinned ────────────────────

    def test_a_created_terminal_carries_the_maps_sentinel_command(self):
        """What makes the row VIRTUAL. Several unrelated subsystems recognise it by this exact
        string, so writing anything else creates a row they all read as a live process."""
        self._seed_session()
        response = self._ensure()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIs(payload["reused"], False)
        self.assertEqual(payload["terminal"]["command"], VIRTUAL_RPC_COMMANDS_BY_RUNTIME["pi"])
        self.assertEqual(payload["terminal"]["status"], "running")

    def test_a_second_call_reuses_the_row_rather_than_creating_a_second(self):
        """Idempotence is the endpoint's stated contract, and the failure mode it names is two
        terminals for one agent."""
        self._seed_session()
        first = self._ensure()
        self.assertEqual(first.status_code, 200, first.text)
        second = self._ensure()
        self.assertEqual(second.status_code, 200, second.text)
        self.assertIs(second.json()["reused"], True)
        self.assertEqual(second.json()["terminal"]["id"], first.json()["terminal"]["id"])
