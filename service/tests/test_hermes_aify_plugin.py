from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO / "integrations" / "hermes-aify-plugin"


class HermesAifyPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(PLUGIN_ROOT))

    def tearDown(self) -> None:
        try:
            sys.path.remove(str(PLUGIN_ROOT))
        except ValueError:
            pass

    def test_gateway_patch_registers_visible_bind_method(self) -> None:
        from aify_hermes_plugin.gateway_patch import patch_gateway_server

        class FakeTeeTransport:
            def __init__(self, primary, bridge):
                self.primary = primary
                self.bridge = bridge

        bridge_transport = object()
        primary_transport = object()
        session = {
            "session_key": "visible-key",
            "transport": primary_transport,
            "running": False,
        }
        module = types.SimpleNamespace(
            _methods={},
            _sessions={"visible-sid": session},
            _stdio_transport=object(),
            TeeTransport=FakeTeeTransport,
            current_transport=lambda: bridge_transport,
            _ok=lambda rid, result: {"id": rid, "result": result},
            _err=lambda rid, code, msg: {
                "id": rid,
                "error": {"code": code, "message": msg},
            },
            logger=types.SimpleNamespace(info=lambda *args, **kwargs: None),
        )

        patch_gateway_server(module)

        self.assertIn("aify.session.bind_transport", module._methods)
        response = module._methods["aify.session.bind_transport"](
            "1", {"session_id": "visible-key"}
        )
        self.assertEqual(response["result"]["session_id"], "visible-sid")
        self.assertEqual(response["result"]["session_key"], "visible-key")
        self.assertTrue(response["result"]["mirrored"])
        self.assertIsInstance(session["transport"], FakeTeeTransport)
        self.assertIs(session["transport"].primary, primary_transport)
        self.assertIs(session["transport"].bridge, bridge_transport)

    def test_gateway_patch_registers_visible_render_notice_method(self) -> None:
        from aify_hermes_plugin.gateway_patch import patch_gateway_server

        writes: list[dict] = []

        class FakeTransport:
            def write(self, obj):
                writes.append(obj)
                return True

        session = {
            "session_key": "visible-key",
            "transport": FakeTransport(),
            "running": False,
        }
        module = types.SimpleNamespace(
            _methods={},
            _sessions={"visible-sid": session},
            _stdio_transport=FakeTransport(),
            current_transport=lambda: None,
            _ok=lambda rid, result: {"id": rid, "result": result},
            _err=lambda rid, code, msg: {
                "id": rid,
                "error": {"code": code, "message": msg},
            },
            logger=types.SimpleNamespace(info=lambda *args, **kwargs: None),
        )

        patch_gateway_server(module)

        self.assertIn("aify.session.render_notice", module._methods)
        response = module._methods["aify.session.render_notice"](
            "2",
            {
                "session_id": "visible-key",
                "notice": "aify-comms wake from sc-hermes-test-2",
                "status": "aify-comms message received",
            },
        )

        self.assertEqual(response["result"]["session_id"], "visible-sid")
        event_types = [w.get("params", {}).get("type") for w in writes]
        self.assertEqual(event_types, ["review.summary", "status.update"])
        self.assertEqual(
            writes[0]["params"]["payload"]["text"],
            "aify-comms wake from sc-hermes-test-2",
        )
        self.assertEqual(
            writes[1]["params"]["payload"],
            {"kind": "aify-comms", "text": "aify-comms message received"},
        )

    def test_gateway_patch_discovers_mcp_before_tui_agent_build(self) -> None:
        from aify_hermes_plugin.gateway_patch import patch_gateway_server

        calls: list[str] = []
        fake_tools = types.ModuleType("tools")
        fake_mcp_tool = types.ModuleType("tools.mcp_tool")

        def discover_mcp_tools():
            calls.append("discover")
            return ["mcp_aify_comms_comms_register"]

        fake_mcp_tool.discover_mcp_tools = discover_mcp_tools
        old_tools = sys.modules.get("tools")
        old_mcp_tool = sys.modules.get("tools.mcp_tool")
        sys.modules["tools"] = fake_tools
        sys.modules["tools.mcp_tool"] = fake_mcp_tool

        def make_agent(*args, **kwargs):
            calls.append("make")
            return {"ok": True}

        module = types.SimpleNamespace(
            _methods={},
            _sessions={},
            _stdio_transport=object(),
            _make_agent=make_agent,
            current_transport=lambda: None,
            _ok=lambda rid, result: {"id": rid, "result": result},
            _err=lambda rid, code, msg: {
                "id": rid,
                "error": {"code": code, "message": msg},
            },
            logger=types.SimpleNamespace(
                info=lambda *args, **kwargs: None,
                debug=lambda *args, **kwargs: None,
            ),
        )

        try:
            patch_gateway_server(module)
            self.assertEqual(module._make_agent("sid", "key"), {"ok": True})
        finally:
            if old_tools is None:
                sys.modules.pop("tools", None)
            else:
                sys.modules["tools"] = old_tools
            if old_mcp_tool is None:
                sys.modules.pop("tools.mcp_tool", None)
            else:
                sys.modules["tools.mcp_tool"] = old_mcp_tool

        self.assertEqual(calls, ["discover", "make"])

    def test_web_server_patch_exports_gateway_url_for_dashboard_mcp_children(self) -> None:
        from aify_hermes_plugin.patches import patch_hermes_cli_web_server

        keys = [
            "AIFY_HERMES_PORT",
            "AIFY_HERMES_GATEWAY_URL",
            "HERMES_TUI_GATEWAY_URL",
            "AIFY_HERMES_GATEWAY_TOKEN",
            "AIFY_HERMES_GATEWAY_TOKEN_ENV",
        ]
        old_env = {key: os.environ.get(key) for key in keys}
        for key in keys:
            os.environ.pop(key, None)
        os.environ["AIFY_HERMES_PORT"] = "61234"
        module = types.SimpleNamespace(_SESSION_TOKEN="token-123")

        try:
            patch_hermes_cli_web_server(module)

            expected = "ws://127.0.0.1:61234/api/ws?token=token-123"
            self.assertEqual(os.environ["AIFY_HERMES_GATEWAY_URL"], expected)
            self.assertEqual(os.environ["HERMES_TUI_GATEWAY_URL"], expected)
            self.assertEqual(os.environ["AIFY_HERMES_GATEWAY_TOKEN"], "token-123")
            self.assertEqual(
                os.environ["AIFY_HERMES_GATEWAY_TOKEN_ENV"],
                "AIFY_HERMES_GATEWAY_TOKEN",
            )
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_active_session_file_patch_preserves_wrapper_file(self) -> None:
        from aify_hermes_plugin.patches import patch_hermes_cli_main

        target = Path(tempfile.mkdtemp()) / "visible-session.json"
        module = types.SimpleNamespace(os=os, tempfile=tempfile)

        def fake_launch_tui():
            fd, active_session_file = tempfile.mkstemp(
                prefix="hermes-tui-active-session-", suffix=".json"
            )
            os.close(fd)
            Path(active_session_file).write_text('{"session_id":"sid"}')
            os.unlink(active_session_file)
            return active_session_file

        module._launch_tui = fake_launch_tui
        patch_hermes_cli_main(module)

        old_env = os.environ.get("HERMES_TUI_ACTIVE_SESSION_FILE")
        os.environ["HERMES_TUI_ACTIVE_SESSION_FILE"] = str(target)
        try:
            returned = module._launch_tui()
        finally:
            if old_env is None:
                os.environ.pop("HERMES_TUI_ACTIVE_SESSION_FILE", None)
            else:
                os.environ["HERMES_TUI_ACTIVE_SESSION_FILE"] = old_env

        self.assertEqual(returned, str(target))
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), '{"session_id":"sid"}')


if __name__ == "__main__":
    unittest.main()
