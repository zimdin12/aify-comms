"""The served app ignores old generic-container definitions and still serves Comms."""
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from service import config, db, main, pi_resident_flip, status_push
from service.tests.served_routes import walk_routes


def test_the_comms_app_does_not_serve_generic_container_management():
    paths = {route.path for route in walk_routes(main.create_app()) if getattr(route, 'path', None)}
    assert {'/health', '/info', '/ready', '/api/v1/messages/send', '/ws'} <= paths
    assert not {path for path in paths if path.startswith(('/api/v1/containers', '/route/', '/api/v1/gpu'))}


def test_an_old_container_definition_is_ignored_on_startup(tmp_path, monkeypatch):
    (tmp_path / 'service.json').write_text(json.dumps({
        'name': 'fixture-comms', 'mcp_enabled': False,
        'containers': {'definitions': {'fixture': {'image': 'fixture-image'}}},
    }), encoding='utf-8')
    monkeypatch.setenv('CONFIG_DIR', str(tmp_path))
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.delenv('SERVICE_NAME', raising=False)
    fixture = config.ServiceConfig.load()
    fixture.api_key = ''
    fixture.mcp_enabled = False
    monkeypatch.setattr(main, 'get_config', lambda: fixture)
    monkeypatch.setattr(main, '_setup_logging', Mock())
    monkeypatch.setattr(main, '_run_dispatch_reconcile_once', AsyncMock(return_value={}))
    monkeypatch.setattr(main, '_periodic_dispatch_reconcile', AsyncMock())
    monkeypatch.setattr(main, 'get_relay', lambda: SimpleNamespace(enabled=False, start=Mock(), stop=AsyncMock()))
    monkeypatch.setattr(pi_resident_flip, '_periodic_pi_resident_flip_loop', AsyncMock())
    monkeypatch.setattr(status_push, 'periodic_status_push', AsyncMock())
    monkeypatch.setattr(db, '_db_path', tmp_path / 'data' / 'aify.db')
    # The old build must not reach a Docker daemon even on the negative control.
    retired = ModuleType('service.containers.manager')
    retired.load_container_definitions = Mock(return_value=({}, {}))
    retired.ContainerManager = Mock(side_effect=AssertionError('would create a generic manager'))
    monkeypatch.setitem(sys.modules, retired.__name__, retired)

    with TestClient(main.create_app(), base_url='http://localhost') as client:
        health = client.get('/health')
        assert health.status_code == 200
        assert client.get('/ready').json() == {'status': 'ready', 'checks': {}}
        info = client.get('/info').json()
        assert 'health' in info['endpoints']
        assert {'containers', 'groups'}.isdisjoint(info)
        assert {'containers', 'gpu', 'route'}.isdisjoint(info['endpoints'])
    assert fixture.name == 'fixture-comms', 'control: the legacy configuration file was loaded'
    retired.load_container_definitions.assert_not_called()
    retired.ContainerManager.assert_not_called()
