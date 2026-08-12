"""Plan 4 default settings flip — wrapper-backed delivery is now the default."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.api_core.settings import DEFAULT_SETTINGS


def test_managed_via_wrapper_defaults_to_codex_hermes_only():
    assert DEFAULT_SETTINGS["managed_via_wrapper"] == ["codex", "hermes"], (
        f"Plan 4 default flip: expected [codex,hermes], got {DEFAULT_SETTINGS['managed_via_wrapper']}"
    )


def test_managed_pty_eager_spawn_defaults_to_true():
    assert DEFAULT_SETTINGS["managed_pty_eager_spawn"] is True
