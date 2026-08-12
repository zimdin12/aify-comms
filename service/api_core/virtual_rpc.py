"""The synthetic command strings that mark a VIRTUAL rpc terminal. Neutral leaf — constants only.

A managed pi/hermes/codex/opencode worker with no real PTY gets a synthesized `terminal_sessions` row
whose `command` is one of these sentinels. Nothing executes them; they exist so a row can be
recognised as virtual rather than a live process, which is why several unrelated subsystems compare
against them: the terminal reconcilers, terminal consistency, sessions, the agents surfaces, and
worker liveness.

A NEUTRAL LEAF ON PURPOSE. These were the reviewer's stated exception — a constant governing several
future owners goes somewhere neutral rather than into whichever subject module happens to reach it
first. Worker-liveness needs `VIRTUAL_RPC_COMMAND_SET`, but a channel-delivery module owning the
definition of what a virtual terminal IS would be wrong, and every other reader would then import
"channel" to ask a terminal question.

The set is DERIVED from the map rather than re-typed, and both are here so they cannot drift: a
sentinel present in one and absent from the other is a virtual terminal that half the system fails to
recognise.
"""

from __future__ import annotations


VIRTUAL_PI_RPC_COMMAND = "aify://virtual-rpc/pi"
VIRTUAL_HERMES_RPC_COMMAND = "aify://virtual-rpc/hermes"
VIRTUAL_CODEX_RPC_COMMAND = "aify://virtual-rpc/codex"
VIRTUAL_OPENCODE_RPC_COMMAND = "aify://virtual-rpc/opencode"
VIRTUAL_RPC_COMMANDS_BY_RUNTIME = {
    "pi": VIRTUAL_PI_RPC_COMMAND,
    "hermes": VIRTUAL_HERMES_RPC_COMMAND,
    "codex": VIRTUAL_CODEX_RPC_COMMAND,
    "opencode": VIRTUAL_OPENCODE_RPC_COMMAND,
}
VIRTUAL_RPC_COMMAND_SET = set(VIRTUAL_RPC_COMMANDS_BY_RUNTIME.values())
