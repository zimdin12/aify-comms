"""Full installer phases against v0.5.6-produced state, never the host home.

Run with AIFY_UPGRADE_DEPS pointing at an existing node_modules directory.
AIFY_UPGRADE_EVIDENCE retains disposable homes and complete transcripts.
Network/npm, PowerShell PATH writes and runtime launches are stubbed. Native Node,
Git, filesystem copies, old/current config/hook/plugin/registry producers are real.
Claude/Codex MCP CLI persistence is outside this test; Hermes YAML is produced by
both actual installers. No service or environment daemon is launched.
"""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
DEPS = os.environ.get("AIFY_UPGRADE_DEPS")


@unittest.skipUnless(DEPS, "set AIFY_UPGRADE_DEPS to installed dependencies")
class LegacyUpgradeTests(unittest.TestCase):
    def test_rendered_check_with_shell_native_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            bridge = home / "bridge/mcp/stdio"
            bridge.mkdir(parents=True)
            (bridge / "server.js").write_text("console.log('parse only');\n")
            env = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_", "HERMES_", "CODEX_", "CLAUDE_"))}
            env.update(HOME=home.as_posix(), USERPROFILE=home.as_posix(), AIFY_NO_PROMPT="1", MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*", AIFY_WRAPPER_TEMPLATE_DIR=(Path(DEPS) / "aify-wrapper/wrappers").as_posix())
            native = (home / "bridge").as_posix()
            if os.name == "nt":
                native = subprocess.check_output([BASH, "-c", 'cygpath -u "$1"', "fixture", native], text=True).strip()
            env["AIFY_HOME"] = native
            # Codex render exits before native shim PATH writes.
            done = subprocess.run([BASH, "-c", 'function powershell.exe() { return 0; }; export -f powershell.exe; exec bash "$@"', "fixture", str(REPO / "install.sh"), "--client", "codex", "http://127.0.0.2:1", "--emit-wrappers", home.as_posix()], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(done.returncode, 0, done.stderr)
            done = subprocess.run([BASH, str(home / "aify-comms"), "--check"], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_old_producers_to_current(self):
        root = Path(tempfile.mkdtemp(prefix="legacy-upgrade-", dir=os.environ.get("AIFY_UPGRADE_EVIDENCE")))
        if not os.environ.get("AIFY_UPGRADE_EVIDENCE"):
            self.addCleanup(shutil.rmtree, root)
        old = root / "old"
        current = root / "current"
        old.mkdir(); current.mkdir()
        archive = subprocess.check_output(["git", "-C", str(REPO), "archive", "v0.5.6"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(old, filter="data")
        for name in ("install.sh", "VERSION", ".git"):
            shutil.copy2(REPO / name, current / name)
        for name in ("scripts", "mcp", "integrations", ".agents", ".claude", ".claude-plugin"):
            shutil.copytree(REPO / name, current / name, ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
        # Read-only dependency junction; npm is stubbed and cannot mutate it.
        for repo in (old, current):
            dest = repo / "mcp/stdio/node_modules"
            if os.name == "nt":
                subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(dest), str(Path(DEPS).resolve())], check=True, capture_output=True)
            else:
                dest.symlink_to(Path(DEPS).resolve(), target_is_directory=True)
        home = root / "home"; home.mkdir()
        bins = root / "bin"; bins.mkdir()
        profile = home / "profile"; profile.mkdir()
        config = profile / "config.yaml"
        operator = 'model: operator-model\nmcp_servers:\n  other-mcp:\n    command: operator-tool\n'
        config.write_text(operator, encoding="utf-8")
        key = "fixture-upgrade-key-0123456789abcdef"
        envfile = home / "operator.env"
        envfile.write_text('OPERATOR_SETTING=keep\nAPI_KEY=' + key + '\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_", "HERMES_", "HERDR_", "CODEX_", "CLAUDE_")) and k not in ("API_KEY", "CREDENTIAL_REF")}
        for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            env[name] = home.as_posix()
        env.update(HERMES_HOME=profile.as_posix(), CODEX_HOME=(home / "codex").as_posix(),
                   AIFY_HOME=(home / "bridge").as_posix(), AIFY_ENV_FILE=envfile.as_posix(),
                   AIFY_NO_PROMPT="1", AIFY_HERMES_COMMAND="hermes", AIFY_HERMES_INSTALL_ROOT=(home / "no-source").as_posix(),
                   AIFY_API_KEY=key, AIFY_SERVICE_REGISTRY=(home / "services.json").as_posix(),
                   AIFY_HERMES_LEGACY_SOURCE_PATCH="0")
        if os.name == "nt":
            env["AIFY_HOME"] = subprocess.check_output([BASH, "-c", 'cygpath -u "$1"', "fixture", env["AIFY_HOME"]], text=True).strip()
        node = shutil.which("node")
        git = shutil.which("git")
        for name, body in {
            "npm": 'exit 0', "curl": 'exit 7', "powershell.exe": 'exit 0',
            "docker": 'exit 1',
            "hermes": 'test "$*" = "config path" || exit 90; printf "%s/config.yaml\\n" "$HERMES_HOME"',
            "claude": 'test "$1" = mcp || exit 90; exit 0',
            "codex": 'test "$1" = mcp || exit 90; exit 0',
            "node": 'exec "' + Path(node).as_posix() + '" "$@"',
            "git": 'exec "' + Path(git).as_posix() + '" "$@"',
        }.items():
            p = bins / name
            p.write_text('#!/bin/bash\n' + body + '\n', newline="\n")
            p.chmod(0o755)
        def run(label, repo, *args, disabled=False):
            child = env.copy()
            child.pop("MSYS_NO_PATHCONV", None); child.pop("MSYS2_ARG_CONV_EXCL", None)
            if disabled:
                child.update(MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
            done = subprocess.run([BASH, "-c", 'b="$1"; command -v cygpath >/dev/null && b="$(cygpath -u "$b")"; export PATH="$b:/usr/bin:/bin"; shift; exec bash "$@"', "fixture", bins.as_posix(), (repo / "install.sh").as_posix(), *args], cwd=repo, env=child, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            (root / (label + ".log")).write_text(done.stdout + done.stderr, encoding="utf-8")
            self.assertEqual(done.returncode, 0, label + ': ' + done.stderr[-2000:])
            return done.stdout + done.stderr
        endpoint = "http://127.0.0.2:1"
        for client in ("claude", "codex", "hermes"):
            run("old-" + client, old, "--client", client, endpoint, "--with-hook")
        self.assertEqual(json.loads((home / "bridge/mcp/stdio/package.json").read_text())["version"], "0.5.6")
        old_config = config.read_text()
        self.assertIn('AIFY_SERVER_URL: "' + endpoint + '"', old_config)
        self.assertIn('  other-mcp:', old_config)
        self.assertNotIn('AIFY_API_KEY:', old_config)  # actual v0.5.6 omission
        self.assertIn('--environment-bridge', (home / ".local/bin/aify-comms").read_text())
        shutil.copy2(config, root / "old-produced-config.yaml")
        # Other service is an operator addition, not a fabricated legacy comms entry.
        other = {"endpoint": "http://127.0.0.3:1", "endpointEnv": [], "keyEnv": [], "mcp": []}
        registry = Path(env["AIFY_SERVICE_REGISTRY"])
        registry.write_text(json.dumps({"version": 1, "services": {"other-service": other}}))
        before_env = envfile.read_bytes()
        for disabled in (False, True):
            for client in ("claude", "codex", "hermes"):
                out = run(f"current-{disabled}-{client}", current, "--client", client, endpoint, disabled=disabled)
                self.assertNotIn("failed node --check", out)
                self.assertIn("aify-env", out)
            updated = config.read_text()
            self.assertIn('model: operator-model', updated)
            self.assertIn('  other-mcp:', updated)
            self.assertIn(key, updated)
            self.assertEqual(envfile.read_bytes(), before_env)
            services = json.loads(registry.read_text())["services"]
            self.assertEqual(services["other-service"], other)
            self.assertEqual(services["aify-comms"]["endpoint"], endpoint)
            self.assertTrue(Path(services["aify-comms"]["mcp"][0]["args"][0]).is_file())
            stamp = (home / "bridge/.aify-version").read_text()
            self.assertIn(subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(), stamp)
            check = subprocess.run([BASH, str(home / ".local/bin/aify-comms"), "--check"], env={**env, "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*"}, capture_output=True, text=True)
            (root / f"check-{disabled}.log").write_text(check.stdout + check.stderr)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self._assert_standalone_doctor(home, env)
        print("Retained upgrade evidence:", root)

    def _assert_standalone_doctor(self, home, env):
        # Run the alias emitted by the full installer, not a hand-written launcher.
        # Only its owned doctor target is replaced: no real health/auth/process probes.
        alias = home / ".local/bin/aify-doctor"
        self.assertTrue(alias.is_file(), "installer must still emit aify-doctor")
        doctor = home / "bridge/mcp/stdio/doctor.js"
        doctor.write_text(
            "console.log(JSON.stringify({argv: process.argv.slice(1), "
            "noPathConv: process.env.MSYS_NO_PATHCONV, "
            "argConvExcl: process.env.MSYS2_ARG_CONV_EXCL}));\n",
            encoding="utf-8", newline="\n")
        args = ["--json", "--strict", "argument with spaces", "", "/literal/argument"]
        done = subprocess.run(
            [BASH, str(alias), *args],
            env={**env, "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*"},
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        (home.parent / "standalone-doctor.log").write_text(done.stdout + done.stderr, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["argv"][0].replace("\\", "/"), doctor.as_posix())
        self.assertEqual(report["argv"][1:], args)
        self.assertEqual(report["noPathConv"], "1")
        self.assertEqual(report["argConvExcl"], "*")


if __name__ == "__main__":
    unittest.main(verbosity=2)
