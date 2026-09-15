"""The resident hook writers in install.sh, executed against a temp HOME and a stub service.

The hooks used to curl the turn routes with no X-API-Key, so on a service with API_KEY set every one
was answered 401 and discarded by `|| true`. The static greps in test_install_claude_turn_hooks.py
proved a curl line was WRITTEN; nothing proved a request would be ACCEPTED. So this lifts the writers
verbatim, runs them over a HOME that already holds the old curl entries (the state every existing
install is in), runs them twice, and then executes the commands they wrote against a stub that
records the path, body and key.

Lifted, not restated: a copy here would pass while install.sh did something else.
"""

import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
BRIDGE_DIR = REPO / "mcp" / "stdio"

LIFTED = (
    "is_git_bash_windows",
    "path_for_node",
    "shell_quote",
    "agent_state_hook_command",
    "install_claude_turn_start_hook",
    "install_claude_turn_end_hook",
    "install_codex_turn_hooks",
    "install_hermes_turn_hooks",
)

LEGACY_CURL = (
    'if [ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then curl -sS --max-time 2 -X POST '
    '"${AIFY_COMMS_URL%/}/api/v1/agents/${AIFY_AGENT_ID}/ROUTE" >/dev/null 2>&1 || true; fi'
)
LEGACY_GATE = (
    'if [ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then if command -v node >/dev/null 2>&1 '
    '&& [ -f "/old/bridge/claude-stop-gate.js" ]; then node "/old/bridge/claude-stop-gate.js" 2>/dev/null || true; '
    'else curl -sS --max-time 2 -X POST "${AIFY_COMMS_URL%/}/api/v1/agents/${AIFY_AGENT_ID}/turn-end" '
    '>/dev/null 2>&1 || true; fi; fi'
)
USER_HOOK = {"hooks": [{"type": "command", "command": "echo mine"}]}


def _bash() -> str:
    found = shutil.which("bash")
    if not found or not shutil.which("node"):
        pytest.skip("bash and node are required")
    return found


def _posix(p) -> str:
    return str(p).replace("\\", "/")


@functools.lru_cache(maxsize=1)
def _git_bash() -> bool:
    """Whether the bash these writers run under is Git Bash, where install.sh's path_for_node is `cygpath -w`."""
    result = subprocess.run([_bash(), "-c", "uname -s"], capture_output=True, text=True)
    return result.stdout.strip().upper().startswith(("MINGW", "MSYS", "CYGWIN"))


def _native(p) -> str:
    """A path in the form the installer writes into a runtime's config on this host: what hermes and codex
    themselves hold, so it is the form an operator's approval and codex's trust are recorded against."""
    return _posix(p).replace("/", "\\") if _git_bash() else _posix(p)


def _other_separator(p) -> str:
    return _posix(p) if _git_bash() else _posix(p).replace("/", "\\")


def _codex_hash(event: str, command: str) -> str:
    """Codex's trusted_hash for a plain command hook: sha256 over sorted compact JSON of its identity."""
    identity = {"event_name": event, "hooks": [{"async": False, "command": command, "timeout": 3, "type": "command"}]}
    return "sha256:" + hashlib.sha256(json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def _lifted() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    blocks = []
    for name in LIFTED:
        match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
        # Helpers may come and go; a writer that vanished is the only extraction failure worth naming,
        # since a missing helper fails the writers that call it below.
        assert match or not name.startswith("install_"), f"install.sh no longer defines {name}; repoint this test"
        if match:
            blocks.append(match.group(0))
    return "\n".join(blocks)


def _legacy(route: str) -> dict:
    return {"hooks": [{"type": "command", "command": LEGACY_CURL.replace("ROUTE", route), "timeout": 3}]}


def _seed(home: Path) -> None:
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
        "UserPromptSubmit": [_legacy("turn-start")],
        "PostToolUse": [USER_HOOK, _legacy("turn-start")],
        "Stop": [{"hooks": [{"type": "command", "command": LEGACY_GATE, "timeout": 3}]}],
        "SessionStart": [{"matcher": "compact", "hooks": [{"type": "command", "command": LEGACY_GATE, "timeout": 3}]}],
        "PermissionRequest": [USER_HOOK],
    }}), encoding="utf-8")
    (home / ".codex").mkdir()
    (home / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {
        "UserPromptSubmit": [_legacy("turn-start")],
        "Stop": [_legacy("turn-end"), USER_HOOK],
    }}), encoding="utf-8")
    hooks = home / ".hermes" / "agent-hooks"
    hooks.mkdir(parents=True)
    start = hooks / "aify-turn-start.sh"
    start.write_text("#!/usr/bin/env bash\n" + LEGACY_CURL.replace("ROUTE", "turn-start") + "\n", encoding="utf-8")
    (home / ".hermes" / "config.yaml").write_text(
        "model: x\n"
        "hooks:\n"
        "  pre_llm_call:\n"
        '    - matcher: ".*"\n'
        f"      command: {json.dumps('bash ' + json.dumps(_native(start)))}\n"
        "      timeout: 3\n"
        "    - command: echo mine\n"
        "  post_tool_call:\n"
        '    - matcher: ".*"\n'
        f'      command: bash "{_posix(hooks)}/aify-notify.sh"\n'
        "      timeout: 3\n",
        encoding="utf-8",
    )
    hooks_json = _native(home / ".codex" / "hooks.json")
    (home / ".codex" / "config.toml").write_text(
        "[features]\n"
        "hooks = true\n"
        "\n"
        f"[hooks.state.'{hooks_json}:stop:0:0']\n"
        'trusted_hash = "sha256:the-old-curl-hook"\n'
        "\n"
        f"[hooks.state.'{hooks_json}:stop:1:0']\n"
        'trusted_hash = "sha256:the-operators-own-hook"\n'
        "\n"
        "[shell_environment_policy.set]\n"
        'KEEP = "me"\n',
        encoding="utf-8",
    )


def _install(home: Path) -> None:
    script = home / "writers.sh"
    script.write_text(
        f'AIFY_BRIDGE_DIR="{_posix(BRIDGE_DIR)}"\n'
        'hermes_config_root() { printf "%s" "$HOME/.hermes"; }\n'
        "enable_codex_hooks_feature() { :; }\n"
        + _lifted()
        + "\ninstall_claude_turn_end_hook\ninstall_claude_turn_start_hook\n"
        "install_codex_turn_hooks\ninstall_hermes_turn_hooks\n",
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": _posix(home)}
    env.pop("CODEX_HOME", None)
    env.pop("HERMES_HOME", None)
    result = subprocess.run([_bash(), _posix(script)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr


def _commands(groups) -> list:
    return [h.get("command", "") for g in groups or [] for h in g.get("hooks", [])]


def _hermes_hooks(config: str) -> dict:
    """event -> list of command values, read from the `hooks:` block by indentation."""
    events, current, inside = {}, None, False
    for line in config.splitlines():
        if re.match(r"^hooks:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            inside = False
        if not inside:
            continue
        key = re.match(r"^  ([a-z_]+):\s*$", line)
        if key:
            current = key.group(1)
            events.setdefault(current, [])
            continue
        cmd = re.match(r"^\s*-?\s*command:\s*(.+)$", line)
        if cmd and current:
            value = cmd.group(1).strip()
            events[current].append(json.loads(value) if value.startswith('"') else value)
    return events


class _Stub:
    def __init__(self):
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
                stub.requests.append((self.path, body, self.headers.get("x-api-key")))
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        # 127.0.0.2: a 127.0.0.1 endpoint makes the bridge's resolver add the real 127.0.0.1:8800 as a fallback.
        self.server = ThreadingHTTPServer(("127.0.0.2", 0), Handler)
        self.url = f"http://127.0.0.2:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _run_hook(command: list | str, stub: _Stub, home: Path, agent_id="installed-hook") -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_", "CLAUDE_MCP_"))}
    env.update({
        "HOME": _posix(home),
        "AIFY_SERVICE_REGISTRY": "/aify-sealed-in-tests/does-not-exist.json",
        "AIFY_AGENT_ID": agent_id,
        "AIFY_COMMS_URL": stub.url,
        "AIFY_API_KEY": "hook-key",
    })
    argv = [_bash(), "-c", command] if isinstance(command, str) else command
    result = subprocess.run(argv, input='{"hook_event_name":"x"}', capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", "codex parses a hook's stdout; it must stay empty"


@pytest.fixture(scope="module")
def installed():
    with tempfile.TemporaryDirectory(prefix="aify-statehooks-") as tmp:
        home = Path(tmp)
        _seed(home)
        _install(home)
        _install(home)
        yield home


def _one_aify(commands: list, needle: str) -> str:
    ours = [c for c in commands if "agent-state-event.mjs" in c or "claude-stop-gate.js" in c or "/api/v1/agents/" in c]
    assert len(ours) == 1, f"expected exactly one aify hook, got {ours}"
    assert needle in ours[0], ours[0]
    assert "curl" not in ours[0], "the unauthenticated curl entry survived a reinstall"
    return ours[0]


CLAUDE_WIRING = {
    "UserPromptSubmit": 'agent-state-event.mjs" turn-start',
    "PostToolUse": 'agent-state-event.mjs" turn-start',
    "Stop": "claude-stop-gate.js",
    "SessionStart": "claude-stop-gate.js",
    "StopFailure": 'agent-state-event.mjs" turn-end',
    "PermissionRequest": 'agent-state-event.mjs" blocked',
}

CODEX_WIRING = {
    "UserPromptSubmit": 'agent-state-event.mjs" turn-start',
    "Stop": 'agent-state-event.mjs" turn-end',
    "Interrupt": 'agent-state-event.mjs" turn-end',
    "PermissionRequest": 'agent-state-event.mjs" blocked',
    "PostToolUse": 'agent-state-event.mjs" unblocked',
}


def test_claude_hooks_replace_the_curl_entries_once_and_keep_the_users(installed):
    hooks = json.loads((installed / ".claude" / "settings.json").read_text())["hooks"]
    for event, needle in CLAUDE_WIRING.items():
        _one_aify(_commands(hooks.get(event)), needle)
    assert [g.get("matcher") for g in hooks["SessionStart"]] == ["compact"]
    assert "echo mine" in _commands(hooks["PostToolUse"])
    assert "echo mine" in _commands(hooks["PermissionRequest"])


def test_codex_hooks_replace_the_curl_entries_once_and_keep_the_users(installed):
    hooks = json.loads((installed / ".codex" / "hooks.json").read_text())["hooks"]
    for event, needle in CODEX_WIRING.items():
        _one_aify(_commands(hooks.get(event)), needle)
    assert "echo mine" in _commands(hooks["Stop"])


def test_hermes_keeps_the_approved_turn_start_command_and_adds_one_entry_per_event(installed):
    root = installed / ".hermes"
    events = _hermes_hooks((root / "config.yaml").read_text())
    start = f'bash "{_native(root / "agent-hooks" / "aify-turn-start.sh")}"'
    # Hermes approves a shell hook by its exact (event, command) pair, so changing this string would
    # silently unregister a turn-start the operator already approved.
    assert events["pre_llm_call"] == [start, "echo mine"], events
    for event, script in (("on_session_end", "aify-turn-end.sh"), ("pre_approval_request", "aify-blocked.sh"),
                          ("post_approval_response", "aify-unblocked.sh")):
        assert events.get(event) == [f'bash "{_native(root / "agent-hooks" / script)}"'], events
    assert len(events["post_tool_call"]) == 1
    assert "curl" not in (root / "agent-hooks" / "aify-turn-start.sh").read_text()


HERMES_WIRING = (("pre_llm_call", "aify-turn-start.sh"), ("on_session_end", "aify-turn-end.sh"),
                 ("pre_approval_request", "aify-blocked.sh"), ("post_approval_response", "aify-unblocked.sh"))


def test_hermes_reinstall_finds_its_entries_written_with_EITHER_separator():
    """On Git Bash the command names its script after a backslash, and an older install may hold either
    form. The writer looked for `/<script>` only, so on Windows every reinstall added one more copy of
    each hook and hermes ran all of them. Seeded here in the form this host does NOT write."""
    with tempfile.TemporaryDirectory(prefix="aify-statehooks-sep-") as tmp:
        home = Path(tmp)
        _seed(home)
        hooks = home / ".hermes" / "agent-hooks"
        config = "model: x\nhooks:\n" + "".join(
            f"  {event}:\n    - command: {json.dumps('bash ' + json.dumps(_other_separator(hooks / script)))}\n"
            "      timeout: 3\n"
            for event, script in HERMES_WIRING)
        (home / ".hermes" / "config.yaml").write_text(config, encoding="utf-8")
        _install(home)
        _install(home)
        events = _hermes_hooks((home / ".hermes" / "config.yaml").read_text())
        for event, script in HERMES_WIRING:
            assert events.get(event) == [f'bash "{_native(hooks / script)}"'], (event, events)


def _trust(config: str) -> dict:
    """`[hooks.state.'<key>']` -> its trusted_hash, and every such header in order, read as codex writes them."""
    found, headers, key = {}, [], None
    for line in config.splitlines():
        header = re.match(r"^\[hooks\.state\.'(.+)'\]\s*$", line)
        if header:
            key = header.group(1)
            headers.append(key)
            continue
        if line.startswith("["):
            key = None
        value = re.match(r'^trusted_hash = "([^"]*)"$', line)
        if value and key:
            found[key] = value.group(1)
    return found, headers


def test_codex_trust_is_recorded_for_exactly_the_hooks_written_at_their_index(installed):
    """Codex skips an untrusted hooks.json hook, and a managed worker resuming a thread waits at codex's
    review screen with nobody to answer. So the writer records codex's hash for each hook it writes,
    keyed by the group's index, and replaces the old aify entry IN PLACE so the operator's own hook
    after it keeps its index and the trust recorded for it."""
    hooks_file = installed / ".codex" / "hooks.json"
    hooks = json.loads(hooks_file.read_text())["hooks"]
    config = (installed / ".codex" / "config.toml").read_text()
    found, headers = _trust(config)
    assert len(headers) == len(set(headers)), f"a trust table was written twice: {headers}"

    assert _commands(hooks["Stop"])[1] == "echo mine", "the operator's hook moved off the index its trust names"
    assert found[f"{_native(hooks_file)}:stop:1:0"] == "sha256:the-operators-own-hook"
    assert 'KEEP = "me"' in config

    expected = {}
    for event, needle in CODEX_WIRING.items():
        index = next(i for i, g in enumerate(hooks[event]) if "agent-state-event.mjs" in json.dumps(g))
        snake = re.sub(r"(?<!^)([A-Z])", r"_\1", event).lower()
        expected[f"{_native(hooks_file)}:{snake}:{index}:0"] = _codex_hash(snake, hooks[event][index]["hooks"][0]["command"])
    assert {k: found.get(k) for k in expected} == expected
    assert set(found) == set(expected) | {f"{_native(hooks_file)}:stop:1:0"}, found
    pytest.importorskip("tomllib").loads(config)


def test_codex_hash_is_the_one_codex_itself_records():
    """A vector codex-cli 0.153.4 wrote into a real config.toml on 2026-09-15, for the stop hook an earlier
    install of this service put in hooks.json. It is what makes _codex_hash codex's formula and not ours."""
    assert _codex_hash("stop", LEGACY_CURL.replace("ROUTE", "turn-end")) == (
        "sha256:8b910bca97ab5099b112cb419d506e83970cba70f51b4d2cf9623522a4bb8141")


def test_codex_trust_leaves_a_key_the_config_defines_another_way():
    """A second definition of one TOML key makes a config.toml codex refuses to load, which stops every
    codex session on the host. A key already set some other way is left for codex to ask about."""
    with tempfile.TemporaryDirectory(prefix="aify-statehooks-dotted-") as tmp:
        home = Path(tmp)
        _seed(home)
        key = f"{_native(home / '.codex' / 'hooks.json')}:stop:0:0"
        dotted = f'hooks.state.{json.dumps(key)}.trusted_hash = "sha256:set-as-a-dotted-key"'
        (home / ".codex" / "config.toml").write_text(dotted + "\n", encoding="utf-8")
        _install(home)
        config = (home / ".codex" / "config.toml").read_text()
        assert config.count(json.dumps(key)[1:-1]) == 1, config
        assert f"'{key}'" not in config, config
        assert f":user_prompt_submit:0:0']" in config, "control: the other hooks were still trusted"
        pytest.importorskip("tomllib").loads(config)


def test_the_written_commands_reach_the_service_with_the_key(installed):
    claude = json.loads((installed / ".claude" / "settings.json").read_text())["hooks"]
    codex = json.loads((installed / ".codex" / "hooks.json").read_text())["hooks"]
    hermes = _hermes_hooks((installed / ".hermes" / "config.yaml").read_text())
    cases = [
        (_one_aify(_commands(claude["PermissionRequest"]), "blocked"), "/status-event", {"kind": "blocked"}),
        (_one_aify(_commands(claude["StopFailure"]), "turn-end"), "/turn-end", {}),
        (_one_aify(_commands(claude["PostToolUse"]), "turn-start"), "/turn-start", {}),
        (_one_aify(_commands(codex["Interrupt"]), "turn-end"), "/turn-end", {}),
        (_one_aify(_commands(codex["PostToolUse"]), "unblocked"), "/status-event", {"kind": "unblocked"}),
        (hermes["pre_llm_call"][0], "/turn-start", {}),
        (hermes["on_session_end"][0], "/turn-end", {}),
        (hermes["pre_approval_request"][0], "/status-event", {"kind": "blocked"}),
        (hermes["post_approval_response"][0], "/status-event", {"kind": "unblocked"}),
    ]
    stub = _Stub()
    try:
        for command, route, body in cases:
            stub.requests.clear()
            _run_hook(command, stub, installed)
            assert len(stub.requests) == 1, (command, stub.requests)
            path, sent, key = stub.requests[0]
            assert path == f"/api/v1/agents/installed-hook{route}", (command, path)
            assert key == "hook-key", (command, "the hook request must authenticate")
            assert (json.loads(sent) if sent else None) == body, (command, sent)
        stub.requests.clear()
        _run_hook(_one_aify(_commands(claude["PermissionRequest"]), "blocked"), stub, installed, agent_id="")
        assert stub.requests == [], "a plain session with no aify identity must post nothing"
    finally:
        stub.close()
