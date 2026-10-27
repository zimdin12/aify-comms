"""Hermes on an HTTP-tools launch shares and saves files through its plugin (0.9.11).

The service's tools cannot touch the agent's disk and no comms process runs inside hermes, so the plugin registers
`comms_share_file` and `comms_save_file`, which run the bridge's `shared-file-cli.mjs`. Executed end to end here:
the plugin's own registration and handlers, the real CLI under node, against a loopback stand-in for the service.
Binary bytes with a NUL and a CRLF must arrive and come back unchanged.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO / "integrations" / "hermes-aify-plugin"
PAYLOAD = b"PK\x03\x04\x00binary\r\nbytes\xff"


class _StandIn(http.server.BaseHTTPRequestHandler):
    received: list[dict] = []

    def log_message(self, *_args):  # quiet
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _StandIn.received.append({"path": self.path, "key": self.headers.get("X-API-Key"), "body": body})
        self._reply(200, b'{"ok": true}', "application/json")

    def do_GET(self):  # noqa: N802
        _StandIn.received.append({"path": self.path, "key": self.headers.get("X-API-Key")})
        self._reply(200, PAYLOAD, "application/zip", {"Content-Disposition": 'attachment; filename="a.zip"'})

    def _reply(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class _Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, **kwargs):
        self.tools[kwargs["name"]] = kwargs


class HermesFileToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(PLUGIN_ROOT))
        from aify_hermes_plugin import file_tools
        cls.file_tools = file_tools
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StandIn)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        sys.path.remove(str(PLUGIN_ROOT))

    def setUp(self):
        _StandIn.received.clear()
        self.file_tools._schemas = None
        # Every input the CLI resolves, sealed: the stand-in's URL, a fixture key, a fixture agent.
        url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.env = mock.patch.dict(os.environ, {
            "AIFY_COMMS_MCP": "http", "AIFY_SERVER_URL": url, "CLAUDE_MCP_SERVER_URL": url, "AIFY_SERVER_URLS": "",
            "AIFY_API_KEY": "fixture-key", "CLAUDE_MCP_API_KEY": "", "AIFY_AGENT_ID": "hermes-probe",
            "AIFY_COMMS_AGENT_ID": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def registered(self):
        ctx = _Ctx()
        self.file_tools.register(ctx)
        return ctx.tools

    def test_an_http_launch_registers_both_tools_with_the_channels_schemas(self):
        tools = self.registered()
        self.assertEqual(sorted(tools), ["comms_save_file", "comms_share_file"])
        self.assertEqual(tools["comms_share_file"]["schema"]["parameters"]["required"], ["filePath"])
        self.assertEqual(tools["comms_share_file"]["toolset"], "aify-comms-files")

    def test_control_a_stdio_launch_registers_none(self):
        os.environ["AIFY_COMMS_MCP"] = ""
        self.assertEqual(self.registered(), {})

    def test_a_binary_file_is_shared_byte_for_byte_as_the_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.zip"
            source.write_bytes(PAYLOAD)
            reply = self.registered()["comms_share_file"]["handler"]({"filePath": str(source)})
        self.assertIn(f'Shared "a.zip" ({len(PAYLOAD)} bytes)', reply)
        [post] = _StandIn.received
        self.assertEqual((post["path"], post["key"]), ("/api/v1/shared", "fixture-key"))
        self.assertIn(PAYLOAD, post["body"], "the file's bytes did not reach the service unchanged")
        self.assertIn(b"hermes-probe", post["body"], "the sender is not the agent")

    def test_a_shared_binary_is_saved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.zip"
            reply = self.registered()["comms_save_file"]["handler"]({"name": "a.zip", "savePath": str(target)})
            self.assertEqual(target.read_bytes(), PAYLOAD, reply)
        self.assertEqual(_StandIn.received[0]["path"], "/api/v1/shared/a.zip")

    def test_a_failure_is_a_reply_not_a_crash(self):
        reply = self.registered()["comms_share_file"]["handler"]({"filePath": str(REPO / "no-such-file.zip")})
        self.assertIn("Cannot read", reply)
        self.assertEqual(_StandIn.received, [], "nothing may be sent for a file that could not be read")


if __name__ == "__main__":
    unittest.main()
