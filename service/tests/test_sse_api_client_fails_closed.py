"""A server error must not reach an agent as "you have no mail".

REPORTED BY A REVIEWER ON ANOTHER INSTANCE, 2026-08-18 (H5). `service/sse/api_client.py`'s `api()`
returned `resp.json()` regardless of status, and `{"status":…, "text":…}` when the body would not
parse. Neither carries `detail` — the key EVERY caller in this package branches on:

    r = await api("GET", "/messages/inbox/…")
    if "detail" in r:
        return f"Error: {r['detail']}"
    msgs = r.get("messages", [])
    if not msgs:
        return "Inbox empty."

So a 500, a proxy's HTML error page, or a dropped upstream rendered as **"Inbox empty."** — and as
"No agents registered", and "No results". The agent then acts on an absence that was really an
outage. For tools whose entire job is telling an agent what it has been asked to do, a false empty is
the worst available failure: it is indistinguishable from the truth and it is actionable.

The fix returns an error SHAPE, which is why no caller changed. These tests pin both directions,
because failing closed is only correct if success still works.
"""

from __future__ import annotations

import unittest
from unittest import mock

import service.sse.api_client as api_client


class FakeResponse:
    def __init__(self, status_code: int, text: str, json_value=None, raises: bool = False):
        self.status_code = status_code
        self.text = text
        self._json = json_value
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._json


class FakeClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *a, **k):
        return self._response

    async def post(self, *a, **k):
        return self._response

    async def delete(self, *a, **k):
        return self._response


async def _call(response, method: str = "GET"):
    with mock.patch.object(api_client.httpx, "AsyncClient", lambda *a, **k: FakeClient(response)):
        return await api_client.api(method, "/messages/inbox/agent-1")


class TheClientFailsClosed(unittest.IsolatedAsyncioTestCase):
    async def test_a_500_with_a_PLAINTEXT_body_is_an_error_not_an_empty(self):
        result = await _call(FakeResponse(500, "Internal Server Error", raises=True))
        self.assertIn("detail", result,
                      "a 500 produced a dict with no `detail`, which every caller renders as empty")
        self.assertIn("500", result["detail"])
        self.assertNotIn("messages", result, "an error must not look like a payload")

    async def test_a_500_whose_body_PARSES_is_also_an_error(self):
        # The half the report did not name, and the more likely one in production: nothing looked at
        # the status code at all, so a perfectly valid JSON error body was returned as success.
        result = await _call(FakeResponse(500, '{"error":"db locked"}', json_value={"error": "db locked"}))
        self.assertIn("detail", result, "a JSON 500 was passed through as if it were a payload")
        self.assertEqual(result["status"], 500)

    async def test_a_detail_from_the_API_is_PRESERVED_not_replaced(self):
        # FastAPI's own error shape. The caller prints `detail`, so the API's message must survive —
        # replacing it with a generic one would make every error read the same.
        # Returned UNCHANGED: the service already explained itself in the key callers read, so there
        # is nothing to add and no reason to reshape it. `test_sse_api_client.py` pins this exact
        # equality for the same reason, and the fix here is additive precisely so it keeps holding.
        result = await _call(FakeResponse(404, '{"detail":"agent not found"}',
                                          json_value={"detail": "agent not found"}))
        self.assertEqual(result, {"detail": "agent not found"})

    async def test_an_HTML_login_page_with_a_200_is_not_success(self):
        # Something that is not this API answered — a proxy, a captive portal. Non-empty and
        # unparseable is not a payload, and returning `{}` here would be another silent empty.
        result = await _call(FakeResponse(200, "<html><body>Sign in</body></html>", raises=True))
        self.assertIn("detail", result)
        self.assertIn("non-JSON", result["detail"])

    async def test_the_detail_is_TRUNCATED_so_a_page_of_html_cannot_flood_the_agent(self):
        result = await _call(FakeResponse(500, "x" * 5000, raises=True))
        self.assertLess(len(result["detail"]), 400, "an error body was pasted into the agent whole")


class SuccessStillWorks(unittest.IsolatedAsyncioTestCase):
    async def test_a_normal_payload_is_returned_unchanged(self):
        # ANTI-VACUITY. Every test above would also pass if `api()` returned an error for everything,
        # which would break all of comms.
        payload = {"messages": [{"id": "m1"}], "total": 1, "showing": 1}
        self.assertEqual(await _call(FakeResponse(200, "{}", json_value=payload)), payload)

    async def test_an_EMPTY_204_body_is_success_with_no_content(self):
        # DELETE endpoints answer this way. It must not become an error, or every successful
        # deletion would report a failure to the agent that asked for it. The pre-existing
        # `{"status", "text"}` shape is KEPT (`test_sse_api_client.py` pins it); what matters here is
        # only that no `detail` appears, since that is what every caller reads as failure.
        result = await _call(FakeResponse(204, "", raises=True), method="DELETE")
        self.assertNotIn("detail", result, "a no-content success was reported as an error")
        self.assertEqual(result["status"], 204)

    async def test_a_genuinely_empty_inbox_is_still_an_empty_inbox(self):
        # The case the false empty was impersonating. It has to keep working, or the fix would only
        # have moved the confusion.
        payload = {"messages": [], "total": 0, "showing": 0}
        result = await _call(FakeResponse(200, "{}", json_value=payload))
        self.assertNotIn("detail", result, "an empty inbox was reported as an error")
        self.assertEqual(result["messages"], [])


if __name__ == "__main__":
    unittest.main()
