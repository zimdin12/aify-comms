"""Compaction: an agent's working memory replaced by a summary of it. Both ways, decided here.

NATIVE types the runtime's own compaction command (`/compact`, hermes `/compress`) into a managed
agent's live console, then Enter. The runtime summarises in place and the session id is kept. The
command is a property of the runtime and lives on its adapter (`native_compact_command`); a runtime
with none is refused rather than sent a guess.

HANDOFF ends the session and starts a fresh one. Its first message no longer carries the old
conversation: it names the previous session, says where its transcript is when the runtime's layout
makes that derivable, and tells the agent to read its last N messages with `comms_inbox`. Message
bodies copied into a brief were a second, staler copy of what the inbox already holds.

Pure: no database, no clock. The route gathers the facts and these decide.
"""

from __future__ import annotations

from dataclasses import dataclass

from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.runtimes import adapter_for
from service.status_engine import WORKER_AT_REST_STATUSES

#: How many recent messages a fresh session is told to read when the caller names no number.
DEFAULT_RECENT_MESSAGES = 10
#: The most it may be told to read. A brief asking for hundreds is a context flood by another road.
MAX_RECENT_MESSAGES = 80

#: What a refusal is about, for a caller that branches on it. The message says it to a person.
RESIDENT = "resident"
UNSUPPORTED_RUNTIME = "unsupported-runtime"
NO_CONSOLE = "no-console"
NO_TUI = "no-tui"
MID_TURN = "mid-turn"
PROMPT_ON_SCREEN = "prompt-on-screen"
NOT_AT_PROMPT = "not-at-prompt"


@dataclass(frozen=True)
class NativeCompactTarget:
    """What the route knows about the agent, gathered before anything is decided."""

    agent_id: str
    session_mode: str
    runtime: str
    status: str
    terminal_id: str = ""
    terminal_command: str = ""


@dataclass(frozen=True)
class NativeCompactDecision:
    """Either the command to type, or why not. Never both."""

    command: str = ""
    refused: str = ""
    message: str = ""

    @property
    def allowed(self) -> bool:
        return bool(self.command) and not self.refused


def native_compact_command(runtime) -> str:
    """The runtime's verified compaction command, or "" for none (an unknown runtime included)."""
    try:
        return str(adapter_for(runtime).native_compact_command or "")
    except ValueError:
        return ""


def _refuse(code: str, message: str) -> NativeCompactDecision:
    return NativeCompactDecision(refused=code, message=message)


def decide_native_compact(target: NativeCompactTarget) -> NativeCompactDecision:
    """Whether typing the runtime's compaction command into this console is safe, and which command.

    FAILS CLOSED at every step: a missing mode, runtime, console or status is a refusal.
    """
    agent = target.agent_id
    mode = str(target.session_mode or "").strip().lower()
    if mode != "managed":
        return _refuse(
            RESIDENT,
            f"{agent} is {mode or 'not managed'}: its console is the operator's own terminal, and typing "
            "into it is not aify-comms' to do. Ask the operator to run the runtime's compact command "
            'there, or use mode "handoff".',
        )
    runtime = str(target.runtime or "").strip() or "unknown"
    command = native_compact_command(runtime)
    if not command:
        return _refuse(
            UNSUPPORTED_RUNTIME,
            f'Runtime "{runtime}" has no verified native compaction command, so nothing is typed. '
            'Use mode "handoff".',
        )
    terminal_id = str(target.terminal_id or "").strip()
    if not terminal_id:
        return _refuse(
            NO_CONSOLE,
            f"{agent} has no live console to type {command} into. Native compaction acts on a running "
            'session; with none running, use mode "handoff".',
        )
    if terminal_id.startswith("vterm_") or str(target.terminal_command or "") in VIRTUAL_RPC_COMMAND_SET:
        return _refuse(
            NO_TUI,
            f"{agent}'s console is synthesized by the bridge, not a {runtime} TUI, so {command} would "
            'arrive as a prompt rather than run. Use mode "handoff".',
        )
    status = str(target.status or "").strip().lower()
    if status in WORKER_AT_REST_STATUSES:
        return NativeCompactDecision(command=command)
    if status == "working":
        return _refuse(
            MID_TURN,
            f"{agent} is mid-turn: {command} typed now would be queued behind the turn or taken as input "
            "to it. Retry when it is online.",
        )
    if status == "blocked":
        return _refuse(
            PROMPT_ON_SCREEN,
            f"{agent} is showing a prompt, and typed text would answer it. Read it with "
            "comms_console_tail and settle the prompt first.",
        )
    return _refuse(
        NOT_AT_PROMPT,
        f"{agent} is {status or 'of unknown status'}, not idle at its prompt "
        f"({' or '.join(WORKER_AT_REST_STATUSES)}), so {command} is not typed.",
    )


def recent_messages_count(value) -> int:
    """The number of messages a brief asks for: the default when unset, clamped to the range."""
    if value is None or value == "":
        return DEFAULT_RECENT_MESSAGES
    return max(0, min(MAX_RECENT_MESSAGES, int(value)))


def transcript_location(runtime, session_handle, workspace) -> str:
    """Where the previous session's transcript can be read, from the runtime's layout, or ""."""
    try:
        adapter = adapter_for(runtime)
    except ValueError:
        return ""
    return adapter.transcript_location(str(session_handle or ""), str(workspace or ""))


def handoff_brief(*, agent_id, source_session_id, session_handle, runtime, transcript, recent_messages) -> str:
    """The first message of a handoff's fresh session.

    It points at the history rather than copying it: the inbox holds the messages, the transcript
    holds the conversation, and the brief says how to reach both.
    """
    count = recent_messages_count(recent_messages)
    handle = str(session_handle or "").strip()
    lines = [
        f'Handoff compaction: this is a fresh session for agent "{agent_id}". The previous session was '
        "ended to free its context, and none of it is loaded here.",
        "",
        f"Previous session: {source_session_id or 'unknown'}" + (f" ({runtime})" if runtime else ""),
        f"Previous native session id: {handle}" if handle else "Previous native session id: not recorded",
        f"Previous transcript: {transcript}" if transcript else "Previous transcript: location not known to aify-comms",
        "Read the transcript only for a detail your messages do not hold; it can be large.",
        "",
    ]
    if count:
        lines.append(
            f"To get up to date, read the last {count} messages first: "
            f'comms_inbox(agentId="{agent_id}", filter="all", limit={count}, peek=true).'
        )
    else:
        lines.append("No recent messages were requested. Check comms_inbox for unread work.")
    return "\n".join(lines)
