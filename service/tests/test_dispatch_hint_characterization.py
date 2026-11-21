"""Pin the operator-visible hints before moving their selection to delivery facts."""
import pytest

from service.api_core.dispatch_hint import _dispatch_fix_hint
from service.tests.test_dispatch_fix_hint import RESIDENT_BRIDGE_REASON, row


@pytest.mark.parametrize('runtime,fix,commands', [
    ('codex', 'Restart Codex, then re-register from the exact live Codex session you want to wake.',
     ['comms_register(agentId="fixture", role="tester", runtime="codex")']),
    ('claude-code', 'Start Claude with claude-aify, then re-register from that exact live Claude session.',
     ['claude-aify', 'comms_register(agentId="fixture", role="tester", runtime="claude-code")']),
    ('opencode', 'Resident OpenCode sessions are presence-only. Spawn a persistent OpenCode agent from a connected dashboard environment.',
     ['comms_envs()', 'comms_spawn(from="<your-agent>", agentId="fixture-teammate", role="tester", runtime="opencode")']),
    ('pi', 'Resident Oh My Pi sessions are presence-only. Spawn a persistent Pi agent from a connected dashboard environment.',
     ['comms_envs()', 'comms_spawn(from="<your-agent>", agentId="fixture-teammate", role="tester", runtime="pi")']),
])
def test_resident_hint_keeps_the_exact_fix_and_command_order(runtime, fix, commands):
    agent = row(runtime=runtime, role='tester')
    result = _dispatch_fix_hint('fixture', agent, 'offline')
    assert result['fix'] == fix
    assert result['suggestedCommands'] == [*commands, 'comms_agent_info(agentId="fixture")']
    bridge = _dispatch_fix_hint('fixture', agent, RESIDENT_BRIDGE_REASON)
    assert bridge['fix'].startswith('Restart the visible resident wrapper')
    assert bridge['suggestedCommands'] == [
        f'comms_register(agentId="fixture", role="tester", runtime="{runtime}")',
        'comms_agent_info(agentId="fixture")',
    ]
