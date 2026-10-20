"""The D8 rows of the switched status view (hard-switch plan, page 1, "The D8 rows"). PURE.

The switched view reads an agent's word from aify-env's record. Three cases come before, or instead of, that record,
in this precedence:

1. An operator stop on an UNDEFINED agent reads `stopped`. comms holds that stop itself, because no aify-env defines
   the agent and so none journals the stop. A defined agent's stop is aify-env's, and reaches comms in its record.
2. A record present: the record decides (the view's truth table), so this returns None.
3. No record: `unknown`, except Q3's proven-absent wake handle, which reads `offline`. No row makes a no-record agent
   available; D8 starts no undefined agent, so availability would promise a start that never comes.
"""
from __future__ import annotations

D8_STOPPED = "stopped"
D8_UNKNOWN = "unknown"
D8_OFFLINE = "offline"


def d8_word(*, defined: bool, operator_stopped: bool, has_record: bool, wake_handle_absent: bool) -> str | None:
    """The word a D8 row gives, or None when the agent's record decides."""
    if operator_stopped and not defined:
        return D8_STOPPED
    if has_record:
        return None
    return D8_OFFLINE if wake_handle_absent else D8_UNKNOWN
