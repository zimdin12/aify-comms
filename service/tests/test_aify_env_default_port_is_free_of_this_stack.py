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
PUBLISHED = re.compile(r'^\s*-\s*"(?:\$\{[A-Z_]+:-)?(\d+)\}?:\d+"', re.MULTILINE)


def published_host_ports() -> set[int]:
    return {int(m) for m in PUBLISHED.findall(COMPOSE.read_text(encoding="utf-8"))}


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
