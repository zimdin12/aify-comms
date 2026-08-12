"""The vocabulary contract is the single home, and these tests are what keep it honest.

Finding H1 of the v0.2 review: *"the system's core vocabulary has no single home — it is hand-copied
across the language boundary."* It has since cost us twice in ways that reached users:

  - the runtime alias map is written out in Python AND in `mcp/stdio/runtimes.js`;
  - the agent status vocabulary in the debug skill still taught SIX states for months after
    `starting` and `misconfigured` shipped, so an agent reading it could RESTART a worker that was
    already booting — the one action `starting` exists to prevent.

Python now has exactly one owner (`service/api_core/vocabulary.py`, loading the JSON), so there is
nothing to agree with on this side. The JS bridge KEEPS its own literal map, because `install.sh`
copies only `mcp/stdio/` to `~/.aify-comms` and a file under `service/` does not exist on that host.
Forcing a runtime dependency there would break the native-copy install, so instead the copy is
allowed and its agreement is enforced — the repo's standing rule that a duplication finding becomes
an agreement test rather than a forced refactor.

The JS side of the same agreement lives in `mcp/stdio/tests/vocabulary-agreement.test.js`. Both
directions are needed: this one catches Python drifting, that one catches the bridge drifting.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from service.api_core.vocabulary import (
    AGENT_STATUSES,
    CANONICAL_RUNTIMES,
    LAUNCHABLE_RUNTIMES,
    RUNTIME_ALIASES,
    SESSION_MODES,
)

REPO = Path(__file__).resolve().parent.parent.parent
BRIDGE_RUNTIMES = REPO / "mcp" / "stdio" / "runtimes.js"
STATUS_ENGINE = REPO / "service" / "status_engine.py"


class VocabularyContractTests(unittest.TestCase):
    def test_the_bridge_alias_map_agrees_with_the_contract(self):
        """The duplication H1 named, now unable to drift silently."""
        source = BRIDGE_RUNTIMES.read_text(encoding="utf-8")
        match = re.search(r"const RUNTIME_ALIASES = new Map\(\[(.*?)\]\);", source, re.S)
        self.assertIsNotNone(match, "could not find RUNTIME_ALIASES in runtimes.js — did it move?")
        bridge = dict(re.findall(r'\["([^"]+)",\s*"([^"]+)"\]', match.group(1)))
        self.assertEqual(
            bridge,
            dict(RUNTIME_ALIASES),
            "mcp/stdio/runtimes.js RUNTIME_ALIASES has diverged from "
            "service/contracts/vocabulary.json. The bridge keeps its own copy on purpose (install.sh "
            "does not ship service/ to ~/.aify-comms), so the copy must be kept in step by hand — "
            "update BOTH, in the same commit.",
        )

    def test_the_status_engine_emits_exactly_the_contract_statuses(self):
        """`derive()` is the sole status authority, so its vocabulary IS the product's."""
        source = STATUS_ENGINE.read_text(encoding="utf-8")
        match = re.search(r"VALID_STATUSES\s*=\s*\((.*?)\)", source, re.S)
        self.assertIsNotNone(match, "VALID_STATUSES not found in status_engine.py")
        engine = set(re.findall(r'"([a-z_]+)"', match.group(1)))
        self.assertEqual(
            engine,
            set(AGENT_STATUSES),
            "service/status_engine.py VALID_STATUSES and the vocabulary contract disagree. "
            "Whichever gained a state, the other must gain it too — and so must the skill status "
            "tables, which is the failure this contract exists to stop repeating.",
        )

    def test_every_alias_resolves_to_a_canonical_runtime(self):
        """An alias pointing at a non-canonical id would normalize to a runtime nothing handles."""
        for alias, target in RUNTIME_ALIASES.items():
            with self.subTest(alias=alias):
                self.assertIn(target, CANONICAL_RUNTIMES)

    def test_every_canonical_runtime_is_reachable_by_its_own_name(self):
        """Identity mapping: `_normalize_runtime("codex")` must be `codex`, not fall through."""
        for runtime in CANONICAL_RUNTIMES:
            with self.subTest(runtime=runtime):
                self.assertEqual(RUNTIME_ALIASES.get(runtime), runtime)

    def test_launchable_is_a_subset_of_canonical(self):
        self.assertTrue(LAUNCHABLE_RUNTIMES <= CANONICAL_RUNTIMES)

    def test_the_live_code_path_reads_the_contract(self):
        """The point of a single owner is that the live code path reads it.

        The assertion follows the OWNER, not the file it used to live in: v0.5.1e moved the
        normalizers to `service/api_core/runtime.py`, so that is where the alias map must be read.
        The router still owns the launchable set and session modes at its own call sites.
        """
        from service.api_core import runtime as runtime_core
        from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

        self.assertIs(runtime_core._RUNTIME_ALIASES, RUNTIME_ALIASES)
        self.assertIs(runtime_core._SESSION_MODES, SESSION_MODES)
        self.assertIs(api_v2._SESSION_MODES, SESSION_MODES)
        self.assertIs(api_v2._LAUNCHABLE_RUNTIMES, LAUNCHABLE_RUNTIMES)
        self.assertFalse(
            hasattr(api_v2, "_RUNTIME_ALIASES"),
            "the router should no longer import the alias map -- its only consumer moved out, and "
            "importing a name nobody reads makes a module look like an owner it is not",
        )

    def test_normalize_runtime_still_behaves_identically(self):
        """Structural change: the mapping moved, the answers must not."""
        from service.control_plane import _normalize_runtime

        for raw, expected in [
            ("claude", "claude-code"), ("claude_code", "claude-code"), ("CLAUDE", "claude-code"),
            ("omp", "pi"), ("oh-my-pi", "pi"), ("pi_agent", "pi"),
            ("hermes-agent", "hermes"), ("codex", "codex"), ("opencode", "opencode"),
            ("", "generic"), (None, "generic"), ("nonsense-runtime", "nonsense-runtime"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(_normalize_runtime(raw), expected)

    def test_the_contract_is_not_mutable_through_the_loader(self):
        """Single-worker process globals: one careless update would leak to every later request."""
        with self.assertRaises(TypeError):
            RUNTIME_ALIASES["claude"] = "hijacked"  # type: ignore[index]

    def test_the_contract_file_parses_and_is_not_truncated(self):
        data = json.loads((REPO / "service" / "contracts" / "vocabulary.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["runtimes"]["aliases"]), 15)
        self.assertEqual(len(data["agentStatuses"]["values"]), 8)

    def test_every_status_has_a_meaning(self):
        """A vocabulary without meanings is how the skill table drifted in the first place."""
        for status in AGENT_STATUSES:
            with self.subTest(status=status):
                from service.api_core.vocabulary import AGENT_STATUS_MEANINGS

                self.assertTrue(AGENT_STATUS_MEANINGS.get(status), f"{status} has no documented meaning")


if __name__ == "__main__":
    unittest.main()
