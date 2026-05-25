"""Plan 4 Task 17: hermes-session-resume wake-mode removed. Hermes resident
agents use the gateway path (hermes-live wake-mode); fresh handles
captured via discoverSessionId."""

import subprocess


def test_hermes_session_resume_not_returned_as_wake_mode():
    """Plan 4: verify no live code path returns 'hermes-session-resume' as
    a wake_mode value. Comments referencing the deprecated mode are OK;
    string literal returned as wake mode is not."""
    out = subprocess.run(
        ["grep", "-rn", "hermes-session-resume", "service/routers/api_v2.py"],
        capture_output=True, text=True, cwd="C:/Docker/aify-comms"
    )
    for line in out.stdout.splitlines():
        # Skip pure-comment lines
        stripped = line.split(":", 2)[-1].strip()
        if stripped.startswith("#"):
            continue
        # Heuristic: if "return" + "hermes-session-resume" are on the same
        # line (or in a triple-quoted return value), that's the bug.
        if "return" in stripped and '"hermes-session-resume"' in stripped:
            raise AssertionError(
                f"hermes-session-resume still returned as wake mode: {line}"
            )
        if "return" in stripped and "'hermes-session-resume'" in stripped:
            raise AssertionError(
                f"hermes-session-resume still returned as wake mode: {line}"
            )
