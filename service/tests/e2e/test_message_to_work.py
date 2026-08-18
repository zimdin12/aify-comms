"""send → stored → readable → replied → threaded, against a real service over real HTTP.

THE PATH THIS ASSERTS is the one docs/ARCHITECTURE.md calls "how a message becomes work", and the
properties are asserted together because they only mean anything together. A message that is stored but
unreadable, or replied to but unthreaded, is a message the sender will ask about again.

WHY IT IS WORTH AN E2E TEST WHEN 4000 UNIT TESTS EXIST. Every one of those runs in-process against
`TestClient`, sharing the interpreter with the code under test. This suite is the only thing that
exercises the service as a PROCESS: booted from cold, serving over a socket, with its reconcile sweep
running on its own timer. `CLAUDE.md` has described this round trip as the full end-to-end test since
the project began — as a paragraph telling a human what to do by hand. This is that paragraph, executed.

ENDPOINT SHAPES ARE READ FROM /openapi.json, NOT REMEMBERED. The first draft of this file guessed
`POST /api/v1/agents/register` and got a 405; registration is `POST /api/v1/agents`. Inventing an API
from memory is a recorded mistake in this repo and it wastes a full debugging cycle every time.
"""

from __future__ import annotations

import pytest

from service.tests.e2e.harness import E2EStack

SENDER = "e2e-sender"
TARGET = "e2e-target"


@pytest.fixture()
def stack(tmp_path):
    with E2EStack(data_dir=tmp_path / "stack") as running:
        yield running


def _register(stack: E2EStack, agent_id: str, **overrides) -> dict:
    body = {
        "agentId": agent_id,
        "role": "coder",
        "runtime": "claude-code",
        "sessionMode": "resident",
        "cwd": "/w",
    }
    body.update(overrides)
    return stack.api("POST", "/api/v1/agents", body)


def _inbox(stack: E2EStack, agent_id: str, **params) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in {"filter": "all", "limit": 50, **params}.items())
    payload = stack.api("GET", f"/api/v1/messages/inbox/{agent_id}?{query}")
    if isinstance(payload, list):
        return payload
    for key in ("messages", "items", "inbox"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise AssertionError(f"could not find the message list in the inbox payload: {payload!r}")


def test_both_agents_register_and_appear_in_the_roster(stack):
    """The precondition every later assertion rests on, asserted separately so a registration failure
    is reported as one rather than as a mysterious empty inbox."""
    _register(stack, SENDER)
    _register(stack, TARGET)
    # `GET /api/v1/agents` answers `{"agents": {<id>: {...}}}` — a MAP keyed by id, not a list and not
    # a bare map. Read from the live service rather than assumed; my first version treated the envelope
    # itself as the map and reported the two agents as missing when they were registered fine.
    roster = stack.api("GET", "/api/v1/agents")
    agents = roster.get("agents", roster)
    ids = set(agents) if isinstance(agents, dict) else {a.get("id") for a in agents}
    assert {SENDER, TARGET} <= ids, f"the registered agents are not in the roster: {sorted(ids)}"


def test_a_message_is_STORED_before_anything_tries_to_deliver_it(stack):
    """Storage precedes delivery — the first of the four load-bearing properties. A message that
    cannot be delivered is still a message; nothing is dropped because a target was busy."""
    _register(stack, SENDER)
    _register(stack, TARGET)

    sent = stack.api("POST", "/api/v1/messages/send", {
        "from_agent": SENDER, "to": TARGET, "type": "request",
        "subject": "do the thing", "body": "please do it", "trigger": False,
    })
    assert sent.get("ok"), f"the send was refused: {sent}"
    message_id = sent["messageId"]

    stored = [m for m in _inbox(stack, TARGET) if m.get("id") == message_id]
    assert stored, (
        f"the message was accepted but is not in {TARGET}'s inbox. Storage precedes delivery: a "
        f"message that cannot be delivered is still a message."
    )
    assert stored[0].get("subject") == "do the thing"
    assert stored[0].get("from") == SENDER or stored[0].get("fromAgent") == SENDER


def test_a_reply_is_THREADED_to_the_message_it_answers(stack):
    """A reply that exists but is not threaded leaves the contract open and the sender re-asking — the
    exact failure the reply-reminder cycle exists to chase."""
    _register(stack, SENDER)
    _register(stack, TARGET)

    sent = stack.api("POST", "/api/v1/messages/send", {
        "from_agent": SENDER, "to": TARGET, "type": "request",
        "subject": "do the thing", "body": "please do it", "trigger": False,
    })
    original_id = sent["messageId"]

    reply = stack.api("POST", "/api/v1/messages/send", {
        "from_agent": TARGET, "to": SENDER, "type": "response",
        "subject": "Re: do the thing", "body": "done", "inReplyTo": original_id,
        "trigger": False,
    })
    assert reply.get("ok"), f"the reply was refused: {reply}"

    received = [m for m in _inbox(stack, SENDER) if m.get("id") == reply["messageId"]]
    assert received, "the reply never reached the sender's inbox"
    threaded_to = received[0].get("inReplyTo") or received[0].get("in_reply_to")
    assert threaded_to == original_id, (
        f"the reply is in the inbox but threaded to {threaded_to!r} instead of {original_id!r}. An "
        f"unthreaded reply does not close the contract it answers."
    )


def test_the_service_SURVIVES_the_round_trip_and_is_still_healthy(stack):
    """ANTI-VACUITY on the whole file: every assertion above would also pass against a service that
    crashed immediately afterwards, and a crash is exactly the class of failure an in-process suite
    cannot see."""
    _register(stack, SENDER)
    _register(stack, TARGET)
    stack.api("POST", "/api/v1/messages/send", {
        "from_agent": SENDER, "to": TARGET, "type": "request",
        "subject": "s", "body": "b", "trigger": False,
    })
    assert stack.api("GET", "/health").get("status") == "healthy", (
        "the service is no longer healthy after a round trip"
    )
