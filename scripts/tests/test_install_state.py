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

    def docker_like_real_docker(self, present_running=(), present_all=()):
        """A docker stub that answers the way the REAL daemon answered when measured.

        Grounded in an observation, not invented: on Docker 29.5.3 a `name=` filter matches the
        container name WITHOUT a leading slash, so `^/aify-comms-service$` finds nothing while
        `^aify-comms-service$` and a bare substring both find it. That difference is the whole bug,
        and a stub that ignored it could not tell the broken filter from the fixed one.
        """
        running = " ".join(present_running)
        allc = " ".join(present_all)
        self.fake("docker", f"""
if [ "$1" != "ps" ]; then exit 1; fi
shift
all=0; filter=""
for arg in "$@"; do
  case "$arg" in
    -a) all=1 ;;
    name=*) filter="${{arg#name=}}" ;;
  esac
done
names="{running}"; [ "$all" = 1 ] && names="{allc}"
# A leading slash in the filter matches nothing, exactly as the real daemon behaved.
case "$filter" in "^/"*) exit 0 ;; esac
needle="$filter"; needle="${{needle#^}}"; needle="${{needle%$}}"
for n in $names; do case "$n" in *"$needle"*) printf '%s
' "$n" ;; esac; done
exit 0
""")

    def test_a_running_service_container_is_never_reported_absent(self):
        # THE REGRESSION: the filter carried a leading slash, so this said `absent` while the
        # container was up and `health: healthy` printed beside it.
        self.docker_like_real_docker(present_running=["aify-comms-service"], present_all=["aify-comms-service"])
        self.assertEqual(self.state()["container"], "running")

    def test_the_container_check_can_still_say_no(self):
        # Drive the control by REMOVING what it watches. Without this, a filter matching everything
        # would pass the test above and report `running` on a host with no container at all.
        self.docker_like_real_docker(present_running=[], present_all=[])
        self.assertEqual(self.state()["container"], "absent")
        # And a container that exists but is not up is neither of those.
        self.docker_like_real_docker(present_running=[], present_all=["aify-comms-service"])
        self.assertEqual(self.state()["container"], "stopped")

    def test_another_projects_container_is_not_mistaken_for_ours(self):
        # The filter is a substring, so the exactness has to come from the comparison. A host
        # running `aify-comms-service-staging` and nothing else has no service container here —
        # `absent`, because ours does not exist, running or otherwise. (This assertion first said
        # `stopped`; the code was right and the expectation was wrong.)
        self.docker_like_real_docker(present_running=["aify-comms-service-staging"], present_all=["aify-comms-service-staging"])
        self.assertEqual(self.state()["container"], "absent")

    def test_unparseable_registry_never_reports_absence_or_presence(self):
        registry = self.home / "services.json"
        self.env["AIFY_SERVICE_REGISTRY"] = registry.as_posix()
        for text in ('{broken', '{"aify-comms":', '{"version":99,"services":{}}'):
            with self.subTest(text=text):
                registry.write_text(text)
                report = self.state()
                self.assertEqual(report["registeredInRegistry"], "unknown")
                self.assertEqual(registry.read_text(), text)

    @unittest.skipUnless(os.environ.get("AIFY_UPGRADE_DEPS"), "set AIFY_UPGRADE_DEPS to installed dependencies")
    def test_real_registry_reader_distinguishes_valid_invalid_and_read_error(self):
        # The ordinary fixture has no Node/dependencies. Exercise the parser too,
        # with positive controls so an import/path failure cannot pass as UNKNOWN.
        stdio = self.repo / "mcp/stdio"
        parser = stdio / "node_modules/aify-wrapper/lib/registry.mjs"
        parser.parent.mkdir(parents=True)
        shutil.copy2(Path(os.environ["AIFY_UPGRADE_DEPS"]) / "aify-wrapper/lib/registry.mjs", parser)
        shutil.copy2(REPO / "mcp/stdio/service-name.mjs", stdio / "service-name.mjs")
        self.fake("node", 'exec "' + Path(shutil.which("node")).as_posix() + '" "$@"')
        registry = self.home / "registry with spaces.json"
        native = registry.as_posix()
        if os.name == "nt":
            native = subprocess.check_output([BASH, "-c", 'cygpath -u "$1"', "fixture", native], text=True).strip()
        self.env.update(AIFY_SERVICE_REGISTRY=native, MSYS2_ARG_CONV_EXCL="*")
        entry = {"endpoint": "http://127.0.0.2:1", "mcp": []}
        cases = [
            (json.dumps({"version": 1, "services": {"aify-comms": entry}}), "yes"),
            (json.dumps({"version": 1, "services": {"other": entry}}), "no"),
            ('{"version":1,"services":{}}', "no"),
            ('{broken', "unknown"),
            ('{"aify-comms":', "unknown"),
            ('{"version":99,"services":{}}', "unknown"),
            ('{"version":1,"services":{"aify-comms":{}}}', "unknown"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                registry.write_text(text)
                self.assertEqual(self.state()["registeredInRegistry"], expected)
                self.assertEqual(registry.read_text(), text)
        registry.unlink()
        registry.mkdir()  # Real fs.readFileSync error, not a replacement helper.
        self.assertEqual(self.state()["registeredInRegistry"], "unknown")
        registry.rmdir()
        registry.write_text(cases[0][0])
        parser.unlink()  # Import failure must not turn a valid entry into absence.
        self.assertEqual(self.state()["registeredInRegistry"], "unknown")

    def test_missing_registry_reports_no(self):
        self.env["AIFY_SERVICE_REGISTRY"] = (self.home / "absent.json").as_posix()
        self.assertEqual(self.state()["registeredInRegistry"], "no")

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
