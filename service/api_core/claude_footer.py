"""Claude's turn footer, read one way: the rules both readers of it use (2026-10-14 audit, S3).

`console_working.py` asks whether claude is working now; `terminal_text.py`'s awaiting-input guard asks
where the last turn footer ends, so it reads only what came after it. Each kept its own copy, and they
drifted: the guard never learned the current running footer, so a working claude's scrollback prose read
as a live question. The rules live here; `TURN_FOOTER` is built from them, never written out again.
"""

from __future__ import annotations

import re

from service.api_core.console_prompts import plain_text

#: The running footer: an elapsed timer and a live token counter inside one pair of brackets,
#: `✻ Actualizing… (49m 37s · ↓ 79.0k tokens)`. A completed row renders `+3 tool uses · ↓ 12.1k
#: tokens` with no timer and no bracket, and a finished turn leaves `✻ Worked for 49m 37s`, so neither
#: matches. This is the rule the bridge classifier used before it was deleted (`95ba31d3`).
#: THE LINE MUST OPEN WITH A SPINNER FRAME. Unanchored, prose quoting a footer anywhere on an idle
#: screen -- an agent explaining this very rule -- held the lease. `·` and `*` are frames too.
RUNNING_FOOTER = re.compile(
    r"^[ \t]*[·*✱✶✽✺✹✷✵✳✢✻][ \t][^\n]*\((?:\d+h[ \t]+)?(?:\d+m[ \t]+)?\d+s[ \t]*·[ \t]*[↓↑][ \t]*[\d.]+k?[ \t]*tokens",
    re.MULTILINE,
)
#: The older footer, `✻ Crunched for 3m 12s (esc to interrupt · ...)`. The spinner glyph must sit on
#: the same line so prose quoting the phrase does not count.
INTERRUPT_FOOTER = re.compile(r"[✱✶✽✺✹✷✵✳✢✻][^\n]*esc to interrupt")
#: A finished turn, `✻ Worked for 49m 37s`. Not working; it still ends the turn's output.
FINISHED_FOOTER = re.compile(r"[✱✶✽✺✹✷✵✳✢✻]\s+\S+\s+for\s+\d+\s*[hms]\b", re.I)

#: Any of them, for a reader that needs where the last turn footer ends rather than whether one runs.
TURN_FOOTER = re.compile(
    "|".join(f"(?:{rule.pattern})" for rule in (RUNNING_FOOTER, INTERRUPT_FOOTER, FINISHED_FOOTER)),
    re.MULTILINE | re.I,
)


def shows_claude_working(screen: str) -> bool:
    """Whether the screen shows claude in a turn now: a running or an interrupt-style footer."""
    text = plain_text(screen)
    return bool(RUNNING_FOOTER.search(text) or INTERRUPT_FOOTER.search(text))
