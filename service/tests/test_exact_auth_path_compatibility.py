"""Issue 43: exact auth without breaking docs, transport, browser carriers or dashboard assets.

Real apps and the real MCP mount, but never the main service lifespan or a listening server.
"""
import asyncio
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from service import main, new_dashboard_app as dashboard
from service.config import ServiceConfig
from service.dashboard_access import COOKIE as DASHBOARD_COOKIE
from service.ws import ConnectionManager

KEY = 'issue43-fixture-key'
LOOPBACK = 'http://127.0.0.1:8800'
DOC_PATHS = ('/docs', '/docs/oauth2-redirect', '/redoc', '/openapi.json')


def api_app(key=KEY):
    config = ServiceConfig(api_key=key, operator_key='', mcp_enabled=False, cors_origins=['*'])
    with mock.patch.object(main, 'get_config', return_value=config):
        app = main.create_app()
    app.state.ws_manager = ConnectionManager()
    return app


class ExactAuthCompatibility(unittest.TestCase):
    def client(self, key=KEY):
        client = TestClient(api_app(key), base_url=LOOPBACK)
        self.addCleanup(client.close)
        return client

    def test_real_docs_schema_and_oauth_redirect_require_the_configured_key(self):
        client = self.client()
        for path in DOC_PATHS:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)
                self.assertEqual(client.get(path, headers={'X-API-Key': 'wrong'}).status_code, 401)
                self.assertEqual(client.get(path, headers={'X-API-Key': KEY}).status_code, 200)
        self.assertEqual(client.get('/health').status_code, 200)

    def test_browser_navigation_exchanges_the_key_then_docs_and_schema_use_only_the_cookie(self):
        client = self.client()
        navigation = {'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document', 'accept': 'text/html'}
        response = client.get('/docs', params={'api_key': KEY}, headers=navigation, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn('api_key', response.headers['location'])
        self.assertIn(main.APIKeyMiddleware.COOKIE, client.cookies)
        for path in DOC_PATHS:
            with self.subTest(path=path):
                self.assertEqual(client.get(path, headers=navigation).status_code, 200)
        self.assertIn('openapi', client.get('/openapi.json').json())
        client.cookies.clear()
        self.assertEqual(client.get('/openapi.json').status_code, 401)

    def test_preflight_stays_credential_free_but_the_real_request_does_not(self):
        client = self.client()
        response = client.options('/openapi.json', headers={
            'Origin': 'http://127.0.0.1:8801', 'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'x-api-key',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('x-api-key', response.headers['access-control-allow-headers'].lower())
        self.assertEqual(client.get('/openapi.json').status_code, 401)
        self.assertEqual(client.get('/openapi.json', headers={'X-API-Key': KEY}).status_code, 200)

    def test_keyless_docs_and_a_prefix_lookalike_remain_open(self):
        client = self.client('')
        client.app.add_api_route('/healthz-admin', lambda: {'fixture': True})
        for path in (*DOC_PATHS, '/health', '/healthz-admin'):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)
        self.assertEqual(client.get('/__issue43_absent__').status_code, 404)

    def test_the_real_mcp_mount_accepts_a_header_key_and_is_open_only_when_keyless(self):
        async def exercise(key):
            app = api_app(key)
            path = Path(__file__).resolve().parents[2] / 'mcp' / 'sse_server.py'
            spec = importlib.util.spec_from_file_location('issue43_mcp_fixture', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            # Only the transport's lifetime; main.lifespan would start unrelated workers.
            async with module.setup_mcp_server(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=LOOPBACK) as client:
                    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
                    accept = {'accept': 'application/json, text/event-stream'}
                    unauthenticated = await client.post('/mcp/http', json=payload, headers=accept)
                    self.assertEqual(unauthenticated.status_code, 401 if key else 200)
                    wrong = await client.post('/mcp/http', json=payload, headers={**accept, 'X-API-Key': 'wrong'})
                    self.assertEqual(wrong.status_code, 401 if key else 200)
                    authorized = await client.post('/mcp/http', json=payload, headers={**accept, 'X-API-Key': KEY})
                    self.assertEqual(authorized.status_code, 200, authorized.text)
                    tools = {tool['name'] for tool in authorized.json()['result']['tools']}
                    self.assertIn('comms_send', tools, 'control: the actual tool transport was not reached')

        for key in (KEY, ''):
            with self.subTest(keyed=bool(key)):
                asyncio.run(exercise(key))

    def test_websocket_handshake_still_checks_the_key_and_accepts_the_api_cookie(self):
        client = self.client()
        host = {'host': '127.0.0.1:8800'}
        for suffix in ('', '?api_key=wrong'):
            with self.subTest(refused=suffix), self.assertRaises(WebSocketDisconnect) as refused:
                with client.websocket_connect('ws://127.0.0.1:8800/ws' + suffix, headers=host):
                    pass
            self.assertEqual(refused.exception.code, 1008)
        client.get('/', params={'api_key': KEY}, follow_redirects=False)
        self.assertIn(main.APIKeyMiddleware.COOKIE, client.cookies)
        with client.websocket_connect('ws://127.0.0.1:8800/ws?agent_id=fixture-reader', headers=host) as socket:
            socket.send_text('fixture-ping')
            self.assertIn('fixture-reader', client.app.state.ws_manager.online_agents())


class DashboardAssetsCompatibility(unittest.TestCase):
    def test_unused_dashboard_schema_is_disabled_keyed_and_keyless(self):
        for key in (KEY, ''):
            with self.subTest(keyed=bool(key)), mock.patch.object(
                    dashboard, 'get_config', return_value=ServiceConfig(api_key=key, operator_key='')):
                client = TestClient(dashboard.app, base_url='http://127.0.0.1:8801')
                try:
                    response = client.get('/openapi.json')
                    self.assertEqual(response.status_code, 404, response.text)
                    self.assertEqual(client.get('/health').status_code, 200)
                    self.assertEqual(client.get('/assets/app.js').status_code, 200)
                    page = client.get('/', params={'api_key': key} if key else {})
                    self.assertEqual(page.status_code, 200)
                finally:
                    client.close()

    def test_signed_cookie_navigation_and_every_admitted_static_file_still_work(self):
        config = ServiceConfig(api_key=KEY, operator_key='issue43-fixture-operator')
        with mock.patch.object(dashboard, 'get_config', return_value=config):
            client = TestClient(dashboard.app, base_url='http://127.0.0.1:8801')
            try:
                self.assertEqual(client.get('/').status_code, 401)
                self.assertEqual(client.post('/login', data={'key': 'wrong'}).status_code, 401)
                signed_in = client.post('/login', data={'key': KEY}, follow_redirects=False)
                self.assertEqual(signed_in.status_code, 303)
                self.assertIn(DASHBOARD_COOKIE, client.cookies)
                self.assertNotIn(main.APIKeyMiddleware.COOKIE, client.cookies, 'these are different cookie carriers')
                page = client.get('/')
                self.assertEqual(page.status_code, 200)
                self.assertIn('issue43-fixture-operator', page.text)
                assets = sorted(file.relative_to(dashboard.APP_DIR).as_posix()
                                for file in dashboard.APP_DIR.rglob('*') if file.is_file()
                                and dashboard.AssetsOnly.is_asset(file.relative_to(dashboard.APP_DIR).as_posix()))
                self.assertIn('app.js', assets)
                self.assertIn('styles.css', assets)
                for asset in assets:
                    with self.subTest(asset=asset):
                        self.assertEqual(client.get('/assets/' + asset).status_code, 200, asset)
                self.assertEqual(client.get('/assets/index.html').status_code, 404)
                self.assertEqual(client.get('/assets/__issue43_absent__.mjs').status_code, 404)
                client.cookies.clear()
                self.assertEqual(client.get('/').status_code, 401)
                self.assertEqual(client.get('/assets/app.js').status_code, 200, 'login assets remain public')
            finally:
                client.close()
