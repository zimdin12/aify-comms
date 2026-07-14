"""A resume command must never hand the operator an identity-less session.

Regression guard for the general-manager incident (2026-07-14): the dashboard's
resume command was `claude-aify --resume <id>` with no `--aify-agent`. The wrapper
only exports AIFY_AGENT_ID when the agent id is passed, and EVERY turn-state path
is gated on that variable — the bridge's turn detector (server.js), the Stop /
UserPromptSubmit / PostToolUse hooks, and the session-store capture hook. So an
operator who copied that command got a session that registered, messaged and
heartbeated normally while its status latched forever (only the channel sidecar
could still SET `working`; nothing left alive could CLEAR it).

The command the product hands out must not be the command that breaks the agent.
"""

import unittest

from service.routers.api_v2 import _resume_command_for
from service.runtimes import adapter_for


class ResumeCommandCarriesAgentId(unittest.TestCase):
    def test_every_wrapper_runtime_emits_aify_agent(self):
        for runtime in ("claude-code", "codex", "hermes", "pi", "opencode"):
            with self.subTest(runtime=runtime):
                cmd = _resume_command_for(runtime, "sess-1", "agent-x")
                self.assertIn("--aify-agent agent-x", cmd, cmd)
                self.assertIn("--resume sess-1", cmd, cmd)

    def test_claude_exact_form(self):
        self.assertEqual(
            _resume_command_for("claude-code", "sess-AAA", "general-manager"),
            "claude-aify --aify-agent general-manager --resume sess-AAA",
        )

    def test_falls_back_cleanly_when_agent_id_is_unknown(self):
        # No agent id to give -> still a usable command, just without the flag.
        # (The wrapper's own handle->agent recovery covers this path.)
        self.assertEqual(
            adapter_for("claude-code").resume_command("sess-AAA"),
            "claude-aify --resume sess-AAA",
        )

    def test_no_handle_yields_empty_not_a_broken_command(self):
        self.assertEqual(_resume_command_for("claude-code", "", "agent-x"), "")


if __name__ == "__main__":
    unittest.main()
