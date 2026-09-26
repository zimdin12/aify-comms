"""The service's shutdown fits inside the time Docker gives it before SIGKILL.

The terminal-output drain runs in the lifespan shutdown, which uvicorn reaches only after every open
connection has finished. With no `--timeout-graceful-shutdown` that wait is unbounded, and a claim
long-poll holds a connection for 20-25 s while `/listen` can hold one for 600 s, so Docker's stop
timeout (10 s by default, 1 s on the running container when measured) killed the process first and
the drain never ran (v0.7.2, external review). uvicorn's wait is now bounded and the service's grace
period covers that wait, the drain and a margin for closing the pool.

Read from the two files that decide it, so a change to either is judged against the other.
"""

import json
import re
import unittest
from pathlib import Path

from service.terminal_write_queue import SHUTDOWN_DRAIN_SECONDS

ROOT = Path(__file__).resolve().parents[2]
MARGIN_SECONDS = 2  # closing the pool and the change feed after the drain


def _cmd_flag(cmd: list, flag: str):
    return float(cmd[cmd.index(flag) + 1]) if flag in cmd else None


def _service_block(compose: str) -> str:
    """The `service:` entry of the top-level `services:` map, up to the next two-space key."""
    match = re.search(r"^  service:\n((?:(?:    .*)?\n)+?)(?=^  \S)", compose, re.M)
    return match.group(1) if match else ""


def _seconds(value: str) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)(s|m)?\s*", value)
    if not match:
        raise ValueError(f"unreadable duration {value!r}")
    return float(match.group(1)) * (60 if match.group(2) == "m" else 1)


class ShutdownFitsInsideTheStopGracePeriodTests(unittest.TestCase):
    def test_uvicorn_bounds_its_wait_and_the_grace_period_covers_the_drain(self):
        cmd_line = next(l for l in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines() if l.startswith("CMD "))
        cmd = json.loads(cmd_line[len("CMD "):])
        graceful = _cmd_flag(cmd, "--timeout-graceful-shutdown")
        self.assertIsNotNone(graceful, f"uvicorn waits for open long-polls without limit: {cmd}")

        block = _service_block((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertIn("container_name", block, "CONTROL: the service block was not found; this reader is stale")
        grace = re.search(r"^    stop_grace_period:\s*(\S+)", block, re.M)
        self.assertIsNotNone(grace, "no stop_grace_period: Docker's default (10 s) or the container's own applies")
        needed = graceful + SHUTDOWN_DRAIN_SECONDS + MARGIN_SECONDS
        self.assertGreaterEqual(_seconds(grace.group(1)), needed,
                                f"the grace period must cover uvicorn's wait {graceful} s, the drain {SHUTDOWN_DRAIN_SECONDS} s and {MARGIN_SECONDS} s")

    def test_the_readers_can_say_no(self):
        self.assertIsNone(_cmd_flag(["python", "-m", "uvicorn"], "--timeout-graceful-shutdown"))
        self.assertEqual(_service_block("services:\n  other:\n    image: x\n  next:\n"), "")
        self.assertEqual(_seconds("20s"), 20)
        self.assertEqual(_seconds("1m"), 60)
