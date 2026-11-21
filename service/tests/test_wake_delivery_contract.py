"""Characterize the existing delivery decisions before changing how they are stored.

The expected outputs are fixtures, not a read of the runtime registry or channel sets.
No live agent, bridge, environment, database or spawn is used.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.api_core import channel_coldstart, channel_delivery, send_preflight
from service.api_core.execution_mode import _agent_execution_mode
from service.api_core.liveness import _agent_wake_mode


NO_RESIDENT_RUN = (
    'agent capabilities do not include "resident-run" — re-register from a live '
    "aify-wrapper session with the runtime's wake handle."
)
NO_MANAGED_RUN = 'agent capabilities do not include "managed-run"'
CLAUDE_NO_CHANNEL = (
    'agent "target" is a resident Claude session without channelEnabled. '
    'Restart with `claude-aify` (which sets AIFY_CHANNELS_ENABLED=1) and re-register from that session.'
)
CODEX_NO_APP_SERVER = (
    'agent "target" is a resident Codex session without a live appServerUrl. '
    'Restart with `codex-aify` and re-register passing `appServerUrl="$AIFY_CODEX_APP_SERVER_URL"` '
    'and `sessionHandle="$CODEX_THREAD_ID"`.'
)
CODEX_NO_HANDLE = (
    'agent "target" is a resident Codex session without a bound session handle. '
    'Re-register that live session or provide sessionHandle explicitly.'
)
HERMES_NO_GATEWAY = (
    'agent "target" is a resident Hermes session without a live gatewayUrl. '
    'Restart with the updated `hermes-aify` (which exports AIFY_HERMES_GATEWAY_URL) and re-register '
    '— the bridge auto-detects the gateway from env. Verify the wrapper is current with '
    '`head -30 ~/.local/bin/hermes-aify | grep pick_port` (function exists in the new wrapper).'
)
PI_PRESENCE = (
    'agent "target" is a Pi/OMP presence session, not a triggerable resident target. '
    'Switch to managed or spawn a managed Pi agent so delivery uses the bridge-owned OMP RPC worker.'
)
OPENCODE_PRESENCE = (
    'agent "target" is an OpenCode presence session, not a triggerable resident target. '
    'Create an environment-managed OpenCode agent; resident OpenCode delivery is disabled '
    'until a real multi-client surface is wired.'
)
DISABLED = 'launch mode is disabled'
WRAPPER_OFF = {'managed_via_wrapper': []}


def agent(runtime, mode, caps, config=None, handle=None, launch='detached'):
    return {'id': 'target', 'runtime': runtime, 'session_mode': mode, 'launch_mode': launch,
            'session_handle': handle, 'capabilities': json.dumps(caps) if caps is not None else None,
            'runtime_config': json.dumps(config) if config is not None else None,
            'runtime_state': '{}', 'role': 'coder'}


CLAIMS = [
    pytest.param(agent('claude-code', 'resident', [], {'channelEnabled': True}), 'claude-live', ('resident', None), id='claude-channel-grants-cap'),
    pytest.param(agent('claude-code', 'resident', ['resident-run']), 'claude-needs-channel', (None, CLAUDE_NO_CHANNEL), id='claude-cap-without-channel'),
    pytest.param(agent('claude-code', 'resident', [], {'channelEnabled': 'true'}), 'claude-needs-channel', (None, NO_RESIDENT_RUN), id='claude-resident-flag-not-boolean'),
    pytest.param(agent('claude-code', None, None, {'channelEnabled': True}, launch=None), 'claude-live', ('resident', None), id='missing-mode-and-launch-use-defaults'),
    pytest.param(agent('claude-code', 'managed', ['managed-run']), 'managed-worker', ('channel', None), id='claude-managed-channel-without-flag'),
    pytest.param(agent('claude-code', 'managed', ['resume'], {'channelEnabled': True}), 'message-only', ('channel', None), id='claude-managed-channel-without-cap'),
    pytest.param(agent('claude-code', 'managed', ['resume']), 'message-only', (None, NO_MANAGED_RUN), id='claude-managed-cap-and-flag-absent'),
    pytest.param(agent('claude-code', 'managed', ['managed-run'], launch=' None '), 'disabled', (None, DISABLED), id='disabled-outranks-managed-cap'),
    pytest.param(agent('codex', 'resident', ['resident-run'], {'appServerUrl': 'wss://fixture.invalid'}, 'thread'), 'codex-live', ('resident', None), id='codex-app-server-and-handle'),
    pytest.param(agent('codex', 'resident', ['resident-run'], {'appServerUrl': 'http://fixture.invalid'}, 'thread'), 'codex-thread-resume', ('resident', None), id='codex-non-ws-app-server-falls-back'),
    pytest.param(agent('codex', 'resident', ['resident-run'], {'appServerUrl': 'ws://fixture.invalid'}, '  '), 'codex-missing-handle', (None, CODEX_NO_HANDLE), id='codex-app-server-without-handle'),
    pytest.param(agent('codex', 'resident', [], {'appServerUrl': 'ws://fixture.invalid'}, 'thread'), 'message-only', (None, NO_RESIDENT_RUN), id='codex-app-server-without-cap'),
    pytest.param(agent('codex', 'resident', [], handle='thread'), 'message-only', (None, CODEX_NO_APP_SERVER), id='codex-handle-without-cap-or-config'),
    pytest.param(agent('codex', 'resident', [], launch='none'), 'disabled', (None, CODEX_NO_APP_SERVER), id='resident-refusal-precedes-disabled'),
    pytest.param(agent('codex', 'resident', ['resident-run'], handle='thread', launch='none'), 'disabled', (None, DISABLED), id='resident-valid-but-disabled'),
    pytest.param(agent('codex', 'managed', ['managed-run']), 'managed-worker', ('managed', None), id='codex-managed-no-handle'),
    pytest.param(agent('codex', 'managed', None), 'message-only', ('managed', None), id='codex-managed-legacy-empty-caps'),
    pytest.param(agent('codex', 'managed', ['resident-run']), 'message-only', (None, NO_MANAGED_RUN), id='codex-managed-wrong-cap'),
    pytest.param(agent('codex', 'unknown', ['managed-run']), 'codex-missing-handle', (None, CODEX_NO_APP_SERVER), id='unknown-session-mode-is-resident'),
    pytest.param(agent('hermes', 'resident', [], {'gatewayUrl': 'wss://fixture.invalid'}), 'hermes-live', ('resident', None), id='hermes-gateway-without-handle-or-cap'),
    pytest.param(agent('hermes', 'resident', ['resident-run'], {'gatewayUrl': 'https://fixture.invalid'}, 'native'), 'hermes-missing-handle', (None, HERMES_NO_GATEWAY), id='hermes-native-handle-does-not-replace-gateway'),
    pytest.param(agent('hermes', 'resident', [], {'gatewayUrl': 'WSS://fixture.invalid'}), 'hermes-live', (None, 'agent "target" is a resident Hermes session without a bound session handle. Restart with hermes-aify and a resumable session handle, or create an environment-managed session.'), id='hermes-uppercase-gateway-without-handle-split'),
    pytest.param(agent('hermes', 'managed', None), 'managed-worker', ('managed', None), id='hermes-managed-flag-absent'),
    pytest.param(agent('hermes', 'managed', [], {'channelEnabled': True}), 'managed-worker', ('channel', None), id='hermes-managed-channel-flag'),
    pytest.param(agent('pi', 'resident', ['resident-run'], handle='native'), 'presence-only', (None, PI_PRESENCE), id='pi-presence-even-with-cap-and-handle'),
    pytest.param(agent('pi', 'resident', None), 'presence-only', (None, PI_PRESENCE), id='pi-presence-even-without-handle'),
    pytest.param(agent('pi', 'managed', None), 'managed-worker', ('managed', None), id='pi-managed-backfills-caps'),
    pytest.param(agent('opencode', 'resident', ['resident-run'], handle='native'), 'presence-only', (None, OPENCODE_PRESENCE), id='opencode-presence-even-with-cap-and-handle'),
    pytest.param(agent('opencode', 'resident', None), 'presence-only', (None, OPENCODE_PRESENCE), id='opencode-presence-even-without-handle'),
    pytest.param(agent('opencode', 'managed', ['managed-run']), 'managed-worker', ('managed', None), id='opencode-legacy-managed-dispatch'),
    pytest.param(agent('opencode', 'managed', ['resume']), 'message-only', (None, NO_MANAGED_RUN), id='opencode-managed-wrong-cap'),
    pytest.param(agent(None, None, None), 'message-only', (None, 'runtime "generic" does not support active dispatch'), id='missing-runtime-is-message-only'),
]


def db_with_agent(row):
    return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=AsyncMock(return_value=row))))


def preflight(monkeypatch, *, fresh=True, status='online', unavailable=None):
    for name, value in {
        '_load_settings': WRAPPER_OFF,
        '_resident_bridge_is_fresh': fresh,
        '_get_dispatch_state_for_agent': {},
        '_compute_agent_status': status,
        '_managed_environment_unavailable_reason': unavailable,
    }.items():
        monkeypatch.setattr(send_preflight, name, AsyncMock(return_value=value))


@pytest.mark.parametrize('row,wake,execution', CLAIMS)
def test_wake_execution_and_recipient_availability(monkeypatch, row, wake, execution):
    assert _agent_wake_mode(row) == wake
    assert _agent_execution_mode(row, settings=WRAPPER_OFF) == execution
    preflight(monkeypatch)
    available, refused = asyncio.run(send_preflight._preflight_live_send_recipients(db_with_agent(row), ['target']))
    assert available == ([('target', execution[0])] if execution[0] else [])
    assert [(item['targetAgentId'], item['recipientStatus'], item['reason']) for item in refused] == (
        [('target', 'online', execution[1])] if execution[1] else []
    )


@pytest.mark.parametrize('fresh,status,unavailable,recipient_status,reason', [
    (False, 'online', None, 'offline', 'resident bridge heartbeat is gone; restart the resident wrapper or switch to managed'),
    (True, 'misconfigured', None, 'misconfigured', 'agent status is "misconfigured"'),
    (True, 'online', 'owning environment is unavailable', 'offline', 'owning environment is unavailable'),
])
def test_recipient_can_be_unavailable_even_with_a_valid_wake_path(monkeypatch, fresh, status, unavailable, recipient_status, reason):
    preflight(monkeypatch, fresh=fresh, status=status, unavailable=unavailable)
    row = agent('claude-code', 'resident', ['resident-run'], {'channelEnabled': True})
    available, refused = asyncio.run(send_preflight._preflight_live_send_recipients(db_with_agent(row), ['target']))
    assert available == []
    assert [(item['recipientStatus'], item['reason']) for item in refused] == [(recipient_status, reason)]


def test_execution_refuses_a_different_requested_runtime_before_other_checks():
    assert _agent_execution_mode(agent('codex', 'resident', []), requested_runtime='claude') == (
        None, 'requested runtime "claude" does not match registered runtime "codex"'
    )


@pytest.mark.parametrize('runtime,coldstart,sidecar_live,sidecar_dead,wrapper', [
    ('claude-code', True, (True, False, False), (False, True, False), 'channel'),
    ('codex', True, (False, False, False), (False, False, False), 'channel'),
    ('hermes', True, (True, False, False), (False, True, False), 'channel'),
    ('pi', False, (False, False, False), (False, False, False), 'managed'),
    ('opencode', False, (False, False, False), (False, False, False), 'managed'),
])
def test_channel_consumers_keep_coldstart_sidecar_and_wrapper_paths_distinct(
    monkeypatch, runtime, coldstart, sidecar_live, sidecar_dead, wrapper,
):
    row = agent(runtime, 'managed', ['managed-run'])
    assert _agent_execution_mode(row, settings={'managed_via_wrapper': True}) == (wrapper, None)

    db = db_with_agent(row)
    queue_spawn = AsyncMock()
    monkeypatch.setattr(channel_coldstart, '_load_settings', AsyncMock(return_value={}))
    monkeypatch.setattr(channel_coldstart, '_has_live_managed_wrapper_child', AsyncMock(return_value=False))
    monkeypatch.setattr(channel_coldstart, '_coldstart_spawn_request_for_dispatch', queue_spawn)
    asyncio.run(channel_coldstart._coldstart_cold_channel_members(db, SimpleNamespace(from_agent='sender'), [('target', 'channel')]))
    if coldstart:
        queue_spawn.assert_awaited_once_with(db, 'target', runtime=runtime, settings={}, requested_by='sender')
    else:
        queue_spawn.assert_not_awaited()

    monkeypatch.setattr(channel_delivery, '_has_live_terminal_session', AsyncMock(return_value=True))
    monkeypatch.setattr(channel_delivery, 'wrapper_child_state', AsyncMock(return_value=False))
    monkeypatch.setattr(channel_delivery, 'channel_sidecar_state', AsyncMock(side_effect=[True, False]))
    for expected in (sidecar_live, sidecar_dead):
        result = asyncio.run(channel_delivery._worker_liveness_for(db, row, agent_session_mode='managed', live_session=False))
        assert tuple(result) == expected
