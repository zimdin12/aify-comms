"""A shared `.json` FILE is read back as a file, not as the inline-text envelope.

The read route answers a stored file with `FileResponse(path, filename=name)`, typed from the name, so a
`.json` file arrives as `application/json`, the same type as text shared inline (`{content, meta}`). A
reader that told them apart by type alone took the file for an envelope: `{"answer":42}` read as empty, and
`{"content":"inner"}` as its member alone (review of 9f6e1a90). The attachment header is the difference.

Driven through the real routes, and the real response handed to the service's `comms_read`. The JS readers
(`isStoredFile`, `mcp/stdio/shared-file-transfer.mjs`) are held to the headers asserted here by
`the-channel-moves-files-on-an-http-launch.test.js`.
"""

import asyncio

from service.sse import shared_file_tools as sf
from service.tests._base import FastApiTestCase

FILES = {"ordinary.json": b'{"answer":42}\n', "document.json": b'{"content":"inner text","other":17}\r\n'}


class ASharedJsonFileIsReadAsAFile(FastApiTestCase):
    def _read_with_comms_read(self, response):
        async def fake_request(method, path, **_):
            return response

        original, sf.request = sf.request, fake_request
        try:
            return asyncio.run(sf.comms_read(name=response.request.url.path.rsplit("/", 1)[-1]))
        finally:
            sf.request = original

    def test_a_json_file_comes_back_as_an_attachment_and_is_described_as_a_file(self):
        for name, payload in FILES.items():
            with self.subTest(name=name):
                up = self.client.post("/api/v1/shared", data={"from_agent": "alice", "name": name},
                                      files={"file": (name, payload, "application/octet-stream")})
                self.assertEqual(up.status_code, 200, up.text)
                read = self.client.get(f"/api/v1/shared/{name}")
                self.assertEqual(read.content, payload)
                self.assertEqual((read.headers["content-type"], read.headers["content-disposition"]),
                                 ("application/json", f'attachment; filename="{name}"'))
                self.assertTrue(self._read_with_comms_read(read).startswith(f'Binary artifact "{name}" ({len(payload)} bytes).'))

    def test_control_text_shared_inline_is_still_rendered(self):
        up = self.client.post("/api/v1/shared", data={"from_agent": "alice", "name": "inline.json", "content": '{"answer":42}'})
        self.assertEqual(up.status_code, 200, up.text)
        read = self.client.get("/api/v1/shared/inline.json")
        self.assertNotIn("content-disposition", read.headers)
        self.assertTrue(self._read_with_comms_read(read).endswith('{"answer":42}'))
