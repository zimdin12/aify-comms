"""Pack the exact Git tree, not working-tree files or a hand-built skills fixture."""
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SKILLS = Path(".agents") / "skills"


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, check=True).stdout


def test_npm_packs_only_the_exact_canonical_skill_tree(tmp_path):
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm is not on PATH")

    # In a clean checkout this is HEAD's tree. Authors can also pack a staged review candidate.
    tree = _git("write-tree").decode().strip()
    source = tmp_path / "source"
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", tree))) as archive:
        archive.extractall(source, filter="data")

    package = json.loads((source / "package.json").read_bytes())
    assert set(package) == {"name", "version", "private", "files", "aifySkills"}
    assert package["name"] == "aify-comms-skills"
    assert package["private"] is True
    assert package["files"] == [SKILLS.as_posix()]
    assert package["aifySkills"] == {"directory": SKILLS.as_posix()}

    # npm is run offline, with no lifecycle scripts, credentials or ambient npm config.
    env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "ComSpec", "PATHEXT") if k in os.environ}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), TMP=str(tmp_path), TEMP=str(tmp_path),
               TMPDIR=str(tmp_path), npm_config_cache=str(tmp_path / "cache"),
               npm_config_userconfig=str(tmp_path / "npm-user"), npm_config_globalconfig=str(tmp_path / "npm-global"))
    run = subprocess.run([npm, "pack", "--offline", "--ignore-scripts", "--json", "--pack-destination", str(tmp_path)],
                         cwd=source, env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    [packed] = json.loads(run.stdout)

    # Compare to Git blobs, not to the copy handed to npm. npm always includes root metadata files.
    paths = _git("ls-tree", "-r", "-z", "--name-only", tree, "--", SKILLS.as_posix()).decode().split("\0")
    expected = {path for path in paths if path}
    assert any(path.endswith("/SKILL.md") for path in expected)
    with tarfile.open(tmp_path / packed["filename"], "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        assert len(names) == len(members), "npm tarball has a duplicate member"
        assert names == {f"package/{path}" for path in expected | {"package.json", "README.md", "LICENSE"}}
        for member in members:
            assert member.isfile() and not (member.mode & 0o111), member.name
        for path in sorted(expected):
            assert archive.extractfile(f"package/{path}").read() == _git("show", f"{tree}:{path}"), path
