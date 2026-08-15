"""Tuning constants the control plane declared but never read.

NINE CONSTANTS AND NINE BORROW SHIMS, retired together in v0.5.4. Each of these was declared in
`service/control_plane.py` and read by one or two OTHER modules through a function-scope
a function-scope import of the carrier — the borrow pattern the reconcilers' docstrings record as
deferred debt. (Spelled out in prose rather than quoted, because the tracked shim count is a grep for
that exact string and a docstring quoting it would inflate the number it exists to measure.) The control plane never read any of them itself: grep found exactly one occurrence of
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

# Moved here from service/reconcilers/sessions.py in v0.5.4. It lived in a reconciler while
# `api_core/liveness.py` needed it, which is an api_core leaf reaching UP for a constant — and that
# inversion forced `reconcilers/sessions.py` to import `_agent_liveness` inside a function body,
# because a module-level import would have closed the loop. This module imports nothing, so owning
# the constant here breaks the cycle and both imports are now ordinary module-level ones.
#
# Phase 3 (2026-06-03) — ONE canonical `agent_sessions.status` live set.
# This is the FULL set of agent_sessions.status values that count as a live
# (not-yet-terminal) session row, used by the session reconcilers
# (_reconcile_dead_session_status / _reconcile_duplicate_resident_sessions),
# the new on-read deriver (_compute_session_display_status), and embedded into
# the dashboard bootstrap config so Dashboard Next reads the SAME set instead
# of its own wider hardcode. It is a SUPERSET of _LIVE_SESSION_STATUSES, which
# lives in api_core/liveness.py:
# _LIVE_SESSION_STATUSES is the narrower "live agent-status engine" gate used by
# _compute_live_status_cache (which treats attached/active/idle as worker-detail
# rather than session-live), whereas this set is the session-row liveness set the
# reconcilers historically used as their inline `live_states` tuple. Keep these
# two distinct on purpose — collapsing them would change the agent-status engine.
# Members are EXACTLY the inline `live_states` tuple the two session reconcilers
# historically used, so adopting the constant is behavior-preserving for them.
LIVE_SESSION_STATUSES = {
    "running",
    "attached",
    "active",
    "idle",
    "starting",
    "recovering",
}
