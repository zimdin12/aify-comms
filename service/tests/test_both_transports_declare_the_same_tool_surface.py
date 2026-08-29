r"""The two transports agree on tool NAMES. This is about what those tools ACCEPT.

`transport-parity.test.js` compares the names stdio and SSE expose, and carries a declared list of
tools that are intentionally SSE-only. Nothing compared their PARAMETERS, so the same tool could take
different arguments on the two transports with nothing to say which -- and the failure is quiet in
the way this repo keeps meeting: an SSE client passing a parameter only stdio declares is rejected,
with no hint that the other transport would have taken it.

FOUND BY MEASURING, on 2026-08-29: 22 tools declared by both, and TEN of them disagreed. One was a
capability an SSE client simply did not have -- `comms_dispatch` had no `priority`, so every SSE
dispatch took the endpoint's "normal" default while a stdio client could mark one urgent.
`DispatchRequest.priority` had accepted it all along. That one is closed in the same change; the
other nine are real and each has a reason, recorded below.

THE POINT OF THE TABLE is not the nine entries. It is that the next divergence has to arrive as a
decision with a reason attached, instead of as drift nobody sees. That is the same shape as the
SSE-only NAME list next door, and as `NOT_AGENT_COLUMNS` in the rename gate.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.tool_schemas import tool_parameters

REPO = Path(__file__).resolve().parents[2]
SSE = REPO / "service" / "sse"

#: Parameters the SSE transport takes for its own plumbing, never from a caller's tool call.
PLUMBING = frozenset({"self", "ctx", "context", "request"})

#: `from` is a Python KEYWORD, so no SSE tool can spell the actor the way stdio does. That is forced
#: by the language rather than decided by anyone, so it is normalised once instead of being declared
#: on each of the twelve tools that take an actor.
ALIASES = {"from": "from_agent"}

#: (tool, side, parameter) -> why it is only on that side. `side` is the transport that HAS it.
#:
#: Every reason here was read out of the code it describes, not inferred from the parameter's name.
DECLARED_DIFFERENCES = {
    # The two deletes added on 2026-08-18 name the actor after the WIRE FIELD whose ownership check
    # they trigger -- `requestedBy` is what the endpoint refuses to act without -- while stdio names
    # the actor `from` on every tool it has. Cosmetic, and the SSE spelling is the clearer of the
    # two: it says whose authority is being checked rather than who is talking.
    ("comms_channel_delete", "stdio", "from_agent"): "stdio spells the actor `from` on every tool",
    ("comms_channel_delete", "sse", "requestedBy"): "named after the endpoint's ownership-check field",
    ("comms_unshare", "stdio", "from_agent"): "stdio spells the actor `from` on every tool",
    ("comms_unshare", "sse", "requestedBy"): "named after the endpoint's ownership-check field",

    # stdio can join ANOTHER agent to a channel; SSE joins the caller and passes `from_agent` as the
    # member. NOT closed here: who may add somebody else to a channel is a permissions question, and
    # widening or narrowing it is not a parity decision to make from a test.
    ("comms_channel_join", "stdio", "agentId"): "stdio can join an agent other than the caller",

    # `silent=true` is legacy inbox-only delivery -- the SSE docstring says so in those words. stdio
    # hardcodes `shouldTrigger = true` and never grew the parameter.
    ("comms_channel_send", "sse", "silent"): "legacy inbox-only delivery; stdio never grew it",
    ("comms_send", "sse", "silent"): "legacy inbox-only delivery; stdio never grew it",

    # The stdio bridge RUNS AS an agent and fills the actor from `AIFY_AGENT_ID` rather than asking.
    # SSE serves arbitrary clients and has no ambient identity, so it must be told.
    ("comms_console_input", "sse", "from_agent"): "SSE has no ambient identity; the bridge does",

    # Local-machine capabilities. The SSE server is reached over the network, so there is no browser
    # of the caller's to open and no path of the caller's to read.
    ("comms_dashboard", "stdio", "open"): "opens a browser on the machine the bridge runs on",
    ("comms_share", "stdio", "filePath"): "reads a file from the caller's own filesystem",

    # `comms_register`'s own SSE docstring is the record: "SSE clients can coordinate work, but
    # cannot host local runtime launches." Seven of these eight describe a launch or a host.
    # `description` is the odd one out and is a genuine omission rather than a boundary.
    ("comms_register", "stdio", "appServerUrl"): "launch/hosting detail an SSE client has no part in",
    ("comms_register", "stdio", "launchMode"): "launch/hosting detail an SSE client has no part in",
    ("comms_register", "stdio", "machineId"): "launch/hosting detail an SSE client has no part in",
    ("comms_register", "stdio", "managedBy"): "launch/hosting detail an SSE client has no part in",
    ("comms_register", "stdio", "runtime"): "launch/hosting detail an SSE client has no part in",
    ("comms_register", "stdio", "sessionHandle"): "launch/hosting detail an SSE client has no part in",
    ("comms_register", "stdio", "sessionMode"): "launch/hosting detail an SSE client has no part in",
    ("comms_register", "stdio", "description"): "not a launch detail; an omission, not a boundary",
}


def sse_tool_parameters() -> dict[str, set[str]]:
    """tool name -> the parameters its SSE signature declares.

    Read from the `async def comms_*` signature by AST. The tools are declared bare and registered
    by a `register(mcp_server)` call, so the signature IS the schema FastMCP publishes.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(SSE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("comms_"):
                continue
            args = node.args
            names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
            found[node.name] = {n for n in names if n not in PLUMBING}
    return found


def differences(stdio: dict[str, set[str]], sse: dict[str, set[str]]) -> set[tuple[str, str, str]]:
    """Every (tool, side, parameter) one transport declares and the other does not, aliases applied."""
    normalised = {tool: {ALIASES.get(name, name) for name in names} for tool, names in stdio.items()}
    out: set[tuple[str, str, str]] = set()
    for tool in set(normalised) & set(sse):
        for name in normalised[tool] - sse[tool]:
            out.add((tool, "stdio", name))
        for name in sse[tool] - normalised[tool]:
            out.add((tool, "sse", name))
    return out


class BothTransportsDeclareTheSameToolSurfaceTests(unittest.TestCase):
    def test_the_two_readers_find_the_tools_they_are_supposed_to(self):
        """Anti-vacuity: the comparison is over the INTERSECTION, so two empty readers agree
        perfectly. Both must find a real inventory, and a tool known to take parameters must arrive
        with them on both sides."""
        stdio, sse = tool_parameters(), sse_tool_parameters()
        self.assertGreaterEqual(len(stdio), 30, f"the stdio reader found only {len(stdio)} tools")
        self.assertGreaterEqual(len(sse), 15, f"the SSE reader found only {len(sse)} tools")
        self.assertGreaterEqual(len(set(stdio) & set(sse)), 20, "the shared surface looks truncated")
        for side, schemas in (("stdio", stdio), ("sse", sse)):
            self.assertIn("body", schemas.get("comms_send", set()), f"{side} lost comms_send's body")

    def test_every_parameter_difference_is_declared_with_a_reason(self):
        undeclared = sorted(differences(tool_parameters(), sse_tool_parameters())
                            - set(DECLARED_DIFFERENCES))
        self.assertEqual(
            [], undeclared,
            "one transport declares a parameter the other does not, and nothing says why. Either "
            "give the other transport the parameter, or add it to DECLARED_DIFFERENCES with the "
            "reason it belongs to one side:\n  "
            + "\n  ".join(f"{tool} ({side} only): {name}" for tool, side, name in undeclared),
        )

    def test_no_declaration_describes_a_difference_that_is_gone(self):
        """The other direction, and the one that rots silently: a declared difference that has been
        closed leaves a reason standing for something that is no longer true, and the next reader
        believes it."""
        stale = sorted(set(DECLARED_DIFFERENCES)
                       - differences(tool_parameters(), sse_tool_parameters()))
        self.assertEqual(
            [], stale,
            "these differences no longer exist; delete their entries rather than leaving a reason "
            "for something that is not happening:\n  "
            + "\n  ".join(f"{tool} ({side}): {name}" for tool, side, name in stale),
        )

    def test_every_declaration_carries_an_actual_reason(self):
        for key, reason in DECLARED_DIFFERENCES.items():
            self.assertTrue(str(reason).strip(), f"{key} is declared with no reason")

    def test_the_comparison_sees_a_planted_difference_and_honours_the_alias(self):
        """The detector, driven directly. A comparison that returned nothing would pass the two
        tests above by looking at nothing, and the alias is the one normalisation it applies -- if
        that stopped working, every actor parameter would read as a difference."""
        stdio = {"comms_x": {"from", "to", "onlyHere"}}
        sse = {"comms_x": {"from_agent", "to"}}
        self.assertEqual({("comms_x", "stdio", "onlyHere")}, differences(stdio, sse))
        # Same shapes, nothing extra: the alias alone must make them agree.
        self.assertEqual(set(), differences({"comms_x": {"from", "to"}}, {"comms_x": {"from_agent", "to"}}))
        # And a tool only one side has is not a parameter difference -- names are the other gate's job.
        self.assertEqual(set(), differences({"comms_only_stdio": {"a"}}, {"comms_x": {"a"}}))

    def test_an_sse_dispatch_can_set_a_priority(self):
        """The difference this change closed rather than declared. It is asserted by name because
        the table above would happily hold an entry for it instead, and an accepted absence is how
        it lived for as long as it did."""
        self.assertIn("priority", sse_tool_parameters().get("comms_dispatch", set()))
        self.assertIn("priority", tool_parameters().get("comms_dispatch", set()))


if __name__ == "__main__":
    unittest.main()
