"""The runtime adapters' shared contract, and the resume command that must not latch an agent.

`display_name` and `resume_command` were among the 71 service functions the suite never entered.
Both are part of a contract EVERY adapter has to satisfy, so this file is a census over the registry
rather than a test of one adapter: a sixth runtime added tomorrow is covered the day it appears.

THE RESUME COMMAND CARRIES AN INCIDENT. It is what the dashboard hands an operator to attach to a
session, and it MUST include `--aify-agent <id>` when the agent is known. Every turn-state path in a
wrapper-launched session is gated on AIFY_AGENT_ID, which the wrapper only exports when the id is
passed — so a resume command without it produces a session that registers, messages and heartbeats
normally while its status latches forever. That is the general-manager "always working" incident,
and the command the product hands out must never be the one that breaks the agent.

THE PLACEHOLDER SETS ARE THE OTHER SHARED RULE. A runtime that has no session yet reports one
anyway: `unknown`, `default`, `none`, `null`. Treating any of those as a real handle produces a
`--resume unknown`, which starts a NEW session while claiming to continue an old one — the operator
sees an agent that lost its context for no visible reason.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from service.runtimes import _REGISTRY, adapter_for
from service.runtimes.base import HANDLE_PLACEHOLDERS, MODEL_PLACEHOLDERS, RuntimeAdapter

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
    def test_EVERY_adapter_carries_the_agent_id_when_it_knows_it(self):
        """THE INCIDENT, as a census. An adapter that forgets `--aify-agent` hands the operator a
        session whose status latches forever — registering, messaging and heartbeating normally the
        whole time, which is why it took a live investigation to find."""
        for runtime in ALL_RUNTIMES:
            with self.subTest(runtime=runtime):
                command = adapter_for(runtime).resume_command("sess-123", "lc-coder")
                self.assertIn("--aify-agent lc-coder", command)
                self.assertIn("sess-123", command)

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
        runtime CLI produces a session aify-comms cannot see at all."""
        for runtime in ALL_RUNTIMES:
            with self.subTest(runtime=runtime):
                adapter = adapter_for(runtime)
                self.assertTrue(
                    adapter.resume_command("s", "a").startswith(adapter.wrapper_name),
                    f"{runtime} resumes with something other than {adapter.wrapper_name}",
                )

    def test_a_new_adapter_that_forgets_resume_command_fails_LOUDLY(self):
        """The base raises rather than returning something plausible, and names the runtime — an
        empty string here would reach the dashboard as a copyable command that does nothing."""
        with self.assertRaises(NotImplementedError) as caught:
            _Bare().resume_command("s")
        self.assertIn("bare", str(caught.exception))


class SharedNormalisationTests(unittest.TestCase):
    def setUp(self):
        self.adapter = _Bare()

    def test_display_name_falls_back_to_the_runtime_name(self):
        self.assertEqual(self.adapter.display_name, "bare")

    def test_every_shipped_adapter_declares_a_HUMAN_display_name(self):
        """It is what an operator reads in the dashboard. Falling back to the canonical name would
        show `claude-code` where the product says `Claude Code`."""
        for runtime in ALL_RUNTIMES:
            with self.subTest(runtime=runtime):
                adapter = adapter_for(runtime)
                self.assertTrue(adapter.display_name)
                self.assertIsInstance(adapter.display_name, str)

    def test_a_placeholder_handle_is_NOT_a_session(self):
        """`--resume unknown` starts a NEW session while claiming to continue one, and the operator
        sees an agent that lost its context for no visible reason."""
        for placeholder in sorted(HANDLE_PLACEHOLDERS):
            for spelling in (placeholder, placeholder.upper(), f"  {placeholder}  "):
                with self.subTest(handle=spelling):
                    self.assertEqual(self.adapter.normalize_session_handle(spelling), "")

    def test_a_real_handle_survives_normalisation_intact(self):
        for handle in ("sess-123", "01JAB2C3D4", "thread_abc-DEF"):
            with self.subTest(handle=handle):
                self.assertEqual(self.adapter.normalize_session_handle(handle), handle)

    def test_whitespace_and_none_normalise_to_empty(self):
        for raw in (None, "", "   ", "\t\n"):
            with self.subTest(raw=raw):
                self.assertEqual(self.adapter.normalize_session_handle(raw), "")

    def test_resume_args_are_EMPTY_for_a_handle_that_is_not_one(self):
        """The caller splices these into a command line. Returning `["--resume", ""]` produces a
        flag with no value, which most CLIs read as the next argument."""
        for raw in (None, "", "unknown", "DEFAULT"):
            with self.subTest(raw=raw):
                self.assertEqual(self.adapter.resume_args(raw), [])
        self.assertEqual(self.adapter.resume_args("sess-1"), ["--resume", "sess-1"])

    def test_a_placeholder_MODEL_is_not_an_override(self):
        """`auto` and `default` mean "let the runtime choose". Passing them through as a model name
        hands the CLI a model that does not exist."""
        for placeholder in sorted(MODEL_PLACEHOLDERS):
            with self.subTest(model=placeholder):
                self.assertEqual(self.adapter.normalize_model_override(placeholder.upper()), "")
        self.assertEqual(self.adapter.normalize_model_override("  opus  "), "opus")

    def test_the_two_placeholder_sets_are_deliberately_DIFFERENT(self):
        """`auto` is a model placeholder and not a handle one; `none`/`null` are handle placeholders
        and not model ones. Collapsing them would silently drop a model legitimately called `none`
        or accept a handle of `auto`."""
        self.assertIn("auto", MODEL_PLACEHOLDERS)
        self.assertNotIn("auto", HANDLE_PLACEHOLDERS)
        self.assertIn("null", HANDLE_PLACEHOLDERS)
        self.assertNotIn("null", MODEL_PLACEHOLDERS)


class SessionEnvTests(unittest.TestCase):
    """These read the LIVE environment, so every test seals what it touches — the operator's own
    shell exports several of these variables, and a test that read one would pass here and fail
    anywhere else (or, worse, the reverse)."""

    def setUp(self):
        self.adapter = _Bare()

    def _sealed(self, **values):
        clear = {var: "" for var in ("BARE_SESSION_ID", "OTHER_SESSION_ID")}
        return mock.patch.dict(os.environ, {**clear, **values}, clear=False)

    def test_the_session_id_comes_from_the_declared_env_var(self):
        with self._sealed(BARE_SESSION_ID="sess-live"):
            self.assertEqual(self.adapter.get_current_session_id(), "sess-live")

    def test_a_placeholder_in_the_environment_is_not_a_session(self):
        """A runtime that exports `CLAUDE_SESSION_ID=unknown` before it has one is the normal case
        at boot, not an edge one."""
        with self._sealed(BARE_SESSION_ID="unknown"):
            self.assertIsNone(self.adapter.get_current_session_id())

    def test_no_session_variable_at_all_answers_None(self):
        with self._sealed():
            self.assertIsNone(self.adapter.get_current_session_id())

    def test_the_FIRST_declared_variable_that_has_a_value_wins(self):
        """Order matters: hermes declares two, and the first is the canonical one. Reading them in
        the wrong order attaches to a stale session id left by an earlier run."""

        class _TwoVars(RuntimeAdapter):
            name = "two"
            session_env_vars = ["BARE_SESSION_ID", "OTHER_SESSION_ID"]

        with mock.patch.dict(os.environ, {"BARE_SESSION_ID": "first", "OTHER_SESSION_ID": "second"}):
            self.assertEqual(_TwoVars().get_current_session_id(), "first")
        with mock.patch.dict(os.environ, {"BARE_SESSION_ID": "", "OTHER_SESSION_ID": "second"}):
            self.assertEqual(_TwoVars().get_current_session_id(), "second")

    def test_diagnostic_env_says_UNSET_rather_than_showing_nothing(self):
        """It is read by an operator diagnosing a session that will not attach. An empty string next
        to a variable name reads as "set to empty", which is a different fault."""
        with self._sealed():
            self.assertEqual(self.adapter.diagnostic_env(), {"BARE_SESSION_ID": "(unset)"})
        with self._sealed(BARE_SESSION_ID="sess-1"):
            self.assertEqual(self.adapter.diagnostic_env(), {"BARE_SESSION_ID": "sess-1"})

    def test_reading_the_environment_does_not_change_it(self):
        with self._sealed(BARE_SESSION_ID="  sess-1  "):
            self.adapter.get_current_session_id()
            self.assertEqual(os.environ["BARE_SESSION_ID"], "  sess-1  ")

    def test_the_seal_leaves_the_environment_as_it_found_it(self):
        """The rule this suite exists under: a test that sets a session variable and walks away
        decides the behaviour of every later test in the run."""
        before = os.environ.get("BARE_SESSION_ID")
        with self._sealed(BARE_SESSION_ID="sess-1"):
            pass
        self.assertEqual(os.environ.get("BARE_SESSION_ID"), before)


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
