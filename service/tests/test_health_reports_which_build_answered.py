"""`/health` must say WHICH build answered, not only that something did.

aify-env's doctor asks every registered service for a self-report at `<endpoint>/health` and renders
`status`, and `version` when the service supplies one -- `probeService`, in aify-env's services module
reads exactly those fields. aify-comms answered `{"status":"healthy"}` and nothing else, so a
multi-service doctor could say a service was up and never which code was serving.

That is the blind spot aify-comms' OWN doctor exists to close, reappearing one layer out. CLAUDE.md
states it plainly: a healthy /health says nothing about which code. The `service` check compares the
build stamp against repo HEAD for exactly this reason, and none of that evidence was reachable by
anything asking across the service boundary.

The version is the one already stamped at build time and read by `/version`, the root endpoint and
`/openapi.json`. This adds no second source: a version declared anywhere but the stamp is what
test_version_single_source.py exists to fail.
"""

from fastapi.testclient import TestClient

from service.config import get_config
from service.main import app


def _health() -> dict:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200, response.text
        return response.json()


def test_health_still_reports_status_first():
    """Anchors the rest. aify-env treats a body without a string `status` as "answered, but not with a
    health report" -- so losing this field would make the service unanswerable while looking fine."""
    body = _health()
    assert isinstance(body.get("status"), str)
    assert body["status"] == "healthy"


def test_health_names_the_build_that_answered():
    body = _health()
    assert isinstance(body.get("version"), str), (
        "aify-env renders `version` when a service supplies one; without it a multi-service doctor "
        "can say a service is up and never which code is serving"
    )
    assert body["version"] == get_config().version, "the version must come from the build stamp, not a literal"
    assert body["version"], "an empty version is not an answer"


def test_health_carries_the_build_sha_so_the_answer_can_be_checked():
    """`version` alone cannot distinguish two builds of the same release, which is the case this
    project actually hits: the fleet runs a checkout that has moved on since the tag."""
    body = _health()
    assert isinstance(body.get("build"), str) and body["build"], (
        "the short build sha names the exact code answering"
    )
    assert body["build"] == get_config().build_short


def test_health_reports_nothing_about_agents():
    """aify-env's probe deliberately reads only status/version/detail. A service volunteering agent
    counts would make the environment tier a second place that answers questions about agents, and
    two components deriving status is how two answers start disagreeing."""
    body = _health()
    for forbidden in ("agents", "agentCount", "sessions", "working"):
        assert forbidden not in body, f"/health must not carry {forbidden}"
