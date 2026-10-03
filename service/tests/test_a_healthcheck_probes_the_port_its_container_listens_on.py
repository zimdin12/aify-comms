"""Each service's healthcheck probes the port its container listens on, not the one the host publishes.

The service's check read `http://localhost:${SERVICE_PORT:-8800}/health`, but SERVICE_PORT is the HOST side
of `"${SERVICE_PORT:-8800}:8800"`. A stack published elsewhere (the staging stack, SERVICE_PORT=8820) probed
a port nothing listens on: 3482 failures in a row against a service answering 200 on 8800 (2026-10-03).

The container port is DERIVED from each service's own port mapping, so this holds for any service added.
"""

import re
import unittest
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
#: `- "${HOST_VAR:-8800}:8800"` or `- "8188:8188"`: the CONTAINER side is the right half.
MAPPING = re.compile(r'^\s*-\s*"[^"]*:(?:\$\{[A-Z_]+:-)?(\d+)\}?"\s*$', re.MULTILINE)
PROBE = re.compile(r'http://localhost:([^/"]+)/')


def services(text: str) -> dict[str, str]:
    """Each top-level service's block of the compose file, by name."""
    body = text.split("\nservices:\n", 1)[1]
    body = re.split(r"\n(?=[a-z_]+:\s*$)", body, maxsplit=1, flags=re.MULTILINE)[0]
    blocks = re.split(r"^  ([A-Za-z0-9_-]+):\s*$", body, flags=re.MULTILINE)
    return dict(zip(blocks[1::2], blocks[2::2]))


class AHealthcheckProbesThePortItsContainerListensOn(unittest.TestCase):
    def test_every_healthcheck_probes_a_container_port_of_its_own_service(self):
        checked = []
        for name, block in services(COMPOSE.read_text(encoding="utf-8")).items():
            if "healthcheck:" not in block:
                continue
            listening = set(MAPPING.findall(block))
            for port in PROBE.findall(block.split("healthcheck:", 1)[1]):
                checked.append(name)
                self.assertIn(port, listening, f"{name}'s healthcheck probes {port}; its container listens on {sorted(listening)}")
        self.assertGreaterEqual(len(checked), 2, f"judged too few healthchecks to mean anything: {checked}")
