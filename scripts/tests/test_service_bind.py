"""Check the host addresses rendered by Compose without starting containers."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
PORTS = {
    "service": (8800, 8800),
    "new-dashboard": (8801, 8801),
    "https-proxy": (8443, 8443),
}
CUSTOM_PORTS = {
    "service": (18800, 8800),
    "new-dashboard": (18801, 8801),
    "https-proxy": (18443, 18443),
}


@pytest.fixture(scope="module")
def compose_cli():
    docker = shutil.which("docker")
    if not docker or subprocess.run([docker, "compose", "version"], capture_output=True, timeout=15).returncode:
        pytest.skip("Docker Compose CLI is not available")
    return [docker, "compose", "--profile", "https", "--env-file", ".env", "-f", COMPOSE_FILE.name]


@pytest.mark.parametrize("binding,host_ip,custom", [
    (None, None, False), ("", None, False), ("127.0.0.1", "127.0.0.1", False),
    ("127.0.0.1", "127.0.0.1", True),
])
def test_service_bind_changes_only_the_three_host_addresses(tmp_path, compose_cli, binding, host_ip, custom):
    shutil.copyfile(COMPOSE_FILE, tmp_path / COMPOSE_FILE.name)
    values = {} if binding is None else {"SERVICE_BIND": binding}
    if custom:
        values.update(SERVICE_PORT="18800", NEW_DASHBOARD_PORT="18801", HTTPS_PORT="18443")
    (tmp_path / ".env").write_text("".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8")
    controlled = {"SERVICE_BIND", "SERVICE_PORT", "NEW_DASHBOARD_PORT", "HTTPS_PORT"}
    env = {key: value for key, value in os.environ.items() if key.upper() not in controlled
           and not key.upper().startswith("COMPOSE_")}
    result = subprocess.run([*compose_cli, "config", "--format", "json"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    expected = CUSTOM_PORTS if custom else PORTS
    actual = {}
    for name, service in services.items():
        if service.get("ports"):
            port, = service["ports"]
            actual[name] = (int(port["published"]), port["target"], port.get("host_ip") or None)
    assert actual == {name: (*pair, host_ip) for name, pair in expected.items()}
