"""Start a real service against a scratch database, drive it over HTTP, stop it.

A REAL PROCESS, not `TestClient`. The e2e suite exists to catch what in-process tests cannot — startup
ordering, the reconcile sweep running on its own timer, and a caller talking over real HTTP. TestClient
shares the interpreter and would hide all three, which is precisely why the 4000 tests above this
directory have never exercised them.

IT NEVER POINTS AT A RUNNING SERVICE. The port is ephemeral and the database is a `tmp_path`. The
recorded incident behind that rule: a hostile-env run that set `AIFY_SERVER_URL` to `127.0.0.1:8800`
and registered six agents into the operator's production registry, then queued 22 junk spawn requests.
`E2EStack` therefore derives its own URL and passes it to anything it starts, rather than reading one
from the environment.

BOOT IS AWAITED BY POLLING /health, NOT BY SLEEPING. A fixed sleep long enough today is a flake
tomorrow, and this repo has paid for that twice; a sleep too short produces a connection error that
reads as a product failure. If the process dies during boot the harness raises with its captured
output, because "did not become healthy" without the reason is the least useful failure a suite can
give you.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

#: Long enough for a cold interpreter plus schema creation on a slow filesystem (the repo often sits on
#: a 9p/WSL2 mount), short enough that a genuinely broken boot fails the suite rather than hanging it.
DEFAULT_BOOT_TIMEOUT = 45.0

#: The operator key this stack runs with. A fixed literal is correct here and not a secret: the stack is
#: ephemeral, bound to 127.0.0.1 on a random port, and torn down with the test. Tests that assert the
#: operator path need a key that IS configured, because unconfigured fails closed by design.
E2E_OPERATOR_KEY = "e2e-operator-key"


def _free_port() -> int:
    """An ephemeral port the OS says is free.

    Racy in principle — the port could be taken between here and bind — and correct in practice for a
    test host. The alternative, a fixed port, collides with a developer's own service, which is the
    failure this must not have.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ServiceDidNotBoot(RuntimeError):
    """Raised with the service's own output, because the reason is the whole diagnostic value."""


class E2EStack:
    """A running service, addressable over HTTP. Use as a context manager."""

    #: The service derives its database as `Path(config.data_dir) / "aify.db"` (service/main.py) and
    #: reads no DB_PATH at all. The harness therefore takes a DATA DIRECTORY and DERIVES the filename
    #: the same way, rather than accepting a `db_path` it cannot honour — which is what the first
    #: version did, and the isolation test caught it by finding no database where it was promised.
    DB_FILENAME = "aify.db"

    def __init__(self, data_dir: Path, boot_timeout: float = DEFAULT_BOOT_TIMEOUT):
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / self.DB_FILENAME
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._boot_timeout = float(boot_timeout)
        self._proc: Optional[subprocess.Popen] = None

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────

    def __enter__(self) -> "E2EStack":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    def start(self) -> "E2EStack":
        repo_root = Path(__file__).resolve().parents[3]
        env = dict(os.environ)
        env.update({
            # DATA_DIR is the only knob the service reads for this; DB_PATH would be ignored, and
            # setting it would imply a control the harness does not have.
            "DATA_DIR": str(self.data_dir),
            # Explicitly EMPTY rather than inherited: if the developer's shell has an API key set, the
            # stack would demand it on every call here and every assertion would fail on auth instead
            # of on the behaviour under test.
            "API_KEY": "",
            "OPERATOR_KEY": E2E_OPERATOR_KEY,
            # Point every in-process consumer at THIS stack, never at whatever the shell had.
            "AIFY_SERVER_URL": self.base_url,
            "PYTHONPATH": str(repo_root),
            "PYTHONUNBUFFERED": "1",
        })
        # ROLE FLAGS ARE STRIPPED, not overridden with a falsy value. `AIFY_ENVIRONMENT_BRIDGE` once
        # turned a test run into the environment bridge and reaped seven live gateway hosts; a suite
        # that inherits it would do the same from here.
        for role_flag in ("AIFY_ENVIRONMENT_BRIDGE", "AIFY_AGENT_ID", "AIFY_AGENT_ROLE"):
            env.pop(role_flag, None)

        self._proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "service.main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--no-access-log"],
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        deadline = time.time() + self._boot_timeout
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise ServiceDidNotBoot(
                    "the service exited during boot:\n" + self._drain_output()[-4000:]
                )
            try:
                if self.api("GET", "/health").get("status") == "healthy":
                    return self
            except (urllib.error.URLError, ConnectionError, OSError, json.JSONDecodeError):
                time.sleep(0.2)
        output = self._drain_output()
        self.stop()
        raise ServiceDidNotBoot(
            f"the service did not report healthy within {self._boot_timeout}s. Its output:\n"
            + output[-4000:]
        )

    def stop(self) -> None:
        """Terminate, then kill if it will not go. Never leaves a listener behind."""
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

    def _drain_output(self) -> str:
        if not self._proc or not self._proc.stdout:
            return "(no output captured)"
        try:
            return (self._proc.stdout.read() or b"").decode("utf-8", "replace")
        except Exception:  # pragma: no cover - diagnostics must never mask the real failure
            return "(output could not be read)"

    # ── driving it ───────────────────────────────────────────────────────────────────────────

    def api(self, method: str, path: str, body: Optional[dict] = None,
            headers: Optional[dict] = None, expect_error: bool = False) -> dict:
        """One HTTP call. Returns the decoded body.

        `expect_error=True` returns the decoded body of a 4xx/5xx instead of raising — which the
        refusal tests need, because a refusal's TEXT is the thing under test and an exception would
        throw it away.
        """
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if not expect_error:
                raise
            text = exc.read().decode("utf-8", "replace")
            decoded = json.loads(text) if text else {}
            decoded.setdefault("_status", exc.code)
            return decoded
        return json.loads(text) if text else {}

    def status_of(self, method: str, path: str, body: Optional[dict] = None,
                  headers: Optional[dict] = None) -> int:
        """The HTTP status alone, for assertions that are about the code rather than the body."""
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return int(response.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    def is_listening(self) -> bool:
        """Is anything answering on this stack's port? Used to prove teardown actually tore down."""
        with socket.socket() as probe:
            probe.settimeout(1.0)
            return probe.connect_ex(("127.0.0.1", self.port)) == 0
