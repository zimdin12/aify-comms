"""Pin that _default_console_command emits `--resume <handle>` for all runtimes
that support it once the handle is stored. The codex carve-out (removed in
Plan 1 of the RuntimeAdapter refactor) is the primary regression target.

This goes through `_default_console_command`, the session-row wrapper, rather than the adapters
directly (those are `service/tests/runtimes/test_console_command.py`): it is the only test that proves the stored
`session_handle` reaches the adapter at all."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.api_core.capabilities import _default_console_command

#: (runtime, handle, interactive, substrings that must appear, substrings that must not).
CASES = [
    # claude: managed answers for itself (--auto); both modes resume a known handle.
    ("claude-code", "h1", False, ["claude-aify", "--aify-agent a", "--auto", "--resume h1"], []),
    ("claude-code", "h1", True, ["claude-aify --aify-agent a", "--resume h1"], ["--auto"]),
    # codex: Plan 1 dropped the carve-out, so managed AND interactive resume; no handle, no resume.
    ("codex", "thread-uuid", False, ["codex-aify", "--aify-agent a", "--resume thread-uuid"], []),
    ("codex", "thread-uuid", True, ["codex-aify --aify-agent a", "--resume thread-uuid"], []),
    ("codex", "", False, ["codex-aify --aify-agent a"], ["--resume"]),
    ("hermes", "hh", False, ["hermes-aify --aify-agent a", "--resume hh"], []),
    # pi: managed resumes; interactive stays fresh on purpose (the 026H control-sequence trap).
    ("pi", "omp-uuid", False, ["pi-aify --aify-agent a", "--resume omp-uuid"], []),
    ("pi", "omp-uuid", True, ["pi-aify --aify-agent a"], ["--resume"]),
]


def test_the_console_command_resumes_a_stored_handle_where_the_runtime_supports_it():
    for runtime, handle, interactive, present, absent in CASES:
        session = {"agent_id": "a", "session_handle": handle, "runtime": runtime}
        cmd = _default_console_command(session, "/tmp", interactive=interactive)
        case = f"{runtime} handle={handle!r} interactive={interactive}: {cmd!r}"
        for part in present:
            assert part in cmd, f"{part!r} missing -- {case}"
        for part in absent:
            assert part not in cmd, f"{part!r} present -- {case}"
