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

#: Settings that are declared and read by NOTHING, on purpose, each with the reason stated at its
#: declaration in `settings.py`. Both are "retained for settings-response compatibility only": the
#: behaviour moved into the bridge, which decides from its own environment at process start and never
#: polls the service. Neither is exposed by the dashboard -- the compaction one's comment says so
#: explicitly -- so no operator can toggle a no-op.
#:
#: ADDING A NAME HERE IS A DECISION, not a repair. A new setting with no reader is the defect this
#: file exists to catch, and the fix is to wire it or delete it.
DELIBERATELY_UNREAD = {
    "console_auto_confirm_claude_dev_channels",
    "console_auto_confirm_claude_compaction",
}


def _sources() -> dict[str, str]:
    out = {}
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        try:
            out[rel.as_posix()] = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return out


def _declaration_free_settings_module(text: str) -> str:
    """`settings.py` with the DEFAULT_SETTINGS literal removed.

    The literal is where every key is DECLARED, so counting it as a reader would make every setting
    look consulted. The rest of the file is legitimate reader code -- `managed_terminal_backing_enabled`
    and three others are read by helpers a few lines below the dict.
    """
    start = text.find("DEFAULT_SETTINGS = {")
    if start == -1:
        return text
    end = text.find("\n}", start)
    return text[:start] + (text[end:] if end != -1 else "")


class EverySettingHasAReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from service.api_core.settings import DEFAULT_SETTINGS

        cls.declared = dict(DEFAULT_SETTINGS)
        cls.sources = _sources()
        cls.sources[DECLARATION] = _declaration_free_settings_module(cls.sources[DECLARATION])

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
        # AND THE DECLARATION MUST NOT COUNT AS ITS OWN READER, or this whole file is vacuous. Asked
        # of a key that appears ONLY inside the dict: checking for the identifier `DEFAULT_SETTINGS`
        # does not work, because the stripped module still references it legitimately when merging
        # defaults and inside a reader helper.
        self.assertNotIn(
            "console_auto_confirm_claude_dev_channels", self.sources[DECLARATION],
            "the settings literal is still in the reader corpus; every key would look consulted",
        )

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
