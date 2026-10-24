"""The standalone installer carries an existing synthetic key and records its launcher.

THE DEFECT, measured 2026-09-02. `install.sh` resolved the service key on every run -- clients got
it, because `scripts/api-key.sh` reads `.env` -- but the hand-off to aify-env's credential store sat
inside `if [ "$WITH_API_KEY" = true ]`. That flag asks for a key to be GENERATED; it never meant
"this host has no key". So an operator who set `API_KEY` by hand and re-ran the installer got
configured clients and an aify-env with no credential at all.

WHAT THAT LOOKS LIKE FROM OUTSIDE, which is why it went unnoticed: every advertisement to
`/environments/heartbeat` is refused with 401, `advertising` stays false, the aify-comms bridge
correctly keeps describing the host, and aify-env reports healthy throughout. The operator sees
spawns refusing and no environment online, with nothing anywhere naming a credential. It is the same
shape `scripts/api-key.sh` exists to fix one layer up: a key in `.env` the installer would not
propagate, where the obvious remedy -- run the installer again -- makes no difference.

THIS RUNS THE INSTALLER, it does not read it. The existing install tests are static-text checks, and
a static check on this would have passed the whole time the defect existed: the carry line was
present and correct, in a branch that never ran. The only thing that distinguishes the two is
executing the path.

Each class runs from its own tracked-source copy, installed dependencies and synthetic .env.
No checkout .env or gitignored build/spec is copied. Missing installed dependencies fail rather than make
the credential cases look passed. The npm stub accepts local dependency maintenance only because
the fixture already holds the installed tree; global operations and platform writes are denied.

These cases cover --standalone, credential-carrier.sh and its credential status readback, not the
default guided chain. That chain's handoff is covered by the bridge's
a-plain-install-hands-off-to-the-chain-once.test.js; its connect/store ordering by aify-wrapper's
connect-registry-and-launchers.test.js. No default-chain end-to-end claim is made here.

SANDBOXED THREE WAYS, AND THE SANDBOX IS ASSERTED RATHER THAN ASSUMED. An earlier version set `HOME`
and `AIFY_HOME` only -- and `install.sh` registers the MCP server by invoking `claude mcp add`, whose
CLI resolves its own config path from `USERPROFILE` on Windows and ignored both. The installer duly
rewrote the operator's REAL `~/.claude.json` with a server path inside this test's temporary
directory, which `tearDown` then deleted; every later session found aify-comms pointing at nothing.

So: `claude` and `aify-env` are both stubs on PATH, so the real CLIs are never reached;
`HOME`, `USERPROFILE` and `AIFY_HOME` all point into the temp root. The real config file is searched
only for this run's unique path and is never restored over another session's writes. Native config
roots and inherited service/credential inputs are sealed as well.

The stubs are what the assertions read: a credential carried is a credential COMMAND ISSUED, and the
argv proves it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

#: Search the real config only for this fixture's path. Never restore another session's bytes.
REAL_CLAUDE_CONFIG = Path.home() / ".claude.json"
REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"

#: The stub records argv here. A fixed path rather than one passed through the environment: the
#: carrier runs the command in its own context, and a log path that arrived as an exported variable
#: silently produced an empty write and a stub that looked like it had never been called.
STUB = """#!/usr/bin/env bash
echo "CALLED: $*" >> "{log}"
if [ "$1 $2" = "credential set" ]; then cat > "{stdin}"; fi
echo "aify-comms-testref.key"
"""

#: A `claude` that records its argv and does nothing else. Without it `install.sh` reaches the real
#: CLI, which resolves its config from USERPROFILE and writes the operator's machine.
CLAUDE_STUB = """#!/usr/bin/env bash
echo "CLAUDE: $*" >> "{log}"
exit 0
"""


def bash() -> str | None:
    return shutil.which("bash")


@unittest.skipIf(bash() is None, "bash is required to run install.sh")
@unittest.skipUnless(INSTALL_SH.is_file(), "install.sh is missing")
class InstallCarriesTheKeyTests(unittest.TestCase):
    SYNTHETIC_KEY = "standalone-installer-fixture-not-an-operator-key"
    #: ONE INSTALLER RUN FOR THE WHOLE CLASS, because it is the same run every assertion reads.
    #: Executing `install.sh` costs about a minute, and this file ran it twice to ask two questions
    #: about one recorded behaviour -- which is the shape T1 exists to remove: cost without coverage.
    #: The state asserted below is a LOG OF WHAT HAPPENED, so sharing it across tests shares
    #: evidence rather than mutable state.
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.root = Path(cls._tmp.name)
        cls.log = cls.root / "calls.log"
        cls.stdin = cls.root / "credential.stdin"
        cls._copy_source()
        binaries = cls.root / "bin"
        binaries.mkdir()
        log = str(cls.log).replace(chr(92), "/")
        # Forward slashes: these paths are read by bash, and a Windows separator inside a shell
        # redirection is an escape sequence rather than a directory.
        for name, body in (("aify-env", STUB), ("claude", CLAUDE_STUB)):
            stub = binaries / name
            stub.write_text(body.format(log=log, stdin=cls.stdin.as_posix()), encoding="utf-8", newline=chr(10))
            stub.chmod(0o755)
        cls.binaries = binaries
        cls._stub_platform_commands()
        #: THE SEAL IS A SEARCH, NOT A HASH -- corrected 2026-09-03, and the old version was doing
        #: real damage. It recorded the file's BYTES and failed if they differed afterwards, on the
        #: premise that only `install.sh` could have changed them. That premise is false on this
        #: machine: every live Claude Code session rewrites `~/.claude.json` continuously, so the
        #: comparison fired on other people's writes -- and then "restored" a thirty-second-old
        #: snapshot over them. A test that intermittently reverts the operator's live config is
        #: worse than the leak it was watching for.
        #:
        #: What a leak actually looks like is specific and cannot be produced by anyone else: an MCP
        #: server entry pointing INSIDE this test's temp root, a path that did not exist until a
        #: moment ago. So that is what is searched for, and nothing else is touched.
        cls._leak_marker = str(cls.root).replace(chr(92), "/")
        cls.result = cls._install()

    @classmethod
    def _copy_source(cls):
        cls.fixture_repo = cls.root / "repo"
        tracked = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z"],
            capture_output=True, check=True, timeout=60,
        ).stdout.decode("utf-8").split("\0")
        # Build-owned ignored inputs come from their policy, not a second filename list.
        cls.ignored_service_inputs = {
            line.strip().removeprefix("/")
            for line in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip().removeprefix("/").startswith("service/")
        }
        if ("install.sh" not in tracked or ".env" in tracked or not cls.ignored_service_inputs
                or any(Path(relative).match(pattern)
                       for relative in tracked for pattern in cls.ignored_service_inputs)):
            raise AssertionError("unsafe installer source list")
        for relative in filter(None, tracked):
            source = REPO / relative
            if not source.resolve().is_relative_to(REPO.resolve()):
                raise AssertionError("installer source escaped its checkout")
            target = cls.fixture_repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        dependencies = REPO / "mcp" / "stdio" / "node_modules"
        if not dependencies.is_dir():
            raise AssertionError("run npm ci in mcp/stdio before the standalone installer tests")
        shutil.copytree(dependencies, cls.fixture_repo / "mcp" / "stdio" / "node_modules")
        (cls.fixture_repo / ".env").write_text(
            "API_KEY=" + cls.SYNTHETIC_KEY + "\n", encoding="utf-8", newline="\n",
        )

    @classmethod
    def _stub_platform_commands(cls):
        # npm acquisition is outside this test. The real installed SDK and native addon still load.
        prefix = cls.binaries
        package = prefix / "node_modules" / "aify-env" / "bin"
        package.mkdir(parents=True)
        (package / "aify-env.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8", newline="\n")
        npm = '#!/usr/bin/env bash\ncase "$*" in\n'
        npm += '"root -g") printf \'%s\\n\' "' + (prefix / "node_modules").as_posix() + '";;\n'
        npm += '"prefix -g") printf \'%s\\n\' "' + prefix.as_posix() + '";;\n'
        npm += '"install --silent") exit 0;;\n*) echo "unexpected fixture npm operation" >&2; exit 97;;\nesac\n'
        (prefix / "npm").write_text(npm, encoding="utf-8", newline="\n")
        (prefix / "npm").chmod(0o755)
        for name in ("powershell.exe", "pwsh.exe", "reg.exe", "icacls", "icacls.exe", "curl", "docker"):
            stub = prefix / name
            stub.write_text('#!/usr/bin/env bash\necho "' + name + '" >> "'
                            + (cls.root / "blocked.log").as_posix() + '"\nexit 97\n',
                            encoding="utf-8", newline="\n")
            stub.chmod(0o755)

    @classmethod
    def _environment(cls, home=None):
        home = Path(home) if home else cls.root
        keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "PROGRAMFILES",
                "PROGRAMFILES(X86)", "PROGRAMDATA"}
        env = {key: value for key, value in os.environ.items() if key.upper() in keep}
        env["PATH"] = str(cls.binaries) + os.pathsep + env.get("PATH", "")
        paths = {"HOME": home, "USERPROFILE": home, "APPDATA": home / "AppData/Roaming",
                 "LOCALAPPDATA": home / "AppData/Local", "XDG_CONFIG_HOME": home / ".config",
                 "TEMP": home / "tmp", "TMP": home / "tmp", "TMPDIR": home / "tmp",
                 "AIFY_HOME": home / ".aify-comms"}
        for key, path in paths.items():
            path.mkdir(parents=True, exist_ok=True)
            env[key] = str(path)
        env["AIFY_ENV_FILE"] = str(cls.fixture_repo / ".env")
        env["AIFY_SERVICE_REGISTRY"] = str(home / ".aify" / "services.json")
        return env

    @classmethod
    def tearDownClass(cls):
        # THE SEAL IS CHECKED BEFORE THE CLEANUP, and that ordering is load-bearing. It ran after
        # until 2026-09-02, so a cleanup that raised -- which it does on Windows whenever the
        # installer leaves a handle open in the temp tree -- skipped the check entirely. The one
        # safety assertion in this file was reachable only when nothing else went wrong.
        #
        # And the cleanup no longer raises: a temp directory this test cannot delete is housekeeping,
        # not a result. It errored the whole class in the full suite while passing in isolation,
        # which is the most expensive shape a test can have -- green alone, red together, and neither
        # about the thing under test.
        try:
            if REAL_CLAUDE_CONFIG.is_file():
                # READ AS TEXT AND SEARCHED FOR THIS RUN'S OWN PATH. Both separator forms, because
                # `claude mcp add` writes whichever the shell handed it and a search for one finds
                # nothing when the other was used -- a seal that cannot see the leak is the shape
                # this whole file exists to refuse.
                current = REAL_CLAUDE_CONFIG.read_text(encoding="utf-8", errors="replace")
                windows_form = cls._leak_marker.replace("/", chr(92))
                leaked = cls._leak_marker in current or windows_form in current
                if leaked:
                    # NOT RESTORED FROM A SNAPSHOT. Other sessions have almost certainly written to
                    # this file since, and overwriting them to undo our own entry trades a small
                    # mess for a larger one. The failure is loud and names the path to remove, which
                    # is a repair a human can make safely and a test cannot.
                    raise AssertionError(
                        "install.sh wrote the REAL ~/.claude.json despite the sandbox: it contains "
                        f"{cls._leak_marker!r}, which is this test's temp root and is about to be "
                        "deleted. The stubs no longer cover every path the installer takes there. "
                        "Remove any MCP server entry naming that path -- it has NOT been auto-"
                        "restored, because other live sessions write this file and a snapshot "
                        "restore would revert them."
                    )
        finally:
            # Best-effort, and last. A leftover temp directory is swept by the runner's own per-run
            # root; a failure here must never turn a passing class red.
            try:
                cls._tmp.cleanup()
            except OSError:
                pass

    @classmethod
    def _install(cls, *extra: str) -> subprocess.CompletedProcess:
        env = cls._environment()
        # POSIX-form PATH. A `C:/...` entry is silently ignored by this shell, so the stub is never
        # found and every probe falls through to the real binary -- which reads as "the installer did
        # not carry the key" no matter what the installer does.
        posix_bin = "/" + str(cls.binaries).replace(":", "").replace("\\", "/")
        env["PATH"] = posix_bin + ":" + os.environ.get("PATH", "")
        probe = subprocess.run(
            [bash(), "-c", "command -v aify-env; command -v claude; command -v npm; command -v intentionally-missing-installer-fixture-command"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        normalize = lambda text: text.replace(":", "").replace("\\", "/").lstrip("/").casefold()
        expected = [normalize(str(cls.binaries / name)) for name in ("aify-env", "claude", "npm")]
        if probe.returncode != 1 or list(map(normalize, probe.stdout.splitlines())) != expected:
            raise AssertionError("fixture commands were not selected: " + probe.stdout)
        return subprocess.run(
            [bash(), str(cls.fixture_repo / "install.sh"), "--standalone", "--client", "claude", "http://127.0.0.2:1", *extra],
            cwd=str(cls.fixture_repo), env=env, capture_output=True, text=True, timeout=900,
        )

    def calls(self) -> str:
        try:
            return self.log.read_text(encoding="utf-8")
        except OSError:
            return ""

    def test_the_standalone_fixture_owns_its_credential_inputs(self):
        """A checkout's missing or poisoned ambient .env/spec cannot qualify this fixture."""
        self.assertNotEqual(self.fixture_repo, REPO)
        self.assertEqual((self.fixture_repo / ".env").read_text(encoding="utf-8"),
                         "API_KEY=" + self.SYNTHETIC_KEY + "\n")
        for pattern in sorted(self.ignored_service_inputs):
            with self.subTest(ignored_service_input=pattern):
                self.assertFalse(list(self.fixture_repo.glob(pattern)), "a build-owned ignored input was copied")
        self.assertFalse((self.root / "blocked.log").exists(), "an unexpected platform operation was attempted")

    def test_THE_SEAL_FIRES_ON_A_LEAK_AND_NOT_ON_SOMEBODY_ELSES_WRITE(self):
        """The seal's own control, added 2026-09-03 after it spent an unknown period firing wrongly.

        It used to compare the file's BYTES before and after. On this machine every live Claude Code
        session rewrites `~/.claude.json` continuously, so it fired on other people's writes -- and
        then "restored" a thirty-second-old snapshot over them. A test that intermittently reverts
        the operator's live config is worse than the leak it was watching for, and it reproduced
        roughly one run in three.

        A real leak is specific and nobody else can produce it: a path inside THIS run's temp root,
        which did not exist a moment ago. This asserts both directions, because a seal that cannot
        say no is not evidence when it says yes."""
        marker = type(self)._leak_marker
        self.assertTrue(marker, "the seal has no marker, so it can never detect anything")
        self.assertIn(tempfile.gettempdir().replace(chr(92), "/").lower(), marker.lower(),
                      "the marker must be this run's own temp root, not a general path")

        # PRESENT: a config naming this run's temp root, in either separator form.
        for form in (marker, marker.replace("/", chr(92))):
            self.assertIn(form, '{"mcpServers":{"aify-comms":{"args":["' + form + '/x.js"]}}}',
                          "the search cannot see the leak in this separator form")

        # ABSENT: an ordinary config, and one an unrelated session rewrote. Neither is a leak.
        for innocent in ('{"mcpServers":{}}', '{"mcpServers":{"other":{"args":["C:/elsewhere/x.js"]}}}'):
            self.assertNotIn(marker, innocent)
            self.assertNotIn(marker.replace("/", chr(92)), innocent)

    def test_the_stub_is_reachable(self):
        """CONTROL. Without this, an unreachable stub makes every assertion below vacuous -- and that
        is exactly what happened while this test was being written: a Windows-form PATH entry was
        ignored, the real binary answered, and the log stayed empty for the wrong reason."""
        result = subprocess.run(
            [bash(), "-c", "command -v aify-env"],
            env={**os.environ, "PATH": "/" + str(self.binaries).replace(":", "").replace("\\", "/")
                 + ":" + os.environ.get("PATH", "")},
            capture_output=True, text=True, timeout=60,
        )
        self.assertIn(str(self.binaries.name), result.stdout.replace("\\", "/"),
                      f"the stub is not what `aify-env` resolves to: {result.stdout.strip()!r}")

    def test_standalone_carries_an_existing_key_without_asking_for_one(self):
        """THE DEFECT. No `--with-api-key`: the key is already on the host, and it must still reach
        the tier that needs it."""
        self.assertIsNotNone(self.result, "the installer did not run, so this proves nothing")
        self.assertEqual(self.result.returncode, 0, f"installer failed: {self.result.stdout[-2000:]}\n{self.result.stderr[-2000:]}")
        self.assertIn(
            "credential set --service aify-comms --stdin", self.calls(),
            "the installer did not hand the key to aify-env, so its advertisements will 401 and no "
            "environment will come online -- with nothing naming a credential anywhere",
        )
        self.assertEqual(self.stdin.read_text(encoding="utf-8"), self.SYNTHETIC_KEY)

    def test_standalone_also_reads_the_credential_back(self):
        """The carrier verifies through the public path rather than trusting its own write. A store
        that accepted the key and could not return it is the failure that leaves everything looking
        configured."""
        self.assertIn("credential status --service aify-comms", self.calls())

    def test_standalone_records_its_launcher_so_a_connect_can_re_render_it(self):
        """THE SAME INSTALLER RUN, a second question about it: does it leave the record `aify-wrapper connect` re-renders
        from? It wrote none until 2026-10-21, so a connect reported every launcher it rendered stale and left it on the old
        registry. The record must name the launcher natively (Node reads it), and replaying it must re-render that launcher
        for a changed registry while a launcher with no record is reported stale and left byte for byte."""
        self.assertEqual(self.result.returncode, 0, f"installer failed: {self.result.stdout[-2000:]}\n{self.result.stderr[-2000:]}")
        root = type(self).root
        records = root / ".aify" / "launchers"
        launcher = root / ".local" / "bin" / "claude-aify"
        self.assertTrue(launcher.is_file(), "CONTROL: the install rendered its launcher where this test looks")
        self.assertTrue(records.is_dir(), "the install left no render record beside its registry")
        targets = [path.read_bytes().split(b"\0")[2].decode() for path in records.glob("*.args")]
        self.assertIn("TARGET=" + launcher.as_posix(), targets, "the record does not name the launcher by its native path")

        # A launcher no installer recorded, and a registry the launcher was not built against.
        unrecorded = launcher.with_name("codex-aify")
        shutil.copyfile(launcher, unrecorded)
        before = {path.name: path.read_bytes() for path in (launcher, unrecorded)}
        registry = root / ".aify" / "services.json"
        changed = json.loads(registry.read_text(encoding="utf-8"))
        self.assertIn("aify-comms", changed["services"], "CONTROL: the install registered itself in this registry")
        changed["services"]["other"] = {"endpoint": "http://127.0.0.2:2"}
        registry.write_text(json.dumps(changed, indent=2) + "\n", encoding="utf-8", newline="\n")
        wrapper = root / ".aify-comms" / "mcp" / "stdio" / "node_modules" / "aify-wrapper"
        script = ("import { pathToFileURL } from 'node:url';"
                  "const [lib, dest, registryPath, wrapperRoot] = process.argv.slice(1);"
                  "const { rerenderLaunchers } = await import(pathToFileURL(lib).href);"
                  "console.log(JSON.stringify(rerenderLaunchers({ dest, registryPath, wrapperRoot })));")
        replay = subprocess.run(
            ["node", "--input-type=module", "-e", script, str(wrapper / "lib" / "launcher-rerender.mjs"),
             str(launcher.parent), str(registry), str(wrapper)],
            capture_output=True, text=True, timeout=300, env=self._environment())
        self.assertEqual(replay.returncode, 0, replay.stderr)
        rows = {row["name"]: row["state"] for row in json.loads(replay.stdout)}
        self.assertEqual(rows, {"claude-aify": "re-rendered", "codex-aify": "stale"})
        self.assertNotEqual(launcher.read_bytes(), before["claude-aify"], "the recorded launcher was not rebuilt for the new registry")
        self.assertEqual(unrecorded.read_bytes(), before["codex-aify"], "the launcher with no record was touched")

    def test_emit_wrappers_renders_and_records_nothing(self):
        """The render-only hook every launcher test uses keeps the caller's HOME, so a record it wrote would land in the
        operator's real ~/.aify/launchers. It renders into the directory it was given and leaves nothing beside a registry."""
        with tempfile.TemporaryDirectory() as home:
            out = Path(home) / "out"
            result = subprocess.run(
                [bash(), str(self.fixture_repo / "install.sh"), "--client", "claude", "http://127.0.0.2:1", "--emit-wrappers", out.as_posix()],
                cwd=str(self.fixture_repo), capture_output=True, text=True, timeout=600,
                env=self._environment(home))
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
            self.assertTrue((out / "claude-aify").is_file(), "CONTROL: the hook rendered its launcher")
            self.assertFalse((Path(home) / ".aify" / "launchers").exists(), "--emit-wrappers wrote a render record")


if __name__ == "__main__":
    unittest.main()
