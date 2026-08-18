"""The harness must start a REAL service and stop it, or every test built on it proves nothing.

This is the first test of Phase 0 and it is deliberately about the harness rather than the product. A
leaked service process would make every later e2e run share state with this one — and the failure would
appear somewhere else entirely, as a test that passes because a previous run left the right row behind.
"""

from __future__ import annotations

import pytest

from service.tests.e2e.harness import E2EStack, ServiceDidNotBoot


def test_the_stack_serves_health_and_then_stops_listening(tmp_path):
    stack = E2EStack(data_dir=tmp_path)
    with stack:
        health = stack.api("GET", "/health")
        assert health.get("status") == "healthy", f"the service is up but not healthy: {health}"
        assert stack.is_listening(), "the stack reported healthy but nothing is on its port"

    assert not stack.is_listening(), (
        "the service is STILL listening after teardown. A leaked process makes every later e2e run "
        "share state with this one, and the resulting failure appears in an unrelated test."
    )


def test_the_stack_uses_its_own_database_not_a_shared_one(tmp_path):
    """The rule this suite must never break. A stack that wrote to the operator's database would
    register test agents into the live registry — which has happened here, from a hostile-env run that
    pointed AIFY_SERVER_URL at 127.0.0.1:8800."""
    stack = E2EStack(data_dir=tmp_path / "isolated")
    db_path = stack.db_path
    with stack:
        # REGISTRATION IS `POST /api/v1/agents`, not `/agents/register`. I guessed the latter and got
        # a 405 — the same class of mistake as inventing an endpoint name from memory, which this repo
        # has recorded before. Confirmed against the live /openapi.json: required fields are agentId
        # and role.
        stack.api("POST", "/api/v1/agents", {
            "agentId": "e2e-isolation-probe", "role": "coder", "runtime": "claude-code",
            "sessionMode": "resident", "cwd": "/w",
        })
    assert db_path.exists(), (
        f"the stack did not create its own database at {db_path}; it wrote somewhere else, which "
        "means it may have written to a real one"
    )


def test_two_stacks_do_not_collide(tmp_path):
    """Ephemeral ports, so a developer's own service on 8800 is never touched and two e2e tests can
    run without a fixed-port collision."""
    with E2EStack(data_dir=tmp_path / "a") as first:
        with E2EStack(data_dir=tmp_path / "b") as second:
            assert first.port != second.port, "two stacks were handed the same port"
            assert first.api("GET", "/health")["status"] == "healthy"
            assert second.api("GET", "/health")["status"] == "healthy"


def test_a_boot_failure_raises_WITH_the_reason(tmp_path):
    """ANTI-VACUITY on the diagnostics. A harness that reports "did not become healthy" without the
    service's own output turns every environment problem into a mystery — and the first thing anyone
    would do is blame the test."""
    unusable = tmp_path / "not-a-directory"
    unusable.write_text("this is a file, not a directory", encoding="utf-8")
    stack = E2EStack(data_dir=unusable / "nested", boot_timeout=8.0)
    with pytest.raises(ServiceDidNotBoot) as raised:
        stack.start()
    stack.stop()
    message = str(raised.value)
    assert len(message) > 60, (
        f"the boot failure carried no diagnostic output, only: {message!r}"
    )
