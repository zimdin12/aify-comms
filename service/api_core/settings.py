"""Service settings: the defaults, the cache, and the single accessor.

v0.5.1g, and the reviewer's REVERSED ruling from v0.5 slice 7. Back then `_load_settings` was allowed
to stay router-owned and be borrowed, on the grounds that seaming it would change more than the
moment of a read. The measurement changed that: it is reached by twelve route domains, which makes
router ownership the problem rather than the pragmatic choice.

MOVED VERBATIM, AND THAT IS THE WHOLE CONSTRAINT. Same call sites, same call timing, same cache
semantics — including the `"pytest" not in sys.modules` bypass, which exists so tests get isolation
and set-then-read behaviour rather than a 5-second stale window. Hoisting settings reads to callers,
batching them per request, or changing the TTL would each be a behaviour change, and v0.5.x is
contracted to an empty behaviour changelog. None of that happened here.

`_SETTINGS_CACHE` is a PROCESS GLOBAL and moving it is the delicate part: a second module-level
assignment anywhere would fork it silently — no error, the cache simply stops being shared — so
`service/tests/test_process_global_identity.py` tracks its owner and fails if a second one appears.
That file's GLOBALS map was updated in the same commit, which is the mechanism working as intended:
the owner moved, so the line naming the owner had to move too.

A leaf: standard library only. It takes the db handle as an argument rather than importing one.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any


DEFAULT_SETTINGS = {
    "retention_days": 90,
    "max_messages_per_agent": 1000,
    "max_shared_size_mb": 500,
    "dashboard_refresh_seconds": 15,
    "rotation_enabled": True,
    # Proof-based status (2026-06-18): a single short liveness window replaces the old
    # idle_minutes(5)/offline_minutes(30) time-decay. Wrappers beat liveness every 30s, so
    # 90s = 3 missed beats cleanly separates "missed one" from "gone". No idle/stale decay.
    "agent_liveness_seconds": 90,
    "environment_offline_seconds": 90,
    # How long a SETTLED `offline` agent's status cache is trusted before the reconcile
    # sweep re-validates it. An offline agent only leaves that state via a cache-invalidating
    # event, so the hot read path need not re-derive it every poll (that was the `database is
    # locked` write-storm root cause, 2026-06-18). Bounds env-return lag in the rare no-event
    # case; recovery on any real agent event is immediate via invalidation.
    "agent_offline_revalidate_seconds": 180,
    # When a runtime reports (via bridge-heartbeat) a session id that DIFFERS from
    # the pinned handle and it is NOT owned by another live agent (a safe
    # self-change — e.g. claude compacted/restarted into a fresh session id),
    # auto-adopt the new id instead of parking it as `pending_session_id` and
    # waiting for a manual Confirm. Default ON: managed agents are single-owner,
    # so the drift is almost always a legit self-change, and parking it causes a
    # session-changed → stale-console-owner → recycle loop. The cross-agent
    # COLLISION guard runs BEFORE this and ALWAYS parks (never auto-stolen), so
    # turning this on cannot adopt a session id that belongs to another live agent.
    "auto_confirm_session_id": True,
    "reply_contracts_enabled": True,
    "reply_reminder_minutes": 10,
    "reply_reminder_repeat_minutes": 10,
    # Cap the number of reply reminders per unanswered require_reply run so an
    # owing agent is never nagged forever (runtime-agnostic governance). A
    # sane non-zero default bounds out-of-the-box behaviour; an operator can
    # set 0 to explicitly opt into unlimited reminders.
    "reply_reminder_max_count": 3,
    # Light-reminder cadence (operator decision 2026-07-02): reminders keep
    # firing (no backoff — loops must never stall), but only every Nth one is
    # the FULL teaching format; the ones in between are a one-line nudge that
    # still carries the reply anchor. With the default 3, reminders 1-2 are
    # light and 3 is full, 4-5 light, 6 full, ... 0 or 1 = every reminder full.
    "reply_reminder_full_every": 3,
    # After a delivered require_reply run has sat this long with no reply — well past the
    # reminder cycle (10/20/30 min) — its worker turn is presumed dead (model 429 / mid-turn
    # interrupt / stall). Reconcile FAILS it with a clear cause so it doesn't strand as
    # "delivered" forever (looking idle) and the sender gets a visible failure notice. 0 = off.
    "stranded_reply_fail_minutes": 45,
    "contract_stale_hours": 24,
    "active_run_stale_minutes": 30,
    # Tighter cleanup window for managed dispatches. Default 5 min.
    # A managed run with an empty claim_bridge_id that hasn't progressed
    # within this window is presumed orphaned — the bridge crashed
    # between claim and the controller's failure-PATCH, OR the failure
    # PATCH hit a transient connection error and was logged-but-lost.
    # Tuned tighter than the 30-min generic terminal window because
    # managed dispatches are typically per-turn and shouldn't linger.
    "active_managed_run_stale_minutes": 5,
    # Absolute wall-clock ceiling for a claimed/running managed run, applied
    # REGARDLESS of bridge liveness. Catches the case where the owning bridge
    # keeps heartbeating but the inner controller died without PATCHing the run
    # terminal — the bridge-liveness reaper above never fires, so the agent is
    # pinned `working` forever. Keyed on real staleness (no progress events +
    # started/claimed age) so genuinely-progressing runs are never aged out.
    "active_managed_run_wall_ceiling_minutes": 30,
    # WS3 Task 3.2 (2026-06-02): backstop for `queued` dispatch_runs that no
    # reaper otherwise covers. A queued run whose target has NO live claimer
    # (no fresh channel-sidecar AND no claiming bridge) and is older than this
    # is never deliverable — it would pile up to the buffer cap and hard-reject
    # future sends (buffer_full). After this window such a run is FAILED with an
    # actionable error and mirrored back to the sender. A queued run WITH a live
    # claimer is left alone (it will be claimed on the next poll). Default 180s
    # comfortably exceeds the claim poll cadence + lazy-autostart-on-claim spawn.
    "queued_run_backstop_seconds": 180,
    # WS4 Task 4.3: TTL before a TERMINAL dispatch_run whose endpoints have no
    # live owner (tombstoned/removed/unknown target AND from) is pruned. Keeps a
    # just-removed agent's recent audit history briefly, then GCs it so removed
    # agents and test teardown don't accrete dispatch_runs forever. Never touches
    # non-terminal runs or any run referencing a currently-live agent.
    "orphaned_dispatch_run_retention_hours": 24,
    "managed_claude_model": "",
    "managed_claude_effort": "high",
    # Retained for settings-response compatibility only. Prompt recognition
    # and confirmation belong to the bridge's cursor-verified rules layer.
    "console_auto_confirm_claude_dev_channels": True,
    # Retained for settings-response compatibility only. The bridge decides at
    # process start from AIFY_AUTO_CONFIRM_COMPACTION; it does not poll this
    # service setting. Dashboard Next therefore does not expose a no-op toggle.
    "console_auto_confirm_claude_compaction": True,
    "managed_terminal_backing_enabled": True,
    # Universal delivery-mode flag (operator's design):
    #   false (default, the target architecture) — managed dispatch
    #     uses each runtime's proper delivery channel:
    #       * managed claude: aify-comms-channel notifications (the
    #         claude-channel.js MCP server inside the wrapper PTY
    #         claims and emits notifications/claude/channel events).
    #       * managed codex/pi/opencode/hermes: native RPC adapters
    #         (createCodexController, createPiController, etc.) via
    #         executionModes=["managed"] /dispatch/claim polling.
    #         No PTY-input typing.
    #   true (legacy / opt-in escape hatch) — bridge writes the
    #     dispatch body directly into the wrapper PTY as a
    #     bracketed-paste terminal_control. Operator-visible Console
    #     pop-up. Used as a working baseline when channel/RPC
    #     delivery is misconfigured or under investigation.
    #
    # Earlier name was claude_managed_channel_only and gated only the
    # claude-channel split. The current name covers ALL managed
    # runtimes and inverts the polarity so the proper-delivery path
    # is the default.
    "insert_messages_via_console": False,
    # Reply contract for managed/delivered runs. The intended model is:
    # aify-comms message in -> reply OUT via comms_send(inReplyTo=...) (a tool
    # call the agent makes); genuinely-direct terminal input -> direct output.
    # The injected prompt always directs agents to reply with comms_send.
    # This flag only controls the fallback when an agent finishes a delivered
    # run WITHOUT sending an explicit reply:
    #   True  (B, safety-net) — the bridge auto-mirrors the run summary back to
    #          the sender so the human/teammate still gets something.
    #   False (A, strict)     — no auto-mirror; the run stays reply-owed and the
    #          missing reply is surfaced rather than fabricated from final text.
    # Default True preserves prior behavior; set False to enforce strict
    # comms_send-only replies.
    "managed_reply_capture_fallback": True,
    # Slices 1/2/4 (proactive wrapper-PTY at spawn-request completion).
    # When true AND managed_terminal_backing_enabled is also true, the
    # service eagerly launches the agent's wrapper PTY at spawn-request
    # transition to "running" — the console pre-exists by the time the
    # first dispatch arrives, no "console pop-up on first send" UI
    # symptom, and subsequent dispatches reuse via slice-3's
    # console-attach reuse + the existing dispatch _active_terminal_for_agent.
    # Default true for normal dashboard operation: terminal-backed managed
    # agents should have an operator-visible Console before the first turn
    # needs it. Roll back by flipping this false.
    "managed_pty_eager_spawn": True,                    # Plan 4 (2026-05-25): auto-spawn on dispatch; was False
    # Unified-backing refactor (2026-05-24): when set, managed dispatches for
    # the listed runtimes route through a *-aify wrapper PTY (mirror of how
    # managed claude already works via claude-channel.js inside claude-aify)
    # instead of through the bridge's native RPC adapters (CodexSession,
    # HermesSession, HermesManagedGatewaySession, PiSession). The wrapper's
    # in-process MCP bridge claims /dispatch/claim and delivers via the
    # wrapper's local backing. Dashboard Session Console renders the real
    # Ink TUI of the wrapper via xterm.js.
    #   false: existing native managed dispatch flow.
    #   true: eligible codex / hermes managed runs route via wrapper.
    #   ["hermes", "codex"]: per-runtime opt-in during rollout.
    # claude-code is always wrapper-backed (claude-channel.js); not gated.
    # pi is structurally excluded (omp single-client RPC + bridge-owned
    # mutex make the wrapper pattern impossible). pi managed stays on
    # the persistent PiSession synth-terminal path. See DECISIONS.md.
    "managed_via_wrapper": ["codex", "hermes"],  # wrapper-backed default for Codex/Hermes; was False
    # Auto-close persistent workers (virtual rpc terminals) that have
    # been idle for this many minutes. 0 disables (default). Operator
    # asked for this 2026-05-22: after sending a message the agent
    # comes online (worker spawns), but if no follow-up arrives within
    # the window the worker should close to free resources and reflect
    # the actual operational state (available). The reconciler checks
    # every cycle (60s) and only acts on workers with no in-flight
    # dispatch_runs.
    "worker_idle_close_enabled": False,
    "worker_idle_close_minutes": 0,
    "managed_codex_model": "",
    "managed_codex_effort": "high",
    "managed_pi_model": "",
    "managed_pi_effort": "",
    "resident_lease_seconds": 150,
    # Show dashboard controls for explicit resident<->managed ownership
    # switches. The wrappers still auto-detect at launch; this governs only
    # visibility of the manual override controls.
    "manual_session_mode": True,
    # (status_engine flag removed 2026-06-18: the proof-based event engine
    # service.status_engine.derive is now the ONE status authority.)
    "dashboard_title": "AIFY Comms",
    "dashboard_theme": "default",
    "dashboard_primary_color": "",
    "dashboard_secondary_color": "",
    "dashboard_tertiary_color": "",
}


# In-memory settings cache (perf, 2026-06-04). _load_settings is called on nearly
# every hot request (dispatch/claim, heartbeat, status compute) — 55 call sites — so
# re-reading + JSON-parsing the settings table each time was a measurable chunk of the
# poll-load CPU that was hammering the service. Cache the merged dict with a short TTL;
# writes invalidate immediately (_invalidate_settings_cache) so changes still apply at
# once. Callers get a shallow copy so they can't mutate the cached dict.
_SETTINGS_CACHE: dict[str, Any] = {"value": None, "at": 0.0}


_SETTINGS_CACHE_TTL = 5.0


async def _load_settings(db):
    cached = _SETTINGS_CACHE["value"]
    if (
        cached is not None
        and "pytest" not in sys.modules  # bypass under tests: preserves isolation + set-then-read
        and (time.monotonic() - _SETTINGS_CACHE["at"]) < _SETTINGS_CACHE_TTL
    ):
        return dict(cached)
    settings = {**DEFAULT_SETTINGS}
    sc = await db.execute("SELECT key, value FROM settings")
    for row in await sc.fetchall():
        try:
            settings[row["key"]] = json.loads(row["value"])
        except Exception:
            pass
    _SETTINGS_CACHE["value"] = dict(settings)
    _SETTINGS_CACHE["at"] = time.monotonic()
    return settings


# Moved here in v0.5.2c: the invalidator belongs with the cache it invalidates. It had been
# left in the router when the cache moved in v0.5.1g, which meant the router still looked like
# the owner of settings-cache lifecycle while owning none of the state.
def _invalidate_settings_cache() -> None:
    _SETTINGS_CACHE["value"] = None
    _SETTINGS_CACHE["at"] = 0.0


def _managed_terminal_backing_enabled(settings: dict[str, Any]) -> bool:
    return bool(settings.get("managed_terminal_backing_enabled", DEFAULT_SETTINGS["managed_terminal_backing_enabled"]))
