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
import time
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
    "install_codex_mcp_env_vars",
    "hermes_hook_bash",
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


def _codex_hash(event: str, command: str, run_async: bool = False, timeout: int = 3) -> str:
    """Codex's trusted_hash for a plain command hook: sha256 over sorted compact JSON of its identity."""
    identity = {"event_name": event, "hooks": [{"async": run_async, "command": command, "timeout": timeout, "type": "command"}]}
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
    def __init__(self, answer_after: float = 0.0, host="127.0.0.2"):
        self.requests = []
        self.answered = threading.Event()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
                stub.requests.append((self.path, body, self.headers.get("x-api-key")))
                time.sleep(answer_after)
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
                stub.answered.set()

            def log_message(self, *args):
                pass

        # 127.0.0.2: a 127.0.0.1 endpoint makes the bridge's resolver add the real 127.0.0.1:8800 as a fallback.
        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.url = f"http://{host}:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _run_hook(command: list | str, stub: _Stub, home: Path, agent_id="installed-hook", extra_env=None) -> float:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_", "CLAUDE_MCP_"))}
    env.update({
        "HOME": _posix(home),
        "AIFY_SERVICE_REGISTRY": "/aify-sealed-in-tests/does-not-exist.json",
        "AIFY_AGENT_ID": agent_id,
        "AIFY_COMMS_URL": stub.url,
        "AIFY_API_KEY": "hook-key",
    })
    env.update(USERPROFILE=_posix(home), APPDATA=_posix(home / "AppData/Roaming"),
               LOCALAPPDATA=_posix(home / "AppData/Local"), TEMP=_posix(home), TMP=_posix(home))
    env.update(extra_env or {})
    argv = [_bash(), "-c", command] if isinstance(command, str) else command
    started = time.monotonic()
    result = subprocess.run(argv, input='{"hook_event_name":"x"}', capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", "codex parses a hook's stdout; it must stay empty"
    return time.monotonic() - started


def _await_request(stub: _Stub, deadline: float = 10.0) -> None:
    """A hermes hook leaves `node` posting after it returns, so its request can land later."""
    end = time.monotonic() + deadline
    while not stub.requests and time.monotonic() < end:
        time.sleep(0.05)


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
    "StopFailure": 'agent-state-event.mjs" turn-end',
    "PermissionRequest": 'agent-state-event.mjs" blocked',
}

CODEX_WIRING = {
    "UserPromptSubmit": 'agent-state-event.mjs" turn-start',
    "Stop": 'agent-state-event.mjs" turn-end',
    "Interrupt": 'agent-state-event.mjs" turn-end',
    "SessionStart": 'agent-state-event.mjs" turn-end',
    "PermissionRequest": 'agent-state-event.mjs" blocked',
    "PostToolUse": 'agent-state-event.mjs" unblocked',
}


def _aify_hook(groups) -> dict:
    return next(h for g in groups for h in g.get("hooks", []) if "agent-state-event.mjs" in h.get("command", "")
                or "claude-stop-gate.js" in h.get("command", ""))


def test_claude_hooks_replace_the_curl_entries_once_and_keep_the_users(installed):
    hooks = json.loads((installed / ".claude" / "settings.json").read_text())["hooks"]
    for event, needle in CLAUDE_WIRING.items():
        _one_aify(_commands(hooks.get(event)), needle)
        # In the background, so a prompt and a tool call never wait on the service (hook_event_order.py).
        assert _aify_hook(hooks[event]).get("async") is True, event
    # Compaction may continue the turn, so it is gated; a started, resumed or cleared session is idle, and a
    # session resumed after a kill ends on a user message the gate would read as a turn in flight (2026-10-13).
    starts = {g.get("matcher"): _commands([g]) for g in hooks["SessionStart"]}
    assert set(starts) == {"compact", "startup|resume|clear"}, starts
    _one_aify(starts["compact"], "claude-stop-gate.js")
    _one_aify(starts["startup|resume|clear"], 'agent-state-event.mjs" turn-end')
    assert all(h.get("async") is True for g in hooks["SessionStart"] for h in g["hooks"])
    assert "echo mine" in _commands(hooks["PostToolUse"])
    assert "echo mine" in _commands(hooks["PermissionRequest"])


def test_codex_hooks_replace_the_curl_entries_once_and_keep_the_users(installed):
    hooks = json.loads((installed / ".codex" / "hooks.json").read_text())["hooks"]
    for event, needle in CODEX_WIRING.items():
        _one_aify(_commands(hooks.get(event)), needle)
        assert _aify_hook(hooks[event]).get("async") is True, event
    assert "echo mine" in _commands(hooks["Stop"])


def _hook_bash() -> str:
    """The bash a hermes hook names: Git Bash by its path on Windows, where a bare `bash` is WSL's (System32 first)."""
    if not _git_bash():
        return "bash"
    found = subprocess.run([_bash(), "-c", 'cygpath -m "$(command -v bash)"'], capture_output=True, text=True)
    return f'"{found.stdout.strip()}"'


def test_hermes_hooks_name_their_bash_and_add_one_entry_per_event(installed):
    root = installed / ".hermes"
    events = _hermes_hooks((root / "config.yaml").read_text())
    bash = _hook_bash()
    # Hermes approves a shell hook by its exact (event, command) pair; install.sh carries an approved one over
    # to this command (scripts/hermes-hooks-after-install.cjs), so a managed hermes keeps running it.
    assert events["pre_llm_call"] == [f'{bash} "{_native(root / "agent-hooks" / "aify-turn-start.sh")}"', "echo mine"], events
    for event, script in (("on_session_end", "aify-turn-end.sh"), ("pre_approval_request", "aify-blocked.sh"),
                          ("post_approval_response", "aify-unblocked.sh")):
        assert events.get(event) == [f'{bash} "{_native(root / "agent-hooks" / script)}"'], events
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
            assert events.get(event) == [f'{_hook_bash()} "{_native(hooks / script)}"'], (event, events)


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
        hook = hooks[event][index]["hooks"][0]
        expected[f"{_native(hooks_file)}:{snake}:{index}:0"] = _codex_hash(snake, hook["command"], hook.get("async", False))
    # The turn-start context hook, trusted as the state hooks are, at its own group's index.
    index, hook = _context_hook(hooks)
    expected[f"{_native(hooks_file)}:user_prompt_submit:{index}:0"] = _codex_hash("user_prompt_submit", hook["command"], timeout=5)
    assert {k: found.get(k) for k in expected} == expected
    assert set(found) == set(expected) | {f"{_native(hooks_file)}:stop:1:0"}, found
    pytest.importorskip("tomllib").loads(config)


def _context_hook(hooks: dict) -> tuple:
    """The one UserPromptSubmit group holding the runner command the installer writes: its index and its hook. Matched
    by that exact command, since a group merely naming the file may be the operator's."""
    found = [(i, g) for i, g in enumerate(hooks.get("UserPromptSubmit", []))
             if any(h.get("command") == CONTEXT_COMMAND for h in g.get("hooks", []))]
    assert len(found) == 1, f"expected exactly one turn-start group, got {found}"
    index, group = found[0]
    assert set(group) == {"hooks"} and len(group["hooks"]) == 1, f"the runner shares its group: {group}"
    return index, group["hooks"][0]


CONTEXT_COMMAND = ('if [ -n "${AIFY_AGENT_ID:-}" ]; then node "'
                   + _posix(BRIDGE_DIR) + '/node_modules/aify-wrapper/bin/aify-turn-start.mjs"; fi')


def test_codex_turn_start_runs_the_runner_in_its_own_synchronous_group(installed):
    """Codex adds a UserPromptSubmit hook's printed additionalContext to the turn, so the runner is synchronous (an
    async hook's output is not waited for), prints to codex (no redirect), and runs only for an agent, since Codex
    Desktop reads the same hooks.json. Installed twice over a file holding the old curl entry: still one group, after
    the state hook, which keeps its index and its trust."""
    hooks = json.loads((installed / ".codex" / "hooks.json").read_text())["hooks"]
    index, hook = _context_hook(hooks)
    assert hook == {"type": "command", "command": CONTEXT_COMMAND, "timeout": 5}, hook
    state = next(i for i, g in enumerate(hooks["UserPromptSubmit"]) if "agent-state-event.mjs" in json.dumps(g))
    assert (state, index) == (0, 1), hooks["UserPromptSubmit"]


def test_codex_turn_start_hook_keeps_the_operators_hook_at_its_index():
    """Codex keys trust by a group's index, so the new group goes after the operator's own and stays there."""
    with tempfile.TemporaryDirectory(prefix="aify-statehooks-ctx-") as tmp:
        home = Path(tmp)
        _seed(home)
        hooks_file = home / ".codex" / "hooks.json"
        seeded = json.loads(hooks_file.read_text())
        seeded["hooks"]["UserPromptSubmit"].append(USER_HOOK)
        hooks_file.write_text(json.dumps(seeded), encoding="utf-8")
        for _ in range(2):
            _install(home)
            groups = json.loads(hooks_file.read_text())["hooks"]["UserPromptSubmit"]
            assert _commands(groups)[1:] == ["echo mine", CONTEXT_COMMAND], groups


NAMING_ONLY = [{"type": "command", "command": "node operator-owned.mjs --label aify-turn-start.mjs", "timeout": 3},
               {"type": "command", "command": "node operator-owned.mjs --label agent-state-event.mjs", "timeout": 3}]


def test_a_hook_that_only_names_an_aify_file_is_never_trusted_or_replaced():
    """A file name inside a command is no proof the installer wrote it (review of e6fc8155). An operator's hook naming
    either runner, on its own or beside another hook, keeps its group and its index, and gets no trust recorded."""
    with tempfile.TemporaryDirectory(prefix="aify-statehooks-naming-") as tmp:
        home = Path(tmp)
        _seed(home)
        hooks_file = home / ".codex" / "hooks.json"
        seeded = json.loads(hooks_file.read_text())
        single = {"hooks": [NAMING_ONLY[0]]}
        mixed = {"hooks": [{"type": "command", "command": "echo mine", "timeout": 3}, NAMING_ONLY[0]]}
        # The review's counterexamples: the gate head with a file name, the runner's shape at another path, and the
        # installer's exact context command under an event it was never written for.
        gated = {"hooks": [{"type": "command", "timeout": 3,
                            "command": 'if [ -n "${AIFY_AGENT_ID:-}" ]; then echo agent-state-event.mjs; fi'}]}
        elsewhere = {"hooks": [{"type": "command", "timeout": 5,
                                "command": 'if [ -n "${AIFY_AGENT_ID:-}" ]; then node "C:/operator/aify-turn-start.mjs"; fi'}]}
        displaced = {"hooks": [{"type": "command", "command": CONTEXT_COMMAND, "timeout": 5}]}
        seeded["hooks"]["UserPromptSubmit"] += [single, mixed, elsewhere]
        seeded["hooks"]["Stop"] += [{"hooks": [NAMING_ONLY[1]]}, gated, displaced]
        hooks_file.write_text(json.dumps(seeded), encoding="utf-8")
        _install(home)
        hooks = json.loads(hooks_file.read_text())["hooks"]
        assert hooks["UserPromptSubmit"][1:4] == [single, mixed, elsewhere], "an operator group naming the runner was replaced"
        assert hooks["Stop"][2:5] == [{"hooks": [NAMING_ONLY[1]]}, gated, displaced], "an operator Stop group was replaced"
        assert _context_hook(hooks)[1]["command"] == CONTEXT_COMMAND, "CONTROL: the runner's own group was written"
        found, _ = _trust((home / ".codex" / "config.toml").read_text())
        operators = NAMING_ONLY + [g["hooks"][0] for g in (gated, elsewhere)]
        named = {f"{_native(hooks_file)}:{event}:{g}:{h}" for event, groups in (("user_prompt_submit", hooks["UserPromptSubmit"]),
                                                                                 ("stop", hooks["Stop"]))
                 for g, group in enumerate(groups) for h, hook in enumerate(group["hooks"])
                 if hook in operators or (event == "stop" and hook == displaced["hooks"][0])}
        assert len(named) == 6, named
        assert not named & set(found), f"trust recorded for a hook the installer did not write: {named & set(found)}"
        index, _ = _context_hook(hooks)
        assert f"{_native(hooks_file)}:user_prompt_submit:{index}:0" in found, "CONTROL: the written hook was trusted"


def _current_state_start(home: Path) -> str:
    """The state start command this installer writes, read from an install into a fresh home."""
    home.mkdir()
    _seed(home)
    _install(home)
    groups = json.loads((home / ".codex" / "hooks.json").read_text())["hooks"]["UserPromptSubmit"]
    [command] = [h["command"] for g in groups for h in g["hooks"] if "agent-state-event.mjs" in h["command"]]
    return command


def test_two_aify_groups_in_one_event_are_left_in_place_and_untrusted():
    """Review of 94e28914: removing a duplicate shifts every operator group after it off the index its trust names. With
    the 2026-05 curl form and today's command both present, that hook family's groups are left exactly where they are and
    none of them is trusted anew; the operator's trust for its own group stays where it was. The refusal is per family:
    the separate context hook in the same event is still appended after them and trusted."""
    with tempfile.TemporaryDirectory(prefix="aify-statehooks-dup-") as tmp:
        start = _current_state_start(Path(tmp) / "probe")
        home = Path(tmp) / "home"
        home.mkdir()
        _seed(home)
        hooks_file = home / ".codex" / "hooks.json"
        operator_a = {"hooks": [{"type": "command", "command": "echo a", "timeout": 3}]}
        operator_b = {"hooks": [{"type": "command", "command": "echo b", "timeout": 3}]}
        ours_now = {"hooks": [{"type": "command", "command": start, "timeout": 3, "async": True}]}
        seeded = json.loads(hooks_file.read_text())
        seeded["hooks"]["UserPromptSubmit"] = [_legacy("turn-start"), operator_a, ours_now, operator_b]
        hooks_file.write_text(json.dumps(seeded), encoding="utf-8")
        key_b = f"{_native(hooks_file)}:user_prompt_submit:3:0"
        config = home / ".codex" / "config.toml"
        config.write_text(config.read_text() + f"\n[hooks.state.'{key_b}']\ntrusted_hash = \"sha256:operator-b\"\n",
                          encoding="utf-8")
        _install(home)
        groups = json.loads(hooks_file.read_text())["hooks"]["UserPromptSubmit"]
        assert groups[:4] == [_legacy("turn-start"), operator_a, ours_now, operator_b], "a group moved or was replaced"
        found, _ = _trust(config.read_text())
        assert found[key_b] == "sha256:operator-b", "the operator's trust no longer names its own group"
        for index in (0, 2):
            assert f"{_native(hooks_file)}:user_prompt_submit:{index}:0" not in found, f"group {index} was trusted"
        index, _ = _context_hook({"UserPromptSubmit": groups})
        assert index == 4, "CONTROL: the runner's own group was still appended"


def test_one_aify_group_is_replaced_where_it_stands():
    """The healthy case beside it: one group of ours (the 2026-05 curl form) between two operator groups is replaced
    in place and trusted there, and the operator group after it keeps its index."""
    with tempfile.TemporaryDirectory(prefix="aify-statehooks-one-") as tmp:
        home = Path(tmp)
        _seed(home)
        hooks_file = home / ".codex" / "hooks.json"
        operator_a = {"hooks": [{"type": "command", "command": "echo a", "timeout": 3}]}
        operator_b = {"hooks": [{"type": "command", "command": "echo b", "timeout": 3}]}
        seeded = json.loads(hooks_file.read_text())
        seeded["hooks"]["UserPromptSubmit"] = [operator_a, _legacy("turn-start"), operator_b]
        hooks_file.write_text(json.dumps(seeded), encoding="utf-8")
        _install(home)
        groups = json.loads(hooks_file.read_text())["hooks"]["UserPromptSubmit"]
        assert (groups[0], groups[2]) == (operator_a, operator_b)
        assert "agent-state-event.mjs" in groups[1]["hooks"][0]["command"], "the curl form was not replaced in place"
        found, _ = _trust((home / ".codex" / "config.toml").read_text())
        assert f"{_native(hooks_file)}:user_prompt_submit:1:0" in found


class _TurnStartService(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        self.seen.append((self.path, self.rfile.read(int(self.headers.get("content-length") or 0)).decode()))
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"text": "OPERATOR RULE", "firedEventIds": []}).encode())

    def log_message(self, *args):
        pass


def test_the_written_context_command_prints_what_the_service_says_and_only_for_an_agent(installed):
    """The command as codex will run it, with codex's own UserPromptSubmit input: the runner's JSON reaches stdout,
    which is where codex reads it. Without AIFY_AGENT_ID, as in Codex Desktop, it prints nothing and asks no one."""
    hooks = json.loads((installed / ".codex" / "hooks.json").read_text())["hooks"]
    command = _context_hook(hooks)[1]["command"]
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TurnStartService)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _TurnStartService.seen = []
    try:
        with tempfile.TemporaryDirectory(prefix="aify-turnstart-host-") as tmp:
            home = Path(tmp)
            (home / ".aify" / "credentials").mkdir(parents=True)
            # Bytes: write_text gives CRLF on Windows, and a credential with a control character is no key at all.
            (home / ".aify" / "credentials" / "dash.key").write_bytes(b"probe-key\n")
            registry = home / ".aify" / "services.json"
            registry.write_text(json.dumps({"version": 1, "services": {"dash": {
                "endpoint": f"http://127.0.0.1:{server.server_address[1]}", "credentialRef": "dash.key",
                "turnStart": {"path": "/events/turn-start", "budgetMs": 800}}}}), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_", "CLAUDE", "HERDR_"))}
            env.update(HOME=_posix(home), USERPROFILE=str(home), AIFY_SERVICE_REGISTRY=str(registry))
            codex_input = json.dumps({"session_id": "codex-thread-1", "turn_id": "t1", "transcript_path": None,
                                      "cwd": str(home), "hook_event_name": "UserPromptSubmit", "model": "m",
                                      "permission_mode": "default", "prompt": "hi"})
            run = lambda extra: subprocess.run([_bash(), "-c", command], input=codex_input, capture_output=True,
                                               text=True, env={**env, **extra}, timeout=30)
            plain = run({})
            assert (plain.returncode, plain.stdout, _TurnStartService.seen) == (0, "", []), plain.stderr
            agent = run({"AIFY_AGENT_ID": "probe-agent"})
            assert agent.returncode == 0, agent.stderr
            assert json.loads(agent.stdout) == {"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit", "additionalContext": "OPERATOR RULE"}}, agent.stdout
            assert [path for path, _ in _TurnStartService.seen] == ["/events/turn-start"]
    finally:
        server.shutdown()
        server.server_close()


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


def test_a_hermes_hook_returns_before_the_service_answers(installed):
    """Hermes waits on each hook until it exits, so the hook leaves `node` posting and returns. The stub
    answers a second after the request arrives, inside the poster's own 2 s limit: a hook that waited on
    its post returns after the answer."""
    hermes = _hermes_hooks((installed / ".hermes" / "config.yaml").read_text())
    stub = _Stub(answer_after=1.0)
    try:
        _run_hook(hermes["pre_llm_call"][0], stub, installed)
        assert not stub.answered.is_set(), "the hook returned only after the service answered"
        _await_request(stub)
        assert [path for path, _, _ in stub.requests] == ["/api/v1/agents/installed-hook/turn-start"]
    finally:
        stub.close()


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
            before = time.time() * 1_000_000
            _run_hook(command, stub, installed)
            _await_request(stub)
            assert len(stub.requests) == 1, (command, stub.requests)
            path, sent, key = stub.requests[0]
            assert path == f"/api/v1/agents/installed-hook{route}", (command, path)
            assert key == "hook-key", (command, "the hook request must authenticate")
            sent = json.loads(sent)
            # When the hook fired, in host microseconds, and on which host: they order the background
            # hooks' events (service/api_core/hook_event_order.py).
            fired = sent.pop("firedAtUs")
            assert isinstance(fired, int) and before - 1_000_000 <= fired <= time.time() * 1_000_000, (command, fired)
            assert re.fullmatch(r"[a-z0-9_-]+:.+", sent.pop("machineId")), command
            assert sent == body, (command, sent)
        stub.requests.clear()
        _run_hook(_one_aify(_commands(claude["PermissionRequest"]), "blocked"), stub, installed, agent_id="")
        assert stub.requests == [], "a plain session with no aify identity must post nothing"
    finally:
        stub.close()

@pytest.mark.parametrize("mode", ["resident", "managed"])
def test_g5_rendered_hooks_report_to_env_without_a_comms_url(installed, mode):
    """Execute installed hooks for all residents and managed Claude/Hermes, including Claude's Stop gate."""
    claude = json.loads((installed / ".claude" / "settings.json").read_text())["hooks"]
    codex = json.loads((installed / ".codex/hooks.json").read_text())["hooks"]
    hermes = _hermes_hooks((installed / ".hermes/config.yaml").read_text())
    cases = [(_one_aify(_commands(claude[event]), needle), kind)
             for event, needle, kind in (("UserPromptSubmit", "turn-start", "turn-start"),
                                        ("Stop", "claude-stop-gate.js", "turn-end"),
                                        ("PermissionRequest", "blocked", "blocked"))]
    if mode == "resident":
        cases += [(_one_aify(_commands(codex[event]), needle), kind)
                  for event, needle, kind in (("UserPromptSubmit", "turn-start", "turn-start"),
                                             ("Stop", "turn-end", "turn-end"),
                                             ("PermissionRequest", "blocked", "blocked"),
                                             ("PostToolUse", "unblocked", "unblocked"))]
    cases += [(hermes[event][0], kind) for (event, _), kind in zip(
        HERMES_WIRING, ("turn-start", "turn-end", "blocked", "unblocked"))]
    stub = _Stub(host="127.0.0.1")
    try:
        folder = installed / ".aify/env"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "hook-test.json").write_text(json.dumps({"url": stub.url, "instance": "hook-test",
            "pid": os.getpid(), "startedAt": "2026-10-01T00:00:00.000Z"}))
        # ONE LIFETIME PER MODE. The other mode's stub held a port this one can be given, and under suite load a
        # hook that mode ran can post after it closed: counted here it read as a duplicate (2026-10-12).
        lifetime = {"resident": "7f3c9e2a-1111-4111-8111-111111111111",
                    "managed": "7f3c9e2a-2222-4222-8222-222222222222"}[mode]
        extra = {"AIFY_COMMS_URL": "", "AIFY_ENV_INSTANCE": "hook-test", "AIFY_LIFETIME": lifetime,
                 "AIFY_SESSION_MODE": mode}
        for command, kind in cases:
            stub.requests.clear()
            _run_hook(command, stub, installed, extra_env=extra)
            # The default budget: a hermes hook's detached node posts after the hook returns, and under suite
            # load that took longer than the 3 s this once allowed (failed 2026-10-12, passes alone).
            _await_request(stub)
            ours = [r for r in stub.requests if json.loads(r[1]).get("lifetime") == lifetime]
            assert len(ours) == 1, (mode, command, stub.requests)
            route, body, key = ours[0]
            assert route == "/agents/installed-hook/turn-event"
            assert key is None
            body = json.loads(body)
            assert body == {"instance": "hook-test", "lifetime": extra["AIFY_LIFETIME"], "kind": kind,
                            "firedAtUs": body["firedAtUs"]}
            assert isinstance(body["firedAtUs"], int) and body["firedAtUs"] > 0
    finally:
        stub.close()


def test_g5_codex_installer_forwards_the_host_minted_binding(tmp_path):
    home = tmp_path
    config = home / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.aify-comms]\ncommand = "node"\n')
    script = f"SCRIPT_DIR={shlex.quote(_posix(REPO))}\n" + _lifted() + "\ninstall_codex_mcp_env_vars\n"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_", "CLAUDE_MCP_"))}
    env.update(HOME=_posix(home), USERPROFILE=_posix(home),
               APPDATA=_posix(home / "AppData/Roaming"), LOCALAPPDATA=_posix(home / "AppData/Local"),
               TEMP=_posix(home), TMP=_posix(home), TMPDIR=_posix(home))
    env.pop("CODEX_HOME", None)
    script_path = home / "forwarding.sh"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run([_bash(), _posix(script_path)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    import tomllib
    forwarded = tomllib.loads(config.read_text())["mcp_servers"]["aify-comms"]["env_vars"]
    for name in ("AIFY_ENV_URL", "AIFY_ENV_INSTANCE", "AIFY_LIFETIME"):
        assert name in forwarded, name
