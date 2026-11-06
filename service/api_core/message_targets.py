"""One target-selector rule for API admission and the HTTP MCP tool (#53). PURE."""
from dataclasses import dataclass

# Match ECMAScript String.trim, so the stdio tool and Python agree on blank selectors.
# Python's default strip also removes U+001C..U+001F, but does not remove U+FEFF.
_SELECTOR_WHITESPACE = ("\t\n\v\f\r          "
                        "        　﻿")


@dataclass(frozen=True)
class MessageTargets:
    to: str | None
    to_role: str | None
    error: str


def select_message_targets(to: str | None, to_role: str | None) -> MessageTargets:
    """Blank is absent; a meaningful ID/role keeps its exact spelling, never silently trimmed."""
    direct = to if to is not None and to.strip(_SELECTOR_WHITESPACE) else None
    role = to_role if to_role is not None and to_role.strip(_SELECTOR_WHITESPACE) else None
    if direct and role:
        error = "Use either 'to' or 'toRole', not both"
    elif not direct and not role:
        error = "Need 'to' or 'toRole'"
    else:
        error = ""
    return MessageTargets(direct, role, error)
