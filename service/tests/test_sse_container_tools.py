"""The five container tools, driven for the first time.

They were reachable over SSE from the day the transport shipped and nothing called a line of them.
That is the same gap `test_sse_renderers.py` was written to close for the `comms_*` renderers — and
the reason it stayed open here is structural: these lived in the middle of a 730-line module that
cannot be imported by name (`mcp/` is the PyPI package, not this repo's), so reaching them meant
loading a file by path and accepting whatever else that executed. Extracted, they are an ordinary
import.

WHAT THESE ASSERT IS THE DEGENERATE PATH, mostly. Every one of the five answers "No container
manager configured" when the app was never bound or carries no manager, and that branch is the one a
real deployment hits when Docker is unavailable — an agent asking `gpu_status` on a host without a
manager must get that sentence, not an AttributeError on None. The happy paths are driven against a
fake manager to pin the shape of what is returned, because two of them (`list_containers`,
`gpu_status`) pass a manager's data straight through to an agent.
"""

from __future__ import annotations

import asyncio
import inspect
import unittest

from service.sse import container_tools as ct


class _State:
    def __init__(self, manager=None):
        if manager is not None:
            self.container_manager = manager


class _App:
    def __init__(self, manager=None):
        self.state = _State(manager)


class _Gpu:
    def get_status(self):
        return {"0": "vllm", "1": None}


class _Manager:
    """Only what the tools actually touch. A fuller fake would hide which surface they depend on."""

    def __init__(self, fail=None):
        self.definitions = {"vllm": object()}
        self.states = {}
        self.docker = True
        self.gpu = _Gpu()
        self.stopped = []
        self._fail = fail

    def list_containers(self):
        return [{"name": "vllm", "status": "running"}]

    def get_groups(self):
        return {"gpu": ["vllm"]}

    def get_container_logs(self, name, tail=50):
        return f"{name}:{tail} lines"

    async def start_container(self, name):
        if self._fail:
            raise RuntimeError(self._fail)

        class _S:
            status = type("St", (), {"value": "running"})()
            internal_url = "http://vllm:8000"

        return _S()

    async def stop_container(self, name):
        if self._fail:
            raise RuntimeError(self._fail)
        self.stopped.append(name)


def run(coro):
    return asyncio.run(coro)


class ContainerToolsTests(unittest.TestCase):
    def setUp(self):
        # The module global is shared with the live transport, which imports this same module.
        # Restoring it is what keeps this file from changing another test's answers.
        self.addCleanup(ct.bind_app, None)

    # ── nothing bound ────────────────────────────────────────────────────────────────
    def test_every_tool_says_so_when_no_manager_is_configured(self):
        """The branch a Docker-less host takes. It must be a sentence, never an exception."""
        ct.bind_app(None)
        self.assertIsNone(ct.get_manager())
        for tool in (ct.list_containers, ct.gpu_status):
            self.assertEqual({"error": "No container manager configured"}, run(tool()))
        for tool in (ct.start_container, ct.stop_container):
            self.assertEqual({"error": "No container manager configured"}, run(tool("vllm")))
        self.assertEqual("No container manager configured", run(ct.container_logs("vllm")))

    def test_an_app_without_a_manager_is_the_same_as_no_app(self):
        """`app.state` exists but carries no container_manager — a real startup ordering case."""
        ct.bind_app(_App())
        self.assertIsNone(ct.get_manager())
        self.assertEqual({"error": "No container manager configured"}, run(ct.list_containers()))

    # ── bound ────────────────────────────────────────────────────────────────────────
    def test_list_and_gpu_pass_the_manager_data_through(self):
        ct.bind_app(_App(_Manager()))
        self.assertEqual(
            {"containers": [{"name": "vllm", "status": "running"}], "groups": {"gpu": ["vllm"]}},
            run(ct.list_containers()),
        )
        self.assertEqual({"0": "vllm", "1": None}, run(ct.gpu_status()))

    def test_an_unknown_container_is_refused_before_the_manager_is_asked(self):
        """And `start` lists what IS available — an agent given only "Unknown" cannot recover."""
        ct.bind_app(_App(_Manager()))
        self.assertEqual(
            {"error": "Unknown container: nope", "available": ["vllm"]},
            run(ct.start_container("nope")),
        )
        self.assertEqual({"error": "Unknown container: nope"}, run(ct.stop_container("nope")))
        self.assertEqual("Unknown container: nope", run(ct.container_logs("nope")))

    def test_start_and_stop_report_the_manager_failure_rather_than_raising(self):
        """A raise here would surface to the agent as a transport error with no cause in it."""
        ct.bind_app(_App(_Manager(fail="docker daemon is not running")))
        self.assertEqual({"error": "docker daemon is not running"}, run(ct.start_container("vllm")))
        self.assertEqual({"error": "docker daemon is not running"}, run(ct.stop_container("vllm")))

    def test_the_happy_paths(self):
        manager = _Manager()
        ct.bind_app(_App(manager))
        self.assertEqual({"status": "running", "url": "http://vllm:8000"}, run(ct.start_container("vllm")))
        self.assertEqual({"status": "stopped", "name": "vllm"}, run(ct.stop_container("vllm")))
        self.assertEqual(["vllm"], manager.stopped)
        self.assertEqual("vllm:5 lines", run(ct.container_logs("vllm", tail=5)))

    # ── registration ─────────────────────────────────────────────────────────────────
    def test_register_puts_exactly_the_declared_tools_on_the_server(self):
        """The move's real risk: declaration and registration are now separate steps.

        A tool that stops being registered does not fail anything here — it simply stops existing
        for every SSE client, silently. So the registrar is driven against a recording stand-in and
        checked against `TOOLS`, and `TOOLS` is checked against what the module actually declares.
        """
        registered = []

        class _Server:
            def tool(self):
                return lambda fn: registered.append(fn) or fn

        ct.register(_Server())
        self.assertEqual(list(ct.TOOLS), registered)
        self.assertEqual(
            ["container_logs", "gpu_status", "list_containers", "start_container", "stop_container"],
            sorted(t.__name__ for t in registered),
        )

    def test_the_declared_tool_list_is_not_missing_a_public_coroutine(self):
        """Catches the reverse: a sixth tool added to the module and never added to TOOLS.

        `bind_app`/`get_manager` are the module's non-tool surface and are named here rather than
        inferred, so adding a helper is a deliberate edit to this list instead of a silent pass.
        """
        public = {
            name for name in vars(ct)
            if not name.startswith("_") and inspect.iscoroutinefunction(getattr(ct, name))
        }
        self.assertEqual({t.__name__ for t in ct.TOOLS}, public)


if __name__ == "__main__":
    unittest.main()
