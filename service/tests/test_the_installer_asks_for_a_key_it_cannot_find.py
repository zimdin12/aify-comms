"""The installer asked for nothing when a host had no key, and every call then 401'd.

THE DEFECT, measured 2026-09-02. `install.sh` resolved the service key from the shell or `.env` and,
finding neither, carried on silently. A host was configured with no key at all; every advertisement
to the service was refused with 401; both sides reported healthy; and the operator lost a day to a
fleet that would not spawn, with no component anywhere naming a credential. An installer that
proceeds with a missing value is how that happens.

WHY ASKING IS NOT THE SAME AS GENERATING. `--generate` MINTS a key, which is right for a fresh host
with nobody to ask. Asking is right when a key already exists somewhere else -- another machine, a
password manager, a second service -- and only this host is missing it. Silence guessed neither, and
guessing wrong is worse than either: a generated key on a host that should have joined an existing
fleet 401s exactly as loudly as no key at all.

THESE TEST THE UNATTENDED BRANCH, which is the one nothing routine reaches. A prompt cannot be driven
here, but the property that matters when nobody is watching can be: with no terminal the installer
must ask nobody, print nothing, and exit 0 -- the same as the read-only path -- because CI, a service
manager and an agent all run it that way, and a script that hangs or errors there is worse than one
that says nothing.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API_KEY_SH = REPO / "scripts" / "api-key.sh"
INSTALL_SH = REPO / "install.sh"


def bash() -> str | None:
    return shutil.which("bash")


@unittest.skipIf(bash() is None, "bash is required")
@unittest.skipUnless(API_KEY_SH.is_file(), "scripts/api-key.sh is missing")
class TheInstallerAsksTests(unittest.TestCase):
    def _run(self, *args, env_file_contents: str = "") -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(env_file_contents, encoding="utf-8")
            env = dict(os.environ)
            # The SHELL half of the resolver is sealed too: an ambient key on the machine running
            # these tests would make every assertion below pass for the wrong reason.
            env.pop("CLAUDE_MCP_API_KEY", None)
            env.pop("AIFY_API_KEY", None)
            env["AIFY_ENV_FILE"] = str(env_file)
            return subprocess.run(
                [bash(), str(API_KEY_SH), *args],
                env=env, capture_output=True, text=True, timeout=60,
                stdin=subprocess.DEVNULL,
                # SEALING STDIN IS NOT SEALING THE TERMINAL. External review, Round 8 M16.
                #
                # `/dev/tty` is the CONTROLLING TERMINAL, not stdin: redirecting stdin from /dev/null
                # leaves it open, so under an interactive pytest `exec 3>/dev/tty` succeeds and the
                # prompt below it waits for a human until the 60-second timeout fires. The test then
                # reports a timeout in the resolver rather than "your terminal was borrowed".
                #
                # `start_new_session` calls setsid(), which detaches the child from the controlling
                # terminal so /dev/tty cannot be opened at all -- which is the unattended condition
                # these tests are ABOUT, made real instead of assumed. POSIX-only and ignored on
                # Windows, where this suite currently runs and the hazard does not arise.
                #
                # Same shape as this repo's own rule that unsetting an env var is not sealing an
                # input: the seal has to be on the thing the code actually reads.
                start_new_session=True,
            )

    def test_with_no_key_and_no_terminal_it_asks_nobody_and_says_nothing(self):
        """The unattended branch. A prompt written to a device that is not there prints
        '/dev/tty: No such device or address' on every run, which teaches an operator that errors
        there are normal -- and an installer that hangs waiting for an answer nobody can give is
        worse still."""
        result = self._run("--ask")
        self.assertEqual(result.stdout.strip(), "", "a key was produced from nowhere")
        self.assertEqual(result.stderr.strip(), "",
                         f"the unattended path printed: {result.stderr.strip()!r}")
        self.assertEqual(result.returncode, 0,
                         "no key and nobody to ask is not a failure; the caller decides what to do")

    def test_it_matches_the_read_only_path_exactly_when_there_is_nothing_to_ask(self):
        """CONTROL, and the reason the codes matter: `install.sh` calls this and treats a non-zero
        exit as a problem. If `--ask` diverged from the plain read, adding the ask would have turned
        every keyless install into a reported failure."""
        asked = self._run("--ask")
        plain = self._run()
        self.assertEqual((asked.returncode, asked.stdout.strip()),
                         (plain.returncode, plain.stdout.strip()))

    def test_a_key_already_present_is_returned_and_NOT_asked_about(self):
        """Re-running the installer is the update path. Asking again for a key the host already
        holds trains an operator to paste secrets nothing needed."""
        result = self._run("--ask", env_file_contents="API_KEY=a-key-that-is-long-enough-to-pass\n")
        self.assertEqual(result.stdout.strip(), "a-key-that-is-long-enough-to-pass")
        self.assertEqual(result.returncode, 0)

    def test_a_weak_key_is_reported_rather_than_refused_on_the_read_path(self):
        """Unchanged by the ask, and pinned while this file is being edited: a weak key already in
        use is the operator's running state -- the service is on it and every installed bridge holds
        it -- so aborting an ordinary install neither rotates it nor helps."""
        result = self._run("--ask", env_file_contents="API_KEY=short\n")
        self.assertEqual(result.stdout.strip(), "short")
        self.assertIn("WARNING", result.stderr)

    @unittest.skipUnless(INSTALL_SH.is_file(), "install.sh is missing")
    def test_the_installer_actually_calls_the_asking_path(self):
        """A helper proven alone leaves the call to it unproven, and this repo has shipped exactly
        that. `install.sh` cannot be executed here without a full install, so the call is read --
        which is weaker than running it, and is said so rather than dressed up."""
        # THROUGH THE DECIDER SINCE 2026-09-04, which is where `--ask` is now called from. The chain
        # is checked whole rather than one link: install.sh must reach the decider, and the decider
        # must reach the ask. Asserting only the first would pass on a decider that resolved nothing,
        # which is the same disconnected-call-site defect this docstring is about.
        decider = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "api-key-for-install.sh"
        self.assertIn("scripts/api-key-for-install.sh", INSTALL_SH.read_text(encoding="utf-8"),
                      "install.sh no longer reaches the script that resolves and decides, so a host "
                      "with no key is configured silently -- the defect this file exists for")
        self.assertIn('api-key.sh" --ask', decider.read_text(encoding="utf-8"),
                      "the decider no longer performs the ask, so nothing does")


    def test_STDOUT_CARRIES_THE_KEY_AND_NOTHING_ELSE(self):
        """Everything this script prints on stdout becomes the installed credential.

        The caller does `RESOLVED_API_KEY="$(...)"`, so a status line written to stdout would BE the
        key baked into every client on the host -- and the failure would present as every client
        401'ing after a successful-looking install. Caught while writing the script, and pinned here
        because the next person adding a message to it will reach for `echo`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("API_KEY=known-test-key-1234567890\n", encoding="utf-8")
            env = dict(os.environ)
            for name in ("API_KEY", "CLAUDE_MCP_API_KEY", "AIFY_API_KEY"):
                env.pop(name, None)
            env["AIFY_ENV_FILE"] = str(env_file)
            result = subprocess.run(
                [bash(), str(Path(__file__).resolve().parents[2] / "scripts" / "api-key-for-install.sh")],
                env=env, capture_output=True, text=True, timeout=60,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout, "known-test-key-1234567890",
            f"stdout carried more than the key: {result.stdout!r}. Whatever is here is what gets "
            "installed as the credential.",
        )

    def test_the_GENERATE_path_also_keeps_stdout_clean(self):
        """The branch that actually had the bug, which the test above does not reach.

        My first version of that test ran the ordinary resolve path and passed happily while the
        `--generate` branch printed a status line straight to stdout -- a mutation restoring the bug
        did not redden it. Two branches, two proofs; a test that exercises one and claims the
        property for both is the shape this suite keeps finding in its own scanners.

        `--generate` WRITES to `.env`, so the file is a sealed temp one: pointed at the real thing
        this would mint a key into the operator's environment.
        """
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("", encoding="utf-8")
            env = dict(os.environ)
            for name in ("API_KEY", "CLAUDE_MCP_API_KEY", "AIFY_API_KEY"):
                env.pop(name, None)
            env["AIFY_ENV_FILE"] = str(env_file)
            result = subprocess.run(
                [bash(), str(Path(__file__).resolve().parents[2] / "scripts" / "api-key-for-install.sh"),
                 "--generate"],
                env=env, capture_output=True, text=True, timeout=60,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(
            "API key in place", result.stdout,
            f"the generate path printed a status line on stdout: {result.stdout!r}. The caller "
            "captures stdout as the credential, so that text WOULD BE the key installed into every "
            "client on the host.",
        )
        self.assertTrue(result.stdout.strip(), "the generate path produced no key at all")
        self.assertNotIn(" ", result.stdout.strip(), "stdout carries something other than a bare key")


class TheInstallerActsOnWhatTheResolverSaysTests(unittest.TestCase):
    """`install.sh` must DECIDE on the resolver's answer, which `|| true` stopped it doing.

    EXTERNAL REVIEW, Round 8 H8. The tests above pin `api-key.sh --ask` answering 0 with no key when
    there is no `/dev/tty`, and that branch is right -- this repo's documented install path is a
    coding agent pointed at the checkout, which has no terminal. Their justification is "the caller
    decides what to do".

    THE CALLER WAS NOT DECIDING. `install.sh` wrapped the call in `|| true`, so every outcome became
    "no key": an unattended install finished with no key, no credential carrier and NOTHING SAID, and
    the same line swallowed exit 3 (the shell and `.env` name DIFFERENT keys) and exit 5 (a `.env`
    value that cannot be parsed the way Compose parses it). Both are conditions that install the
    wrong credential into every client on the host.

    READ AS SOURCE, deliberately: reaching these branches for real means running the installer, which
    rewrites this machine's MCP configuration and every wrapper on it. `--emit-wrappers` exists
    precisely because this file is not safe to execute for its side effects, and it exits long before
    this code.
    """

    INSTALL = Path(__file__).resolve().parents[2] / "install.sh"
    #: The decision moved out of `install.sh` on 2026-09-04. It went to a script beside
    #: `api-key.sh` for the reason that file gives -- the installer is already past the size gate,
    #: and a resolver decision is testable here without running something that rewrites this
    #: machine's MCP configuration.
    DECIDER = Path(__file__).resolve().parents[2] / "scripts" / "api-key-for-install.sh"

    def setUp(self):
        self.text = self.INSTALL.read_text(encoding="utf-8") + self.DECIDER.read_text(encoding="utf-8")

    def test_the_ask_is_no_longer_wrapped_in_or_true(self):
        self.assertNotIn(
            'api-key.sh" --ask || true', self.text,
            "`|| true` turns a CONFLICT and an UNPARSEABLE .env into 'no key, carry on' -- which is "
            "how the wrong credential gets baked into every client on the host",
        )

    def test_a_conflict_and_an_unparseable_env_both_STOP_the_install(self):
        # Exit 3 and exit 5 are the resolver saying the two sources disagree, or that it cannot read
        # one the way Compose will. Continuing past either installs a credential nobody chose.
        for code in ("3)", "5)"):
            self.assertIn(code, self.text, f"exit {code[0]} is no longer handled at the call site")
        self.assertIn(
            "Refusing to install clients whose credential could not be determined", self.text,
            "an unexpected resolver failure no longer stops the install. An installer that carries "
            "on past a failure in its own credential resolver is the shape this repo has been bitten "
            "by before",
        )

    def test_a_KEYLESS_install_says_so(self):
        # The whole finding is that this outcome was silent. It stays legal -- a loopback-only host
        # with no key is a supported configuration -- so it reports rather than refuses.
        self.assertIn(
            "The clients being installed will connect WITHOUT a credential", self.text,
            "an install that ends with no key is silent again. The operator learns about it when the "
            "service starts refusing every client that was just installed",
        )

    def test_the_generate_path_is_NOT_given_the_keyless_notice(self):
        # CONTROL. `--with-api-key` always produces a key, so printing 'no API key' there would be a
        # lie -- and a notice that fires on both paths is one an operator learns to ignore.
        # `--generate` RETURNS BEFORE the notice rather than being excluded by a condition: that
        # branch either produces a key or exits 1, so it can never reach a message about not having
        # one. A condition would have been a second rule to keep in step with the branch above it.
        self.assertIn('if [ "$MODE" = "--generate" ]; then', self.text,
                      "the generate path no longer has its own branch")
        generate_at = self.text.index('if [ "$MODE" = "--generate" ]; then')
        notice_at = self.text.index('echo "No API key: this host has none set')
        exit_at = self.text.index("  exit 0", generate_at)
        self.assertLess(exit_at, notice_at,
                        "the generate branch no longer returns before the keyless notice, so an "
                        "install that just made a key can be told it has none")


if __name__ == "__main__":
    unittest.main()
