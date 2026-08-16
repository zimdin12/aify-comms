r"""The gate that refuses a Windows drive-letter cwd on a POSIX host missed the backslash form.

`_validate_registration_cwd` refuses a codex live-app-server RESIDENT agent whose cwd cannot exist on
its host: a Windows drive path on linux/darwin/wsl, or a `/mnt/...` WSL path on win32. Its message
says "not a Windows drive-letter path".

    _WINDOWS_DRIVE_CWD_RE = ^[a-zA-Z]:/

That matches `C:/repo` and NOT `C:\repo` — the canonical Windows drive-letter path, and the very
thing the message names. So a codex live agent on linux registering `C:\repo` passed validation, and
the app-server received a directory that does not exist on that host: the failure this gate exists to
prevent. Same shape as the bridge's agent-id parsers missing the dot (d10cd6bf) — a pattern that does
not cover every spelling of what it rejects.

The refusal was one of 41 operator-facing 4xx messages in the service that no test had exercised.

BACKSLASHES IN THIS FILE ARE BUILT FROM A CODEPOINT, deliberately. While investigating I "confirmed"
the defect with a shell heredoc whose `\\` collapsed to `\`, making Python read `\r` as a CARRIAGE
RETURN — so the string under test was `C:` + CR + `epo`, which neither the old nor the new pattern
matches. The reading was right and the measurement was not. `chr(92)` cannot be mangled by anything.
"""

from __future__ import annotations

import unittest

from fastapi import HTTPException

from service.api_core.registration_gates import _validate_registration_cwd

BACKSLASH_CWD = "C:" + chr(92) + "repo"
FORWARD_CWD = "C:/repo"
WSL_CWD = "/mnt/c/repo"
LIVE_APP_SERVER = {"appServerUrl": "ws://127.0.0.1:55555"}


def _check(machine_id, cwd, *, runtime="codex", session_mode="resident", config=LIVE_APP_SERVER):
    _validate_registration_cwd(
        agent_id="sc-codex",
        runtime=runtime,
        session_mode=session_mode,
        machine_id=machine_id,
        cwd=cwd,
        runtime_config=config,
    )


def _refusal(machine_id, cwd) -> str:
    try:
        _check(machine_id, cwd)
    except HTTPException as exc:
        assert exc.status_code == 400, exc.status_code
        return str(exc.detail)
    raise AssertionError(f"{cwd!r} on {machine_id} was admitted; expected a refusal")


class RegistrationCwdGateTests(unittest.TestCase):
    # ── a Windows path on a POSIX host, in BOTH spellings ────────────────────────────────────

    def test_a_windows_drive_cwd_is_refused_on_posix_hosts_either_way(self):
        for family in ("linux:box", "darwin:box", "wsl:box"):
            for label, cwd in (("forward", FORWARD_CWD), ("backslash", BACKSLASH_CWD)):
                with self.subTest(family=family, spelling=label):
                    detail = _refusal(family, cwd)
                    self.assertIn("Invalid cwd", detail)
                    self.assertIn("sc-codex", detail, "the operator needs the agent named")

    def test_the_posix_refusal_suggests_a_native_path_for_that_family(self):
        # The full static tails, so the refusal-coverage gate can see that these two are tested —
        # it matches the longest literal chunk, and `"/mnt/<drive>/"` alone is a different fragment.
        self.assertIn('", not a Windows drive-letter path.', _refusal("linux:box", BACKSLASH_CWD))
        self.assertIn("/mnt/<drive>/", _refusal("linux:box", BACKSLASH_CWD))
        self.assertIn("/mnt/<drive>/", _refusal("wsl:box", BACKSLASH_CWD))
        self.assertIn("/Users/", _refusal("darwin:box", BACKSLASH_CWD))

    # ── a WSL path on Windows ───────────────────────────────────────────────────────────────

    def test_a_wsl_mount_path_is_refused_on_windows(self):
        detail = _refusal("win32:box", WSL_CWD)
        self.assertIn("Invalid cwd", detail)
        self.assertIn('"C:/repo"', detail, "the fix must be shown, not just the fault")
        # The full static tail, so `test_every_refusal_is_exercised.py` can see this one is tested:
        # it matches the longest literal chunk of the message, and the fragments above are shorter.
        self.assertIn(
            '" on Windows. Use forward-slash drive-letter form like "C:/repo", not a "/mnt/..." WSL path.',
            detail,
        )

    def test_windows_accepts_both_drive_spellings(self):
        """The mirror of the first test: on win32 a drive path is CORRECT, in either separator. A
        pattern widened to catch backslashes must not start refusing them on their own platform."""
        for cwd in (FORWARD_CWD, BACKSLASH_CWD):
            with self.subTest(cwd=cwd):
                self.assertIsNone(_check("win32:box", cwd))

    # ── native paths pass everywhere ────────────────────────────────────────────────────────

    def test_a_native_posix_path_is_accepted(self):
        for family in ("linux:box", "darwin:box", "wsl:box", "win32:box"):
            with self.subTest(family=family):
                self.assertIsNone(_check(family, "/srv/repo"))

    def test_a_wsl_mount_path_is_accepted_on_wsl_and_linux(self):
        self.assertIsNone(_check("wsl:box", WSL_CWD))
        self.assertIsNone(_check("linux:box", WSL_CWD))

    # ── the gate's scope, which the widened pattern must not enlarge ────────────────────────

    def test_the_gate_only_applies_to_codex_resident_with_a_live_app_server(self):
        """It exists for the codex app-server's AbsolutePathBuf, so it must stay off every other
        path. A cwd check that started refusing hermes or managed registrations would break
        registrations that were never its subject."""
        self.assertIsNone(_check("linux:box", BACKSLASH_CWD, runtime="hermes"))
        self.assertIsNone(_check("linux:box", BACKSLASH_CWD, session_mode="managed"))
        self.assertIsNone(_check("linux:box", BACKSLASH_CWD, config={}))
        self.assertIsNone(_check("linux:box", BACKSLASH_CWD, config=None))

    def test_an_unknown_machine_family_is_not_second_guessed(self):
        """`machineId` may be absent or unrecognised. The gate can only compare a path against a
        family it knows, and refusing on a guess would block a legitimate registration."""
        self.assertIsNone(_check("", BACKSLASH_CWD))
        self.assertIsNone(_check("something-odd", BACKSLASH_CWD))

    def test_an_empty_cwd_is_not_refused(self):
        self.assertIsNone(_check("linux:box", ""))
        self.assertIsNone(_check("linux:box", "   "))
