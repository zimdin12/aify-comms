"""The agent status vocabulary crosses a language boundary — bind the copies to the source.

H1, from the 2026-07-31 responsibility audit.

`status_engine.VALID_STATUSES` is THE shared contract of this service: agents read the status to
decide whether to send, the dashboard filters on it, the reconciler acts on it. It is authoritative
in Python and it was hand-retyped in JavaScript in three more places — `SESSION_FILTER_KINDS` and
`SESSION_LIVE_KINDS` in `app.js`, and an independent `new Set([...])` of the same four values in
`chat.js`. Nothing verified that any copy still matched, and the vocabulary is not served by the API,
so there was no runtime path by which the client could learn it either.

WHY THIS MATTERS MORE THAN A TYPO CHECK. The drift is silent by construction. `resolveStatus` ends
`STATUS_KINDS[raw] || STATUS_KINDS.unknown`, so a seventh server-side state does not throw — it
renders as a muted grey "unknown" chip and filters into nothing. The dashboard keeps working and
quietly stops telling the truth, which is the failure mode this whole project keeps fighting.

The JS side now has ONE owner (`status.js`'s `AGENT_STATUSES` / `LIVE_AGENT_STATUSES`); this test is
what binds that owner to the Python source. It is deliberately a source-text assertion rather than a
running-JS assertion: the dashboard has no Python-visible runtime, and a regex over one declaration
is exact enough to fail on any real drift while staying readable.
"""

import re
import unittest
from pathlib import Path

from service.status_engine import VALID_STATUSES

DASHBOARD = Path(__file__).resolve().parents[1] / "new_dashboard"


def _js_array(source: str, name: str) -> list[str]:
    """Extract a `export const NAME = ['a', 'b'];` literal from JS source."""
    match = re.search(
        r"export const %s\s*=\s*\[(.*?)\]" % re.escape(name), source, re.S
    )
    assert match, f"{name} is not declared as an array literal any more — update this test WITH the change"
    return re.findall(r"['\"]([a-z-]+)['\"]", match.group(1))


class StatusVocabularyBindingTests(unittest.TestCase):
    def setUp(self):
        self.status_js = (DASHBOARD / "status.js").read_text(encoding="utf-8")

    def test_js_agent_statuses_match_python_exactly_and_in_order(self):
        """The JS list must equal VALID_STATUSES — same members, same order.

        Order matters because SESSION_FILTER_KINDS drives the filter row's button order; a reordering
        is not a correctness bug but it IS an unreviewed UI change, and catching it here is free.
        """
        self.assertEqual(
            _js_array(self.status_js, "AGENT_STATUSES"),
            list(VALID_STATUSES),
            "service/new_dashboard/status.js AGENT_STATUSES has drifted from "
            "service/status_engine.py VALID_STATUSES — the dashboard will render the missing state "
            "as a grey 'unknown' chip and filter it into nothing",
        )

    def test_live_subset_is_derived_from_the_list_not_retyped(self):
        """`LIVE_AGENT_STATUSES` must be computed, not a second hand-typed literal.

        Two independently-typed sets is the exact defect this consolidation removed (app.js and
        chat.js each declared the same four values). Re-introducing a literal here would restore it
        while looking tidy, so assert on the MECHANISM, not only on today's values.
        """
        self.assertNotRegex(
            self.status_js,
            r"export const LIVE_AGENT_STATUSES\s*=\s*\[",
            "LIVE_AGENT_STATUSES must be derived from AGENT_STATUSES, not retyped as a literal",
        )
        non_live = _js_array(self.status_js, "NON_LIVE_AGENT_STATUSES")
        self.assertTrue(set(non_live) <= set(VALID_STATUSES), f"unknown status in the exclusion list: {non_live}")
        # And the derivation must actually yield the intended live set.
        #
        # `starting` joined it on 2026-08-11 and that is a deliberate classification, not a
        # side effect of adding a state: a managed spawn inside its boot window IS reachable —
        # a send queues and is delivered when the worker arrives, exactly as it did when this
        # window reported `available`. Listing it here means a future change that quietly moved
        # it into NON_LIVE (and so stopped it being counted as sendable) has to say so.
        self.assertEqual(
            [s for s in VALID_STATUSES if s not in non_live],
            ["working", "online", "available", "blocked", "starting"],
        )

    def test_every_agent_status_has_a_presentation_entry(self):
        """A canonical status with no STATUS_KINDS entry silently renders as 'unknown'.

        This is the check that would catch adding a seventh state server-side and forgetting the
        dashboard: the suite goes red instead of the chip going grey.
        """
        missing = [s for s in VALID_STATUSES if not re.search(r"^\s+%s:" % re.escape(s), self.status_js, re.M)]
        self.assertEqual(missing, [], f"STATUS_KINDS has no entry for: {missing} — they would render as 'unknown'")

    def test_the_old_hand_copies_are_gone(self):
        """Guard the consolidation itself, across EVERY dashboard module.

        The copies were removed; this fails if one is reintroduced by a future edit that "just needs
        the list here too". That is how both copies got there the first time.

        SCOPE WIDENED in v0.5.4. This scanned exactly ("app.js", "chat.js") — the two files the copies
        historically lived in. The decomposition has since created ~25 client modules, and a literal
        re-declared in any of them would have passed unnoticed: the guard kept passing while covering a
        shrinking share of its own subject. Second gate in two slices with that shape, after
        inspector-refresh.test.mjs. After moving code, check whether a gate's SCOPE was defined by where
        that code used to live.

        Discovery rather than a longer tuple, for the reason test_new_dashboard_app.py already records:
        a hardcoded list needs editing once per extraction, forever, and passes silently the one time
        someone forgets. FIXTURES ARE EXCLUDED deliberately — fixtures/app.before-*.js is a
        pre-extraction snapshot that still contains these very copies, so scanning it would fail the
        guard on a file that ships to nobody.
        """
        sources = [
            path
            for path in sorted(DASHBOARD.rglob("*.js")) + sorted(DASHBOARD.rglob("*.mjs"))
            if "fixtures" not in path.parts
            and not path.name.endswith((".test.js", ".test.mjs"))
            # status.js is the OWNER. Its AGENT_STATUSES literal legitimately begins with these four, so
            # the widened scan flagged it on the first run — the guard means "no module OTHER than the
            # owner may re-declare the list", and that exclusion was implicit while the scan named only
            # app.js and chat.js. Made explicit now that the scan is by discovery.
            and path.name != "status.js"
        ]
        self.assertGreater(len(sources), 10, "the module scan found almost nothing — has the layout moved?")
        for path in sources:
            self.assertNotRegex(
                path.read_text(encoding="utf-8"),
                r"\[\s*'working',\s*'online',\s*'available',\s*'blocked'",
                f"{path.name} re-declares the live status list — import LIVE_AGENT_STATUSES from status.js instead",
            )

    def test_the_hand_copy_detector_actually_detects(self):
        """Anti-vacuity: a widened scan that matches nothing is worse than the narrow one it replaced.

        Without this, a regex broken by a future formatting change would report every module clean.
        """
        import re as _re

        pattern = r"\[\s*'working',\s*'online',\s*'available',\s*'blocked'"
        self.assertRegex("const LIVE = ['working', 'online', 'available', 'blocked'];", pattern)
        self.assertRegex("new Set(['working','online','available','blocked'])", pattern,
                         "spacing must not let a copy through")
        self.assertNotRegex("const X = ['working', 'offline'];", pattern,
                            "a different list must not be mistaken for the live one")


if __name__ == "__main__":
    unittest.main()
