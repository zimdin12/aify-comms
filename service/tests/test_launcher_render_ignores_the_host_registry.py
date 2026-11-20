"""The shared renderer must not consult a registry outside its fixture home."""
import subprocess

from service.tests._launchers import HERMES_RAN, INSTALL_SH, REPO, _render_env, bash, render


def test_shared_launcher_render_ignores_an_inherited_registry(tmp_path, monkeypatch):
    registry = tmp_path / 'invalid-registry.json'
    registry.write_text('{', encoding='utf-8')
    monkeypatch.setenv('AIFY_SERVICE_REGISTRY', registry.as_posix())
    endpoint = 'http://127.0.0.2:2'  # Separate from the shared cache's default key; not contacted.

    # The real installer honors an explicitly chosen registry. This must reach the parser;
    # a missing wrapper dependency or a failure earlier in the installer isn't the control.
    control_env = _render_env(tmp_path)
    control_env['AIFY_SERVICE_REGISTRY'] = registry.as_posix()
    control = subprocess.run(
        [bash(), INSTALL_SH.as_posix(), '--client', 'codex', endpoint,
         '--emit-wrappers', (tmp_path / 'control').as_posix()],
        cwd=REPO, env=control_env, capture_output=True, text=True, timeout=30,
    )
    assert control.returncode == 78, control.stdout + control.stderr
    assert 'invalid-registry.json' in control.stderr, control.stderr

    launchers = render('codex', url=endpoint)
    assert endpoint in launchers['codex-aify']
    assert registry.read_text(encoding='utf-8') == '{'


def test_the_render_stand_in_records_a_hermes_run(tmp_path):
    """The control for render()'s no-Hermes check: with the two fixture roots taken away, the
    installer looks Hermes up, finds only the stand-in, and the stand-in records that it ran."""
    env = _render_env(tmp_path)
    del env['HERMES_HOME']
    env['AIFY_HERMES_INSTALL_ROOT'] = (tmp_path / 'absent').as_posix()
    subprocess.run(
        [bash(), INSTALL_SH.as_posix(), '--client', 'hermes', 'http://127.0.0.2:1',
         '--emit-wrappers', (tmp_path / 'out').as_posix()],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=600,
    )
    assert 'config path' in (tmp_path / HERMES_RAN).read_text(encoding='utf-8')
