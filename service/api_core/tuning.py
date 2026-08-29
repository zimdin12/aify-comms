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

# `\Z` for the same reason as `SAFE_NAME_RE`: `$` also matches before a trailing newline, so
# `"${HERMES_SESSION_ID}\n"` would NOT have been recognised as a placeholder and would have been kept
# as a real session handle. Its only caller strips first, so this was latent rather than live — which
# is exactly why it is worth closing: the guard must not depend on a caller it cannot see.
_SHELL_PLACEHOLDER_HANDLE_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?\Z")

STUCK_STOPPING_GRACE_SECONDS = 900  # a 'stopping' PTY that never reached 'stopped' is wedged

#: How many events a terminal keeps, and therefore how many `GET /terminals/{id}` can return.
#:
#: ONE NUMBER, TWO OWNERS, until 2026-08-29. `reconcilers/terminal_history.py` pruned to a local
#: `keep_events_per_terminal = 200` and `routers/terminals.py` read with a hardcoded `LIMIT 200`.
#: They agreed by coincidence, and the coincidence is load-bearing in both directions: raise the
#: pruner alone and the extra history is unreachable, raise the reader alone and it asks for rows the
#: pruner has already deleted.
#:
#: MEASURED on the operator's database, 2026-08-29: 21 of 26 terminals sit AT OR OVER 200 events, and
#: two hold 208 and 209 -- the pruner runs on the 60s sweep and a busy console outruns it -- so those
#: two responses were already truncating with nothing in them saying so.
TERMINAL_EVENTS_KEPT_PER_TERMINAL = 200

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


# MOVED HERE IN v0.5.4 when the analytics router split in two: the fleet handlers and the
# per-agent one both read it, and a constant read by two modules that do not import each other
# needs a home that neither owns. `tuning.py` imports NOTHING, so it cannot participate in a
# cycle — the same reason `LIVE_SESSION_STATUSES` landed here.
# Analytics data-quality ceiling (2026-06-19). NOT a status timer — used only by the
# work-minutes analytics. Dispatch runs go queued→claimed→completed, and a run that is
# claimed but then abandoned/stuck is force-closed by a 24h reaper, leaving a completed row
# whose claimed→finished span is ~24h of NON-work. Counting COALESCE(started_at, claimed_at)→
# finished for those (a regression in 93f44df) inflated "working total" to absurd values
# (sc-architect showed 909h). A real worked span — even a long autonomous run — never
# approaches this; anything above it is a reaped/stuck run and contributes 0 worked minutes.
WORKED_SPAN_CEILING_SECONDS = 4 * 3600


# MOVED HERE IN v0.5.4 when `reconcilers/spawn_lifecycle.py` split: the orphan reaper and the
# superseded-spawn reaper both read it, and a constant shared by two modules that do not import
# each other needs a home neither owns. Same reason as WORKED_SPAN_CEILING_SECONDS above.
SPAWN_ORPHAN_GRACE_SECONDS = 180  # matches the dispatch queued-run backstop window
