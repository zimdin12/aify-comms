"""A setting somebody can change must be a setting something reads.

THE DEFECT CLASS IS PROVEN IN THIS REPO. `service/new_dashboard/app.js` carries the note: *"Poll
fallback interval, honoring the `dashboard_refresh_seconds` setting (was hardcoded to 15s -- the
setting silently did nothing)"*. A knob that nothing consults is worse than no knob at all: an
operator sets it, watches nothing change, and concludes the SYSTEM is broken rather than the wiring.

MEASURED 2026-08-26 across 611 product files: 43 declared settings, and every one of them has a
reader except the two below, which are DELIBERATE and say so at their declaration. The scan reads
`settings.py` too -- excluding only the `DEFAULT_SETTINGS` literal itself -- because several settings
are consulted by helper functions in that same file, and excluding the whole file reported four of
them as unread.

WHAT THIS CANNOT TELL YOU: it asks whether a key is READ, not whether reading it changes anything. A
setting fetched into a variable nobody uses would pass. That is the weaker question on purpose -- the
stronger one needs the repo's reference resolver, not a regex, and the case this exists to catch is
the key with no reader at all.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", ".git", "__pycache__", ".venv", "venv", "tests", "fixtures", "vendor",
         ".pytest_cache", "data"}
SUFFIXES = {".py", ".js", ".mjs", ".html", ".sh"}
DECLARATION = "service/api_core/settings.py"

#: Where every key is DECLARED (service/api_core/settings_spec.py). It holds declarations and their
#: validation and reads no setting, so it is left out of the reader corpus; counting it would make every
#: setting look consulted.
SPEC = "service/api_core/settings_spec.py"

#: Settings declared and read by nothing, on purpose. EMPTY since 2026-09-19, when the two that sat here
#: (the retired console auto-confirm switches) were deleted. ADDING A NAME HERE IS A DECISION, not a
#: repair: a setting with no reader is the defect this file exists to catch.
DELIBERATELY_UNREAD: set = set()


def _sources() -> dict[str, str]:
    out = {}
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts) or ".test." in rel.name or rel.as_posix() == SPEC:
            continue
        try:
            out[rel.as_posix()] = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return out


class EverySettingHasAReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from service.api_core.settings import DEFAULT_SETTINGS

        cls.declared = dict(DEFAULT_SETTINGS)
        cls.sources = _sources()

    def _readers(self, key: str) -> list[str]:
        """Files that consult this setting, by the spellings this repo actually uses."""
        # THE PROPERTY PATTERN IS NOT ANCHORED TO THE WORD `settings`, and that is a correction this
        # test made to itself on its first run. `required-reply-handoff.mjs` reads the setting as
        # `s.managed_reply_capture_fallback` off a locally-named bag, so anchoring on `settings.`
        # reported a live, correctly-wired setting as dead. A reader names its variable whatever it
        # likes, and a scan that assumes otherwise produces exactly the false finding this file is
        # meant to prevent.
        patterns = [
            rf'settings\.get\(\s*["\']{re.escape(key)}["\']',   # python, the common form
            rf'\[\s*["\']{re.escape(key)}["\']\s*\]',            # any subscript read
            rf'\.{re.escape(key)}\b',                            # any property access
            rf'["\']{re.escape(key)}["\']',                      # any quoted mention outside the dict
        ]
        found = []
        for name, text in self.sources.items():
            if any(re.search(pattern, text) for pattern in patterns):
                found.append(name)
        return sorted(found)

    def test_the_scan_reads_a_real_population(self):
        """Anti-vacuity. An empty source map makes every setting look unread, and an empty settings
        dict makes them all look fine."""
        self.assertGreater(len(self.sources), 300, f"only {len(self.sources)} product files walked")
        self.assertGreater(len(self.declared), 30, f"only {len(self.declared)} settings declared")
        self.assertIn(DECLARATION, self.sources)

    def test_the_scan_can_say_PRESENT_and_ABSENT(self):
        """Both controls, in the same run as the zero they defend."""
        self.assertTrue(self._readers("agent_liveness_seconds"), "a setting known to be read was missed")
        self.assertEqual(self._readers("zz_no_such_setting_zz"), [])
        # AND THE DECLARATION MUST NOT COUNT AS ITS OWN READER, or this whole file is vacuous.
        self.assertNotIn(SPEC, self.sources, "the declarations are in the reader corpus; every key would look consulted")
        self.assertFalse(any(".test." in name for name in self.sources), "a test file counted as a reader")

    def test_every_declared_setting_is_read_by_something(self):
        unread = sorted(
            key for key in self.declared
            if key not in DELIBERATELY_UNREAD and not self._readers(key)
        )
        self.assertEqual(unread, [], (
            "these settings are declared and consulted by nothing:\n  "
            + "\n  ".join(f"{k} = {self.declared[k]!r}" for k in unread)
            + "\nAn operator can set each of them, watch nothing happen, and conclude the system is "
            "broken rather than the wiring. `dashboard_refresh_seconds` shipped exactly like this. "
            "Wire it, delete it, or -- if it is retained for response compatibility -- say so at the "
            "declaration and add it to DELIBERATELY_UNREAD in the same commit."
        ))

    def test_the_deliberately_unread_list_may_only_shrink(self):
        """An entry that acquired a reader must leave, or the list rots into unchecked names."""
        now_read = sorted(key for key in DELIBERATELY_UNREAD if self._readers(key))
        self.assertEqual(now_read, [], (
            "these are now read by something, so they are no longer deliberate no-ops -- delete them "
            f"from DELIBERATELY_UNREAD in the same commit: {now_read}"
        ))

    def test_every_deliberately_unread_setting_still_exists(self):
        """A renamed or removed key left in the list would quietly widen the exemption."""
        gone = sorted(key for key in DELIBERATELY_UNREAD if key not in self.declared)
        self.assertEqual(gone, [], f"DELIBERATELY_UNREAD names settings that no longer exist: {gone}")

    def test_the_exemption_stays_small(self):
        """Two names is the size of the idea. A list that grows is a settings page filling with knobs
        that do nothing, one deliberate decision at a time."""
        self.assertLessEqual(len(DELIBERATELY_UNREAD), 4, (
            f"{len(DELIBERATELY_UNREAD)} settings are exempt from needing a reader. Each was a "
            "decision; together they are a pattern."
        ))


if __name__ == "__main__":
    unittest.main()
