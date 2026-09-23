"""The machine an external key proved reaches every reader that names a sender, stated as a fact.

A message sent with another machine's key is stored with that machine's name (service/api_core/
external_keys.py). An agent learns who sent something from several different places -- the inbox,
the dispatch claim it is woken with, a steer injected mid-turn, the reply hint when it answers, the
SSE transport's inbox -- and when `origin` was added it reached ONE of them and missed the rest. This
file walks each, through the real key middleware.

Also here, because it rides the same send path: a send carrying the OPERATOR key is the operator at
the dashboard acting AS the agent, and must not count as the agent being present.
"""

from __future__ import annotations

import asyncio

from service.api_core.external_keys import parse_external_keys
from service.api_core.message_view import _sender_label
from service.main import APIKeyMiddleware
from service.sse.inbox_tools import _sender_of
from service.tests._base import FastApiTestCase
from service.tests.test_a_message_from_a_sender_we_do_not_know_says_so import _seed_run, _steer_bodies

SERVICE_KEY = "service-key-for-this-file-0001"
PC2_KEY = "pc2-key-for-this-file-000000001"
LAPTOP_KEY = "laptop-key-for-this-file-0000001"
OPERATOR_KEY = "operator-key-for-this-file-0001"
HOME = "claimer"
REMOTE = "pc2-manager"


class _TwoMachinesAndOneLocalAgent(FastApiTestCase):
    """The key middleware with two machines' keys, and one local agent. Helpers only: no tests here."""

    DB_NAME = "aify-external-machine-readers.db"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._app.add_middleware(APIKeyMiddleware, api_key=SERVICE_KEY,
                                external_keys=parse_external_keys(f"pc2:{PC2_KEY},laptop:{LAPTOP_KEY}"))

    def setUp(self) -> None:
        super().setUp()
        self._app.state.config.operator_key = OPERATOR_KEY
        self._post("/api/v1/agents", {
            "agentId": HOME, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
            "machineId": "linux:test-host", "bridgeId": "bridge-claimer", "launchMode": "detached",
            "capabilities": ["resident-run", "resume", "interrupt", "steer"],
            "runtimeConfig": {"channelEnabled": True},
        })

    def _post(self, path: str, body: dict, key: str = SERVICE_KEY, **headers):
        response = self.client.post(path, json=body, headers={"X-API-Key": key, **headers})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _from_pc2(self, subject: str, **extra) -> dict:
        return self._post("/api/v1/messages/send", {
            "from_agent": REMOTE, "to": HOME, "type": "info", "subject": subject, "body": "hello", **extra,
        }, key=PC2_KEY)

    def _inbox(self) -> list[dict]:
        response = self.client.get(f"/api/v1/messages/inbox/{HOME}?peek=true&limit=50",
                                   headers={"X-API-Key": SERVICE_KEY})
        return response.json()["messages"]

    def _claim(self) -> dict:
        return self._post("/api/v1/dispatch/claim", {
            "agentId": HOME, "bridgeId": "channel-linux:test-host-claimer", "bridgeKind": "channel-sidecar",
            "machineId": "linux:test-host", "executionModes": ["channel", "resident"],
        })


class EveryReaderNamesTheMachine(_TwoMachinesAndOneLocalAgent):
    def test_the_dispatch_claim_carries_the_machine(self) -> None:
        self._from_pc2("wake me")
        [message] = [m for m in self._inbox() if m["subject"] == "wake me"]
        _seed_run(message_id=message["id"], from_agent=REMOTE, target=HOME)
        run = self._claim().get("run")
        self.assertIsNotNone(run)
        self.assertEqual(run.get("externalMachine"), "pc2")

    def test_a_steer_into_a_busy_agent_names_the_machine(self) -> None:
        self._post("/api/v1/messages/send", {"from_agent": "local", "to": HOME, "type": "info",
                                             "subject": "keep it busy", "body": "work"})
        [busy] = [m for m in self._inbox() if m["subject"] == "keep it busy"]
        _seed_run(message_id=busy["id"], from_agent="local", target=HOME)
        self.assertIsNotNone(self._claim().get("run"))
        self._from_pc2("mid-turn", trigger=True)
        [steer] = _steer_bodies(HOME)
        self.assertIn("sent from pc2, proven by that machine's key", steer)

    def test_a_reply_to_the_remote_agent_says_which_machine_it_is_on(self) -> None:
        self._from_pc2("answer me")
        result = self._post("/api/v1/messages/send", {"from_agent": HOME, "to": REMOTE, "type": "response",
                                                      "subject": "re", "body": "done", "trigger": True})
        [skipped] = [n for n in result.get("notStarted", []) if n["targetAgentId"] == REMOTE]
        self.assertIn("pc2", skipped["reason"])
        self.assertIn("pc2", skipped["fix"], "with no declared address, the machine is where to send it")

    def test_the_operator_sending_as_an_agent_is_not_the_agent_being_present(self) -> None:
        def present() -> str:
            from service.db import get_db

            async def read():
                db = await get_db()
                try:
                    row = await (await db.execute("SELECT last_present_at FROM agents WHERE id = ?", (HOME,))).fetchone()
                    return str(row[0] or "")
                finally:
                    await db.close()
            return asyncio.run(read())

        async def clear():
            from service.db import get_db
            db = await get_db()
            try:
                await db.execute("UPDATE agents SET last_present_at = '' WHERE id = ?", (HOME,))
                await db.commit()
            finally:
                await db.close()

        body = {"from_agent": HOME, "to": "someone", "type": "info", "subject": "s", "body": "b"}
        asyncio.run(clear())
        self._post("/api/v1/messages/send", body, **{"X-Aify-Operator-Key": OPERATOR_KEY})
        self.assertEqual(present(), "", "the operator sent this from the dashboard; the agent was not here")
        self._post("/api/v1/messages/send", body)
        self.assertNotEqual(present(), "", "CONTROL: the agent's own send still counts")


class AMachineSpeaksOnlyForItsOwnAgents(_TwoMachinesAndOneLocalAgent):
    """Found by an adversarial review of the first version, 2026-09-24; each case was a working repro."""

    DB_NAME = "aify-external-machine-ownership.db"

    def _send_as(self, key: str, sender: str, subject: str, **extra):
        return self.client.post("/api/v1/messages/send", json={
            "from_agent": sender, "to": HOME, "type": "info", "subject": subject, "body": "b", **extra,
        }, headers={"X-API-Key": key})

    def test_a_proven_message_stays_external_when_the_name_registers_here_later(self) -> None:
        self.assertEqual(self._send_as(PC2_KEY, "future", "before").status_code, 200)
        self._post("/api/v1/agents", {"agentId": "future", "role": "coder", "runtime": "generic",
                                      "sessionMode": "resident"})
        [message] = [m for m in self._inbox() if m["subject"] == "before"]
        self.assertIs(message["fromRegistered"], False, "registration says a name exists, not who sent this")
        self.assertEqual(message["externalMachine"], "pc2")

    def test_a_machine_cannot_take_over_another_machines_agent(self) -> None:
        self.assertEqual(self._send_as(LAPTOP_KEY, "lap-mgr", "mine", origin="http://laptop:8800").status_code, 200)
        taken = self._send_as(PC2_KEY, "lap-mgr", "hijack", origin="http://evil:8800")
        self.assertEqual(taken.status_code, 403, taken.text)
        self.assertIn("' cannot speak for it. Each machine's agents need names of their own.", taken.json()["detail"])
        self.assertEqual(self._send_as(LAPTOP_KEY, "lap-mgr", "still mine").status_code, 200,
                         "CONTROL: the machine that owns the name keeps using it")

    def test_the_service_key_cannot_redirect_a_proven_senders_replies(self) -> None:
        self._send_as(LAPTOP_KEY, "lap-mgr", "mine", origin="http://laptop:8800")
        self._send_as(SERVICE_KEY, "lap-mgr", "redirect", origin="http://evil:8800")
        result = self._post("/api/v1/messages/send", {"from_agent": HOME, "to": "lap-mgr", "type": "response",
                                                      "subject": "re", "body": "done", "trigger": True})
        [skipped] = [n for n in result.get("notStarted", []) if n["targetAgentId"] == "lap-mgr"]
        self.assertIn("http://laptop:8800", skipped["reason"])
        self.assertNotIn("evil", skipped["reason"])

    def test_a_local_name_in_another_case_is_still_local(self) -> None:
        for sender in ("Dashboard", "OPERATOR", HOME.upper()):
            with self.subTest(sender):
                self.assertEqual(self._send_as(PC2_KEY, sender, f"as {sender}").status_code, 403)


class TheLabelStatesTheMachineAndQuotesTheClaim(FastApiTestCase):
    """Both transports, one wording. The machine is a fact and is stated; the origin is quoted."""

    def test_both_transports_state_the_machine(self) -> None:
        payload = {"from": REMOTE, "fromRegistered": False, "externalMachine": "pc2", "origin": "10.0.0.9:8800"}
        label = _sender_of(payload)
        self.assertEqual(label, _sender_label(REMOTE, registered=False, origin="10.0.0.9:8800", machine="pc2"))
        self.assertIn("sent from pc2, proven by that machine's key", label)
        self.assertIn('says it is reachable at "10.0.0.9:8800"', label)

    def test_CONTROL_without_a_machine_the_wording_is_what_it_was(self) -> None:
        self.assertEqual(
            _sender_label(REMOTE, registered=False, origin=""),
            f"{REMOTE} (external: not registered here, gave no return address; a reply sent here is only stored here)",
        )
