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
        from aify_hermes_plugin.patches import patch_gateway_server

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
