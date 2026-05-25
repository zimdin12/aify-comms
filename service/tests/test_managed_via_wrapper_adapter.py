"""Regression: _managed_via_wrapper_for_runtime now consults the adapter's
preferred_delivery_mode. Pi is no longer hardcoded-excluded (Plan 2 pi flip).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.routers.api_v2 import _managed_via_wrapper_for_runtime


def test_pi_is_now_eligible_for_managed_via_wrapper():
    # Setting respects True; pi adapter declares managed-via-wrapper as preferred
    settings = {"managed_via_wrapper": True}
    assert _managed_via_wrapper_for_runtime(settings, "pi") is True


def test_pi_list_form_includes_pi():
    settings = {"managed_via_wrapper": ["pi"]}
    assert _managed_via_wrapper_for_runtime(settings, "pi") is True


def test_pi_setting_off_still_returns_false():
    settings = {"managed_via_wrapper": False}
    assert _managed_via_wrapper_for_runtime(settings, "pi") is False


def test_claude_still_excluded():
    # Claude is wrapper-backed via claude-channel.js — not via this flag
    settings = {"managed_via_wrapper": True}
    assert _managed_via_wrapper_for_runtime(settings, "claude-code") is False


def test_codex_hermes_unchanged():
    settings = {"managed_via_wrapper": True}
    assert _managed_via_wrapper_for_runtime(settings, "codex") is True
    assert _managed_via_wrapper_for_runtime(settings, "hermes") is True


def test_runtime_with_preferred_managed_not_wrapper():
    # opencode adapter declares preferred_delivery_mode = "managed" (not
    # "managed-via-wrapper"), so it stays out even when the setting is True.
    settings = {"managed_via_wrapper": True}
    assert _managed_via_wrapper_for_runtime(settings, "opencode") is False
