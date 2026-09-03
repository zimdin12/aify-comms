"""The environment a managed worker launches with, composed once by the tier that knows it.

WHY IT MOVED. Every value here was composed on the HOST, in `mcp/stdio/terminal-env.js`, by the
aify-comms environment bridge. That bridge is being removed and aify-env becomes the process host.
Porting those 90 lines into aify-env would put a second copy of a file where every line has a defect
behind it into a second repo -- the wrapper-template mistake, which this project already paid for
and closed by consuming a package instead of copying.

The service already composes `command` and `argv` for the same launch, and `service/runtimes/*.py`
already declares `session_env_vars` per runtime. The knowledge was here and was being re-derived
there.

WHAT THESE PIN, in order of what they cost when absent:

1. ALWAYS-SET keys are always set, INCLUDING to "". A host merges this overlay over its own
   environment, so a key left out is INHERITED rather than absent. Two separate bugs lived in that
   gap: an unset AIFY_AGENT_ROLE let a worker inherit the bridge's role and overwrite the spawn's
   (ask for a tester, get a coder); an unset AIFY_HERMES_FRESH_CONTEXT let one Reset make every
   later spawn start fresh for ever.
2. The two OVERRIDES are conditional, and correctly the other way round: "" would state that the
   spawn chose an empty model, which a runtime cannot act on.
3. The runtime's session variables come from the ADAPTER, so they cannot drift from the runtime
   definition that owns them.
4. It composes nothing only the host can know -- no base environment, no CODEX_HOME. A process
   environment on the wire would carry whatever the sender happened to hold, including its secrets.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from service.api_core.launch_env import (
    ALWAYS_SET,
    launches_via_wrapper,
    managed_launch_env,
    session_env_vars_for,
)

REPO = Path(__file__).resolve().parents[2]
JS_SOURCE = REPO / "mcp" / "stdio" / "terminal-env.js"


def _terminal(**overrides):
    base = {
        "id": "term-1", "agentId": "sc-lead", "runtime": "claude-code",
        "workspace": "C:/work", "sessionHandle": "",
    }
    base.update(overrides)
    return base


class TheLaunchEnvironmentHasOneOwnerTests(unittest.TestCase):
    def test_every_always_set_key_is_present_even_when_it_is_empty(self):
        """THE DEFECT CLASS. A host merges this over its own environment, so a missing key is an
        INHERITED one. Both bugs this prevents shipped, and neither reported a problem."""
        env = managed_launch_env(terminal=_terminal(), agent={}, workspace="", terminal_id="")
        for name in ALWAYS_SET:
            self.assertIn(name, env, f"{name} was omitted, so a host would inherit whatever it holds")

    def test_an_absent_role_is_written_as_EMPTY_rather_than_left_out(self):
        """The specific one: an inherited AIFY_AGENT_ROLE made a worker's self-register overwrite the
        spawn's role. An empty value makes the child fall back to its own default; an inherited one
        makes it confidently wrong."""
        env = managed_launch_env(terminal=_terminal(), agent={})
        self.assertEqual(env["AIFY_AGENT_ROLE"], "")

    def test_a_reset_reaches_the_worker_and_a_normal_spawn_does_not_inherit_it(self):
        reset = managed_launch_env(
            terminal=_terminal(), agent={"runtimeState": {"resumePolicy": "fresh_context"}},
        )
        ordinary = managed_launch_env(terminal=_terminal(), agent={"runtimeState": {}})
        self.assertEqual(reset["AIFY_HERMES_FRESH_CONTEXT"], "1")
        self.assertEqual(
            ordinary["AIFY_HERMES_FRESH_CONTEXT"], "",
            "an unset value is INHERITED, which is how one Reset became permanent",
        )

    def test_the_two_OVERRIDES_are_omitted_when_unset_rather_than_blanked(self):
        """The opposite rule, and it is not an inconsistency: "" would state that the spawn chose an
        empty model, which a runtime cannot act on."""
        bare = managed_launch_env(terminal=_terminal(), agent={})
        self.assertNotIn("AIFY_MANAGED_MODEL", bare)
        self.assertNotIn("AIFY_MANAGED_EFFORT", bare)
        chosen = managed_launch_env(
            terminal=_terminal(), agent={"model": "opus", "runtimeConfig": {"effort": "high"}},
        )
        self.assertEqual(chosen["AIFY_MANAGED_MODEL"], "opus")
        self.assertEqual(chosen["AIFY_MANAGED_EFFORT"], "high")

    def test_the_session_variables_come_from_the_ADAPTER_that_owns_the_runtime(self):
        """Derived, never listed: a second list would agree with the adapter until one was fixed."""
        self.assertEqual(session_env_vars_for("claude-code"), ["CLAUDE_SESSION_ID"])
        env = managed_launch_env(terminal=_terminal(sessionHandle="abc123"), agent={})
        self.assertEqual(env["CLAUDE_SESSION_ID"], "abc123")
        self.assertEqual(env["AIFY_SESSION_HANDLE"], "abc123")

    def test_an_unknown_runtime_launches_with_no_handle_rather_than_failing(self):
        """A runtime with no adapter is a configuration this service can still act on. Raising here
        would turn an odd row into an unlaunchable one."""
        self.assertEqual(session_env_vars_for("nonesuch"), [])
        env = managed_launch_env(terminal=_terminal(runtime="nonesuch"), agent={})
        self.assertEqual(env["AIFY_RUNTIME"], "nonesuch")

    def test_it_composes_NOTHING_only_the_host_can_know(self):
        """A process environment on the wire carries whatever the sender happened to hold, including
        its secrets. CODEX_HOME names a directory that must be CREATED on the running machine."""
        env = managed_launch_env(terminal=_terminal(runtime="codex"), agent={})
        self.assertNotIn("CODEX_HOME", env)
        self.assertNotIn("PATH", env)
        self.assertLess(
            len(env), 40, "this overlay should be small; a large one means a base env leaked in",
        )

    def test_a_worker_is_never_marked_as_a_bridge(self):
        """An inherited bridge flag is how a process once became the environment bridge and reaped
        seven live gateway hosts."""
        env = managed_launch_env(terminal=_terminal(), agent={})
        self.assertEqual(env["AIFY_ENVIRONMENT_BRIDGE"], "0")
        self.assertEqual(env["AIFY_SESSION_MODE"], "managed")

    def test_managed_via_wrapper_is_the_CALLERS_answer_not_a_guess_here(self):
        """The service owns that setting (`_managed_via_wrapper_for_runtime`) and the bridge already
        fetches it from here. Re-deriving it in this function would be a third reader of one rule."""
        on = managed_launch_env(terminal=_terminal(), managed_via_wrapper=True)
        off = managed_launch_env(terminal=_terminal(), managed_via_wrapper=False)
        self.assertEqual(on["AIFY_MANAGED_VIA_WRAPPER"], "1")
        self.assertEqual(off["AIFY_MANAGED_VIA_WRAPPER"], "0")

    def test_IT_AGREES_WITH_THE_HOST_IMPLEMENTATION_IT_REPLACES(self):
        """THE AGREEMENT TEST, while both exist. `terminal-env.js` still runs in the bridge until
        that bridge is deleted, so a name written by one and not the other is a worker launched
        differently depending on which tier started it -- the hardest class of difference to notice,
        because both paths work.

        It compares NAMES, not values: the values differ legitimately, since the host adds its base
        environment and CODEX_HOME. A name in the JS and not here is the drift that matters.
        """
        source = JS_SOURCE.read_text(encoding="utf-8")
        js_names = set(re.findall(r"^\s{4}(AIFY_[A-Z_]+):", source, re.M))
        # CONTROL: a regex that matched nothing would make the assertion below vacuous, which is this
        # repo's most repeated failure -- a zero that agrees with what you expected raises no
        # collision, so nothing prompts you to check the instrument.
        self.assertGreater(
            len(js_names), 8, f"the scanner read {js_names} -- it is not seeing the file",
        )

        ours = set(managed_launch_env(
            terminal=_terminal(), agent={"model": "m", "runtimeConfig": {"effort": "e"}},
        ))
        missing = sorted(name for name in js_names if name not in ours)
        self.assertEqual(missing, [], "the host writes these and this composer does not")


    def test_CLAUDE_CODE_ALWAYS_LAUNCHES_VIA_ITS_WRAPPER(self):
        """THE DEFECT THIS CLOSES, measured 2026-09-03 on a live fleet. Seven managed workers
        started, registered and read `online`, and every channel dispatch to them sat `queued` for
        ever.

        The launch composer reached for `_managed_via_wrapper_for_runtime`, whose name is nearly
        identical and whose question is different: it asks whether managed dispatch should route
        through a wrapper PTY INSTEAD OF the native adapter, and answers FALSE for claude-code --
        correctly, and for the very reason this must answer TRUE: claude-code is already
        wrapper-backed, so that flag is moot for it.

        `AIFY_MANAGED_VIA_WRAPPER` is read by the CHILD BRIDGE to decide whether to advertise
        channel and resident claim modes. Set to "0", a claude-code worker comes up healthy and
        claims nothing — indistinguishable from a delivery bug anywhere else in the chain."""
        from service.api_core.capabilities import _managed_via_wrapper_for_runtime

        settings = {}
        self.assertTrue(launches_via_wrapper(settings, "claude-code"))
        self.assertFalse(
            _managed_via_wrapper_for_runtime(settings, "claude-code"),
            "if these two ever agree for claude-code, one of them has been changed to answer the "
            "other's question and this test is what should say so",
        )

    def test_the_flag_reaches_the_environment_the_child_bridge_reads(self):
        env = managed_launch_env(
            terminal=_terminal(), managed_via_wrapper=launches_via_wrapper({}, "claude-code"),
        )
        self.assertEqual(env["AIFY_MANAGED_VIA_WRAPPER"], "1")

    def test_pi_still_stays_NATIVE_managed(self):
        """The exclusion the routing flag exists for is preserved: OMP is single-client RPC, and
        dashboard chat and Console must share one native managed controller."""
        self.assertFalse(launches_via_wrapper({}, "pi"))


if __name__ == "__main__":
    unittest.main()
