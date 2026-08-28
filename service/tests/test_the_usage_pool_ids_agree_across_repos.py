"""The quota pool ids the BRIDGE collects under are the ones the SERVICE looks up by.

THE JOIN. `mcp/stdio/usage-collector.js` reads each provider's quota and POSTs a snapshot as
`{source_id: ...}`. `service/usage_cache.py` caches that snapshot under the key it arrives with, and
`derive_usage_source()` computes the key an agent's quota is fetched by. Those spellings have to
agree across a repo boundary.

WHAT A DISAGREEMENT LOOKS LIKE: nothing. `usage_get` is `_USAGE_CACHE.get(source_id)`, and a dict
lookup that misses is not a failure -- it returns None, the agent payload's `poolWeeklyPctLeft` and
its siblings go null, and the dashboard shows no quota. No exception, no log line, no red test,
because each repo's suite is self-consistent. The bridge would still be collecting correctly and the
service would still be serving correctly; only the join would be broken.

MEASURED 2026-08-28: `openai-chatgpt-codex` was spelled in FOUR places across two repos -- twice in
`usage_cache.py`, once as `SOURCE_ID` in `usage_openai.py`, and once as `SOURCE_CODEX` in the
bridge. All four were identical, so nothing was broken; this is a defect with a delay on it. The
three service copies are now one constant, and this test is what holds the fourth in step.

DIRECTION MATTERS. The bridge may collect pools the service does not derive -- adding a provider is
a bridge change first -- so this asserts the service's ids are a SUBSET of the bridge's declared
ones, and reports the other direction as information rather than failure.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.usage_cache import (
    SOURCE_ANTHROPIC_CLAUDE_MAX,
    SOURCE_OPENAI_CHATGPT_CODEX,
    USAGE_SOURCE_IDS,
    derive_usage_source,
)

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = ROOT / "mcp" / "stdio" / "usage-collector.js"

#: `const SOURCE_X = "id";` in the collector. Read rather than retyped: a list here would be a fifth
#: copy of the thing this test exists to keep at one.
_DECLARED = re.compile(r'const\s+SOURCE_[A-Z_]+\s*=\s*"([a-z0-9-]+)"')

#: The pool the service derives but no collector posts. `local-ollama` is deliberate: a hermes agent
#: pointed at a non-ChatGPT backend has no quota to collect, and the id exists so the payload can say
#: "local pool" instead of leaving the field blank and looking broken.
NOT_COLLECTED = frozenset({"local-ollama"})


def bridge_ids() -> set[str]:
    return set(_DECLARED.findall(COLLECTOR.read_text(encoding="utf-8")))


class UsagePoolIdsAgreeAcrossReposTests(unittest.TestCase):
    def test_the_collector_is_present_and_declares_ids(self) -> None:
        """The control. A missing file or a changed declaration shape yields an empty set, and an
        empty set is a subset of everything -- so the comparison below would pass having read
        nothing. This repo has produced that exact wrong zero more than once."""
        self.assertTrue(COLLECTOR.exists(), f"the collector is missing at {COLLECTOR}")
        found = bridge_ids()
        self.assertGreaterEqual(len(found), 2, f"only {len(found)} ids parsed from the collector")
        self.assertIn(SOURCE_OPENAI_CHATGPT_CODEX, found)

    def test_the_scan_can_say_no(self) -> None:
        """The negative control. A pattern matching any string would satisfy everything above."""
        self.assertNotIn("aify-not-a-real-pool", bridge_ids())

    def test_every_id_the_service_derives_is_one_the_bridge_collects(self) -> None:
        collected = bridge_ids()
        orphans = sorted(USAGE_SOURCE_IDS - collected - NOT_COLLECTED)
        self.assertEqual(
            orphans, [],
            "the service derives quota pools the bridge never posts, so `usage_get` misses and the "
            "agent's quota fields go null with no error: " + ", ".join(orphans),
        )

    def test_the_runtime_mapping_only_produces_declared_ids(self) -> None:
        """`derive_usage_source` is the function that computes the lookup key, so it is the thing
        that must not invent one. Every branch, including the hermes split on model base URL."""
        cases = [
            ("claude-code", {}, SOURCE_ANTHROPIC_CLAUDE_MAX),
            ("codex", {}, SOURCE_OPENAI_CHATGPT_CODEX),
            ("hermes", {}, SOURCE_OPENAI_CHATGPT_CODEX),
            ("hermes", {"modelBaseUrl": "https://api.openai.com/chatgpt"}, SOURCE_OPENAI_CHATGPT_CODEX),
            ("hermes", {"modelBaseUrl": "http://localhost:11434"}, "local-ollama"),
        ]
        for runtime, config, expected in cases:
            with self.subTest(runtime=runtime, config=config):
                derived = derive_usage_source(runtime, config)
                self.assertEqual(derived, expected)
                self.assertIn(derived, USAGE_SOURCE_IDS)
        self.assertIsNone(derive_usage_source("opencode", {}), "an unknown runtime must derive no pool")

    def test_a_pool_the_bridge_adds_alone_is_reported_not_failed(self) -> None:
        """Information, not a gate. Adding a provider is a bridge change first, and failing the
        service's suite for work that has not reached it yet would train people to ignore this."""
        extra = sorted(bridge_ids() - USAGE_SOURCE_IDS)
        if extra:
            print(f"note: the bridge collects pools this service does not derive: {extra}")
        self.assertIsInstance(extra, list)


if __name__ == "__main__":
    unittest.main()
