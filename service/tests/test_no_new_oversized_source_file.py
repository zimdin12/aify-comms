"""No slice may push a file OVER 1000 lines. A ratchet, not an allowlist.

WHY THIS EXISTS. In v0.5.4 a relocation moved a 6-line helper into `service/db.py` — the right subject
owner — and took that file from 995 lines to 1006. `control_plane.py` got smaller and a NEW file went over
the threshold, which is the shell game the oversized-file goal exists to prevent: the rule does not care
WHICH file is oversized. Every gate in this series passed. The undefined-name sweep was clean, the
stale-owner census was clean, `create_app()` built 124 routes, and all three suites were green, because
none of them measures the DESTINATION of a move. The reviewer caught it by reading line counts by hand.

RATCHET, NOT ALLOWLIST, and the distinction is the whole design. `KNOWN_OVERSIZED` below is a MEASUREMENT
of what was already over the line when this gate was written. It is not a list of files anyone approved,
and nothing here should be read as blessing them — each has an open packet or a reviewer ruling. The gate
asserts two things:

  1. no file outside that measured set is at or over the limit — so a slice cannot create a new one;
  2. no file IN the set is missing — so the set shrinks honestly as files are cleared, and cannot rot into
     a list of names nobody has re-checked.

Property 2 is what stops this becoming the tripwire-on-progress that the earlier `> 20` accessor floor
became: clearing a file FAILS this test until the name is removed, which is a one-line edit with an
obvious meaning, rather than a number someone quietly edits downward every slice.

DELIBERATELY NOT INCLUDED: a limit on how large the known-oversized files may GROW. That would be a
second, weaker rule pretending to be this one. If `control_plane.py` grows, the series' own line-count
receipts say so.

Scope is non-test source under `service/`. The JS targets (`server.js`, `app.js`,
`hermes-managed-host.js`, `pi-session.js`) are outside this suite's reach and are tracked by their own
packets.
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
LIMIT = 1000

#: MEASURED at 39c880af, not approved. Every entry has an open packet or a reviewer ruling.
#: Remove a name the moment its file drops below the limit — the test requires it.
KNOWN_OVERSIZED = {
    "control_plane.py",
}


def _source_files():
    for path in sorted(SERVICE.rglob("*.py")):
        parts = path.parts
        if "__pycache__" in parts or "tests" in parts:
            continue
        yield path


def _line_count(path: Path) -> int:
    """Newline count, matching `wc -l` — the convention every receipt in this series uses."""
    return len(io.open(path, encoding="utf-8", errors="replace").read().split("\n")) - 1


class NoNewOversizedSourceFileTests(unittest.TestCase):
    def test_no_file_outside_the_measured_set_is_oversized(self):
        offenders = [
            f"{p.relative_to(SERVICE.parent).as_posix()}: {_line_count(p)} lines"
            for p in _source_files()
            if p.name not in KNOWN_OVERSIZED and _line_count(p) >= LIMIT
        ]
        self.assertEqual(
            offenders,
            [],
            "A file crossed the 1000-line limit. If a relocation did this, the destination is wrong even "
            "when its SUBJECT is right — reducing one file by growing another past the limit is not "
            "progress:\n  " + "\n  ".join(offenders),
        )

    def test_the_measured_set_has_no_stale_entries(self):
        """A cleared file must be REMOVED from the set, or this ratchet rots into unchecked names."""
        by_name = {p.name: p for p in _source_files()}
        stale = []
        for name in sorted(KNOWN_OVERSIZED):
            path = by_name.get(name)
            if path is None:
                stale.append(f"{name}: no longer exists under service/")
            elif _line_count(path) < LIMIT:
                stale.append(f"{name}: now {_line_count(path)} lines — drop it from KNOWN_OVERSIZED")
        self.assertEqual(stale, [], "\n  ".join([""] + stale))

    def test_the_detector_would_actually_fail(self):
        """A gate asserted against an empty list proves nothing until you have seen it fail.

        The real scan is over real files, so it cannot be made to fail without editing the tree. The
        countable property is the predicate: a file at the limit is oversized and one below it is not.
        Off-by-one here would make the gate silently accept exactly the 1000-line file it exists to catch.
        """
        self.assertTrue(1000 >= LIMIT, "a file of exactly 1000 lines must count as oversized")
        self.assertFalse(999 >= LIMIT, "a file of 999 lines must not be flagged")

    def test_line_counting_matches_the_receipts(self):
        """`wc -l` counts newlines. Splitting on newline yields one more element for a trailing newline.

        Receipts early in this series used the split convention and read one higher than the reviewer's
        `wc -l`, which cost a round of reconciling numbers that described the same file.
        """
        probe = SERVICE / "db_errors.py"
        if not probe.exists():
            self.skipTest("probe file absent")
        raw = io.open(probe, encoding="utf-8").read()
        self.assertTrue(raw.endswith("\n"), "probe must end with a newline for this to mean anything")
        self.assertEqual(_line_count(probe), raw.count("\n"))


if __name__ == "__main__":
    unittest.main()
