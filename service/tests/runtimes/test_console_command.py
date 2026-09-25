"""Per-adapter console launch, as the one string the service shows for it.

The service flattens `console_argv` with a single space (`_default_console_command` in
api_core/capabilities.py), so these pin that string per runtime.
"""


def _console(adapter, **opts):
    return " ".join(adapter.console_argv(**opts))


def test_claude_console_command_interactive():
    from service.runtimes.claude import ClaudeAdapter
    cmd = _console(ClaudeAdapter(), agent_id="a", handle="h", interactive=True)
    assert cmd == "claude-aify --aify-agent a --resume h"


def test_claude_console_command_managed_with_handle():
    from service.runtimes.claude import ClaudeAdapter
    cmd = _console(ClaudeAdapter(), agent_id="a", handle="h", interactive=False)
    assert cmd == "claude-aify --aify-agent a --auto --resume h"


def test_claude_console_command_managed_no_handle():
    from service.runtimes.claude import ClaudeAdapter
    cmd = _console(ClaudeAdapter(), agent_id="a", handle="", interactive=False)
    assert cmd == "claude-aify --aify-agent a --auto"


def test_codex_console_command_with_handle():
    from service.runtimes.codex import CodexAdapter
    a = CodexAdapter()
    assert _console(a, agent_id="a", handle="h", interactive=True) == "codex-aify --aify-agent a --resume h"
    assert _console(a, agent_id="a", handle="h", interactive=False) == "codex-aify --aify-agent a --resume h"


def test_codex_console_command_no_handle():
    from service.runtimes.codex import CodexAdapter
    cmd = _console(CodexAdapter(), agent_id="a", handle="", interactive=False)
    assert cmd == "codex-aify --aify-agent a"


def test_hermes_console_command_with_handle():
    from service.runtimes.hermes import HermesAdapter
    cmd = _console(HermesAdapter(), agent_id="a", handle="h", interactive=False)
    assert cmd == "hermes-aify --aify-agent a --resume h"


def test_pi_console_command_interactive_no_resume():
    from service.runtimes.pi import PiAdapter
    cmd = _console(PiAdapter(), agent_id="a", handle="h", interactive=True)
    assert cmd == "pi-aify --aify-agent a"


def test_pi_console_command_managed_with_handle():
    from service.runtimes.pi import PiAdapter
    cmd = _console(PiAdapter(), agent_id="a", handle="h", interactive=False)
    assert cmd == "pi-aify --aify-agent a --resume h"


def test_opencode_console_command():
    from service.runtimes.opencode import OpencodeAdapter
    cmd = _console(OpencodeAdapter(), agent_id="a", handle="h", interactive=False)
    assert cmd == "opencode"
