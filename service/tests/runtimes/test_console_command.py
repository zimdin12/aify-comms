"""Per-adapter wrapper_name + console_command assertions.

The console_command outputs must match what _default_console_command produced
in Plan 1/2 so the regression suite (test_console_command_resume.py) passes
unchanged after Plan 3's migration.
"""


def test_claude_wrapper_name():
    from service.runtimes.claude import ClaudeAdapter
    assert ClaudeAdapter().wrapper_name == "claude-aify"


def test_claude_console_command_interactive():
    from service.runtimes.claude import ClaudeAdapter
    cmd = ClaudeAdapter().console_command(agent_id="a", handle="h", interactive=True)
    assert cmd == "claude-aify --aify-agent a --resume h"


def test_claude_console_command_managed_with_handle():
    from service.runtimes.claude import ClaudeAdapter
    cmd = ClaudeAdapter().console_command(agent_id="a", handle="h", interactive=False)
    assert cmd == "claude-aify --aify-agent a --auto --resume h"


def test_claude_console_command_managed_no_handle():
    from service.runtimes.claude import ClaudeAdapter
    cmd = ClaudeAdapter().console_command(agent_id="a", handle="", interactive=False)
    assert cmd == "claude-aify --aify-agent a --auto"


def test_codex_wrapper_name():
    from service.runtimes.codex import CodexAdapter
    assert CodexAdapter().wrapper_name == "codex-aify"


def test_codex_console_command_with_handle():
    from service.runtimes.codex import CodexAdapter
    a = CodexAdapter()
    assert a.console_command(agent_id="a", handle="h", interactive=True) == "codex-aify --aify-agent a --resume h"
    assert a.console_command(agent_id="a", handle="h", interactive=False) == "codex-aify --aify-agent a --resume h"


def test_codex_console_command_no_handle():
    from service.runtimes.codex import CodexAdapter
    cmd = CodexAdapter().console_command(agent_id="a", handle="", interactive=False)
    assert cmd == "codex-aify --aify-agent a"


def test_hermes_wrapper_name():
    from service.runtimes.hermes import HermesAdapter
    assert HermesAdapter().wrapper_name == "hermes-aify"


def test_hermes_console_command_with_handle():
    from service.runtimes.hermes import HermesAdapter
    cmd = HermesAdapter().console_command(agent_id="a", handle="h", interactive=False)
    assert cmd == "hermes-aify --aify-agent a --resume h"


def test_pi_wrapper_name():
    from service.runtimes.pi import PiAdapter
    assert PiAdapter().wrapper_name == "pi-aify"


def test_pi_console_command_interactive_no_resume():
    from service.runtimes.pi import PiAdapter
    cmd = PiAdapter().console_command(agent_id="a", handle="h", interactive=True)
    assert cmd == "pi-aify --aify-agent a"


def test_pi_console_command_managed_with_handle():
    from service.runtimes.pi import PiAdapter
    cmd = PiAdapter().console_command(agent_id="a", handle="h", interactive=False)
    assert cmd == "pi-aify --aify-agent a --resume h"


def test_opencode_wrapper_name():
    from service.runtimes.opencode import OpencodeAdapter
    assert OpencodeAdapter().wrapper_name == "opencode"


def test_opencode_console_command():
    from service.runtimes.opencode import OpencodeAdapter
    cmd = OpencodeAdapter().console_command(agent_id="a", handle="h", interactive=False)
    assert cmd == "opencode"
