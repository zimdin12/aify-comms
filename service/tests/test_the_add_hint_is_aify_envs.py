"""The add hint the card and the misconfigured note give is aify-env's own, specimen for specimen (2026-11-02).

aify-env 0.9.11 owns the rule (launch-roots.mjs parseLaunchRoot, definition-checks.mjs addHintFor): the stored
spelling of a root, and a double-quoted `aify-env roots add` that survives bash, PowerShell and cmd.exe, or why there is
none. comms twins it in service/api_core/workspace.py so a comms surface never offers a command that host's CLI would
refuse or a shell would rewrite. Each specimen is judged by both; they must agree exactly, including the parser's
reason text, which the note shows.

The specimens go over stdin, not argv: a control character (the hint's own subject) cannot ride in a command line.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from service.api_core.workspace import add_hint, parse_launch_root
from service.tests.test_agent_definition_push import ENV_REPOS

#: (folder, windows). Each line names what it discriminates.
SPECIMENS = [
    ("D:\\", True), ("D:", True), ("d:\\Games\\x\\", True), ("C:/", True),          # stored spelling, drive uppercased
    ("D:/My Games", True), ("D://two//slashes", True),                               # spaces quoted; slashes collapsed
    ("D:/Games/../x", True), ("D:/Games/./x", True), ("D:/..hidden", True),          # dot segments; a leading-dot name
    ("//server/share", True), ("\\\\?\\C:\\x", True), ("D:Games", True), ("", True), # UNC, device, drive-relative, empty
    ("/srv/repo", True), ("  D:/padded  ", True),                                    # POSIX on Windows; trimmed input
    ("D:/a\nb", True), ("D:/a\rb", True),                                            # a line break: refused on Windows
    ("D:/100% done", True), ("D:/wow!", True), ('D:/q"uote', True),                  # cmd expansion; the quote itself
    ("D:/back`tick", True), ("D:/$x", True), ("D:/tab\there", True), ("D:/bel\x07", True),
    ("D:/x\x1f", True),            # JS trim keeps U+001F, Python strip() drops it: the trim twin is exact
    ("D:/x\u2028", True),          # JS `.` and trim both treat U+2028 as a line terminator
    ("/srv/repo", False), ("/srv/repo/", False), ("/", False), ("//srv", False),
    ("/srv/./x", False), ("/srv/My Games", False), ("/srv/it's here", False),
    ("/srv/odd\\name", False), ("\\srv\\other", False), ("/srv/a\nb", False),        # POSIX backslash; a line break
    ("rel", False), ("D:/x", False), ("/x\x1f", False), ("  /srv/pad ", False),
]

SCRIPT = """
let input = "";
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", async () => {
  const [roots, checks] = await Promise.all([import(process.argv[1]), import(process.argv[2])]);
  const specimens = JSON.parse(input);
  const parsed = specimens.map(([folder, windows]) => roots.parseLaunchRoot(folder, { windows }));
  const hinted = typeof checks.addHintFor === "function"
    ? specimens.map(([folder, windows]) => checks.addHintFor(folder, windows)) : null;
  console.log(JSON.stringify({ parsed, hinted }));
});
"""


def _aify_env_answers():
    repo = next((r for r in ENV_REPOS if (r / "lib" / "launch-roots.mjs").is_file()), None)
    if repo is None or shutil.which("node") is None:
        pytest.skip("no aify-env checkout with lib/launch-roots.mjs (0.9.11 or newer), or no node")
    run = subprocess.run(["node", "-e", SCRIPT, (repo / "lib" / "launch-roots.mjs").as_uri(),
                          (repo / "lib" / "definition-checks.mjs").as_uri()],
                         input=json.dumps(SPECIMENS), capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_the_stored_root_is_aify_envs_specimen_for_specimen():
    theirs = _aify_env_answers()["parsed"]
    ours = [parse_launch_root(folder, windows=windows) for folder, windows in SPECIMENS]
    assert [(s, o) for s, o, t in zip(SPECIMENS, ours, theirs) if o != t] == [], "comms stores a root differently"
    assert {"root"} in [set(o) for o in ours] and {"refused"} in [set(o) for o in ours], "both answers exercised"


def test_the_add_hint_is_aify_envs_specimen_for_specimen():
    hinted = _aify_env_answers()["hinted"]
    assert hinted is not None, "aify-env does not export addHintFor; the hint cannot be compared, only re-implemented"
    # aify-env wraps its command in markdown backticks for its own report line; comms' surfaces add their own.
    theirs = [{"command": h["command"].strip("`")} if "command" in h else h for h in hinted]
    ours = [add_hint(folder, windows=windows) for folder, windows in SPECIMENS]
    assert [(s, o) for s, o, t in zip(SPECIMENS, ours, theirs) if o != t] == [], "comms hints differently"
    kinds = {next(iter(o)) for o in ours}
    assert kinds == {"command", "unquotable", "refused"}, f"the specimens must exercise every answer, got {kinds}"
