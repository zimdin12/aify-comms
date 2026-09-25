"""The console launch is built as ARGV and flattened at the last moment. Keep the list.

v0.6 Phase 8 stops on one question: aify-comms hands the bridge a shell command STRING, while aify-env
allowlists a launcher FILE and executes it directly. Bridging those has three options, and the one
PHASE8_STATUS.md calls correct — pass launcher and args structurally — was costed as reaching "upstream
into how every managed command is composed".

MEASURED, and that cost is smaller than it reads. Every adapter already builds a list:

    parts = ["claude-aify", "--aify-agent", agent_id]
    if handle:
        parts.extend(["--resume", handle])
    return " ".join(parts)

The structure exists; it was discarded on the last line. So `console_argv` is the real value, and the
one string form (`_default_console_command` in api_core/capabilities.py) is a join of it.

WHAT THIS IS NOT. It does not delegate anything, does not change what the bridge receives, and does not
decide the open question. It removes one argument from it — that the structural form would have to be
built from scratch — and leaves the decision where it belongs.

WHAT THIS DOES NOT RE-PROVE. The command STRINGS themselves are already pinned, with literals, by
test_console_command_resume.py, the per-adapter console tests under tests/runtimes/, and several cases in
test_api_v2_regressions.py. What is checked here is the shape of the list itself.

Terminal rows already queued carry command strings and the dashboard shows them, so a divergence would
mean a delegated spawn and a legacy one launching different processes from the same row - exactly the
"subtly different in ways nobody could attribute" failure the seam refuses to risk.

The adapters are DERIVED from the registry rather than listed, so a sixth runtime cannot arrive with an
argv nobody checked.
"""

from __future__ import annotations

import unittest

from service.runtimes import adapter_for
from service.runtimes import _REGISTRY  # noqa: PLC2701 — the registry IS the population under test


#: Every shape the two inputs can take. `handle` is the only value that varies the branch structure.
CASES = [
    {"agent_id": "agent-1", "handle": "", "interactive": False},
    {"agent_id": "agent-1", "handle": "sess-abc", "interactive": False},
    {"agent_id": "agent-1", "handle": "", "interactive": True},
    {"agent_id": "agent-1", "handle": "sess-abc", "interactive": True},
]


class ConsoleArgvIsTheLaunchShape(unittest.TestCase):
    def test_every_runtime_is_covered(self):
        """The population is the registry, so a new adapter is covered the day it is added."""
        self.assertGreaterEqual(len(_REGISTRY), 5, "the runtime registry shrank; this test is now blind")

    def test_argv_is_a_list_of_strings(self):
        for runtime in sorted(_REGISTRY):
            adapter = adapter_for(runtime)
            for case in CASES:
                with self.subTest(runtime=runtime, **case):
                    argv = adapter.console_argv(**case)
                    self.assertIsInstance(argv, list, "argv must be a list, not a pre-joined string")
                    self.assertTrue(all(isinstance(part, str) for part in argv), "argv holds non-strings")
                    self.assertTrue(argv, "argv is empty, so there is no program to run")

    def test_argv_never_hides_a_space_inside_one_element(self):
        """A joined string cannot express an argument containing a space, so argv must not contain one.

        If it ever did, `" ".join(argv)` would produce a string that re-splits into DIFFERENT arguments
        — the exact quoting class of bug this project has shipped twice. Today no adapter interpolates
        anything that could contain a space; this fails the day one does, which is when the structural
        form stops being a free view of the string and becomes the only correct one.
        """
        for runtime in sorted(_REGISTRY):
            adapter = adapter_for(runtime)
            for case in CASES:
                for part in adapter.console_argv(**case):
                    with self.subTest(runtime=runtime, part=part):
                        self.assertNotIn(" ", part)

    def test_the_first_element_is_the_launcher(self):
        """What aify-env would allowlist. It has to be the program, not a flag."""
        for runtime in sorted(_REGISTRY):
            argv = adapter_for(runtime).console_argv(agent_id="a", handle="", interactive=False)
            self.assertTrue(argv, f"{runtime} produced an empty argv")
            self.assertFalse(argv[0].startswith("-"), f"{runtime} argv starts with a flag: {argv!r}")


if __name__ == "__main__":
    unittest.main()
