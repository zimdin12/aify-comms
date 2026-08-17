"""`_github_compare` — the one function in `/version` that actually leaves the container.

It was among the service functions the suite never entered, and for a reason that looks like
diligence: `test_version_endpoint.py` injects a comparer so the tests stay offline. The effect is
that everything AROUND the network call is tested and the network call itself never runs — including
`test_update_block_present_with_default_comparer`, which is named for the default comparer and
injects a stub that raises.

So this file runs the real function against a real HTTP server on loopback. EXACTLY ONE
SUBSTITUTION is made: the origin `https://api.github.com` is swapped for the local server in the
SHIPPED url template, so the repo, the path shape and the compare DIRECTION under test are the ones
that ship. `test_the_shipped_template_still_points_at_github` asserts that seal — if the origin ever
changes, the swap would silently no-op and this whole file would start calling the real GitHub.

WHY THE DIRECTION MATTERS. `{sha}...main` asks "how far is the build I am running behind main". Flip
it and `behind_by` becomes `ahead_by`: a container four commits stale reports itself current, which
is the exact false green `/version` exists to prevent.

WHY THE HEADERS MATTER. GitHub refuses unauthenticated requests that send no `User-Agent` — the
call would 403 for a reason that has nothing to do with the sha, and `_check_update` would swallow
it into `behind_by: null` forever.

WHAT IT DELIBERATELY DOES NOT COVER. `urlopen`'s 5-second timeout is not exercised: proving it would
cost five seconds of wall clock per run. The unbounded-hang risk it guards is real and untested here.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.config import get_config
from service.routers import health

GITHUB_ORIGIN = "https://api.github.com"

KNOWN_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

# A trimmed compare payload. The real one carries `commits`, `files`, `base_commit`, urls and more —
# the extra keys are here on purpose, because only three fields may cross into `/version`.
COMPARE_PAYLOAD = {
    "status": "behind",
    "behind_by": 7,
    "ahead_by": 0,
    "total_commits": 7,
    "commits": [{"sha": "a" * 40, "commit": {"message": "something an operator need not see"}}],
    "html_url": "https://github.com/zimdin12/aify-comms/compare/x...main",
}


class _CompareHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
        self.server.requests.append({"path": self.path, "headers": dict(self.headers)})
        status, body = self.server.reply
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output readable
        pass


class _FakeGitHub:
    """A real HTTP server on loopback. `reply` is what the next request gets."""

    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _CompareHandler)
        self.server.daemon_threads = True
        self.server.requests = []
        self.respond_with(200, COMPARE_PAYLOAD)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def requests(self) -> list:
        return self.server.requests

    def respond_with(self, status: int, payload) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.server.reply = (status, body)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class GitHubCompareTestCase(unittest.TestCase):
    """Points the SHIPPED url template at a local server; restores everything afterwards."""

    def setUp(self):
        self.github = _FakeGitHub()
        self._saved_template = health._GITHUB_COMPARE_URL
        self._saved_comparer = health._update_comparer
        self.assertTrue(
            self._saved_template.startswith(GITHUB_ORIGIN),
            "the origin swap below would silently no-op and this test would call the real GitHub",
        )
        health._GITHUB_COMPARE_URL = self._saved_template.replace(GITHUB_ORIGIN, self.github.origin, 1)
        health._update_comparer = None
        health._reset_update_cache()

    def tearDown(self):
        health._GITHUB_COMPARE_URL = self._saved_template
        health._update_comparer = self._saved_comparer
        health._reset_update_cache()
        self.github.close()


class GithubCompareCallTests(GitHubCompareTestCase):
    """The network call itself."""

    def test_the_shipped_template_still_points_at_github(self):
        """The seal every other test here depends on. If the origin moves, the substitution in
        setUp stops substituting and the suite starts making real requests to the internet."""
        self.assertTrue(self._saved_template.startswith(GITHUB_ORIGIN + "/repos/"))

    def test_the_running_sha_is_compared_AGAINST_main(self):
        """`{sha}...main` asks how far the running build is BEHIND main. Reversed, `behind_by` and
        `ahead_by` trade places and a stale container reports itself current."""
        health._github_compare(KNOWN_SHA)
        path = self.github.requests[0]["path"]
        self.assertTrue(path.endswith(f"/compare/{KNOWN_SHA}...main"), path)

    def test_the_repo_in_the_url_is_this_one(self):
        health._github_compare(KNOWN_SHA)
        self.assertIn("/repos/zimdin12/aify-comms/compare/", self.github.requests[0]["path"])

    def test_a_USER_AGENT_is_sent(self):
        """GitHub 403s an unauthenticated request with no User-Agent. Without this header the check
        fails for a reason that has nothing to do with the sha — and fails SILENTLY, because
        `_check_update` swallows it into `behind_by: null`."""
        health._github_compare(KNOWN_SHA)
        self.assertEqual(self.github.requests[0]["headers"].get("User-Agent"), "aify-comms")

    def test_the_github_json_media_type_is_requested(self):
        health._github_compare(KNOWN_SHA)
        self.assertEqual(
            self.github.requests[0]["headers"].get("Accept"), "application/vnd.github+json",
        )

    def test_the_three_counts_are_lifted_from_the_payload(self):
        result = health._github_compare(KNOWN_SHA)
        self.assertEqual(result, {"behind_by": 7, "ahead_by": 0, "status": "behind"})

    def test_NOTHING_ELSE_from_the_payload_crosses_the_boundary(self):
        """A real compare response carries every commit message and file path between the two
        revisions. `/version` is unauthenticated — only the three counts may come back."""
        result = health._github_compare(KNOWN_SHA)
        self.assertEqual(set(result), {"behind_by", "ahead_by", "status"})

    def test_a_payload_missing_the_counts_yields_None_not_a_KeyError(self):
        """A proxy, a cached error document, or a future API shape. Missing means unknown, and
        unknown is what `_check_update` already knows how to report."""
        self.github.respond_with(200, {"message": "moved"})
        self.assertEqual(
            health._github_compare(KNOWN_SHA),
            {"behind_by": None, "ahead_by": None, "status": None},
        )

    def test_a_rate_limited_response_RAISES(self):
        """403 is the common failure — 60 requests an hour unauthenticated. The contract is that
        this function raises and the CALLER decides; swallowing here would report a rate-limited
        check as a successful comparison."""
        self.github.respond_with(403, {"message": "API rate limit exceeded"})
        with self.assertRaises(Exception):
            health._github_compare(KNOWN_SHA)

    def test_an_unknown_sha_RAISES(self):
        """404: the build sha is not on GitHub at all — a local commit that was never pushed."""
        self.github.respond_with(404, {"message": "Not Found"})
        with self.assertRaises(Exception):
            health._github_compare(KNOWN_SHA)

    def test_a_body_that_is_not_json_RAISES(self):
        """A captive portal or a proxy error page answers 200 with HTML."""
        self.github.respond_with(200, b"<html>proxy error</html>")
        with self.assertRaises(Exception):
            health._github_compare(KNOWN_SHA)


class CheckUpdateWithTheRealComparerTests(GitHubCompareTestCase):
    """`_check_update` over the real `_github_compare` — no stub in between."""

    def test_with_NO_comparer_injected_the_real_one_is_used(self):
        """The test this file exists for. `_update_comparer` is None here, so `_check_update` falls
        through to `_github_compare` and a real request is made."""
        result = health._check_update(KNOWN_SHA)
        self.assertEqual(len(self.github.requests), 1)
        self.assertEqual(result["behind_by"], 7)
        self.assertEqual(result["status"], "behind")
        self.assertEqual(result["source"], "github-compare")
        self.assertFalse(result["stale"])

    def test_a_rate_limited_github_becomes_behind_by_null_and_never_raises(self):
        """`/version` is the endpoint an operator reads to find out WHY something is wrong. It
        failing because GitHub was rate-limited would be its own outage."""
        self.github.respond_with(403, {"message": "API rate limit exceeded"})
        result = health._check_update(KNOWN_SHA)
        self.assertIsNone(result["behind_by"])
        self.assertTrue(result["stale"])

    def test_a_200_that_answers_null_is_still_STALE(self):
        """`stale` means "this number is not to be trusted", and a successful request that carried
        no count is exactly that — the HTTP status is not the thing being reported on."""
        self.github.respond_with(200, {"status": "identical"})
        result = health._check_update(KNOWN_SHA)
        self.assertIsNone(result["behind_by"])
        self.assertTrue(result["stale"])

    def test_an_UNKNOWN_sha_never_leaves_the_container(self):
        """An unstamped build has nothing to compare. Asking anyway spends one of 60 hourly
        requests to be told 404."""
        result = health._check_update("unknown")
        self.assertEqual(self.github.requests, [])
        self.assertIsNone(result["behind_by"])
        self.assertTrue(result["stale"])

    def test_an_EMPTY_sha_never_leaves_the_container(self):
        health._check_update("")
        self.assertEqual(self.github.requests, [])

    # ── the cache ────────────────────────────────────────────────────────────────────────────

    def test_a_second_check_inside_the_TTL_makes_no_request(self):
        """60 requests an hour, and the dashboard polls `/version`. Without the cache a single
        operator with a browser open exhausts the budget and every check after that reads null."""
        health._check_update(KNOWN_SHA)
        self.github.respond_with(200, {"behind_by": 999, "ahead_by": 0, "status": "behind"})
        second = health._check_update(KNOWN_SHA)
        self.assertEqual(len(self.github.requests), 1)
        self.assertEqual(second["behind_by"], 7, "a cached answer was re-fetched")

    def test_the_cache_EXPIRES(self):
        """A cache with no expiry would pin the first answer for the life of the process — which
        for this container means until it is rebuilt, i.e. exactly when the number stops mattering."""
        health._check_update(KNOWN_SHA)
        health._update_cache_at = time.time() - health._UPDATE_TTL_SECONDS - 1
        self.github.respond_with(200, {"behind_by": 12, "ahead_by": 0, "status": "behind"})
        self.assertEqual(health._check_update(KNOWN_SHA)["behind_by"], 12)
        self.assertEqual(len(self.github.requests), 2)

    def test_a_FAILED_check_is_cached_too(self):
        """Otherwise every poll retries a GitHub that is already refusing us, which is how a
        rate-limit lasts an hour instead of clearing."""
        self.github.respond_with(403, {"message": "API rate limit exceeded"})
        health._check_update(KNOWN_SHA)
        health._check_update(KNOWN_SHA)
        self.assertEqual(len(self.github.requests), 1)


class VersionRouteOverTheRealComparerTests(GitHubCompareTestCase):
    """The wiring: `/version` reaches the network function with the STAMPED sha."""

    def setUp(self):
        super().setUp()
        self._cfg = get_config()
        self._saved_sha = self._cfg.build_sha
        self._cfg.build_sha = KNOWN_SHA
        self.app = FastAPI()
        self.app.include_router(health.router)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self._cfg.build_sha = self._saved_sha
        super().tearDown()

    def test_the_endpoint_compares_the_sha_THIS_BUILD_was_stamped_with(self):
        """Comparing anything else — a hardcoded branch tip, a config default — would report on a
        build that is not the one answering the request."""
        response = self.client.get("/version")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(KNOWN_SHA, self.github.requests[0]["path"])
        self.assertEqual(response.json()["update"]["behind_by"], 7)

    def test_a_github_outage_does_not_make_the_endpoint_fail(self):
        self.github.respond_with(500, {"message": "unavailable"})
        response = self.client.get("/version")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["update"]["behind_by"])


if __name__ == "__main__":
    unittest.main()
