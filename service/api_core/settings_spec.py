"""Every service setting, declared once: its type, bounds, meaning and where the operator sees it.

WHY ONE DECLARATION. Until 2026-09-19 a setting was described three times: a default here, a control
with its own bounds in the dashboard, and a floor table in the write route. They disagreed (liveness:
floor 10 on the server, 30 in the panel), the route dropped bad values silently and still answered
"saved", and six settings had no control at all. Now the dashboard draws its panel from this list
(`GET /settings/schema`), the write route validates against it and refuses with the reason, and
`DEFAULT_SETTINGS` is derived from it.

WHAT CHANGED, after an end-to-end trace of every key against the v0.6 architecture:
  * REMOVED `console_auto_confirm_claude_dev_channels` / `_compaction`: nothing read them.
  * REMOVED `manual_session_mode`: the switch chips it named are no longer gated by it.
  * MERGED `worker_idle_close_enabled` into `worker_idle_close_minutes` (0 = off; see the migration in
    service/db.py).
  * INTERNAL, never drawn on the panel: `managed_terminal_backing_enabled` and
    `managed_pty_eager_spawn`. Under aify-env, workers start from terminal rows, so turning the first
    off stops every managed worker; the legacy regression suites still switch both to exercise the
    pre-console delivery paths.
  * ADVANCED: `managed_via_wrapper`, now validated to the runtimes whose adapter supports it. It is
    baked into each worker's environment at launch, so a change needs those workers restarted.
Stored rows for removed keys are ignored, not deleted.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from service.models import validate_model_shape

#: The dashboard's colour schemes (`service/new_dashboard/theme.js` THEMES).
THEMES = ("default", "forest", "violet", "ember", "ocean", "graphite", "crimson", "indigo")
EFFORTS = ("low", "medium", "high", "xhigh")
_HEX = re.compile(r"\A#[0-9a-fA-F]{6}\Z")

#: Groups in the order the panel shows them. `advanced` settings sit in a collapsed section.
GROUPS = ("Replies & messages", "Agent liveness", "Managed workers", "Files & retention", "Appearance",
          "Advanced")


class SettingError(ValueError):
    """A value this setting cannot hold. The message names the setting and the rule."""


class Setting:
    """One setting: what it holds, its bounds, and what the operator is told about it."""

    def __init__(self, key: str, default: Any, kind: str, group: str, label: str, help: str = "",
                 *, unit: str = "", min: Optional[float] = None, max: Optional[float] = None,
                 choices: tuple = (), applies: str = "now", shown: bool = True) -> None:
        self.key = key
        self.default = default
        self.kind = kind  # bool | int | text | choice | model | color | runtimes
        self.group = group
        self.label = label
        self.help = help
        self.unit = unit
        self.min = min
        self.max = max
        self.choices = choices
        self.applies = applies  # now | next worker start | next rotation
        self.shown = shown  # False: accepted by the API, never drawn on the panel

    def validate(self, value: Any) -> Any:
        """The value to store, or SettingError saying why it cannot be stored."""
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().lower() in ("true", "false"):
                return value.strip().lower() == "true"
            raise SettingError(f'"{self.key}" must be true or false')
        if self.kind == "int":
            if isinstance(value, bool):
                raise SettingError(f'"{self.key}" must be a number')
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise SettingError(f'"{self.key}" must be a number') from None
            if not math.isfinite(number) or number != int(number):
                raise SettingError(f'"{self.key}" must be a whole number')
            if (self.min is not None and number < self.min) or (self.max is not None and number > self.max):
                raise SettingError(f'"{self.key}" must be between {self.min:g} and {self.max:g}')
            return int(number)
        if self.kind == "runtimes":
            if isinstance(value, bool):
                return list(self.choices) if value else []
            items = value if isinstance(value, list) else str(value or "").split(",")
            names = [str(item).strip().lower() for item in items if str(item).strip()]
            unknown = [name for name in names if name not in self.choices]
            if unknown:
                raise SettingError(f'"{self.key}" accepts only: {", ".join(self.choices)} (not {", ".join(unknown)})')
            return [name for name in self.choices if name in names]
        text = "" if value is None else str(value).strip()
        if self.kind == "choice":
            if text not in self.choices:
                raise SettingError(f'"{self.key}" must be one of: {", ".join(c or "(default)" for c in self.choices)}')
            return text
        if self.kind == "model":
            try:
                return validate_model_shape(text) or ""
            except ValueError as exc:
                raise SettingError(f'"{self.key}": {exc}') from None
        if self.kind == "color":
            if text and not _HEX.match(text):
                raise SettingError(f'"{self.key}" must be a colour like #aabbcc, or empty for the scheme default')
            return text.lower()
        if len(text) > 120:
            raise SettingError(f'"{self.key}" must be at most 120 characters')
        return text

    def describe(self) -> dict:
        """The declaration as the dashboard reads it."""
        return {
            "key": self.key, "default": self.default, "kind": self.kind, "group": self.group,
            "label": self.label, "help": self.help, "unit": self.unit, "min": self.min, "max": self.max,
            "choices": list(self.choices), "applies": self.applies,
        }


_R, _L, _W, _F, _A, _X = GROUPS

SETTINGS: tuple[Setting, ...] = (
    # ── Replies & messages ──
    Setting("reply_contracts_enabled", True, "bool", _R, "Remind agents who owe a reply"),
    Setting("reply_reminder_minutes", 10, "int", _R, "First reminder after",
            "Also when a reply counts as overdue on every screen.", unit="min", min=1, max=240),
    Setting("reply_reminder_repeat_minutes", 10, "int", _R, "Repeat reminders every", unit="min", min=1, max=1440),
    Setting("reply_reminder_max_count", 3, "int", _R, "Stop after this many reminders", "0 = no limit.",
            min=0, max=20),
    Setting("reply_reminder_full_every", 3, "int", _R, "Full reminder every Nth",
            "The others are one-line nudges. 0 or 1 = every reminder full.", min=0, max=20),
    Setting("contract_stale_hours", 24, "int", _R, "Stop reminding after",
            "Replies owed longer than this stop being reminded and drop off the contracts list.",
            unit="h", min=1, max=720),
    Setting("managed_reply_capture_fallback", True, "bool", _R, "Send the agent's output when it forgets to reply",
            "Off: the reply stays owed and is shown as missing instead."),
    Setting("away_briefing_hours", 4, "int", _R, "Brief agents returning after",
            "An agent back after this long is sent what arrived while it was gone. 0 = off.",
            unit="h", min=0, max=720),
    # ── Agent liveness ──
    Setting("agent_liveness_seconds", 90, "int", _L, "Agent offline after no heartbeat for",
            "Bridges beat every 30 s, so 90 s is three missed beats.", unit="s", min=30, max=600),
    Setting("environment_offline_seconds", 90, "int", _L, "Machine offline after no heartbeat for",
            unit="s", min=30, max=3600),
    Setting("resident_lease_seconds", 150, "int", _L, "Operator-started session counts as live for",
            "How long a resident (terminal you started yourself) stays live after its last beat.",
            unit="s", min=30, max=3600),
    Setting("auto_confirm_session_id", True, "bool", _L, "Adopt a new session id automatically",
            "When an agent's runtime reports a new conversation id that no other agent holds, "
            "adopt it instead of waiting for you to confirm."),
    # ── Managed workers ──
    Setting("managed_claude_model", "", "model", _W, "Claude model for new workers", "Empty = Claude's default.",
            applies="next worker start"),
    Setting("managed_claude_effort", "high", "choice", _W, "Claude effort for new workers", choices=EFFORTS,
            applies="next worker start"),
    Setting("managed_codex_model", "", "model", _W, "Codex model for new workers", "Empty = Codex's default.",
            applies="next worker start"),
    Setting("managed_codex_effort", "high", "choice", _W, "Codex effort for new workers", choices=EFFORTS,
            applies="next worker start"),
    Setting("worker_idle_close_minutes", 0, "int", _W, "Close idle workers after",
            "A managed worker with no work in flight is stopped after this long. 0 = never.",
            unit="min", min=0, max=1440),
    # ── Files & retention ──
    Setting("max_shared_size_mb", 500, "int", _F, "Largest shared file", unit="MB", min=1, max=100000),
    Setting("message_retention_days", 0, "int", _F, "Delete messages older than", unit="days",
            help="Checked hourly. Deleted messages cannot be recovered. 0 = keep forever.", min=0, max=3650),
    Setting("message_cap_per_agent", 0, "int", _F, "Keep at most this many messages per agent",
            help="The oldest go first, and a channel message counts once per member it was delivered to. "
                 "Checked hourly. 0 = no limit.",
            min=0, max=1000000),
    Setting("orphaned_dispatch_run_retention_hours", 24, "int", _F, "Keep a removed agent's run history for",
            unit="h", min=1, max=8760),
    # ── Appearance ──
    Setting("dashboard_theme", "default", "choice", _A, "Colour scheme", choices=THEMES),
    Setting("dashboard_primary_color", "", "color", _A, "Primary colour", "Actions, brand, focus."),
    Setting("dashboard_secondary_color", "", "color", _A, "Secondary colour", "Selection, links."),
    Setting("dashboard_tertiary_color", "", "color", _A, "Tertiary colour", "Depth, charts."),
    Setting("dashboard_title", "AIFY Comms", "text", _A, "Dashboard title"),
    # ── Advanced ──
    Setting("usage_poll_minutes", 5, "int", _X, "Read the usage pools every",
            "How often the service asks OpenAI and Anthropic for the subscription quota. One read serves every agent.",
            unit="min", min=1, max=1440),
    Setting("insert_messages_via_console", False, "bool", _X, "Type messages into the console (legacy)",
            "Delivers by typing into the agent's terminal instead of its message channel. Scrambles "
            "anything typed at the same time; for diagnosing a broken channel only."),
    Setting("active_run_stale_minutes", 30, "int", _X, "Fail an unowned console-typed turn after",
            unit="min", min=5, max=240),
    Setting("active_managed_run_stale_minutes", 5, "int", _X, "Fail a claimed turn whose bridge vanished after",
            unit="min", min=1, max=120),
    Setting("active_managed_run_wall_ceiling_minutes", 30, "int", _X, "Fail a turn with no progress after",
            "Applies even while the bridge is alive, for a controller that died silently.",
            unit="min", min=5, max=1440),
    Setting("queued_run_backstop_seconds", 180, "int", _X, "Fail a message no live agent can take or answer after",
            unit="s", min=60, max=3600),
    # UNLIKE its neighbour above, this one has no early return: `api_core/status_inputs.py` applies
    # `max(60, ...)` to every value, so a declared min of 30 was a number the panel accepted and the
    # code silently doubled (review finding 12, and this half of it holds).
    Setting("agent_offline_revalidate_seconds", 180, "int", _X, "Re-check an offline agent's status every",
            unit="s", min=60, max=3600),
    Setting("dashboard_refresh_seconds", 15, "int", _X, "Dashboard poll while its live connection is down",
            unit="s", min=5, max=300),
    Setting("managed_via_wrapper", ["codex", "hermes"], "runtimes", _X, "Runtimes delivered through their console wrapper",
            "Leave as codex, hermes. Baked into each worker at launch: restart those workers after a change.",
            choices=("codex", "hermes"), applies="next worker start"),
    Setting("managed_pty_eager_spawn", True, "bool", _X, "Start pi/opencode consoles at spawn",
            shown=False),
    Setting("managed_terminal_backing_enabled", True, "bool", _X, "Create consoles for managed workers",
            "Must stay on under aify-env, which starts every worker from its console row.", shown=False),
    Setting("managed_pi_model", "", "model", _X, "Pi model for new workers (pi is deprecated)",
            applies="next worker start"),
    Setting("managed_pi_effort", "", "choice", _X, "Pi effort for new workers (pi is deprecated)",
            choices=("",) + EFFORTS, applies="next worker start"),
)

BY_KEY: dict[str, Setting] = {setting.key: setting for setting in SETTINGS}

#: Keys that were settings once. A PUT naming one (a dashboard tab opened before the upgrade) is
#: ignored rather than refused; a stored row for one is never read.
RETIRED = frozenset({
    "console_auto_confirm_claude_dev_channels", "console_auto_confirm_claude_compaction",
    "manual_session_mode", "worker_idle_close_enabled",
    "idle_minutes", "offline_minutes", "stale_agent_hours", "status_engine",
    # Rotation's old keys. Nothing ever scheduled rotation, so they never took effect, and hosts whose
    # settings page was saved hold 90 / 1000 in the table. Retired, not reused, so that scheduling it
    # (2026-09-19) cannot start deleting under a value nobody chose knowingly.
    "rotation_enabled", "retention_days", "max_messages_per_agent",
})


def defaults() -> dict[str, Any]:
    return {setting.key: setting.default for setting in SETTINGS}


def validate_update(body: dict) -> dict[str, Any]:
    """The values to store from a PUT body, or SettingError listing every problem at once.

    An unknown key is an error rather than skipped: a dashboard that sends a removed key is out of
    date, and saying so beats silently dropping what the operator typed.
    """
    clean, problems = {}, []
    for key, value in (body or {}).items():
        setting = BY_KEY.get(key)
        if setting is None and key in RETIRED:
            continue
        if setting is None:
            problems.append(f'"{key}" is not a setting')
            continue
        try:
            clean[key] = setting.validate(value)
        except SettingError as exc:
            problems.append(str(exc))
    if problems:
        raise SettingError("; ".join(problems))
    return clean
