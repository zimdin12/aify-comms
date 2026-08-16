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

# v0.5.4: no longer agents-owned. It moved to an api_core leaf so the registration gates could reach
# it without importing upward from a router; see service/api_core/resume_command.py.
from service.api_core.resume_command import _resume_command_for
from service.runtimes import adapter_for, supported_runtimes


class ResumeCommandCarriesAgentId(unittest.TestCase):
    def test_every_wrapper_runtime_emits_aify_agent(self):
        """ENUMERATED FROM THE REGISTRY, not hand-listed.

        This loop named its five runtimes as literals until 2026-08-16, which left the one case the
        guard is for uncovered: a runtime ADDED to `service/runtimes/_REGISTRY` would not join the
        list, so its adapter could ship a resume command with no `--aify-agent` and this test would
        still pass. That is the general-manager incident's exact shape — a session launched without
        an agent id has structurally unfixable status, and nothing errors.

        `supported_runtimes()` is the registry `adapter_for` itself dispatches on, so a new adapter
        is covered the moment it can be resolved at all.
        """
        runtimes = supported_runtimes()
        self.assertGreaterEqual(len(runtimes), 5, f"the registry looks truncated: {runtimes}")
        for runtime in runtimes:
            with self.subTest(runtime=runtime):
                cmd = _resume_command_for(runtime, "sess-1", "agent-x")
                self.assertIn("--aify-agent agent-x", cmd, cmd)
                self.assertIn("--resume sess-1", cmd, cmd)

    def test_the_enumeration_covers_every_adapter_module_on_disk(self):
        """The registry is only a good source if nothing bypasses it. An adapter module that exists
        but is not registered is unreachable through `adapter_for` — either a dead file or a missing
        registration, and both are worth knowing about."""
        import pathlib

        modules = {
            path.stem
            for path in (pathlib.Path(__file__).resolve().parents[1] / "runtimes").glob("*.py")
            if path.stem not in {"__init__", "base"}
        }
        registered = {
            module for module, _cls in
            __import__("service.runtimes", fromlist=["_REGISTRY"])._REGISTRY.values()
        }
        self.assertEqual(
            modules, registered,
            "adapter modules on disk and registry entries disagree — an unregistered adapter is "
            "unreachable through adapter_for, so nothing above would ever test it",
        )

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
