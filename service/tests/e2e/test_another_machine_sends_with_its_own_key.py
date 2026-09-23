"""An agent on another machine sends here with that machine's own key, and can do nothing else.

ASKED BY THE OPERATOR, 2026-09-23 (see service/api_core/external_keys.py). Run against the REAL
service in a subprocess, not the test app, for one reason above the others: which routes an external
key opens is DERIVED from the served routes, and from fastapi 0.137 the app's routers are included
lazily -- a table built from the wrong walk admits nothing, and only the real app can show that.

The same stack also proves the operator key is generated when `.env` sets none.
"""

from __future__ import annotations

import re

import pytest

from service.tests.e2e.harness import E2EStack

SERVICE_KEY = "e2e-service-key-0123456789"
PC2_KEY = "e2e-pc2-key-0123456789abcdef"
HOME = "home-agent"
REMOTE = "pc2-manager"


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    data = tmp_path_factory.mktemp("external-keys")
    env = {"API_KEY": SERVICE_KEY, "EXTERNAL_KEYS": f"pc2:{PC2_KEY}", "OPERATOR_KEY": ""}
    with E2EStack(data_dir=data, env=env) as running:
        running.api("POST", "/api/v1/agents", {
            "agentId": HOME, "role": "coder", "runtime": "claude-code", "sessionMode": "resident", "cwd": "/w",
        }, headers={"X-API-Key": SERVICE_KEY})
        yield running


def _as(key: str) -> dict:
    return {"X-API-Key": key}


def _send(stack, key: str, sender: str, subject: str, **extra) -> int:
    body = {"from_agent": sender, "to": HOME, "type": "info", "subject": subject, "body": "hello", **extra}
    return stack.status_of("POST", "/api/v1/messages/send", body, headers=_as(key))


def _inbox(stack) -> list[dict]:
    return stack.api("GET", f"/api/v1/messages/inbox/{HOME}?peek=true&limit=50", headers=_as(SERVICE_KEY))["messages"]


def test_the_other_machine_can_send_and_its_message_names_the_machine(stack):
    assert _send(stack, PC2_KEY, REMOTE, "from pc2", origin="10.0.0.9:8800") == 200
    [message] = [m for m in _inbox(stack) if m["subject"] == "from pc2"]
    assert message["externalMachine"] == "pc2", message
    assert message["fromRegistered"] is False
    assert message["origin"] == "10.0.0.9:8800", "what the sender declared is kept beside what was proven"


def test_CONTROL_a_local_send_names_no_machine(stack):
    assert _send(stack, SERVICE_KEY, "some-local-tool", "local send") == 200
    [message] = [m for m in _inbox(stack) if m["subject"] == "local send"]
    assert message["externalMachine"] == ""


def test_a_client_cannot_write_the_machine_itself(stack):
    forged = {"externalMachine": "pc2", "external_machine": "pc2", "_external_machine": "pc2"}
    assert _send(stack, SERVICE_KEY, "some-local-tool", "forged machine", **forged) == 200
    [message] = [m for m in _inbox(stack) if m["subject"] == "forged machine"]
    assert message["externalMachine"] == "", "the machine is the key's verdict, never the body's"


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/agents"),                      # the roster, which carries gateway tokens
    ("POST", "/api/v1/agents"),                     # registering here: the operator said it must not
    ("GET", f"/api/v1/messages/inbox/{HOME}"),      # a local agent's mail
    ("GET", "/mcp/sse"),                            # the MCP transport, a mount rather than a route
])
def test_the_key_opens_nothing_else(stack, method, path):
    body = {"agentId": REMOTE, "role": "manager", "runtime": "generic"} if method == "POST" else None
    assert stack.status_of(method, path, body, headers=_as(PC2_KEY)) == 403


def test_the_refusal_says_whose_key_it_is(stack):
    reply = stack.api("GET", "/api/v1/agents", headers=_as(PC2_KEY), expect_error=True)
    assert "pc2" in reply.get("error", ""), reply


@pytest.mark.parametrize("sender", [HOME, "dashboard"])
def test_the_key_cannot_speak_for_anyone_who_lives_here(stack, sender):
    assert _send(stack, PC2_KEY, sender, f"impersonating {sender}") == 403
    assert not [m for m in _inbox(stack) if m["subject"] == f"impersonating {sender}"]


def test_the_refusals_say_why(stack):
    as_home = stack.api("POST", "/api/v1/messages/send", {
        "from_agent": HOME, "to": HOME, "subject": "s", "body": "b"}, headers=_as(PC2_KEY), expect_error=True)
    assert "' speaks only for agents on that machine" in as_home.get("detail", ""), as_home
    nameless = stack.api("POST", "/api/v1/messages/send", {
        "from_agent": "", "to": HOME, "subject": "s", "body": "b"}, headers=_as(PC2_KEY), expect_error=True)
    assert "A message sent with the external key for 'pc2' must name its sender" in nameless.get("detail", ""), nameless


def test_CONTROL_an_unknown_key_is_still_refused_outright(stack):
    assert _send(stack, "not-a-key-anybody-issued", REMOTE, "stranger") == 401


def test_health_says_the_keys_are_enforced(stack):
    assert stack.api("GET", "/health").get("externalKeys") == {"configured": 1, "rejected": 0, "enforced": True}


def test_with_no_operator_key_configured_one_is_generated_into_the_data_volume(stack):
    key = (stack.data_dir / "operator.key").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", key), "expected a generated 32-byte hex key"
