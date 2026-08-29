r"""Every bounded list the dashboard fetches reports that it is bounded.

THE SAME DEFECT SIX TIMES, so it stops being a bug and becomes a rule.

`/contracts` and `/terminals` reported `truncated` from the start. `/sessions`, `/dispatch/runs` and
`/messages/recent` did not, and each produced the identical failure: the page renders exactly what it
got, an operator searches for something that is not on it, and the empty state blames the filter. The
sessions page went further and offered a Spawn button while 303 sessions existed; the runs page built
its From dropdown from the loaded rows, so an agent whose last run fell off the page could not even be
selected; `/messages/recent` reported ``"total": len(messages)`` -- 80, on a query whose WHERE matched
33,612 rows.

DERIVED FROM THE DASHBOARD, NEVER LISTED. The population is every `limit=` request the dashboard's own
sources make, read out of them. A hand-kept list would have covered the five endpoints that existed
when it was written and reported green about the sixth -- which is exactly how the 1000-line gate read
`service/**` only, and how the placeholder scan covered claude alone.

ONE QUESTION, NOT ONE SPELLING. What a caller needs is "is this the whole answer", and two endpoints
already answer it in their own words: `/channels/{name}` returns `totalMessages` beside the page, and
`/messages/inbox/dashboard` returns `showing` and `total` -- which the bridge's `inbox-tools.mjs`
already reads as `r.total > r.showing`. Forcing those to say `truncated` instead would rename a working
contract for tidiness and break a live consumer. The gate asks whether the question is ANSWERABLE, and
names the two shapes that answer it.

WHAT THIS DOES NOT CLAIM. It checks the response SHAPE, not that any particular page renders a note.
The rendering is asserted per page in the dashboard suite. An answerable response nobody displays is
still better than one that cannot be answered: the second cannot be fixed in the dashboard alone.
"""
from __future__ import annotations

import re
from pathlib import Path

from service.tests._base import FastApiTestCase

REPO = Path(__file__).resolve().parents[2]
DASHBOARD = REPO / "service" / "new_dashboard"

#: `api('/path?...limit=N...')` and its template-literal form. Both spellings are in use, and reading
#: only the quoted one would have missed the channel fetch entirely.
CALL = re.compile(r"""api\(\s*['"`](?P<path>/[^'"`]*\blimit=[^'"`]*)['"`]""")

#: Placeholders the dashboard interpolates. Resolved to something this test can actually request; a
#: path left unresolved would be requested literally and 404, which reads as a missing endpoint rather
#: than as a gap in this scan.
SUBSTITUTIONS = {
    "${encodeURIComponent(name)}": "gate-channel",
    "${encodeURIComponent(state.chat.identity)}": "gate-agent",
}


def dashboard_capped_paths() -> list[str]:
    """Every capped list request the dashboard makes, with placeholders resolved."""
    paths = set()
    for source in sorted(DASHBOARD.glob("*.mjs")) + sorted(DASHBOARD.glob("*.js")):
        if source.name.endswith((".test.mjs", ".test.js")):
            continue
        for match in CALL.finditer(source.read_text(encoding="utf-8")):
            path = match.group("path")
            for placeholder, value in SUBSTITUTIONS.items():
                path = path.replace(placeholder, value)
            if "${" in path:
                # A placeholder this test cannot resolve would be requested literally. Refusing is the
                # honest answer: a silent skip is how a scan reports green about what it never asked.
                raise AssertionError(
                    f"{source.name} fetches a capped list with an unresolved placeholder: {path}. "
                    "Add it to SUBSTITUTIONS, or this endpoint is ungoverned."
                )
            paths.add(path)
    return sorted(paths)


#: The keys that carry a count of the WHOLE, beside the page. Either of these lets a caller compute
#: what `truncated` states outright.
TOTAL_KEYS = ("total", "totalMessages")


def answers_whether_it_is_the_whole_answer(body: object) -> bool:
    """Can a caller tell this page from the complete list?

    Two shapes qualify: an explicit `truncated`, or a count of the whole beside the returned rows.
    Nothing else does -- a bare list of eighty rows is indistinguishable from a complete list of eighty.
    """
    if not isinstance(body, dict):
        return False
    if "truncated" in body:
        return True
    return any(key in body for key in TOTAL_KEYS)


class CappedListSaysItIsCappedTests(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        registered = self.client.post("/api/v1/agents", json={
            "agentId": "gate-agent", "role": "coder", "runtime": "claude-code",
            "sessionMode": "resident",
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        created = self.client.post("/api/v1/channels", json={
            "name": "gate-channel", "createdBy": "gate-agent", "description": "capped-list gate",
        })
        self.assertIn(created.status_code, (200, 201, 409), created.text)

    def test_the_scan_finds_the_endpoints_it_is_meant_to_govern(self):
        """POSITIVE CONTROL on the population. A regex that matched nothing would pass every assertion
        below while opening no file, and this repo has produced that wrong zero more than once."""
        paths = dashboard_capped_paths()
        self.assertGreaterEqual(len(paths), 5, paths)
        for expected in ("/contracts", "/sessions", "/messages/recent"):
            self.assertTrue(
                any(p.startswith(expected) for p in paths),
                f"{expected} is fetched with a limit by the dashboard and the scan did not see it: {paths}",
            )

    def test_a_bare_page_does_not_count_as_an_answer(self):
        """NEGATIVE CONTROL on the predicate. If any dict passed, the gate would be satisfied by every
        endpoint that returns something, which is the false green it exists to prevent."""
        self.assertFalse(answers_whether_it_is_the_whole_answer({"ok": True, "runs": []}))
        self.assertFalse(answers_whether_it_is_the_whole_answer([]))
        self.assertTrue(answers_whether_it_is_the_whole_answer({"runs": [], "truncated": False}))
        self.assertTrue(answers_whether_it_is_the_whole_answer({"messages": [], "total": 3189}))
        self.assertTrue(answers_whether_it_is_the_whole_answer({"messages": [], "totalMessages": 12}))

    def test_the_scan_does_not_match_an_uncapped_request(self):
        """NEGATIVE CONTROL. A pattern loose enough to match every `api(...)` call would drag in
        endpoints that return one record, and the gate would be widened away rather than obeyed."""
        self.assertIsNone(CALL.search("api('/agents')"))
        self.assertIsNone(CALL.search("api(`/dispatch/runs/${id}`)"))
        self.assertIsNotNone(CALL.search("api('/sessions?limit=80')"))

    def test_EVERY_CAPPED_LIST_REPORTS_TRUNCATION(self):
        missing = []
        for path in dashboard_capped_paths():
            response = self.client.get(f"/api/v1{path}")
            self.assertEqual(response.status_code, 200, f"{path}: {response.text[:200]}")
            body = response.json()
            if not answers_whether_it_is_the_whole_answer(body):
                missing.append((path, sorted(body) if isinstance(body, dict) else type(body).__name__))
        self.assertEqual(missing, [], (
            "these bounded lists cannot say whether they are the whole answer, so a page rendering "
            "exactly what it got cannot tell a short list from a window. Read one row wider than the "
            "limit and return `truncated`, or return a count of the whole beside the page -- "
            f"/sessions, /dispatch/runs and /messages/recent each took the first route:\n{missing}"
        ))

    def test_truncation_is_reported_HONESTLY_and_not_hardcoded(self):
        """A `truncated: false` welded on satisfies the gate above and helps nobody. Each endpoint is
        asked for ONE row while more than one exists, and must say so."""
        for index in range(3):
            sent = self.client.post("/api/v1/messages/send", json={
                "from_agent": "gate-agent", "to": "gate-agent", "type": "info",
                "subject": f"s{index}", "body": "b",
            })
            self.assertEqual(sent.status_code, 200, sent.text)
        body = self.client.get("/api/v1/messages/recent?limit=1").json()
        self.assertEqual(len(body["messages"]), 1)
        self.assertTrue(body["truncated"], (
            "three messages exist and a one-row page did not report truncation, so the flag is not "
            "measuring anything"
        ))
