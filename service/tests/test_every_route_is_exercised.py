"""Every route the app serves must be exercised by some test.

THE JS SIDE HAS THIS FLOOR AND PYTHON DID NOT. `every-module-is-imported-by-a-test.test.js` states
the reason: it catches "a module extracted in a hurry with no test file, which is invisible to every
other gate here". The obvious port — "every module is imported by a test" — does NOT work in Python
and the numbers say why: 92 of 214 service modules are imported by no test, because a router is
exercised through `create_app()` and a TestClient rather than by import. Porting it would have
produced a 92-entry backlog that misrepresents the coverage this suite genuinely has.

THE ROUTE IS THE RIGHT UNIT HERE. It is what the app actually serves, it is what `create_app()`
already counts (124, gated elsewhere), and a route nothing calls is the same shape of hole: a handler
that could 500 on its first real request with every suite green.

MEASURED FIRST: 127 method+path routes, 7 with no test naming their path. Five are FastAPI/static
(`favicon.*`, `/docs/oauth2-redirect`). The two real ones were both DATA-REPAIR endpoints — the kind
that runs rarely, mutates rows, and is exactly where nobody looks:

    POST /api/v1/messages/cleanup/orphan-unread          now tested by test_orphan_unread_cleanup_query
    POST /api/v1/contracts/hygiene/repair-read-receipts  now tested below

WHAT THIS PROVES AND WHAT IT DOES NOT. Matching a path's literal segments against the test tree shows
a test MENTIONS the route, not that it asserts anything useful about it. It is a floor, exactly as
its JS counterpart says of itself: "'some test imports it' does not mean the module is meaningfully
exercised". What it catches is the case that actually happens — a route added with no test at all.
"""

from __future__ import annotations

import pathlib
import re
import unittest

from service.main import create_app

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Routes FastAPI or the static mount provides, which no handler in this repo owns. Listed rather
#: than pattern-matched so a real route can never hide behind a loose rule.
FRAMEWORK_ROUTES = {
    "GET /favicon.ico",
    "GET /favicon.svg",
    "GET /api/v1/favicon.ico",
    "GET /api/v1/favicon.svg",
    "GET /docs/oauth2-redirect",
}

#: OURS, AND OWED — a different thing from the framework list above, kept separate so the two are
#: not confused. Every route here is a handler this repo owns with no test calling it.
#:
#: THE WHOLE CONTAINER/GPU ROUTER, which is consistent with what its module already shows: 6 of its
#: 10 functions have no test either, and `service/containers/proxy.py` had none until the header
#: filter got one on 2026-08-16. Testing these needs a container MANAGER — they start, stop, pull and
#: proxy to real Docker containers — so it is a fixture-building job rather than a line of assertion,
#: which is exactly why the debt is recorded instead of quietly widened into FRAMEWORK_ROUTES.
#:
#: MAY ONLY SHRINK, on the same ratchet as the JS side's `UNTESTED_BACKLOG`: the test below fails if
#: an entry here is now exercised, so paying one off forces its removal instead of leaving slack a
#: later route could inherit.
UNTESTED_ROUTE_BACKLOG = {
    "GET /api/v1/containers",
    "GET /api/v1/containers/{name}",
    "GET /api/v1/containers/{name}/logs",
    "GET /api/v1/gpu",
    "POST /api/v1/containers/{name}/pull",
    "POST /api/v1/containers/{name}/restart",
    "POST /api/v1/containers/{name}/start",
    "POST /api/v1/containers/{name}/stop",
}


def _routes() -> list[tuple[str, str]]:
    app = create_app()
    found = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path:
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            found.add((method, path))
    return sorted(found)


SELF = pathlib.Path(__file__).name


def _test_tree_text() -> list[str]:
    """Each test file's source, SEPARATELY — and never this one.

    TWO REASONS, both learned within a minute of each other while writing this.

    THIS FILE IS EXCLUDED because it names the very routes it asks about: `FRAMEWORK_ROUTES` spells
    them out and the anti-vacuity case names a deliberately fake path. Counting itself made every one
    of them read as exercised — the gate exonerating exactly what it exists to track. Its JS
    counterpart carries the identical exclusion for the identical reason, and I walked into it anyway.

    PER FILE, NOT ONE BLOB, because a route's segments appearing SOMEWHERE across 260 files says
    almost nothing — `agents` and `stop` are in dozens of them. Requiring them in the SAME file is
    still a floor, but it is a floor about one test rather than about the corpus.
    """
    sources = []
    for path in sorted(pathlib.Path(REPO / "service" / "tests").rglob("*.py")):
        if "__pycache__" in path.parts or "data" in path.parts or path.name == SELF:
            continue
        sources.append(path.read_text(encoding="utf-8", errors="replace"))
    return sources


def _mentioned(path: str, sources: list[str]) -> bool:
    """True when the test tree names every literal segment of this route.

    Path PARAMETERS are stripped: a test calls `/agents/{agent_id}/stop` as `/agents/x/stop`, so the
    braces never appear. Comparing on the literal segments is what makes a parameterised route
    checkable at all — and it is why this is a floor rather than a coverage claim.
    """
    literal = re.sub(r"\{[^}]+\}", "", path)
    segments = [segment for segment in literal.split("/") if segment]
    if not segments:
        return any(path in source for source in sources)
    return any(all(segment in source for segment in segments) for source in sources)


class EveryRouteIsExercisedTests(unittest.TestCase):
    def test_no_route_is_unmentioned_by_the_test_tree(self):
        """THE ONE THAT MATTERS. A route nothing calls can 500 on its first request, all green."""
        sources = _test_tree_text()
        unmentioned = [
            f"{method} {path}" for method, path in _routes()
            if not _mentioned(path, sources)
        ]
        unexpected = sorted(set(unmentioned) - FRAMEWORK_ROUTES - UNTESTED_ROUTE_BACKLOG)
        self.assertEqual(
            unexpected, [],
            "these routes are served but no test names them:\n  " + "\n  ".join(unexpected)
            + "\nAdd a test that calls the route, or — if it is genuinely framework-provided — add it "
            "to FRAMEWORK_ROUTES with the reason.",
        )

    def test_the_framework_list_may_only_shrink(self):
        """A listed route that IS now exercised is slack: it would let a later route of the same
        name inherit the exemption. Same ratchet as the JS side's UNTESTED_BACKLOG."""
        sources = _test_tree_text()
        served = {f"{method} {path}" for method, path in _routes()}
        for entry in sorted(FRAMEWORK_ROUTES):
            with self.subTest(route=entry):
                self.assertIn(entry, served, f"{entry} is listed but not served any more — delete it")
                path = entry.split(" ", 1)[1]
                self.assertFalse(
                    _mentioned(path, sources),
                    f"{entry} is now exercised by a test — delete it from FRAMEWORK_ROUTES",
                )

    def test_the_scan_is_not_silently_matching_nothing(self):
        routes = _routes()
        self.assertGreater(len(routes), 100, f"only {len(routes)} routes found")
        sources = _test_tree_text()
        self.assertGreater(len(sources), 200, f"only {len(sources)} test files read")
        # A path that no test could plausibly mention must be reported, or the matcher is vacuous.
        self.assertFalse(_mentioned("/api/v1/definitely-not-a-real-route-xyzzy", sources))
        # …and one that every suite calls must not be.
        self.assertTrue(_mentioned("/api/v1/agents", sources))

    def test_the_two_repair_endpoints_stay_exercised(self):
        """Named, because they are why this gate exists and both mutate data.

        A general property is easy to satisfy vacuously; these two were the entire real finding, so
        losing their tests should fail here as well as in their own files.
        """
        sources = _test_tree_text()
        for path in (
            "/api/v1/messages/cleanup/orphan-unread",
            "/api/v1/contracts/hygiene/repair-read-receipts",
        ):
            with self.subTest(route=path):
                self.assertTrue(_mentioned(path, sources), f"{path} lost its test")

    def test_THE_BACKLOG_MAY_ONLY_SHRINK(self):
        """An entry that is now exercised is slack — it would let a later route inherit the pass.

        Same ratchet as the JS side's `UNTESTED_BACKLOG`, and the same reason: a debt list that can
        only be added to stops measuring anything.
        """
        sources = _test_tree_text()
        served = {f"{method} {path}" for method, path in _routes()}
        paid, gone = [], []
        for entry in sorted(UNTESTED_ROUTE_BACKLOG):
            if entry not in served:
                gone.append(entry)
                continue
            if _mentioned(entry.split(" ", 1)[1], sources):
                paid.append(entry)
        self.assertEqual(gone, [], "these routes no longer exist — delete them from the backlog")
        self.assertEqual(
            paid, [],
            "these now have a test — delete them from UNTESTED_ROUTE_BACKLOG in the same commit: "
            + ", ".join(paid),
        )

    def test_the_two_lists_mean_different_things(self):
        """Framework routes are NOT ours; backlog routes are ours and untested. Merging them would
        turn owed work into a permanent exemption, which is how a backlog stops being one."""
        self.assertEqual(
            FRAMEWORK_ROUTES & UNTESTED_ROUTE_BACKLOG, set(),
            "a route cannot be both framework-provided and our own untested handler",
        )
        for entry in UNTESTED_ROUTE_BACKLOG:
            self.assertTrue(
                entry.split(" ", 1)[1].startswith("/api/v1/"),
                f"{entry} is not one of our API routes — is it really a backlog entry?",
            )
