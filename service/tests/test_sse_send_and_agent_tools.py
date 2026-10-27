"""Register and presence on the service's tool copy.

Send, channels and the full presence rows are held by the reply fixtures
(`test_tool_replies_match_the_js.py`); what stays here is what they do not reach: registration, and an
agent row with fields missing.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import httpx

from service.sse import agent_tools as ag
from service.sse.api_client import ServiceError


class _Api:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __call__(self, method, path, json_data=None, **kwargs):
        self.calls.append({"method": method, "path": path, "json": json_data})
        return self.payload


def _with_api(module, tool, payload, **kwargs):
    """The tool run against canned answers from `call`, the seam every tool reaches the service by."""
    api = _Api(payload)
    with patch.object(module, "call", api):
        return asyncio.run(tool(**kwargs)), api


def _caller(agent_id):
    """An MCP context whose request names `agent_id` in the X-Aify-Agent-Id header."""
    class _Request:
        headers = {"x-aify-agent-id": agent_id}

    class _RequestContext:
        request = _Request()

    class _Ctx:
        request_context = _RequestContext()

    return _Ctx()


class AgentToolTests(unittest.TestCase):
    def test_register_changes_only_the_header_callers_profile(self):
        """A registration would clear the host's runtime and session; the profile route cannot."""
        out, api = _with_api(ag, ag.comms_register, {"agentId": "agent-a", "role": "coder", "name": "A"},
                             ctx=_caller("agent-a"), role="coder", name="A")
        self.assertEqual([(c["method"], c["path"], c["json"]) for c in api.calls],
                         [("PATCH", "/agents/agent-a/profile", {"role": "coder", "name": "A"})])
        self.assertEqual('Updated "agent-a": role coder, name A.', out)

    def test_an_unregistered_caller_is_told_its_launcher_registers_it(self):
        """No thin record: one written before the launcher's would read generic until it was replaced."""
        async def not_found(*args, **kwargs):
            raise ServiceError(httpx.Response(404, json={"detail": "Agent 'a' not found"}))
        with patch.object(ag, "call", not_found):
            out = asyncio.run(ag.comms_register(ctx=_caller("a"), role="coder"))
        self.assertTrue(out.isError)
        self.assertEqual(ag.NOT_REGISTERED, out.content[0].text)

    def test_any_other_failure_is_reported_as_it_happened(self):
        async def outage(*args, **kwargs):
            raise ServiceError(httpx.Response(500, text="db locked"))
        with patch.object(ag, "call", outage), self.assertRaises(ServiceError):
            asyncio.run(ag.comms_register(ctx=_caller("a"), role="coder"))

    def test_presence_renders_a_half_registered_row_without_inventing_values(self):
        """An agent never seen has no lastSeen; blank would read as "seen, nothing to report". The reply
        fixtures (test_tool_replies_match_the_js.py) hold the full rows; this holds the missing fields."""
        out, _ = _with_api(ag, ag.comms_agents, {"agents": {"b": {"role": "tester"}}}, ctx=None)
        self.assertIn('- b (tester) -- "b" | generic @ ? (resident) | wake: ? | unread: 0 | last seen: ?', out)


if __name__ == "__main__":
    unittest.main()
