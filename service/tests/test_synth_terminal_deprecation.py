"""Plan 4 synth-terminal deprecation: when managed_via_wrapper is on for a
runtime, synth terminal_session row must NOT be created. The wrapper PTY
IS the terminal. Synth stays only for opencode + hard-failure fallback.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))


def test_synth_skipped_for_wrapper_backed_runtimes():
    from service.routers.api_v2 import _synth_terminal_should_be_created
    settings_on = {"managed_via_wrapper": ["codex", "hermes", "pi"]}
    assert _synth_terminal_should_be_created("codex", settings_on) is False
    assert _synth_terminal_should_be_created("hermes", settings_on) is False
    assert _synth_terminal_should_be_created("pi", settings_on) is False


def test_synth_still_created_for_opencode():
    from service.routers.api_v2 import _synth_terminal_should_be_created
    settings_on = {"managed_via_wrapper": ["codex", "hermes", "pi"]}
    assert _synth_terminal_should_be_created("opencode", settings_on) is True


def test_synth_used_when_wrapper_setting_off():
    from service.routers.api_v2 import _synth_terminal_should_be_created
    settings_off = {"managed_via_wrapper": False}
    assert _synth_terminal_should_be_created("codex", settings_off) is True
    assert _synth_terminal_should_be_created("hermes", settings_off) is True
    assert _synth_terminal_should_be_created("pi", settings_off) is True
