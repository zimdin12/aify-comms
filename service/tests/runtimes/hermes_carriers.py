"""Every ambient input hermes session-discovery can read, in ONE place, with a guard against drift.

WHY THIS EXISTS. `test_hermes_session_discovery.py` sealed five env vars and `Path.home()`;
`test_per_adapter.py` sealed ONE. Both test the same function. The second pair of tests passed here and
FAILED in a reviewer's live hermes environment, resolving the operator's real session id instead of the
fixture's expectation — because `HERMES_TUI_ACTIVE_SESSION_FILE` (added to the adapter on 2026-08-17 to
mirror the JS side) is exported by hermes' own TUI and nothing in that file deleted it.

The lesson is not "seal harder in each test". It is that the seal list and the product's read surface must
not be able to drift apart: adding a carrier to the adapter has to break the suite until the list catches
up. `test_carrier_list_matches_the_adapter` below is that gate, and it reads the adapter's SOURCE rather
than trusting this tuple.
"""

from __future__ import annotations

import re
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[2]
HERMES_ADAPTER = SERVICE_ROOT / "runtimes" / "hermes.py"

# Every environment variable `HermesAdapter.discover_session_id` (and what it calls) can read.
HERMES_SESSION_CARRIER_ENV = (
    "AIFY_HERMES_ACTIVE_SESSION_FILE",
    "HERMES_TUI_ACTIVE_SESSION_FILE",
    "HERMES_SESSION_ID",
    "HERMES_SESSION",
    "AIFY_HERMES_GATEWAY_URL",
)


def seal_hermes_session_carriers(monkeypatch, tmp_path) -> Path:
    """Delete every carrier and point `Path.home()` at a fresh directory. Asserts the seal held.

    Returns the sealed home, so a test that wants the sessions-directory fall-through can write into it.
    """
    for name in HERMES_SESSION_CARRIER_ENV:
        monkeypatch.delenv(name, raising=False)
    import os
    leaked = [name for name in HERMES_SESSION_CARRIER_ENV if os.environ.get(name)]
    assert not leaked, f"the env seal did not take for: {leaked}"

    home = tmp_path / "sealed-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert Path.home() == home, "the home seal did not take"
    return home


def carrier_env_names_in_adapter_source() -> set[str]:
    """Env names the adapter reads, taken from its source rather than from this module's own list."""
    source = HERMES_ADAPTER.read_text(encoding="utf-8")
    names = set(re.findall(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"', source))
    # `session_env_vars` is a class attribute the base class reads through `get_current_session_id`, so it
    # is a carrier even though it never appears as an `os.environ.get` literal in this file.
    declared = re.search(r"session_env_vars\s*=\s*\[([^\]]*)\]", source)
    if declared:
        names |= set(re.findall(r'"([A-Z0-9_]+)"', declared.group(1)))
    return names
