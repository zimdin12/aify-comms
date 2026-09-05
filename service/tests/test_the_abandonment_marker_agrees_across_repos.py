"""The doctor's abandoned-claim marker must be a string the service actually writes.

R9-H3's fix depends on a substring match across a language boundary. `spawn_lifecycle.py` (Python,
in the container) writes an error onto a spawn request it gives up on; `spawn-queue-check.mjs`
(JavaScript, on the host) looks for that text to report the failure after the live row is gone.

TWO SOURCES OF TRUTH FOR ONE STRING, and this repo knows how that ends: they agree until somebody
edits one. A reworded error message would not fail anything -- the check would simply stop matching,
count zero abandoned rows, and go green over the incident it exists to report. That is the same
empty-set false green as `env-bridge` and `bridge-current`, arriving by a different door.

So the marker is pinned here, from BOTH sides, with a control proving the pin can fail.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RECONCILER = REPO / "service" / "reconcilers" / "spawn_lifecycle.py"
CHECK = REPO / "mcp" / "stdio" / "spawn-queue-check.mjs"


def _marker_the_doctor_looks_for() -> str:
    """The literal assigned to ABANDONED_MARKER in the JS check."""
    found = re.search(
        r'export const ABANDONED_MARKER\s*=\s*"([^"]+)"',
        CHECK.read_text(encoding="utf-8"),
    )
    assert found, "spawn-queue-check.mjs no longer declares ABANDONED_MARKER"
    return found.group(1)


class AbandonmentMarkerAgreesAcrossReposTests(unittest.TestCase):
    def test_POSITIVE_CONTROL_both_files_were_read(self) -> None:
        """An unreadable file would make every assertion below vacuous."""
        self.assertTrue(RECONCILER.is_file(), RECONCILER)
        self.assertTrue(CHECK.is_file(), CHECK)
        self.assertIn("UPDATE spawn_requests", RECONCILER.read_text(encoding="utf-8"))

    def test_the_service_writes_the_text_the_doctor_matches(self) -> None:
        marker = _marker_the_doctor_looks_for()
        source = RECONCILER.read_text(encoding="utf-8")
        # The message is an f-string split across lines, so compare against the source with its
        # line breaks and indentation collapsed -- the same text the database ends up holding.
        flattened = re.sub(r'"\s*\n\s*(?:f?")?', "", source)
        self.assertIn(
            marker,
            flattened,
            f"the doctor's spawn-queue check looks for {marker!r}, and spawn_lifecycle.py no longer "
            "writes it. The check will match nothing and report a clean queue over abandoned "
            "claims. Change both, or make one read the other.",
        )

    def test_NEGATIVE_CONTROL_a_wrong_marker_would_be_caught(self) -> None:
        """A pin that cannot fail proves nothing about the pin that matters."""
        flattened = re.sub(r'"\s*\n\s*(?:f?")?', "", RECONCILER.read_text(encoding="utf-8"))
        self.assertNotIn("Surrendered: claimed at", flattened)

    def test_the_grace_the_two_sides_age_against_is_the_SAME_number(self) -> None:
        """The window R9-H3 exposed. The doctor flags at CLAIMED_GRACE_SECONDS and the reconciler
        fails at SPAWN_ORPHAN_GRACE_SECONDS; when those drift apart the check either cries wolf
        before the service has decided, or never sees the row at all."""
        js = re.search(r"export const CLAIMED_GRACE_SECONDS\s*=\s*(\d+)", CHECK.read_text(encoding="utf-8"))
        self.assertTrue(js, "spawn-queue-check.mjs no longer declares CLAIMED_GRACE_SECONDS")
        from service.reconcilers.spawn_lifecycle import SPAWN_ORPHAN_GRACE_SECONDS

        self.assertEqual(
            int(js.group(1)),
            int(SPAWN_ORPHAN_GRACE_SECONDS),
            "the doctor and the reconciler no longer age a claimed spawn against the same window",
        )

    def test_the_claimed_carve_out_is_still_absent(self) -> None:
        """Why the doctor must read `failed` rows at all: a claimed row gets NO live-bridge shelter,
        so the service fails it on the very next reconcile pass after the grace. If that ever
        changes, this check's second half becomes dead weight and should be revisited rather than
        left to rot."""
        source = RECONCILER.read_text(encoding="utf-8")
        self.assertIn(
            'stuck_status != "claimed" and bid and bid in live_bridge_ids',
            source,
            "the live-bridge carve-out changed shape; re-read whether a claimed row still gets "
            "failed one reconcile pass after the grace window",
        )


if __name__ == "__main__":
    unittest.main()
