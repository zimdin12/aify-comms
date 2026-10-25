"""Whether a claimed or running terminal-mode run is held at its console: the two facts the reply-reminder sweep reads
(SW5 review R4).

Extracted in 0.9.9 S3b from the legacy status cascade, which computed both on the way to a word it no longer serves:
- `no_backing`: the agent's current session has no terminal, or one not in an active status, so the run can no longer
  occupy the agent. It only withdraws that occupancy; it never overrides aify-env's busy or unknown.
- `awaiting_input`: a claude-code console shows a prompt for the operator ("Awaiting console confirmation."), so a
  reminder would ask the agent for something it cannot do until the operator answers.

The cascade also let an offline owning environment win over both (its word was `offline`); that precedence is gone,
because the view reads such an agent unknown or offline from aify-env and the sweep pauses on unknown already.
"""
from __future__ import annotations

from dataclasses import dataclass

from service.api_core.agent_sessions import _current_agent_session_row
from service.api_core.runtime import _normalize_runtime
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES
from service.api_core.terminal_text import _terminal_prompt_hint_from_raw, _terminal_prompt_hint_from_screen
from service.terminal_snapshot import live_screen_text


@dataclass(frozen=True)
class TerminalHold:
    no_backing: bool
    awaiting_input: bool


async def terminal_run_hold(db, agent_row) -> TerminalHold:
    """The hold on the agent's terminal-mode run, read from its current session and, for claude-code, its screen."""
    session = await _current_agent_session_row(db, agent_row["id"])
    keys = session.keys() if session else ()
    terminal_id = str((session["terminal_id"] if "terminal_id" in keys else "") or "").strip()
    terminal_status = str((session["terminal_status"] if "terminal_status" in keys else "") or "").strip().lower()
    if not terminal_id or terminal_status not in _TERMINAL_ACTIVE_STATUSES:
        return TerminalHold(no_backing=True, awaiting_input=False)
    if _normalize_runtime(str(agent_row["runtime"] or "")) != "claude-code":
        return TerminalHold(no_backing=False, awaiting_input=False)
    return TerminalHold(no_backing=False, awaiting_input=await _terminal_prompt(db, terminal_id) != "")


async def _terminal_prompt(db, terminal_id: str) -> str:
    """The console's input prompt, from the live screen, else the stored tail (the live screen is empty after a
    restart, which is what the tail is for). An unreadable terminal reads as no prompt."""
    try:
        live = live_screen_text(terminal_id)
        if live is not None:
            return _terminal_prompt_hint_from_screen(f"term:{terminal_id}", live)
        row = await (await db.execute("SELECT output, cols FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        return _terminal_prompt_hint_from_raw(f"term:{terminal_id}", row["output"] if row else "",
                                              row["cols"] if row and "cols" in row.keys() else 0)
    except Exception:
        return ""
