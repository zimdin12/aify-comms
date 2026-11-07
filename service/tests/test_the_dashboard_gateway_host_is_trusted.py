"""#27: the exact Docker gateway Host reaches the real keyed API; lookalikes do not.

ASGI fixtures only: no service lifespan, server, Docker DNS or native platform qualification.
"""
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from service import main
from service.config import ServiceConfig
from service.tests._base import FastApiTestCase
from service.ws import ConnectionManager

KEY = 'issue27-fixture-service-key'
AGENT = 'issue27-fixture-reader'
GATEWAY = 'host.docker.internal:8800'
ATTACKERS = ('evil.example:8800', 'host.docker.internal.evil.example:8800',
             'evil-host.docker.internal:8800', 'host.docker.internal-evil:8800')


class TheDashboardGatewayHostIsTrusted(FastApiTestCase):
    def setUp(self):
        super().setUp()
        seeded = self.client.post('/api/v1/agents', json={
            'agentId': AGENT, 'role': 'tester', 'runtime': 'claude-code', 'sessionMode': 'resident',
        })
        self.assertEqual(seeded.status_code, 200, seeded.text)

    def _request_client(self, host, *, key=KEY, trusted_hosts=(), https_sites=''):
        config = ServiceConfig(api_key=key, operator_key='', mcp_enabled=False, cors_origins=['*'],
                               trusted_hosts=list(trusted_hosts), https_sites=https_sites,
                               data_dir=str(self._db_path.parent), config_dir=str(self._db_path.parent))
        with mock.patch.object(main, 'get_config', return_value=config):
            app = main.create_app()
        app.state.config = config
        app.state.testing = True
        app.state.ws_manager = ConnectionManager()
        # This Starlette TestClient splits IPv6 netloc on the first colon before reaching ASGI.
        # Drive that Host field explicitly; this is a guard control, not IPv6 socket qualification.
        client = (TestClient(app, base_url='http://127.0.0.1:8800', headers={'host': host})
                  if host.startswith('[') else TestClient(app, base_url=f'http://{host}'))
        self.addCleanup(client.close)
        return client

    def _assert_directory(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(AGENT, response.json()['agents'], 'control: this did not reach the real directory handler')

    def test_the_exact_gateway_host_reaches_the_keyed_directory_but_still_needs_the_key(self):
        self._assert_directory(self._request_client('127.0.0.1:8800').get(
            '/api/v1/agents', headers={'X-API-Key': KEY}))
        for host in (GATEWAY, 'HOST.DOCKER.INTERNAL:18800'):
            with self.subTest(host=host, credential='valid'):
                self._assert_directory(self._request_client(host).get('/api/v1/agents', headers={'X-API-Key': KEY}))
            for headers in ({}, {'X-API-Key': 'wrong'}):
                with self.subTest(host=host, credential='missing' if not headers else 'wrong'):
                    response = self._request_client(host).get('/api/v1/agents', headers=headers)
                    self.assertEqual(response.status_code, 401, response.text)

    def test_keyless_gateway_access_does_not_make_attacker_hosts_trusted(self):
        for configured_key in (KEY, ''):
            with self.subTest(keyed=bool(configured_key), host=GATEWAY):
                self._assert_directory(self._request_client(GATEWAY, key=configured_key).get(
                    '/api/v1/agents', headers={'X-API-Key': KEY}))
            for host in ATTACKERS:
                with self.subTest(keyed=bool(configured_key), refused=host):
                    response = self._request_client(host, key=configured_key).get(
                        '/api/v1/agents', headers={'X-API-Key': KEY})
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertIn('TRUSTED_HOSTS', response.json()['error'])

    def test_existing_client_addresses_and_explicit_network_aliases_still_work(self):
        cases = (
            ('127.0.0.1:8800', (), ''), ('localhost:8800', (), ''), ('[::1]:8800', (), ''),
            ('10.20.30.40:8800', (), ''), ('operator.fixture:8800', ('operator.fixture',), ''),
            ('https.fixture:8443', (), 'https.fixture:8443'), ('service:8800', ('service',), ''),
        )
        for host, trusted, sites in cases:
            with self.subTest(host=host):
                self._assert_directory(self._request_client(host, trusted_hosts=trusted, https_sites=sites).get(
                    '/api/v1/agents', headers={'X-API-Key': KEY}))
        # Being an arbitrary Docker-looking alias is not a new default grant.
        self.assertEqual(self._request_client('service:8800').get(
            '/api/v1/agents', headers={'X-API-Key': KEY}).status_code, 403)

    def test_trusting_the_gateway_does_not_accept_a_hostile_browser_origin(self):
        for configured_key in (KEY, ''):
            with self.subTest(keyed=bool(configured_key), origin='same-host'):
                self._assert_directory(self._request_client(GATEWAY, key=configured_key).get('/api/v1/agents', headers={
                    'X-API-Key': KEY, 'Origin': 'http://host.docker.internal:9700',
                }))
            with self.subTest(keyed=bool(configured_key), origin='attacker'):
                response = self._request_client(GATEWAY, key=configured_key).get('/api/v1/agents', headers={
                    'X-API-Key': KEY, 'Origin': 'https://evil.example',
                })
                self.assertEqual(response.status_code, 403, response.text)
                self.assertIn('CORS_ORIGINS', response.json()['error'])

    def test_websocket_uses_the_same_exact_host_policy_and_retains_its_own_key_check(self):
        for host in ('127.0.0.1:8800', GATEWAY):
            client = self._request_client(host)
            with self.subTest(host=host, admitted=True):
                try:
                    with client.websocket_connect(f'ws://{host}/ws?agent_id={AGENT}&api_key={KEY}') as socket:
                        socket.send_text('fixture-ping')
                        self.assertIn(AGENT, client.app.state.ws_manager.online_agents())
                except WebSocketDisconnect as error:
                    self.fail(f'valid Host {host} and service key were refused with WebSocket code {error.code}')
            for query in ('', '?api_key=wrong'):
                with self.subTest(host=host, refused=query):
                    with self.assertRaises(WebSocketDisconnect) as refused:
                        with client.websocket_connect(f'ws://{host}/ws{query}'):
                            pass
                    self.assertEqual(refused.exception.code, 1008)
        for host in ATTACKERS:
            with self.subTest(refused=host):
                with self.assertRaises(WebSocketDisconnect) as refused:
                    with self._request_client(host).websocket_connect(f'ws://{host}/ws?api_key={KEY}'):
                        pass
                self.assertEqual(refused.exception.code, 1008)
