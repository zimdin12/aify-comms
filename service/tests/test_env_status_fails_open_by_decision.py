"""`environment_effective_status` fails OPEN in two places, on purpose. Pinned, not fixed.

THE FINDING (external review, 2026-08-18): an environment this classifier cannot classify is treated
as reachable. Two branches do it, and both are in `service/env_status.py`:

  1. `str(row["status"] or "online")` — a NULL/empty stored status becomes `online`.
  2. `except Exception: pass` around the `last_seen` parse — an unparsable timestamp leaves the
     status un-aged, so a dead `online` environment stays `online` forever.

THE RULING (comms-senior-dev, 2026-08-18, verbatim): "confirm leave fail-open for this release, with
a pinned latent-risk note... Fail-closed would turn classifier/data-corruption gaps into fleet-wide
dispatch refusal. Without a reachable corrupt-row path or observed bad row, trying work and letting
claim/reaper paths expose failure is the less dangerous default. Fix only with a real carrier
specimen or a bounded migration/repair story; do not silently flip legacy environments offline."

WHY A TEST AND NOT A COMMENT. This repo's standing principle is "no evidence is not a pass" — a
check that cannot gather evidence must not report ok — and these two branches are that principle
INVERTED. That is a genuine and defensible exception, because the two failure modes are not
symmetric: fail-closed here refuses ALL dispatch across the fleet when a classifier or a row is
wrong, i.e. it converts a bug into an outage, which is the worse of the two. But an exception to a
standing principle that lives only in a comment is one refactor away from being "fixed" by somebody
applying the principle correctly and generically — and that fix would be an outage. So the exception
is written down as an executable decision, in both directions:

  - if somebody makes it fail CLOSED, these tests fail and quote the ruling;
  - if somebody widens the fail-open (e.g. ages terminal decisions like `disabled` by timestamp, or
    swallows a parse error where a status was already known-bad), these tests fail too.

WHAT WOULD JUSTIFY FLIPPING IT, so a future reader is not left guessing at the bar: a real row from a
carrier environment whose stored status is empty or whose `last_seen` does not parse. That is the
"real carrier specimen" in the ruling. Until one exists, the corrupt-row path is hypothetical and the
outage path is not.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from service.env_status import environment_effective_status


def _row(status, last_seen) -> dict:
    """The classifier reads a row by subscript; a dict is the same contract without a DB."""
    return {"status": status, "last_seen": last_seen}


def _iso(delta_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat().replace(
        "+00:00", "Z"
    )


class EnvStatusFailsOpenByDecision(unittest.TestCase):
    # ── the two pinned fail-open branches ────────────────────────────────────────────────────

    def test_an_EMPTY_stored_status_is_treated_as_online(self):
        """Branch 1. Deliberate: fail-closed here refuses dispatch for every legacy row that predates
        the status column being written, i.e. an outage caused by a migration gap."""
        for empty in (None, ""):
            with self.subTest(stored=repr(empty)):
                self.assertEqual(
                    environment_effective_status(_row(empty, _iso(0))), "online",
                    "the empty-status default changed. comms-senior-dev ruled 2026-08-18 to LEAVE "
                    "this fail-open: 'do not silently flip legacy environments offline'. Flipping it "
                    "turns a data gap into fleet-wide dispatch refusal. If you have a real carrier "
                    "specimen, that is the bar the ruling set — say so and cite it.",
                )

    def test_a_WHITESPACE_status_ages_like_nothing_at_all(self):
        """A THIRD fail-open, narrower than the two the review named, recorded because I found it
        while pinning them and it should not be discovered again from scratch.

        `str(row["status"] or "online")` treats `"   "` as present — whitespace is truthy — so it is
        neither defaulted to `online` nor recognised as a heartbeat status, which means it never ages
        no matter how long the bridge has been silent. It is returned verbatim.

        NOT FIXED, for the same reason as the other two and one more: nothing writes a whitespace
        status, so there is no specimen, and `.strip()` here would move such a row from
        "passed through, visibly odd" to "silently online" — widening the fail-open rather than
        closing it. Pinned so the behaviour is a known quantity if a specimen ever appears.
        """
        self.assertEqual(
            environment_effective_status(_row("   ", _iso(-86_400)), offline_seconds=90), "   ",
            "a whitespace status is now handled differently. If it was stripped, check whether it now "
            "defaults to `online`: that would make a silent bridge look reachable, which is a WIDER "
            "fail-open than the one the 2026-08-18 ruling confirmed.",
        )

    def test_an_UNPARSABLE_last_seen_leaves_an_online_environment_online(self):
        """Branch 2. Deliberate, and the more uncomfortable of the two: a corrupt timestamp means the
        classifier cannot tell whether the bridge is alive, and it answers `online` anyway."""
        for bad in ("not-a-date", "", None, "2026-13-45T99:99:99Z"):
            with self.subTest(last_seen=repr(bad)):
                self.assertEqual(
                    environment_effective_status(_row("online", bad)), "online",
                    "an unparsable last_seen now ages the environment. That is fail-CLOSED, and the "
                    "2026-08-18 ruling was to leave it open until a real bad row exists: a parse "
                    "failure is a bug in the writer or the clock, and refusing all dispatch on it "
                    "converts that bug into an outage. The claim/reaper paths expose a genuinely "
                    "dead environment instead.",
                )

    # ── the limits of the fail-open, so it cannot quietly widen ──────────────────────────────

    def test_a_SILENT_bridge_with_a_GOOD_timestamp_still_ages_to_offline(self):
        """ANTI-VACUITY, and the load-bearing half. Every assertion above would also pass if this
        function simply always returned `online`. The fail-open is narrow: it applies only when the
        evidence is UNREADABLE, never when the evidence says the bridge is gone."""
        self.assertEqual(
            environment_effective_status(_row("online", _iso(-3600)), offline_seconds=90), "offline",
            "a bridge silent for an hour must age to offline. If this passes as online, the "
            "fail-open has swallowed the ordinary liveness path and every dead environment now "
            "reads as reachable — the exact false green aify-doctor's env-bridge check exists for.",
        )

    def test_a_DEGRADED_environment_still_ages_out(self):
        """The 2026-07-26 fix, re-pinned here because it is the same defect class one layer down: the
        staleness check was once gated on `status == "online"`, so a `degraded` environment never aged
        and callers treating degraded as connected saw a dead bridge as live."""
        self.assertEqual(
            environment_effective_status(_row("degraded", _iso(-3600)), offline_seconds=90), "offline",
            "a degraded environment stopped ageing out. It stays 'degraded' forever after the bridge "
            "dies, and callers treat degraded as still-connected.",
        )

    def test_terminal_DECISIONS_are_never_aged_by_a_timestamp(self):
        """`offline`/`forgotten`/`disabled` are decisions, not observations. Ageing them would let a
        clock overrule an operator — the opposite direction of the same mistake."""
        for decided in ("offline", "forgotten", "disabled"):
            with self.subTest(status=decided):
                self.assertEqual(
                    environment_effective_status(_row(decided, _iso(0))), decided,
                    f"'{decided}' is an operator/server decision and must survive any timestamp",
                )

    def test_an_unknown_stored_status_is_passed_through_not_defaulted(self):
        """The fail-open default applies to an ABSENT status only. A present-but-unrecognised value is
        returned as-is rather than laundered into `online`: inventing liveness for a value nobody
        wrote is a different and worse thing than trusting a value somebody did write."""
        self.assertEqual(
            environment_effective_status(_row("wedged", _iso(0))), "wedged",
            "an unrecognised stored status was rewritten. The fail-open covers a MISSING status, not "
            "an unexpected one; collapsing the two hides whatever wrote 'wedged'.",
        )


if __name__ == "__main__":
    unittest.main()
