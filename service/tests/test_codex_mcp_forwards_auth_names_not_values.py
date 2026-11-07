"""The real Codex TOML writer forwards the auth pointer and token names, never their values.

The existing host-minted-binding test covers install.sh calling this writer. This test pins the
missing auth-name projection, without a runtime launch, credential lookup or product installation.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CodexMcpForwardsAuthNamesNotValues(unittest.TestCase):
    def test_the_rendered_allowlist_carries_both_auth_names_and_no_secret_values(self):
        with tempfile.TemporaryDirectory(prefix='codex-auth-allowlist-') as temporary:
            home = Path(temporary)
            config = home / 'config.toml'
            config.write_text('[mcp_servers.aify-comms]\ncommand = "node"\nenv_vars = ["OLD_NAME"]\n')
            token = 'fixture-token-must-not-be-written-to-toml'
            url = 'ws://127.0.0.1:18888/fixture-not-a-server'
            env = {name: value for name, value in os.environ.items()
                   if name.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
            env.update({name: str(home) for name in
                        ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'TMPDIR')})
            env.update(AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV='AIFY_CODEX_APP_SERVER_TOKEN',
                       AIFY_CODEX_APP_SERVER_TOKEN=token, AIFY_CODEX_APP_SERVER_URL=url)
            result = subprocess.run(
                ['node', str(ROOT / 'scripts' / 'codex-mcp-env-vars.cjs'), str(config)],
                env=env, capture_output=True, text=True, timeout=15, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = config.read_text()
            forwarded = tomllib.loads(rendered)['mcp_servers']['aify-comms']['env_vars']
            self.assertIn('AIFY_CODEX_APP_SERVER_URL', forwarded, 'control: writer did not emit its existing names')
            for name in ('AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV', 'AIFY_CODEX_APP_SERVER_TOKEN'):
                with self.subTest(required=name):
                    self.assertIn(name, forwarded, f'Codex MCP child loses auth carrier {name}')
            for label, value in (('token', token), ('url', url)):
                with self.subTest(not_serialized=label):
                    self.assertNotIn(value, rendered, f'{label} value was written instead of its variable name')
            self.assertTrue(all(isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
                                for name in forwarded), 'env_vars must contain names only')
