"""A managed claude whose screen shows its running footer is working. Stamp the console lease.

WHY THE SERVICE READS THE SCREEN NOW. The console-working lease (`agent_console_signal.working_at`)
is what holds a long claude turn at `working` once the in-turn ceiling in `_in_turn_survives` has
cleared it: a turn started by the `UserPromptSubmit` hook is never renewable, so after 30 minutes it
reads `online` however hard the agent is working. The lease was stamped by the environment bridge,
which classified the console tail and POSTed `/agents/{id}/console-working`. v0.6.2 deleted that
bridge and nothing replaced the POST, so the lease had no writer and every claude turn longer than
the ceiling fell back to `online`. Measured on a live agent 2026-09-14: `in_turn=1`, turn started 58
minutes earlier, screen showing `(49m 37s · ↓ 79.0k tokens)`, `working_at` eleven days old, dot green.

The screen is already here: every output chunk is fed into a pyte screen in `_append_terminal_output`,
so this needs no new producer on the host and works for every host that streams a console.

ADDITIVE ONLY, same contract as the route: a match stamps the lease, no match does nothing, and the
lease expires on its own `CONSOLE_WORKING_LEASE_SECONDS`. It is still gated on a live worker where it
is read, so it cannot make a dead agent look busy.

NOW THE FALLBACK (2026-09-14). aify-env's aify-comms plugin evaluates Herdr's screen rules next to
the authoritative screen and reports working / idle / blocked on liveness frames
(`service/api_core/host_activity.py`); a fresh observation decides a managed agent's status ahead of
this lease. The lease still answers for an aify-env that sends no observation, or whose observation
has gone stale. It could be retired once every supported aify-env sends the observation; until
then it stays.
"""

from __future__ import annotations

import logging
import re
import time

from service.api_core.console_prompts import plain_text
from service.api_core.runtime import _normalize_runtime
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state
from service.terminal_snapshot import render_live_screen

logger = logging.getLogger("aify_comms.api_core.console_working")

#: The running footer: an elapsed timer and a live token counter inside one pair of brackets,
#: `✻ Actualizing… (49m 37s · ↓ 79.0k tokens)`. A completed row renders `+3 tool uses · ↓ 12.1k
#: tokens` with no timer and no bracket, and a finished turn leaves `✻ Worked for 49m 37s`, so neither
#: matches. This is the rule the bridge classifier used before it was deleted (`95ba31d3`).
#: THE LINE MUST OPEN WITH A SPINNER FRAME. Unanchored, prose quoting a footer anywhere on an idle
#: screen -- an agent explaining this very rule -- held the lease. `·` and `*` are frames too.
_RUNNING_FOOTER = re.compile(
    r"^[ \t]*[·*✱✶✽✺✹✷✵✳✢✻][ \t][^\n]*\((?:\d+h[ \t]+)?(?:\d+m[ \t]+)?\d+s[ \t]*·[ \t]*[↓↑][ \t]*[\d.]+k?[ \t]*tokens",
    re.MULTILINE,
)
#: The older footer, `✻ Crunched for 3m 12s (esc to interrupt · ...)`. The spinner glyph must sit on
#: the same line so prose quoting the phrase does not count.
_INTERRUPT_FOOTER = re.compile(r"[✱✶✽✺✹✷✵✳✢✻][^\n]*esc to interrupt")

#: Well inside the 20s lease, and it keeps a chunk-rate stream (thousands per second) from rendering
#: the screen more than once per terminal per window.
CHECK_INTERVAL_SECONDS = 5.0
_last_checked: dict[str, float] = {}


def shows_claude_working(screen: str) -> bool:
    text = plain_text(screen)
    return bool(_RUNNING_FOOTER.search(text) or _INTERRUPT_FOOTER.search(text))


async def note_console_working(db, terminal) -> None:
    """Stamp the lease when this terminal's screen shows claude working. Never throws: it runs on
    the console stream, and an exception here would blind the console to fix a status dot."""
    try:
        keys = terminal.keys()
        agent_id = str(terminal["agent_id"] if "agent_id" in keys else "") or ""
        runtime = _normalize_runtime(terminal["runtime"] if "runtime" in keys else "")
        if not agent_id or runtime != "claude-code":
            return
        terminal_id = str(terminal["id"])
        tick = time.monotonic()
        if tick - _last_checked.get(terminal_id, float("-inf")) < CHECK_INTERVAL_SECONDS:
            return
        if len(_last_checked) > 1024:
            _last_checked.clear()  # ponytail: ended terminals are never removed; a clear costs one extra render each
        _last_checked[terminal_id] = tick
        rendered = render_live_screen(terminal_id)
        if not rendered or not shows_claude_working(rendered[0]):
            return
        await db.execute(
            "INSERT INTO agent_console_signal (agent_id, working_at, subagents_at) VALUES (?, ?, '') "
            "ON CONFLICT(agent_id) DO UPDATE SET working_at = excluded.working_at",
            (agent_id, _now()),
        )
        await invalidate_agent_live_state(db, agent_id)
    except Exception:
        logger.debug("console working check failed", exc_info=True)
