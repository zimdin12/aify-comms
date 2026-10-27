"""Register and presence on the service's tool copy.

Send, channels and the full presence rows are held by the reply fixtures
(`test_tool_replies_match_the_js.py`); what stays here is what they do not reach: registration, and an
agent row with fields missing.
"""

from __future__ import annotations

import asyncio
import unittest

from service.sse import agent_tools as ag


class _Api:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __call__(self, method, path, json_data=None, params=None):
        self.calls.append({"method": method, "path": path, "json": json_data, "params": params})
        return self.payload


def _with_api(module, tool, payload, **kwargs):
    """The tool run against canned answers on BOTH seams a module may use (`_api`, and the ported tools'
    `call`), so a tool that moved seams cannot reach the real service from a test."""
    api = _Api(payload)
    stubs = {name: api for name in ("_api", "call") if hasattr(module, name)}
    originals = {name: getattr(module, name) for name in stubs}
    for name, stub in stubs.items():
        setattr(module, name, stub)
    try:
        return asyncio.run(tool(**kwargs)), api
    finally:
        for name, original in originals.items():
            setattr(module, name, original)


class AgentToolTests(unittest.TestCase):
    def test_register_falls_back_to_the_id_when_no_name_is_given(self):
        _, api = _with_api(ag, ag.comms_register, {"agentId": "a"}, agentId="a", role="coder")
        self.assertEqual("a", api.calls[0]["json"]["name"])

    def test_register_surfaces_a_refusal(self):
        out, _ = _with_api(ag, ag.comms_register, {"detail": "id already taken"},
                           agentId="a", role="coder")
        self.assertEqual("Error: id already taken", out)

    def test_register_reports_the_id_the_SERVER_accepted(self):
        """The server may normalise the id; echoing the requested one would be a false receipt."""
        out, _ = _with_api(ag, ag.comms_register, {"agentId": "a-1"}, agentId="A_1", role="coder")
        self.assertIn("a-1", out)

    def test_the_description_still_says_an_SSE_client_cannot_host_a_launch(self):
        """It is the first thing a connecting agent reads, and the reason this surface is REDUCED
        rather than incomplete."""
        doc = ag.comms_register.__doc__ or ""
        self.assertIn("cannot host local runtime launches", doc)

    def test_presence_renders_a_half_registered_row_without_inventing_values(self):
        """An agent never seen has no lastSeen; blank would read as "seen, nothing to report". The reply
        fixtures (test_tool_replies_match_the_js.py) hold the full rows; this holds the missing fields."""
        out, _ = _with_api(ag, ag.comms_agents, {"agents": {"b": {"role": "tester"}}}, ctx=None)
        self.assertIn('- b (tester) -- "b" | generic @ ? (resident) | wake: ? | unread: 0 | last seen: ?', out)


if __name__ == "__main__":
    unittest.main()
