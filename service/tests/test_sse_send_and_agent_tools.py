"""Send, dispatch, register and presence — driven for the first time.

The last four `comms_*` tools without a test. `comms_send` is the most-called tool on this transport
and the one whose reply a caller most often acts on without re-checking.

WHAT IS PINNED IS WHO DOES *NOT* GET THE MESSAGE. Both send and dispatch can partially succeed: some
recipients launch, others are declined, and a reply naming only the launched ones lets a caller
believe a team was reached when half of it was skipped. That is the same wrong-belief shape as the
channel and search renderers, in the tool that carries the most traffic.

The flag interaction is pinned too. `steer` and `queueIfBusy` are opposite delivery choices, not
independent switches, so asking for both must resolve one way and not silently send a contradiction
to the server.
"""

from __future__ import annotations

import asyncio
import unittest

from service.sse import agent_tools as ag
from service.sse import send_tools as st


class _Api:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __call__(self, method, path, json_data=None, params=None):
        self.calls.append({"method": method, "path": path, "json": json_data, "params": params})
        return self.payload


def _with_api(module, tool, payload, **kwargs):
    api = _Api(payload)
    original, module._api = module._api, api
    try:
        return asyncio.run(tool(**kwargs)), api
    finally:
        module._api = original


class SendTests(unittest.TestCase):
    def test_no_addressee_is_refused_before_the_api_is_called(self):
        api = _Api({})
        original, st._api = st._api, api
        try:
            out = asyncio.run(st.comms_send(from_agent="me", type="info", subject="s", body="b"))
        finally:
            st._api = original
        self.assertEqual("Error: need 'to' or 'toRole'", out)
        self.assertEqual([], api.calls, "a malformed send must not reach the server at all")

    def test_the_reply_names_the_recipients_the_server_DECLINED_to_start(self):
        out, _ = _with_api(
            st, st.comms_send,
            {"ok": True, "recipients": ["a", "b"],
             "dispatchRuns": [{"targetAgentId": "a", "runId": "r1", "status": "queued"}],
             "notStarted": [{"targetAgentId": "b", "reason": "offline"}]},
            from_agent="me", type="info", subject="s", body="b", to="a",
        )
        self.assertIn("Not started: b: offline", out)
        self.assertIn("a [queued] -> r1", out)

    def test_a_send_that_launched_nothing_says_so(self):
        out, _ = _with_api(
            st, st.comms_send,
            {"ok": True, "recipients": ["a"], "dispatchRuns": [],
             "notStarted": [{"targetAgentId": "a", "reason": "stale"}]},
            from_agent="me", type="info", subject="s", body="b", to="a",
        )
        self.assertIn("no launchable recipients", out)

    def test_a_failed_send_returns_the_server_error_not_a_success_line(self):
        out, _ = _with_api(st, st.comms_send, {"ok": False, "error": "target is offline"},
                           from_agent="me", type="info", subject="s", body="b", to="ghost")
        self.assertEqual("target is offline", out)

    def test_a_failed_send_with_no_error_still_does_not_claim_delivery(self):
        out, _ = _with_api(st, st.comms_send, {"ok": False},
                           from_agent="me", type="info", subject="s", body="b", to="ghost")
        self.assertEqual("No recipients found.", out)

    def test_queueIfBusy_forces_steer_off_even_when_steer_was_asked_for(self):
        """Opposite delivery choices. Sending both would leave the server to guess."""
        _, api = _with_api(st, st.comms_send, {"ok": True, "messageId": "m", "recipients": ["a"]},
                           from_agent="me", type="info", subject="s", body="b", to="a",
                           queueIfBusy=True, steer=True)
        sent = api.calls[0]["json"]
        self.assertIs(False, sent["steer"])
        self.assertIs(True, sent["queueIfBusy"])

    def test_steer_defaults_on_and_can_be_turned_off_explicitly(self):
        for asked, expected in ((None, True), (False, False), (True, True)):
            _, api = _with_api(st, st.comms_send, {"ok": True, "messageId": "m", "recipients": ["a"]},
                               from_agent="me", type="info", subject="s", body="b", to="a",
                               steer=asked)
            self.assertIs(expected, api.calls[0]["json"]["steer"], f"steer={asked}")

    def test_silent_send_does_not_claim_live_delivery(self):
        out, api = _with_api(st, st.comms_send,
                             {"ok": True, "messageId": "m1", "recipients": ["a"],
                              "dispatchRuns": [{"targetAgentId": "a", "runId": "r1"}]},
                             from_agent="me", type="info", subject="s", body="b", to="a", silent=True)
        self.assertNotIn("live delivery", out)
        self.assertIn("m1", out)
        self.assertIs(False, api.calls[0]["json"]["trigger"])

    def test_optional_addressing_fields_are_omitted_when_unset(self):
        _, api = _with_api(st, st.comms_send, {"ok": True, "messageId": "m", "recipients": ["a"]},
                           from_agent="me", type="info", subject="s", body="b", toRole="coder")
        sent = api.calls[0]["json"]
        self.assertEqual("coder", sent["toRole"])
        self.assertNotIn("to", sent)
        self.assertNotIn("inReplyTo", sent)

    def test_requireReply_is_passed_through_including_None(self):
        """None means "use the type default" and is a THIRD state, not a missing value."""
        for asked in (None, True, False):
            _, api = _with_api(st, st.comms_send, {"ok": True, "messageId": "m", "recipients": ["a"]},
                               from_agent="me", type="request", subject="s", body="b", to="a",
                               requireReply=asked)
            self.assertIs(asked, api.calls[0]["json"]["requireReply"], f"requireReply={asked}")


class DispatchTests(unittest.TestCase):
    def test_no_addressee_is_refused_before_the_api_is_called(self):
        api = _Api({})
        original, st._api = st._api, api
        try:
            out = asyncio.run(st.comms_dispatch(from_agent="me", type="info", subject="s", body="b"))
        finally:
            st._api = original
        self.assertEqual("Error: need 'to' or 'toRole'", out)
        self.assertEqual([], api.calls)

    def test_requireStart_selects_the_stricter_mode(self):
        for require, mode in ((True, "require_start"), (False, "start_if_possible")):
            _, api = _with_api(st, st.comms_dispatch, {"ok": True, "runs": [{"targetAgentId": "a",
                               "runId": "r", "status": "queued"}]},
                               from_agent="me", type="info", subject="s", body="b", to="a",
                               requireStart=require)
            self.assertEqual(mode, api.calls[0]["json"]["mode"], f"requireStart={require}")

    def test_a_dispatch_that_created_nothing_says_so_rather_than_rendering_empty(self):
        out, _ = _with_api(st, st.comms_dispatch, {"ok": True, "runs": [], "notStarted": []},
                           from_agent="me", type="info", subject="s", body="b", to="a")
        self.assertEqual("No dispatch runs were created.", out)

    def test_declined_targets_are_listed_under_their_own_heading(self):
        out, _ = _with_api(st, st.comms_dispatch,
                           {"ok": True, "runs": [{"targetAgentId": "a", "runId": "r1", "status": "queued"}],
                            "notStarted": [{"targetAgentId": "b", "reason": "no runtime"}]},
                           from_agent="me", type="info", subject="s", body="b", toRole="coder")
        self.assertIn("Not started:", out)
        self.assertIn("- b: no runtime", out)

    def test_the_two_modes_point_the_caller_somewhere_different(self):
        """requireStart failed loudly already; the other mode has to say prefer comms_send."""
        payload = {"ok": True, "runs": [{"targetAgentId": "a", "runId": "r", "status": "queued"}]}
        strict, _ = _with_api(st, st.comms_dispatch, payload, from_agent="me", type="info",
                              subject="s", body="b", to="a", requireStart=True)
        loose, _ = _with_api(st, st.comms_dispatch, payload, from_agent="me", type="info",
                             subject="s", body="b", to="a", requireStart=False)
        self.assertIn("prefer comms_send", strict)
        self.assertNotIn("prefer comms_send", loose)
        self.assertIn("expects an explicit reply", loose)


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

    def test_no_agents_says_so(self):
        out, _ = _with_api(ag, ag.comms_agents, {"agents": {}})
        self.assertEqual("No agents registered.", out)

    def test_presence_renders_a_half_registered_row_without_inventing_values(self):
        """An agent never seen has no lastSeen; blank would read as "seen, nothing to report"."""
        out, _ = _with_api(ag, ag.comms_agents, {"agents": {
            "a": {"role": "coder", "status": "online", "name": "Ada", "unread": 3, "lastSeen": "t"},
            "b": {"role": "tester"},
        }})
        self.assertIn('- a (coder) [online] -- "Ada" | unread: 3 | last seen: t', out)
        self.assertIn("- b (tester)", out)
        self.assertIn("unread: 0", out)
        self.assertIn("last seen: ?", out)


if __name__ == "__main__":
    unittest.main()
