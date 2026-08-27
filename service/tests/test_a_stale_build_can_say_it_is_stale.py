"""A container behind origin must be ABLE to report itself behind.

THE DEFECT THIS PINS, found live on 2026-08-27. `/version` reported
`{"behind_by": 0, "ahead_by": 149, "status": "ahead", "stale": false}` on a container 150 commits
old, and the dashboard badge -- which reads `update.behind_by` -- printed "up to date with origin".

The compare URL was `compare/{sha}...main`. GitHub reports `ahead_by` / `behind_by` / `status` about
HEAD relative to BASE, so that asked about MAIN and answered under field names every consumer reads
as describing the running build. `behind_by` was therefore structurally pinned at 0 for a stale
build: the one number this whole check exists to produce could never be non-zero.

MEASURED against the real API, running build 1a3de61a:

    compare/1a3de61a...main   ->  status ahead,   ahead_by 150, behind_by 0
    compare/main...1a3de61a   ->  status behind,  ahead_by 0,   behind_by 150

git, independently: 150 commits behind, 0 ahead.

WHY THE EXISTING SUITE DID NOT CATCH IT. `test_github_compare_update_check.py` is thorough about
transport -- the URL host, the User-Agent, timeouts, caching, 403s -- and it feeds a hand-written
COMPARE_PAYLOAD of `{"status": "behind", "behind_by": 7, "ahead_by": 0}`. That payload is what the
API returns for the OTHER direction. A fabricated fixture that agrees with a wrong URL makes every
assertion pass and the shipped behaviour wrong. So this file asserts the PROPERTY the fixture cannot:
that the number reaching a reader grows with staleness, and that the two directions are told apart.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.routers import health


class RealCompareShapes:
    """What api.github.com actually returns, for both directions, OBSERVED rather than invented.

    Recorded verbatim on 2026-08-27 for running build 1a3de61a against main. The point of keeping
    both is that a fixture for only one direction cannot detect using the other.
    """

    #: base = the running build, head = main. Describes MAIN. This is what the code used to ask for.
    SHA_AS_BASE = {"status": "ahead", "ahead_by": 150, "behind_by": 0, "total_commits": 150}
    #: base = main, head = the running build. Describes the RUNNING BUILD, which is the subject.
    SHA_AS_HEAD = {"status": "behind", "ahead_by": 0, "behind_by": 150, "total_commits": 0}


class AStaleBuildCanSayItIsStale(unittest.TestCase):
    def setUp(self):
        health._update_cache = None
        health._update_cache_at = 0.0
        self.addCleanup(self._reset)

    def _reset(self):
        health._update_cache = None
        health._update_cache_at = 0.0
        health.set_update_comparer(None)

    def _check(self, payload):
        health._update_cache = None
        health._update_cache_at = 0.0
        health.set_update_comparer(lambda sha: dict(payload))
        return health._check_update("1a3de61a")

    def test_the_subject_of_the_compare_is_the_RUNNING_BUILD(self):
        """The URL, asserted here as well as in the transport suite, because this is the file that
        explains what the direction means. `{sha}` on the head side makes the running build the
        subject, so GitHub's own field names describe it and no consumer needs to know about a swap.
        """
        self.assertTrue(
            health._GITHUB_COMPARE_URL.endswith("/compare/main...{sha}"),
            "the running build must be the HEAD of the compare, or `behind_by` describes main: "
            + health._GITHUB_COMPARE_URL,
        )

    def test_a_stale_build_reports_a_NON_ZERO_behind_count(self):
        """The property the old shape could not satisfy at any level of staleness."""
        result = self._check(RealCompareShapes.SHA_AS_HEAD)
        self.assertEqual(result["behind_by"], 150)
        self.assertEqual(result["status"], "behind")
        self.assertFalse(result["stale"], "a successful reading must not be marked stale")

    def test_the_WRONG_direction_is_recognisably_wrong(self):
        """ANTI-VACUITY, and the case that makes this file worth having: fed the payload the old URL
        produced, the check reports `behind_by: 0` on a build 150 commits behind. If this ever passes
        as an acceptable answer again, the badge goes back to saying 'up to date with origin'."""
        result = self._check(RealCompareShapes.SHA_AS_BASE)
        self.assertEqual(result["behind_by"], 0)
        self.assertEqual(result["ahead_by"], 150)
        self.assertNotEqual(
            result["behind_by"], RealCompareShapes.SHA_AS_HEAD["behind_by"],
            "the two directions produce the same behind_by, so this test cannot tell them apart",
        )

    def test_a_CURRENT_build_reports_zero_and_that_is_different(self):
        """The other half of the vocabulary. `behind_by: 0` must still be reachable and mean current,
        or the fix would have traded one always-wrong answer for another."""
        identical = {"status": "identical", "ahead_by": 0, "behind_by": 0, "total_commits": 0}
        result = self._check(identical)
        self.assertEqual(result["behind_by"], 0)
        self.assertEqual(result["status"], "identical")

    def test_behind_count_TRACKS_staleness(self):
        """Not merely non-zero: the number must be the count, so a reader can act on it. A constant
        would satisfy every assertion above."""
        seen = []
        for n in (1, 7, 150):
            result = self._check({"status": "behind", "ahead_by": 0, "behind_by": n, "total_commits": 0})
            seen.append(result["behind_by"])
        self.assertEqual(seen, [1, 7, 150])

    def test_an_unreachable_github_is_NULL_not_zero(self):
        """No evidence is not a pass -- this repo's own rule, from the doctor's false green. A failed
        lookup must not be indistinguishable from a current build, which is what `behind_by: 0` would
        say to the badge."""
        health._update_cache = None
        health._update_cache_at = 0.0

        def boom(sha):
            raise OSError("offline")

        health.set_update_comparer(boom)
        result = health._check_update("1a3de61a")
        self.assertIsNone(result["behind_by"], "an unreachable GitHub reported a definite answer")
        self.assertTrue(result["stale"], "a reading that never happened was not marked stale")


if __name__ == "__main__":
    unittest.main()
