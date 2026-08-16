"""What a sender is told when their message is refused because the target's buffer is full.

`_dispatch_buffer_full_hint` builds the `notStarted` entry the sender receives, and nothing named it.
Its whole reason for existing is that a silently-dropped message and a delivered one look identical
from the outside — so if this payload is wrong or thin, the sender learns nothing and retries into
the same wall.

The `fix` text is the part that matters most and is easiest to let rot: it names the three ways out
(wait, interrupt the active run, inspect the queue) and quotes the real counts. A hint that says
"buffer full" without the cap, the current count, or who filled it is a dead end.

Runtime and session mode are NORMALISED here rather than passed through, because the sender uses them
to decide which escape hatch applies, and `claude` versus `claude-code` reaching them as two
different runtimes is how a caller ends up special-casing one spelling.
"""
from __future__ import annotations

import pytest

from service.api_core.dispatch_buffer import _DISPATCH_BUFFER_CAP, _dispatch_buffer_full_hint


class Row(dict):
    def __getitem__(self, key):
        return dict.get(self, key, "")


def hint(row=None, **over):
    kwargs = {
        "from_agent": "manager-bot",
        "current_count": _DISPATCH_BUFFER_CAP,
        "recipient_status": "working",
        "has_active_run": True,
    }
    kwargs.update(over)
    return _dispatch_buffer_full_hint("sc-coder", row, **kwargs)


# ── the payload the sender receives ──────────────────────────────────────────────────────────
def test_the_hint_identifies_the_target_the_sender_and_the_reason():
    out = hint(Row({"runtime": "claude-code", "session_mode": "managed"}))
    assert out["targetAgentId"] == "sc-coder"
    assert out["fromAgent"] == "manager-bot"
    assert out["reason"] == "buffer_full", "callers branch on this string"


def test_the_counts_are_reported_so_the_sender_knows_how_full_is_full():
    out = hint(Row({}), current_count=7)
    assert out["bufferedCount"] == 7
    assert out["bufferCap"] == _DISPATCH_BUFFER_CAP
    assert out["bufferCap"] > 0, "a cap of zero would make the hint unreadable"


def test_the_recipient_state_travels_with_it():
    out = hint(Row({}), recipient_status="working", has_active_run=True)
    assert out["recipientStatus"] == "working"
    assert out["hasActiveRun"] is True

    idle = hint(Row({}), recipient_status="online", has_active_run=False)
    assert idle["recipientStatus"] == "online"
    assert idle["hasActiveRun"] is False


def test_has_active_run_is_passed_through_UNCOERCED():
    """CHARACTERIZATION. Unlike runtime and session mode, this one is not normalised — whatever the
    caller hands over reaches the payload verbatim, so a truthy 1 travels as 1 rather than as true.

    Not a live defect: the sole caller computes it as `bool(dispatch_state.get("hasActiveRun"))` and
    so already passes a real boolean. Recorded because the parameter is annotated `bool` and the
    neighbouring fields ARE normalised, which makes the asymmetry invisible when reading the
    function — and because this payload is serialised to JSON, where 1 and true are different values
    to anything branching on identity."""
    assert hint(Row({}), has_active_run=True)["hasActiveRun"] is True
    assert hint(Row({}), has_active_run=False)["hasActiveRun"] is False
    assert hint(Row({}), has_active_run=1)["hasActiveRun"] == 1
    assert hint(Row({}), has_active_run=None)["hasActiveRun"] is None


# ── the fix text ─────────────────────────────────────────────────────────────────────────────
def test_the_fix_text_quotes_the_real_numbers_and_names_the_ways_out():
    """A hint that says "buffer full" without the counts or an escape hatch is a dead end — the
    sender retries into the same wall."""
    out = hint(Row({}), current_count=9, from_agent="manager-bot")
    fix = out["fix"]
    assert "9 buffered dispatches" in fix
    assert "manager-bot" in fix, "who filled it — the sender may be their own problem"
    assert str(_DISPATCH_BUFFER_CAP) in fix
    assert "comms_run_interrupt" in fix, "the interrupt escape hatch is named as a callable tool"
    assert "comms_agent_info" in fix, "and so is the way to inspect the queue"


def test_the_fix_text_tracks_the_count_it_was_given():
    assert "3 buffered" in hint(Row({}), current_count=3)["fix"]
    assert "9 buffered" in hint(Row({}), current_count=9)["fix"]


# ── runtime and session mode are normalised ──────────────────────────────────────────────────
@pytest.mark.parametrize("given,expected", [("claude", "claude-code"), ("claude-code", "claude-code"), ("CODEX", "codex")])
def test_the_runtime_is_normalised(given, expected):
    """The sender uses this to decide which escape hatch applies; two spellings of one runtime is how
    a caller ends up special-casing one of them."""
    assert hint(Row({"runtime": given}))["runtime"] == expected


def test_the_session_mode_is_normalised_and_defaults_to_resident():
    assert hint(Row({"session_mode": "MANAGED"}))["sessionMode"] == "managed"
    assert hint(Row({"session_mode": ""}))["sessionMode"] == "resident"
    assert hint(Row({}))["sessionMode"] == "resident", "an absent mode is resident, not empty"


def test_an_absent_runtime_becomes_generic_rather_than_empty():
    """`generic` is a runtime the rest of the system understands; "" is not."""
    assert hint(Row({}))["runtime"] == "generic"
    assert hint(Row({"runtime": None}))["runtime"] == "generic"


def test_no_row_at_all_still_produces_a_complete_hint():
    """The recipient row is fetched separately and may be missing — the sender must still be told
    why their message did not land."""
    out = hint(None)
    assert out["reason"] == "buffer_full"
    assert out["runtime"] == "generic"
    assert out["sessionMode"] == "resident"
    assert out["targetAgentId"] == "sc-coder"
    assert "comms_run_interrupt" in out["fix"]


def test_the_payload_keys_are_stable():
    """Consumers read these by name across the wire; the set is pinned so a rename is deliberate."""
    assert set(hint(Row({}))) == {
        "targetAgentId", "reason", "runtime", "sessionMode", "bufferCap",
        "bufferedCount", "recipientStatus", "hasActiveRun", "fromAgent", "fix",
    }
