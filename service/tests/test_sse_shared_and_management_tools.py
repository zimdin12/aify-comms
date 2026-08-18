"""The shared-file and management renderers, driven for the first time.

Same gap as the channel tools: reachable over SSE since the transport shipped, never called by a
test, because getting at them meant loading a 730-line module by path. Extracted, they are imports.

WHAT IS WORTH PINNING HERE is narrower than "does it work". Three of these five say something a
caller acts on and could be wrong about:

  * `comms_read` decides whether a payload has readable content or is a binary the server is only
    holding — and answers in prose either way, so a caller cannot distinguish them by type.
  * `comms_clear` is the only DESTRUCTIVE tool on this transport, and "Nothing to clear." and a list
    of counts are different claims. An agent that reads a no-op as a success does not run it again.
  * `comms_share` is the one tool that does not go through `_api`; it builds a form-encoded request
    itself, so its error handling is its own and shares nothing with the other twenty.
"""

from __future__ import annotations

import asyncio
import unittest

from service.sse import management_tools as mg
from service.sse import shared_file_tools as sf


class _Api:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __call__(self, method, path, json_data=None, params=None):
        self.calls.append({"method": method, "path": path, "json": json_data, "params": params})
        return self.payload


def _with_api(module, tool, payload, **kwargs):
    api = _Api(payload)
    original, module._api = module._api, api
    try:
        return asyncio.run(tool(**kwargs)), api
    finally:
        module._api = original


class SharedFileToolTests(unittest.TestCase):
    def test_read_surfaces_an_error_rather_than_rendering_it_as_content(self):
        out, _ = _with_api(sf, sf.comms_read, {"detail": "not found"}, name="x")
        self.assertEqual("Error: not found", out)

    def test_read_distinguishes_a_binary_from_an_empty_file(self):
        """Both have no content to show; only one of them is a file the caller can ask for again."""
        out, _ = _with_api(sf, sf.comms_read, {"meta": {"from": "a"}}, name="blob.png")
        self.assertIn("binary file on server", out)
        self.assertIn("blob.png", out)

    def test_read_prefixes_content_with_its_provenance_when_there_is_any(self):
        out, _ = _with_api(
            sf, sf.comms_read,
            {"content": "print(1)", "meta": {"from": "coder", "sharedAt": "t", "description": "d"}},
            name="x.py",
        )
        self.assertTrue(out.startswith("From: coder | t | d"), out[:60])
        self.assertTrue(out.endswith("print(1)"))

    def test_read_returns_bare_content_when_nothing_is_known_about_it(self):
        """A header built from missing metadata would read as "From: ? | " — noise presented as fact."""
        out, _ = _with_api(sf, sf.comms_read, {"content": "raw", "meta": {}}, name="x")
        self.assertEqual("raw", out)

    def test_files_is_BOUNDED_and_admits_what_it_withheld(self):
        """Measured live 2026-08-18: this tool took no parameters and returned all 333 shared
        artifacts — 87,014 characters, which the caller's harness refused to inline. An unbounded
        list is a claim on the agent's own context, and a truncated one that does not admit it is
        truncated reads as "that is everything"."""
        many = [{"name": f"f{i}.py", "size": i, "from": "coder", "sharedAt": "t"} for i in range(120)]
        out, _ = _with_api(sf, sf.comms_files, {"files": many})
        listed = [line for line in out.splitlines() if line.startswith("- ")]
        self.assertEqual(len(listed), 50, "the default cap did not apply")
        self.assertIn("showing 50 of 120", out, "the reply did not say what it withheld")
        self.assertIn("limit=", out, "the reply did not say how to see more")

    def test_files_limit_query_and_fromAgent_narrow_the_list(self):
        files = [
            {"name": "alpha.py", "size": 1, "from": "coder", "sharedAt": "t", "description": "parser notes"},
            {"name": "beta.py", "size": 2, "from": "tester", "sharedAt": "t"},
            {"name": "gamma.md", "size": 3, "from": "coder", "sharedAt": "t"},
        ]
        by_agent, _ = _with_api(sf, sf.comms_files, {"files": files}, fromAgent="coder")
        self.assertIn("alpha.py", by_agent)
        self.assertIn("gamma.md", by_agent)
        self.assertNotIn("beta.py", by_agent)

        by_name, _ = _with_api(sf, sf.comms_files, {"files": files}, query="beta")
        self.assertIn("beta.py", by_name)
        self.assertNotIn("alpha.py", by_name)

        # description matches too — the useful case when a name is a hash
        by_desc, _ = _with_api(sf, sf.comms_files, {"files": files}, query="parser")
        self.assertIn("alpha.py", by_desc)

        capped, _ = _with_api(sf, sf.comms_files, {"files": files}, limit=1)
        self.assertEqual(len([l for l in capped.splitlines() if l.startswith("- ")]), 1)

    def test_files_says_how_many_exist_when_a_FILTER_matched_nothing(self):
        """"No results" and "no results here" are different facts — the second one needs the total."""
        files = [{"name": "a.py", "size": 1, "from": "coder", "sharedAt": "t"}]
        out, _ = _with_api(sf, sf.comms_files, {"files": files}, query="nothing-matches-this")
        self.assertIn("1 shared in total", out)

    def test_files_empty_says_empty(self):
        out, _ = _with_api(sf, sf.comms_files, {"files": []})
        self.assertEqual("No shared artifacts.", out)

    def test_files_lists_size_author_and_time_and_omits_a_missing_description(self):
        out, _ = _with_api(sf, sf.comms_files, {"files": [
            {"name": "a.py", "size": 12, "from": "coder", "sharedAt": "t", "description": "notes"},
            {"name": "b.py", "size": 0, "from": "tester", "sharedAt": "t2"},
        ]})
        self.assertIn("- a.py (12B, from: coder, t) -- notes", out)
        self.assertIn("- b.py (0B, from: tester, t2)", out)
        self.assertNotIn("b.py (0B, from: tester, t2) --", out, "no trailing separator without a description")


class ShareUsesItsOwnRequestTests(unittest.TestCase):
    """`comms_share` posts form-encoded, so none of the `_api` machinery covers it."""

    def _run(self, payload, **kwargs):
        sent = {}

        class _Resp:
            def json(self_inner):
                return payload

        class _Client:
            def __init__(self, timeout=None):
                sent["timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, data=None):
                sent.update(url=url, headers=headers, data=data)
                return _Resp()

        original, sf.httpx = sf.httpx, type("m", (), {"AsyncClient": _Client})
        url_original, sf._api_url = sf._api_url, lambda: "http://svc/api/v1"
        try:
            return asyncio.run(sf.comms_share(**kwargs)), sent
        finally:
            sf.httpx, sf._api_url = original, url_original

    def test_share_posts_form_data_to_the_shared_endpoint(self):
        out, sent = self._run({"name": "a.py", "size": 12},
                              from_agent="coder", name="a.py", content="x", description="d")
        self.assertEqual("http://svc/api/v1/shared", sent["url"])
        self.assertEqual(
            {"from_agent": "coder", "name": "a.py", "content": "x", "description": "d"},
            sent["data"],
        )
        self.assertEqual('Shared "a.py" (12 bytes).', out)

    def test_share_surfaces_a_server_error(self):
        out, _ = self._run({"detail": "name taken"}, from_agent="c", name="a", content="x")
        self.assertEqual("Error: name taken", out)

    def test_share_reports_the_name_the_SERVER_chose(self):
        """The server may rename on collision; echoing the requested name would be a false receipt."""
        out, _ = self._run({"name": "a-2.py", "size": 3}, from_agent="c", name="a.py", content="x")
        self.assertIn("a-2.py", out)


class ManagementToolTests(unittest.TestCase):
    def test_clear_distinguishes_a_no_op_from_a_deletion(self):
        """The distinction that matters on the only destructive tool here."""
        out, _ = _with_api(mg, mg.comms_clear, {"ok": True, "cleared": {}}, target="messages")
        self.assertEqual("Nothing to clear.", out)

        out, _ = _with_api(mg, mg.comms_clear, {"ok": True, "cleared": {"messages": 4, "files": 0}},
                           target="all")
        self.assertIn("messages: 4", out)
        self.assertNotIn("files: 0", out, "a zero count is not something that was cleared")

    def test_clear_surfaces_a_refusal(self):
        out, _ = _with_api(mg, mg.comms_clear, {"ok": False, "detail": "unknown target"}, target="nope")
        self.assertEqual("Error: unknown target", out)

    def test_clear_omits_optional_filters_it_was_not_given(self):
        """Sending `olderThanHours: 0` would mean "everything" to a server reading it as a bound."""
        _, api = _with_api(mg, mg.comms_clear, {"ok": True, "cleared": {}}, target="messages")
        self.assertEqual({"target": "messages"}, api.calls[0]["json"])

        _, api = _with_api(mg, mg.comms_clear, {"ok": True, "cleared": {}},
                           target="messages", agentId="a", olderThanHours=2.5)
        self.assertEqual({"target": "messages", "agentId": "a", "olderThanHours": 2.5},
                         api.calls[0]["json"])

    def test_dashboard_reads_the_port_at_CALL_time(self):
        """It calls get_config() rather than closing over the module-level config, so a port change
        takes effect without a restart. Pinned because collapsing it to the module constant is a
        tidy-looking edit that silently changes that."""
        out = asyncio.run(mg.comms_dashboard())
        self.assertIn("/api/v1/dashboard", out)

        original = mg.get_config
        mg.get_config = lambda: type("cfg", (), {"port": 9999})()
        try:
            self.assertIn("localhost:9999", asyncio.run(mg.comms_dashboard()))
        finally:
            mg.get_config = original


if __name__ == "__main__":
    unittest.main()
