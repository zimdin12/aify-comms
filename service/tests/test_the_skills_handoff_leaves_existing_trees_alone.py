"""Exercise the real installer mode and skill-writer blocks in owned homes only."""
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "install.sh"
WRITER = REPO / "scripts" / "install-skill-tree.mjs"


def _bash_executable():
    if os.name != "nt":
        return shutil.which("bash") or pytest.skip("Bash is required")
    git = shutil.which("git")
    roots = Path(git).resolve().parents if git else ()
    for parent in roots:
        candidate = parent / "usr" / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    pytest.skip("Git Bash is required for native Windows paths")


def bash(tmp, script, *args, **values):
    env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT"}}
    env.update(HOME=tmp.as_posix(), USERPROFILE=tmp.as_posix(), MSYSTEM="MINGW64", **values)
    run = subprocess.run([_bash_executable(), "-s", "--", *args], input=script.encode(), cwd=REPO, env=env,
                         capture_output=True, timeout=15)
    return subprocess.CompletedProcess(run.args, run.returncode, run.stdout.decode(), run.stderr.decode())


@pytest.mark.parametrize(("args", "code", "notice"), [
    (["--assets-only"], 78, "requires Env to place skills first"),
    (["--acquire-only", "--skills-placed-v1"], 64, "only for --assets-only"),
    (["--standalone", "--skills-placed-v1"], 64, "only for --assets-only"),
    (["--skills-placed-v1"], 64, "only for --assets-only"),
    (["--assets-only", "--standalone", "--skills-placed-v1"], 64, "choose one install mode"),
])
def test_wrong_mode_refuses_before_the_installer_body(tmp_path, args, code, notice):
    prefix = INSTALLER.read_text(encoding="utf-8").split("# Default the server URL", 1)[0]
    run = bash(tmp_path, prefix + '\nprintf "BODY-REACHED\\n"\n', *args)
    assert run.returncode == code, run.stdout + run.stderr
    assert notice in run.stderr
    assert "BODY-REACHED" not in run.stdout


@pytest.mark.parametrize(("client", "homevar", "relative"), [
    ("claude", "CLAUDE_CONFIG_DIR", "skills"),
    ("codex", "CODEX_HOME", "skills"),
    ("hermes", "HERMES_HOME", "skills/autonomous-ai-agents"),
])
def test_only_standalone_populates_the_runtime_skill_root(tmp_path, client, homevar, relative):
    script = INSTALLER.read_text(encoding="utf-8")
    prefix = script.split("# Default the server URL", 1)[0]
    functions = "\n".join(re.search(rf"^{name}\(\) \{{.*?^\}}", script, re.M | re.S)[0]
                          for name in ("is_git_bash_windows", "path_for_node", "install_skill_tree",
                                       "install_standalone_skills"))
    guidance = script[script.index('echo "[2/4]'):script.index('echo "[3/4]')]
    root, home = shlex.quote(REPO.as_posix()), shlex.quote(tmp_path.as_posix())
    guard = f'\n[ "$(path_for_node "$HOME")" = "$(path_for_node {home})" ] || exit 99\n'
    run_guidance = prefix + f'\nSCRIPT_DIR={root}\n' + functions + guard + (
        'copy_claude_assets() { echo non-skill-claude; }; copy_hermes_assets() { echo non-skill-hermes; }\n'
    ) + guidance
    profile = tmp_path / (".claude" if client == "claude" else "profile with spaces")
    values = {} if client == "claude" else {homevar: profile.as_posix()}
    skills = profile / relative
    guided = bash(tmp_path, run_guidance, "--client", client, "--assets-only", "--skills-placed-v1", **values)
    assert guided.returncode == 0, guided.stderr
    if client != "codex":
        assert f"non-skill-{client}" in guided.stdout
    assert not skills.exists(), "the guided assets step wrote skills itself"
    standalone = bash(tmp_path, run_guidance, "--client", client, "--standalone", **{homevar: profile.as_posix()})
    assert standalone.returncode == 0, standalone.stderr
    source = REPO / (".claude" if client == "claude" else ".agents") / "skills"
    contents = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert contents
    assert {p.relative_to(skills): p.read_bytes() for p in skills.rglob("*") if p.is_file()} == contents


def test_standalone_preserves_identical_different_and_linked_trees(tmp_path):
    source, dest, outside = (tmp_path / part for part in ("source", "dest", "outside"))
    for name in ("new", "identical", "different", "linked"):
        skill = source / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_bytes(b"incoming\n")
    for skill, data in ((dest / "identical", b"incoming\n"), (dest / "different", b"owned\n"),
                        (outside, b"outside\n")):
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_bytes(data)
    keep = dest / "different" / "untracked.txt"
    keep.write_bytes(b"keep\n")
    unchanged = dest / "identical" / "SKILL.md"
    stamp = unchanged.stat().st_mtime_ns
    link = dest / "linked"
    subprocess.run(["node", "-e", "require('node:fs').symlinkSync(process.argv[1],process.argv[2],"
                    "process.platform==='win32'?'junction':'dir')", str(outside), str(link)], check=True)
    run = subprocess.run(["node", str(WRITER), str(source), str(dest)], capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stderr
    assert (dest / "new" / "SKILL.md").read_bytes() == b"incoming\n"
    assert unchanged.stat().st_mtime_ns == stamp
    assert (dest / "different" / "SKILL.md").read_bytes() == b"owned\n"
    assert keep.read_bytes() == b"keep\n"
    assert link.resolve() == outside.resolve()
    assert (outside / "SKILL.md").read_bytes() == b"outside\n"
    assert "different" in run.stderr and "linked" in run.stderr
