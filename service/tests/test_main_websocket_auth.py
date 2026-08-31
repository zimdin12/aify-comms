import tempfile
import unittest

#: A REALISTIC HOST. `TestClient` defaults to `http://testserver`, and the guard now requires every
#: request to arrive on a Host this service trusts -- loopback, a literal IP, or a name the
#: operator declared. `testserver` is none of those, and nothing real sends it; a bridge, a CLI
#: or `curl` reaches the service exactly like this.
LOOPBACK = "http://127.0.0.1:8800"
#: `TestClient.websocket_connect` sends `Host: testserver` REGARDLESS of `base_url` -- measured,
#: by spying on the guard: it received `host="testserver"` from a client built on
#: `http://127.0.0.1:8800`. So a websocket test states the Host itself, or it is testing the
#: refusal of a hostname nothing real sends.
WS_HOST = {"host": "127.0.0.1:8800"}


from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import service.main as main_module
from service.config import ServiceConfig


class WebsocketAuthTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_get_config = main_module.get_config
        config = ServiceConfig(
            data_dir=self._tmpdir.name,
            config_dir=self._tmpdir.name,
            api_key="secret",
            mcp_enabled=False,
            cors_origins=["*"],
        )
        main_module.get_config = lambda: config
        self.app = main_module.create_app()

    def tearDown(self):
        main_module.get_config = self._original_get_config
        self._tmpdir.cleanup()

    def test_websocket_requires_api_key(self):
        with TestClient(self.app, base_url=LOOPBACK) as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/ws?agent_id=tester", headers=WS_HOST):
                    pass

    def test_websocket_accepts_valid_api_key_and_tracks_agent(self):
        with TestClient(self.app, base_url=LOOPBACK) as client:
            with client.websocket_connect("/ws?agent_id=tester&api_key=secret", headers=WS_HOST) as ws:
                ws.send_text("ping")
                self.assertIn("tester", client.app.state.ws_manager.online_agents())
