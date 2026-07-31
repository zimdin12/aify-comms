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
        self.assertEqual(
            [s for s in VALID_STATUSES if s not in non_live],
            ["working", "online", "available", "blocked"],
        )

    def test_every_agent_status_has_a_presentation_entry(self):
        """A canonical status with no STATUS_KINDS entry silently renders as 'unknown'.

        This is the check that would catch adding a seventh state server-side and forgetting the
        dashboard: the suite goes red instead of the chip going grey.
        """
        missing = [s for s in VALID_STATUSES if not re.search(r"^\s+%s:" % re.escape(s), self.status_js, re.M)]
        self.assertEqual(missing, [], f"STATUS_KINDS has no entry for: {missing} — they would render as 'unknown'")

    def test_the_old_hand_copies_are_gone(self):
        """Guard the consolidation itself.

        The copies were removed; this fails if one is reintroduced by a future edit that "just needs
        the list here too". That is how both copies got there the first time.
        """
        for name in ("app.js", "chat.js"):
            source = (DASHBOARD / name).read_text(encoding="utf-8")
            self.assertNotRegex(
                source,
                r"\[\s*'working',\s*'online',\s*'available',\s*'blocked'",
                f"{name} re-declares the live status list — import LIVE_AGENT_STATUSES from status.js instead",
            )


if __name__ == "__main__":
    unittest.main()
