"""What the service's shared-artifact tools do that the JS tools cannot show.

Shares, reads, listings and clears are held to the JS tools case by case by
`test_tool_replies_match_the_js.py`. A BINARY read is the one branch apart: the JS bridge saves it beside the
agent and hands back the path; the service is not on the agent's machine, so it says the artifact is binary
and how large, rather than rendering bytes as text or claiming a file it did not write (0.9.11 plan:
binary transfer for agents without a local process is undecided).
"""

from __future__ import annotations

import asyncio
import unittest

import httpx

from service.sse import shared_file_tools as sf


class BinaryReadTests(unittest.TestCase):
    def _read(self, response: httpx.Response) -> str:
        async def fake_request(method, path, **_):
            return response

        original, sf.request = sf.request, fake_request
        try:
            return asyncio.run(sf.comms_read(name="shot.png"))
        finally:
            sf.request = original

    def test_a_binary_artifact_is_described_not_rendered(self):
        out = self._read(httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, headers={"content-type": "image/png"}))
        self.assertIn('Binary artifact "shot.png" (16 bytes)', out)
        self.assertNotIn("PNG", out.replace("shot.png", ""))

    def test_an_empty_text_artifact_is_not_called_binary(self):
        out = self._read(httpx.Response(200, json={"content": ""}))
        self.assertEqual('"shot.png" — empty or unreadable.', out)


if __name__ == "__main__":
    unittest.main()
