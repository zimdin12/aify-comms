"""`/health` says how many WebSocket clients are connected, because nothing did.

`WSManager.active_count()` has existed and been tested since the manager was written and had NO
product caller at all -- measured 2026-08-26 across every `.py` in the tree. So "is anyone actually
watching the dashboard" could only be answered by opening a browser and looking.

WHY IT IS WORTH A FIELD. It is the denominator for every claim about the broadcast path. `broadcast()`
sends to each socket in sequence, and `terminal_output` alone runs at roughly a frame every 1-4
seconds per live terminal; whether that matters depends entirely on how many sockets there are, and
this review could not size it for exactly that reason. A cost per client is not a cost until the
client count is known.

IT MUST NOT BE ABLE TO BREAK THE HEALTHCHECK. `/health` is what `docker-compose.yml` curls, so an
observability field that could raise would restart a container serving the fleet perfectly well. The
block is wrapped like the two around it, and the tests below break the manager on purpose to prove the
endpoint still answers.

THE APP IS THE REAL ONE, not the api_v2-only test harness: `/health` is mounted by `create_app`, and
the sibling health tests use `service.main.app` for the same reason.
"""

from fastapi.testclient import TestClient

from service.main import app


def _health(client: TestClient) -> dict:
    response = client.get("/health")
    assert response.status_code == 200, response.text
    return response.json()


def test_it_reports_a_number():
    with TestClient(app) as client:
        body = _health(client)
        assert "sockets" in body, "/health does not say how many clients are connected"
        assert isinstance(body["sockets"], int)


def test_the_number_follows_the_manager():
    """Read from the manager rather than invented. A field that always said 0 would look correct on an
    idle host and be useless on a busy one -- which is the only time it gets asked."""
    with TestClient(app) as client:
        manager = app.state.ws_manager
        before = _health(client)["sockets"]

        manager._connections.append(object())
        manager._connections.append(object())
        try:
            after = _health(client)["sockets"]
        finally:
            manager._connections.clear()

        assert after == before + 2, "the count is not coming from the connection manager"
        assert _health(client)["sockets"] == before


def test_a_broken_manager_cannot_take_the_container_down():
    """THE ASSERTION THAT MATTERS MOST. `/health` is the container's healthcheck: if this field could
    raise, docker would restart a service that is serving the fleet perfectly well."""

    class Exploding:
        def active_count(self):
            raise RuntimeError("manager is broken")

    with TestClient(app) as client:
        saved = app.state.ws_manager
        app.state.ws_manager = Exploding()
        try:
            body = _health(client)
            assert body.get("status") == "healthy"
            assert "sockets" not in body, "a failed read reported a number anyway"
        finally:
            app.state.ws_manager = saved


def test_it_survives_no_manager_at_all():
    with TestClient(app) as client:
        saved = app.state.ws_manager
        del app.state.ws_manager
        try:
            assert client.get("/health").status_code == 200
        finally:
            app.state.ws_manager = saved
