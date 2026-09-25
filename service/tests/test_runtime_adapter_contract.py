"""The runtime adapters' shared contract, and the resume command that must not latch an agent.

`resume_command` was among the 71 service functions the suite never entered. It is part of a
contract EVERY adapter has to satisfy, so this file is a census over the registry rather than a test
of one adapter: a sixth runtime added tomorrow is covered the day it appears.

THE RESUME COMMAND CARRIES AN INCIDENT. It is what the dashboard hands an operator to attach to a
session, and it MUST include `--aify-agent <id>` when the agent is known. Every turn-state path in a
wrapper-launched session is gated on AIFY_AGENT_ID, which the wrapper only exports when the id is
passed — so a resume command without it produces a session that registers, messages and heartbeats
normally while its status latches forever. That is the general-manager "always working" incident,
and the command the product hands out must never be the one that breaks the agent.
"""

from __future__ import annotations

import unittest

from service.runtimes import _REGISTRY, adapter_for
from service.runtimes.base import RuntimeAdapter

ALL_RUNTIMES = sorted(_REGISTRY)


class _Bare(RuntimeAdapter):
    """A subclass with nothing filled in — what a new adapter looks like on day one."""

    name = "bare"
    session_env_vars = ["BARE_SESSION_ID"]
    supports_resident = True


class RuntimeRegistryTests(unittest.TestCase):
    def test_the_registry_is_the_five_runtimes_this_product_supports(self):
        """Anti-vacuity for every census below: they all iterate this list."""
        self.assertEqual(
            ALL_RUNTIMES, ["claude-code", "codex", "hermes", "opencode", "pi"],
        )

    def test_every_registered_runtime_resolves_to_an_adapter(self):
        for runtime in ALL_RUNTIMES:
            with self.subTest(runtime=runtime):
                adapter = adapter_for(runtime)
                self.assertIsInstance(adapter, RuntimeAdapter)
                self.assertEqual(adapter.name, runtime)


class ResumeCommandContractTests(unittest.TestCase):
    # The incident census itself -- every adapter carries `--aify-agent <id>` and the handle when it
    # knows the id -- is `test_resume_command_carries_agent_id.py`, through `_resume_command_for`,
    # the function the dashboard and the 409 actually call.
    def test_every_adapter_still_produces_a_command_with_no_agent_id(self):
        """The id is not always known — a session-changed row may have none. The command must still
        be copyable rather than empty or malformed."""
        for runtime in ALL_RUNTIMES:
            with self.subTest(runtime=runtime):
                command = adapter_for(runtime).resume_command("sess-123")
                self.assertIn("sess-123", command)
                self.assertNotIn("--aify-agent", command)

    def test_every_adapter_resumes_through_ITS_OWN_wrapper(self):
        """The wrapper is what exports AIFY_AGENT_ID and starts the bridge. Handing out the bare
        runtime CLI produces a session aify-comms cannot see at all.

        THE PROGRAM, compared as a whole token. The opencode adapter once declared its wrapper as
        `opencode` while resuming through `opencode-aify`, and a prefix check passed because one is a
        prefix of the other. Every wrapper is `<runtime>-aify`, so the program is asserted to be one.
        """
        for runtime in ALL_RUNTIMES:
            with self.subTest(runtime=runtime):
                program = adapter_for(runtime).resume_command("s", "a").split()[0]
                self.assertTrue(
                    program.endswith("-aify"),
                    f"{runtime} resumes by running {program!r}, which is not an aify wrapper",
                )

    def test_a_new_adapter_that_forgets_resume_command_fails_LOUDLY(self):
        """The base raises rather than returning something plausible, and names the runtime — an
        empty string here would reach the dashboard as a copyable command that does nothing."""
        with self.assertRaises(NotImplementedError) as caught:
            _Bare().resume_command("s")
        self.assertIn("bare", str(caught.exception))


class ResidentReadinessTests(unittest.TestCase):
    def test_the_default_readiness_is_just_the_capability(self):
        self.assertTrue(_Bare().is_resident_ready({}))

    def test_every_adapter_agrees_with_itself_about_resident_support(self):
        """`supports_resident` is read by the session-mode switch to refuse a managed-only runtime.
        An adapter whose readiness says yes while its capability says no would let that switch
        through and leave the agent presence-only."""
        for runtime in ALL_RUNTIMES:
            with self.subTest(runtime=runtime):
                adapter = adapter_for(runtime)
                if not adapter.supports_resident:
                    self.assertFalse(
                        adapter.is_resident_ready({}),
                        f"{runtime} is managed-only but reports itself resident-ready",
                    )
