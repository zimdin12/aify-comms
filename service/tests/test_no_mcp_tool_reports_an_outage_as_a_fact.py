"""No `comms_*` tool turns a service error into a confident statement about the fleet.

THE FAILURE. `_api` returns the parsed body, or a dict carrying `detail` when the request failed.
A tool that reads its result without checking gets `{}` or `None` out of `.get(...)` and renders the
absence as a fact -- to an AGENT, which then acts on it.

MEASURED 2026-08-28 by handing each tool a canned HTTP 500 and reading what it said:

    comms_agents        -> "No agents registered."
    comms_channel_list  -> "No channels."
    comms_search        -> 'No results for "anything" (searched: nothing).'
    comms_run_status    -> "Run not found: run-123"

An agent told "Run not found" may re-dispatch work that is already running. An agent told "No agents
registered" may conclude the fleet is gone. Neither statement was true; the service was simply down.

WHY IT SURVIVED A FIX AIMED AT IT. `api_client.py` was changed on 2026-08-18 to return an error SHAPE
rather than a confident empty, and its note explains the reasoning: "Returning an error shape fixes
every caller without touching one, because they already branch on `detail`." That premise held for
fifteen of the twenty call sites and not for these four. A shared fix that depends on how its callers
are written needs the callers checked, and this is that check -- for all of them, not the four.

`comms_dispatch` was the fifth site with no `detail` branch and is NOT a defect: it guards on
`r.get("ok")` instead, which an error dict also fails. It is exercised below anyway, because the
property is "does not lie", not "checks a particular key".
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import importlib

SSE_DIR = Path(__file__).resolve().parent.parent / "sse"


def _transport_modules():
    """Every `*_tools` module in the SSE transport, DISCOVERED rather than listed.

    A hand-written list is a defect with a delay on it: the first draft of this file named a
    `file_tools` that does not exist and omitted three that do, so a new transport would have been
    silently uncovered by the very gate meant to cover it.
    """
    found = []
    for path in sorted(SSE_DIR.glob("*_tools.py")):
        found.append(importlib.import_module("service.sse." + path.stem))
    return found


MODULES = tuple(_transport_modules())

#: What the service hands back when it is broken. `detail` is the key `api_client` promises to set on
#: EVERY error path, which is exactly what the callers are supposed to notice.
ERROR = {"detail": "HTTP 500 from /agents: database is locked", "status": 500, "text": "boom"}

#: A value that satisfies a required parameter without meaning anything. The tools are never allowed
#: to reach the service, so the values only have to be type-plausible.
FILLER = {"limit": 5, "scope": "all", "mode": "full", "filter": "unread"}


def _tools():
    """Every exported coroutine a transport registers, with a callable that invokes it."""
    found = []
    for module in MODULES:
        for tool in getattr(module, "TOOLS", ()):
            found.append((module, tool))
    return found


def _call_args(tool):
    """Fill required parameters only; optional ones keep their defaults."""
    kwargs = {}
    for name, param in inspect.signature(tool).parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        kwargs[name] = FILLER.get(name, "probe")
    return kwargs


#: Phrases that state something about the fleet. If a tool says one of these while the service is
#: down, an agent reads it as a fact.
LIES = (
    "no agents registered", "no channels", "not found", "no results",
    "inbox empty", "no messages", "no files", "no runs",
)


class NoMcpToolReportsAnOutageAsAFactTests(unittest.TestCase):
    def test_the_tools_were_actually_found(self) -> None:
        """The control. An empty tool list passes every assertion below while testing nothing, and
        this repo has produced that wrong zero more than once."""
        tools = _tools()
        self.assertGreaterEqual(len(tools), 10, f"only {len(tools)} tools discovered")
        names = {tool.__name__ for _, tool in tools}
        for expected in ("comms_agents", "comms_run_status", "comms_channel_list"):
            self.assertIn(expected, names, f"{expected} is not among the discovered tools")

    def test_the_probe_can_tell_a_lie_from_an_error(self) -> None:
        """The negative control. `LIES` matching nothing would make the sweep vacuous, so it is
        checked against the exact strings the tools used to return."""
        for sentence in ("No agents registered.", "Run not found: run-123", "No channels."):
            self.assertTrue(
                any(phrase in sentence.lower() for phrase in LIES),
                f"the probe would not have recognised {sentence!r} as a claim about the fleet",
            )
        self.assertFalse(
            any(phrase in "Error: HTTP 500 from /agents".lower() for phrase in LIES),
            "the probe mistakes an honest error for a claim",
        )

    def test_no_tool_states_a_fact_when_the_service_is_broken(self) -> None:
        async def failing(*args, **kwargs):
            return dict(ERROR)

        offenders = []
        covered = 0
        skipped = []
        for module, tool in _tools():
            # A transport that does not reach the service through `_api` cannot be probed this way.
            # `container_tools` calls the app directly. RECORDED, not silently dropped: a gate that
            # quietly skips half its subjects reads exactly like one that passed.
            if not hasattr(module, "_api"):
                skipped.append(tool.__name__)
                continue
            covered += 1
            original = module._api
            module._api = failing
            try:
                answer = str(asyncio.run(tool(**_call_args(tool))))
            except TypeError:
                # A signature this probe cannot fill. Skipped rather than guessed at: inventing an
                # argument would test a call the agent never makes.
                continue
            except Exception as exc:
                # A raise is not a lie. The transport turns it into an error for the agent.
                answer = f"raised {type(exc).__name__}: {exc}"
            finally:
                module._api = original
            lowered = answer.lower()
            if any(phrase in lowered for phrase in LIES) and "error" not in lowered:
                offenders.append(f"{tool.__name__} -> {answer.splitlines()[0][:70]!r}")

        self.assertGreaterEqual(
            covered, 10,
            f"only {covered} tools were actually probed ({len(skipped)} skipped: {skipped}); a sweep "
            "that reaches almost nothing agrees with a healthy repo for the wrong reason",
        )
        self.assertEqual(
            offenders, [],
            "these tools answer a service outage with a statement about the fleet, which the agent "
            "on the other end will act on: " + "; ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
