"""Regression: _managed_via_wrapper_for_runtime consults adapter delivery mode.

Pi is explicitly excluded because OMP is single-client RPC and must keep
dashboard chat and Console on the same native managed controller.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.api_core.capabilities import _managed_via_wrapper_for_runtime


def test_pi_is_not_eligible_for_managed_via_wrapper():
    settings = {"managed_via_wrapper": True}
    assert _managed_via_wrapper_for_runtime(settings, "pi") is False


def test_pi_list_form_still_excludes_pi():
    settings = {"managed_via_wrapper": ["pi"]}
    assert _managed_via_wrapper_for_runtime(settings, "pi") is False


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
