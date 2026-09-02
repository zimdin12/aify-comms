"""aify-env's default port must not be one this stack already publishes.

The two tiers are meant to run on ONE host -- that is the whole point of the three-repo split -- and
aify-env's default was 8801, which `docker-compose.yml` publishes for Dashboard Next. On this machine
that is not hypothetical: `curl http://127.0.0.1:8801/health` returns `{"status":"healthy"}` from the
dashboard, so an operator following aify-env's README would have set AIFY_ENV_ENDPOINT to a port
answering a healthy-looking body from the wrong service.

aify-env's doctor already refuses an impostor -- it requires `processes` and `terminals` in /health, and
reports `failed` plus `unanswered` rather than counting a 200 -- and that defence was written from this
exact collision. A defence is not a reason to keep the collision.

The published ports are DERIVED from the compose file rather than listed here, so a new service in the
stack cannot quietly start colliding.
"""

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.yml"
AIFY_ENV = Path(os.environ.get("AIFY_ENV_REPO", Path.home() / "projects" / "aify-env"))

# "${NEW_DASHBOARD_PORT:-8801}:8801" and "8188:8188" both appear; the HOST port is the left half.
#: The CONTAINER side may be a variable too, not only a literal. The https proxy publishes
#: `"${HTTPS_PORT:-8443}:${HTTPS_PORT:-8443}"` because Caddy listens on whatever port its site
#: addresses name -- and this pattern required a bare number there, so it silently stopped matching
#: that line and the scan lost a port. A collision check that cannot see a published port reports
#: "no collision" for the same reason it reports everything else: it looked at nothing.
PUBLISHED = re.compile(
    r'^\s*-\s*"(?:\$\{[A-Z_]+:-)?(\d+)\}?:(?:\$\{[A-Z_]+:-)?\d+\}?"', re.MULTILINE)


#: The variable name in a `"${VAR:-default}:..."` host port, so the value an operator SET can be
#: looked up rather than only the fallback baked into compose.
PUBLISHED_VAR = re.compile(r'^\s*-\s*"\$\{([A-Z_]+):-\d+\}:', re.MULTILINE)


def env_overrides() -> dict[str, int]:
    """Ports the operator actually set, read from `.env`.

    WITHOUT THIS THE GATE READS A FICTION. Compose defaults are what the file says; `.env` is what
    runs. On 2026-09-02 the dashboard was moved to `NEW_DASHBOARD_PORT=8802` -- aify-env's own
    DEFAULT_PORT -- and this gate stayed green, because it was still reading the `8801` fallback that
    nothing was using. The collision was real: aify-env binds loopback, so it SHADOWED the Docker
    publish instead of failing to start, and the dashboard on that port answered aify-env's 404.
    A published port that fails to bind is loud; one that is quietly shadowed is not.

    Absent or unreadable `.env` yields no overrides, which is correct for a fresh checkout -- there
    the compose defaults ARE what runs.
    """
    path = REPO / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip()
        if value.isdigit():
            found[name.strip()] = int(value)
    return found


def published_host_ports() -> set[int]:
    """Every host port this stack publishes, as CONFIGURED -- defaults overridden by `.env`."""
    text = COMPOSE.read_text(encoding="utf-8")
    overrides = env_overrides()
    ports = {int(m) for m in PUBLISHED.findall(text)}
    for name in PUBLISHED_VAR.findall(text):
        if name in overrides:
            ports.add(overrides[name])
    return ports


def aify_env_default_port() -> int:
    source = (AIFY_ENV / "bin" / "aify-env.mjs").read_text(encoding="utf-8")
    match = re.search(r"const DEFAULT_PORT\s*=\s*(\d+)", source)
    assert match, "aify-env no longer declares DEFAULT_PORT; this gate cannot see the value"
    return int(match.group(1))


def test_the_compose_scan_finds_the_ports_this_stack_is_known_to_publish():
    """Positive control. A regex that silently matched nothing would make the collision test pass by
    looking at an empty set, which is the failure this whole file is about."""
    ports = published_host_ports()
    assert 8800 in ports, f"the service port is missing from the scan: {sorted(ports)}"
    assert 8801 in ports, f"Dashboard Next's port is missing from the scan: {sorted(ports)}"
    assert len(ports) >= 3, f"implausibly few published ports: {sorted(ports)}"


def test_a_port_this_stack_does_not_publish_is_reported_as_free():
    """Negative control: the scan must be able to say NO, or its yes means nothing."""
    assert 9 not in published_host_ports()


@pytest.mark.skipif(not AIFY_ENV.exists(), reason="aify-env checkout not present")
def test_aify_env_default_does_not_collide_with_this_stack():
    default = aify_env_default_port()
    collisions = published_host_ports() & {default}
    assert not collisions, (
        f"aify-env defaults to {default}, which docker-compose.yml publishes. An operator running both "
        "on one host -- the topology this split exists for -- gets a port conflict, or worse points "
        "AIFY_ENV_ENDPOINT at a service that answers /health and is not an environment."
    )
