"""Tuning constants the control plane declared but never read.

NINE CONSTANTS AND NINE BORROW SHIMS, retired together in v0.5.4. Each of these was declared in
`service/control_plane.py` and read by one or two OTHER modules through a function-scope
`from service.control_plane import ...` — the borrow pattern the reconcilers' docstrings record as
deferred debt. The control plane never read any of them itself: grep found exactly one occurrence of
each in that file, its own definition. It was their declaration site and nothing more.

The reasoning is already written in that file beside `_TERMINAL_*_STATUSES` when those moved — *"the
carrier reads it" was never a reason to own a constant, it is a reason to import one*. Here the
carrier did not even read them, so there was not even that.

Each keeps the prose that explains its VALUE, because a bare number is the thing most likely to be
"tidied" to a rounder one by someone who cannot see what it is sized against.
"""
from __future__ import annotations

import re

_RUNTIME_CONFIG_LIVE_KEYS = {
    "appServerUrl",
    "remoteAuthTokenEnv",
    "gatewayUrl",
    "gatewayTokenEnv",
    "channelEnabled",
}

_SESSION_DELETE_ALLOWED_STATUSES = {"stopped", "failed", "lost", "ended", "completed", "cancelled"}

# Workstream B2 (2026-06-01): grace before a managed claude with a LIVE sidecar
# but a DEAD console PTY is treated as a headless orphan worker. Must exceed the
# 30s liveness beat + console startup so a transiently-restarting console (PTY
# respawn between beats) is never falsely reaped.
MANAGED_ORPHAN_GRACE_SECONDS = 90

_SHELL_PLACEHOLDER_HANDLE_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")

STUCK_STOPPING_GRACE_SECONDS = 900  # a 'stopping' PTY that never reached 'stopped' is wedged

LIST_AGENTS_REFRESH_LIMIT = 8

_UNTHREADED_HANDOFF_WINDOW_MS = 24 * 60 * 60 * 1000

_CONSOLE_TAIL_MAX_LINES = 200

_CONSOLE_TAIL_MAX_BYTES = 16 * 1024
