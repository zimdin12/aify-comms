"""Execute the actual assets tail. This pins caller order/status/argv, not native policy or CLI health.

The native Linux chain proof separately uses real collaborators. Here earlier writers and the Node
entrypoint are recording ports so a refusal cannot damage the host or accidentally install a plugin.
The old installer completes without ever calling that port, which must be the retained RED.
"""
import os
import subprocess
from pathlib import Path

import pytest

from service.tests._launchers import bash

REPO = Path(__file__).resolve().parents[2]


def run_tail(tmp_path, *, windows=False, mode="assets-only", client="claude", status=0, fail_earlier=False):
    source = (REPO / "install.sh").read_text(encoding="utf-8")
    tail = source[source.index('echo "[2/4] Installing agent guidance..."'):]
    scripts = tmp_path / "source" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("hook-installed.sh", "install-quickstart.sh"):
        (scripts / name).write_bytes((REPO / "scripts" / name).read_bytes())
    (scripts / "components.sh").write_text('echo components >> "$TRACE"\n', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    bridge = tmp_path / "bridge with a space"
    bridge.mkdir(exist_ok=True)
    (bridge / "usage-preflight.js").write_text("// recording port only\n", encoding="utf-8")
    trace = tmp_path / "trace"
    argv = tmp_path / "argv"
    trace.unlink(missing_ok=True)
    argv.unlink(missing_ok=True)
    functions = ["copy_claude_assets", "copy_codex_assets", "register_stdio_server",
                 "register_claude_channel_server", "migrate_codex_hooks_key", "install_bridge_launcher",
                 "install_claude_wrapper", "install_claude_turn_end_hook", "install_codex_turn_hooks"]
    stubs = "\n".join(f'{name}() {{ echo {name} >> "$TRACE"; }}' for name in functions)
    script = tmp_path / "tail.sh"
    script.write_text(
        'set -euo pipefail\n' + stubs + '\n'
        'install_claude_turn_start_hook() { echo install_claude_turn_start_hook >> "$TRACE"; return "$EARLIER_STATUS"; }\n'
        'install_windows_cmd_shim() { echo doctor-shim >> "$TRACE"; }\n'
        'is_git_bash_windows() { [ "$WINDOWS" = 1 ]; }\n'
        'path_for_node() { printf "native:%s" "$1"; }\n'
        'node() {\n'
        '  if [[ "$1" = */node_modules/aify-wrapper/lib/channel-install.mjs ]]; then\n'
        '    echo channel >> "$TRACE"; printf "%s\\0" "$@" > "$ARGV_FILE"\n'
        '    [ "$CHANNEL_STATUS" = 0 ] || echo "CONTROLLED HELPER REFUSAL" >&2\n'
        '    return "$CHANNEL_STATUS"\n'
        '  fi\n'
        '  echo usage-preflight >> "$TRACE"\n'
        '}\n' + tail, encoding="utf-8",
    )
    env = {**os.environ, "HOME": home.as_posix(), "USERPROFILE": str(home),
           "SCRIPT_DIR": scripts.parent.as_posix(), "AIFY_BRIDGE_DIR": bridge.as_posix(),
           "CLIENT": client, "MODE": mode, "SERVER_URL": "http://fixture.invalid:8800",
           "WITH_HOOK": "false", "WINDOWS": "1" if windows else "0", "TRACE": trace.as_posix(),
           "ARGV_FILE": argv.as_posix(), "CHANNEL_STATUS": str(status), "EARLIER_STATUS": "73" if fail_earlier else "0"}
    result = subprocess.run([bash(), script.as_posix()], env=env, capture_output=True, text=True, timeout=30)
    return result, trace.read_text().splitlines(), argv.read_bytes().split(b"\0")[:-1] if argv.exists() else [], bridge, home


@pytest.mark.parametrize("windows", [False, True])
def test_policy_refusal_is_after_the_last_writer_and_suppresses_completion(tmp_path, windows):
    result, calls, args, bridge, home = run_tail(tmp_path, windows=windows, status=78)
    assert "channel" in calls, "the Claude assets path never invoked the pinned channel helper"
    assert result.returncode == 78, "the channel helper refusal was not propagated"
    assert calls[-3:] == ["usage-preflight", "doctor-shim", "channel"], "the channel step ran before remaining assets completed"
    assert "CONTROLLED HELPER REFUSAL" in result.stderr
    assert "Installation complete" not in result.stdout
    assert "components" not in calls
    assert (home / ".local" / "bin" / "aify-doctor").is_file(), "refusal happened before doctor installation"
    assert [value.decode() for value in args] == [
        f"native:{bridge.as_posix()}/node_modules/aify-wrapper/lib/channel-install.mjs",
        "install", "--bridge-dir", f"native:{bridge.as_posix()}",
    ]
    # Same completed assets can be retried; this is caller continuation, not a claim of real plugin idempotence.
    good, calls, _, _, _ = run_tail(tmp_path, windows=windows)
    assert good.returncode == 0, good.stderr
    assert calls[-2:] == ["channel", "components"]
    assert "Installation complete" in good.stdout and "Quick start:" in good.stdout


def test_an_earlier_assets_failure_never_reaches_channel_work(tmp_path):
    result, calls, args, _, _ = run_tail(tmp_path, fail_earlier=True)
    assert result.returncode == 73
    assert calls[-1] == "install_claude_turn_start_hook"
    assert "channel" not in calls and args == []
    assert "Installation complete" not in result.stdout


@pytest.mark.parametrize("client,mode", [("codex", "assets-only"), ("claude", "full")])
def test_other_client_or_standalone_path_does_not_install_the_channel(tmp_path, client, mode):
    result, calls, args, _, _ = run_tail(tmp_path, client=client, mode=mode)
    assert result.returncode == 0, result.stderr
    assert "channel" not in calls and args == []
    assert "Installation complete" in result.stdout
