"""redeploy.sh carries the installed aify-env endpoint into the installer it re-runs.

`install.sh --env-endpoint <url>` bakes the aify-env the doctor asks into the `aify-comms` launcher.
redeploy.sh passed no --env-endpoint, so every routine update reset a custom one to 127.0.0.1:8802
(v0.7 review). This runs the REAL redeploy.sh in a scratch repo whose install.sh only records its
arguments, with HOME and PATH sealed: no verifier is on PATH, so nothing reaches a live service.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from service.tests._launchers import bash

REPO = Path(__file__).resolve().parents[2]
CUSTOM = "http://10.0.0.5:9999"


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _redeploy(tmp_path: Path, *, env_launcher: str | None) -> str:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "redeploy.sh", repo / "redeploy.sh")
    for name in ("installed-endpoint.sh", "installed-env-endpoint.sh", "deploy-delta.sh"):
        shutil.copy(REPO / "scripts" / name, repo / "scripts" / name)
    record = tmp_path / "install-args"
    _executable(repo / "install.sh", f'#!/bin/bash\nprintf "%s\\n" "$@" > "{record.as_posix()}"\n')

    home = tmp_path / "home"
    wrappers = home / ".local" / "bin"
    wrappers.mkdir(parents=True)
    _executable(wrappers / "claude-aify", '#!/bin/bash\nHARNESS_ENDPOINT="${HARNESS_ENDPOINT-${AIFY_COMMS_URL:-http://10.0.0.5:8800}}"\n')
    if env_launcher is not None:
        _executable(wrappers / "aify-comms", env_launcher)

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "claude", "#!/bin/bash\nexit 0\n")
    path = os.pathsep.join([str(stubs), str(Path(bash()).parent), "/usr/bin"])
    env = {"PATH": path, "HOME": home.as_posix(), "USERPROFILE": home.as_posix(),
           "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "TEMP": tmp_path.as_posix(), "TMP": tmp_path.as_posix()}
    done = subprocess.run([bash(), (repo / "redeploy.sh").as_posix()], capture_output=True, text=True,
                          timeout=120, env=env, cwd=repo)
    assert done.returncode == 0, done.stdout + done.stderr
    return record.read_text(encoding="utf-8")


def test_an_installed_custom_env_endpoint_is_passed_back_to_the_installer(tmp_path):
    args = _redeploy(tmp_path, env_launcher=f'#!/bin/bash\nexport AIFY_ENV_ENDPOINT="{CUSTOM}"\n').split("\n")
    assert "--env-endpoint" in args, args
    assert args[args.index("--env-endpoint") + 1] == CUSTOM
    assert "http://10.0.0.5:8800" in args, "control: the server URL is still recovered"


def test_control_with_nothing_installed_the_installer_default_applies(tmp_path):
    assert "--env-endpoint" not in _redeploy(tmp_path, env_launcher=None).split("\n")


def test_an_unrendered_placeholder_is_not_recovered(tmp_path):
    placeholder = '#!/bin/bash\nexport AIFY_ENV_ENDPOINT="http://@@ENV@@"\n'
    assert "--env-endpoint" not in _redeploy(tmp_path, env_launcher=placeholder).split("\n")
