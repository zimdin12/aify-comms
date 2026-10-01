"""Every spawn request the service writes goes through `definition_start.insert_spawn_request` (P0 C7).

That insert carries the WHERE that asks again whether the agent is defined or withdrawn, so a start
whose binding read was overtaken by a push or a withdrawal queues nothing. A start written by its own
INSERT skips that question: review of 8de83233 (N6) found the undefined old-spec restart doing exactly
that while the other starts were guarded. The population is every product source file under
`service/`, walked on disk so an untracked file is included, never a list of known writers.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]
RAW_INSERT = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+spawn_requests\b", re.IGNORECASE)
THE_GUARDED_INSERT = SERVICE / "api_core" / "definition_start.py"


def product_sources():
    for path in SERVICE.rglob("*.py"):
        if "tests" in path.relative_to(SERVICE).parts:
            continue
        yield path


class EveryStartGoesThroughTheGuardedInsert(unittest.TestCase):
    def test_the_pattern_sees_the_forms_a_raw_insert_takes(self):
        for text in ("INSERT INTO spawn_requests (id)", "insert   into\n spawn_requests(", "INSERT OR IGNORE INTO spawn_requests"):
            self.assertRegex(text, RAW_INSERT)
        self.assertNotRegex("INSERT INTO spawn_requests_archive", RAW_INSERT)

    def test_only_the_guarded_insert_writes_a_spawn_request(self):
        writers = sorted(str(p.relative_to(SERVICE)) for p in product_sources()
                         if RAW_INSERT.search(p.read_text(encoding="utf-8")))
        self.assertIn(str(THE_GUARDED_INSERT.relative_to(SERVICE)), writers,
                      "control: the walk finds the one insert that is meant to be there")
        self.assertEqual(writers, [str(THE_GUARDED_INSERT.relative_to(SERVICE))])
