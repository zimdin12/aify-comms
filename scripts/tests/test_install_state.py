"""Run the real readers in disposable homes; no host credentials or daemons."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")


class InstallStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aify-onboarding-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.repo = self.home / "repo"
        shutil.copytree(REPO / "scripts", self.repo / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_", "HERDR_", "HERMES_", "CODEX_", "CLAUDE_MCP_")) and k != "API_KEY"}
        for key in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
            self.env[key] = self.home.as_posix()
        self.env.update(AIFY_ENV_FILE=(self.home / "empty.env").as_posix(),
                        AIFY_BIN_DIR=self.bin.as_posix(), MSYS_NO_PATHCONV="1")
        # Shell collaborators only. Even an accidental bare launch is recorded and refused.
        for name in ("aify-env", "aify-comms", "docker", "curl", "npm"):
            self.fake(name, 'printf "%s\\n" "$0 $*" >> "$HOME/calls"; exit 1')

    def fake(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8", newline="\n")
        p.chmod(0o755)
        return p

    def run_script(self, script="install-state.sh", *args):
        # /usr/bin is interpreted by Bash, never passed to native Python as a path.
        done = subprocess.run([BASH, "-c", 'fixture_bin="$1"; command -v cygpath >/dev/null && fixture_bin="$(cygpath -u "$fixture_bin")"; export PATH="$fixture_bin:/usr/bin:/bin"; shift; exec bash "$@"',
                               "fixture", self.bin.as_posix(), (self.repo / "scripts" / script).as_posix(), *args],
                              env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def state(self):
        return json.loads(self.run_script("install-state.sh", "--json").stdout)

    def test_hermes_hook_uses_explicit_profile_root(self):
        profile = self.home / "active profile"
        profile.mkdir()
        (profile / "config.yaml").write_text("hooks: notify-check\n")
        self.env["HERMES_HOME"] = profile.as_posix()
        report = self.state()
        self.assertIn("hermes", report["hooks"].split())
        self.assertEqual(report["hookStates"]["hermes"], "installed")

    @unittest.skipUnless(os.name == "nt", "native Windows path syntax")
    def test_hermes_hook_accepts_native_windows_profile_path(self):
        root = self.home / "native profile"
        root.mkdir()
        (root / "config.yaml").write_text("hooks: notify-check\n")
        self.env["HERMES_HOME"] = str(root)
        self.assertEqual(self.state()["hookStates"]["hermes"], "installed")

    def test_container_carries_installer_helpers(self):
        dockerfile = (REPO / "Dockerfile").read_text()
        self.assertIn("COPY scripts/ ./scripts/", dockerfile)

    def test_hermes_hook_uses_existing_command_resolver(self):
        root = self.home / "resolved"
        root.mkdir()
        (root / "config.yaml").write_text("hooks: notify-check\n")
        self.fake("custom-hermes", 'test "$*" = "config path" || exit 90; printf "%s/resolved/config.yaml\\n" "$HOME"')
        self.env["AIFY_HERMES_COMMAND"] = "custom-hermes"
        self.assertEqual(self.state()["hookStates"]["hermes"], "installed")

    def test_unresolved_root_does_not_claim_absent_even_with_legacy_config(self):
        root = self.home / ".hermes"
        root.mkdir()
        (root / "config.yaml").write_text("hooks: notify-check\n")
        report = self.state()
        self.assertEqual(report["hookStates"]["hermes"], "unknown")
        self.assertIn("hermes", report["hooksUnknown"].split())

    def test_failed_root_probe_with_output_stays_unknown(self):
        self.fake("custom-hermes", 'printf "%s/config.yaml\\n" "$HOME"; exit 2')
        self.env["AIFY_HERMES_COMMAND"] = "custom-hermes"
        self.assertEqual(self.state()["hookStates"]["hermes"], "unknown")

    def test_resolved_empty_root_reports_absent(self):
        self.env["HERMES_HOME"] = (self.home / "empty").as_posix()
        self.assertEqual(self.state()["hookStates"]["hermes"], "absent")

    def test_hook_probe_error_is_unknown(self):
        (self.repo / "scripts/hook-installed.sh").write_text("exit 2\n")
        self.assertEqual(set(self.state()["hookStates"].values()), {"unknown"})

    def test_hermes_hook_recognizes_installer_registration(self):
        root = self.home / "profile"
        (root / "agent-hooks").mkdir(parents=True)
        script = root / "agent-hooks/aify-notify.sh"
        script.write_text("node ~/.aify-comms/bridge/notify-check.js\n")
        (root / "config.yaml").write_text(
            'hooks:\n  post_tool_call:\n    - matcher: "*"\n'
            f'      command: bash "{script.as_posix()}"\n      timeout: 10000\n')
        self.env["HERMES_HOME"] = root.as_posix()
        self.assertEqual(self.state()["hookStates"]["hermes"], "installed")

    def test_real_hook_reader_error_stays_unknown(self):
        root = self.home / ".claude"
        root.mkdir()
        (root / "settings.json").write_text('{"command":"notify-check.js"}')
        self.fake("grep", 'case "$*" in *settings.json*) exit 2;; esac; exec /usr/bin/grep "$@"')
        self.assertEqual(self.state()["hookStates"]["claude"], "unknown")

    def test_real_hook_reader_no_match_stays_absent(self):
        root = self.home / ".claude"
        root.mkdir()
        (root / "settings.json").write_text('{"command":"unrelated-hook"}')
        self.assertEqual(self.state()["hookStates"]["claude"], "absent")

    def test_credential_conflict_is_unknown_without_disclosing_key(self):
        secret = "fixture-secret-not-for-report"
        self.env["AIFY_API_KEY"] = secret
        (self.home / "empty.env").write_text("API_KEY=other-fixture-value\n")
        done = self.run_script("install-state.sh", "--json")
        self.assertNotIn(secret, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["apiKey"], "unknown")

    def test_missing_path_command_is_not_full_herdr_absence(self):
        report = self.state()["herdr"]
        self.assertEqual(report["installed"], "unknown")
        self.assertEqual(report["onPath"], False)

    def test_off_path_windows_install_is_found_without_launching_it(self):
        app = self.home / "Programs/Herdr/bin"
        app.mkdir(parents=True)
        (app / "herdr.exe").write_text("not executable code")
        report = self.state()["herdr"]
        self.assertEqual(report["installed"], "installed")
        self.assertEqual(report["runnable"], "unknown")
        self.assertEqual(report["version"], "unknown")
        self.assertFalse(report["onPath"])

    def test_herdr_version_probe_is_explicit_and_bounded(self):
        self.fake("herdr", 'printf "%s\\n" "$*" >> "$HOME/herdr-calls"; test "$*" = "--version" || exit 90; printf "herdr 0.9.0\\n"')
        self.assertEqual(self.state()["herdr"]["runnable"], "unknown")
        self.assertFalse((self.home / "herdr-calls").exists())
        report = json.loads(self.run_script("herdr-state.sh", "--probe").stdout)
        self.assertEqual(report["runnable"], "yes")
        self.assertEqual(report["version"], "0.9.0")
        self.assertEqual((self.home / "herdr-calls").read_text().splitlines(), ["--version"])

    def test_broken_herdr_is_installed_but_not_runnable(self):
        self.fake("herdr", 'exit 126')
        report = json.loads(self.run_script("herdr-state.sh", "--probe").stdout)
        self.assertEqual(report["installed"], "installed")
        self.assertEqual(report["runnable"], "no")
        self.assertEqual(report["version"], "unknown")

    def test_explicit_missing_herdr_directory_scopes_absence(self):
        self.env["HERDR_INSTALL_DIR"] = (self.home / "chosen").as_posix()
        report = self.state()["herdr"]
        self.assertEqual(report["installed"], "missing")
        self.assertEqual(report["scope"], "configured-directory")

    def test_failed_host_probes_are_unknown_not_absent(self):
        (self.bin / "aify-env").unlink()
        report = self.state()
        self.assertEqual(report["aifyEnv"], "unknown")
        self.assertEqual(report["container"], "unknown")
        self.assertEqual(report["serviceHealth"], "unknown")
        self.assertNotIn("aify-env", (self.home / "calls").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
