"""`comms_inbox` promised preview-only triage and consumed the unread state instead.

THE DEFECT (T1), live on BOTH transports until 2026-09-01. Both descriptions said "use
mode=headers for preview-only triage". The route's `mode` only chooses whether bodies are
returned -- `include_body = mode != "headers"` -- while marking read is gated on a SEPARATE `peek`
parameter. So an agent that read the description and followed it exactly destroyed the unread state
it was trying to preserve, and nothing told it that had happened.

`peek` was not a missing capability. It has existed in the route for a long time, the dashboard has
a human Peek toggle, and `notify-check.js` comments on it. It was simply reachable by nobody:
twelve parameter declarations across the two inbox tools and `peek` was not one of them.

WHAT THIS FILE CHECKS, and what it deliberately leaves alone. The ROUTE's peek behaviour is already
covered several times over -- `test_unread_total_is_global_and_current.py` and
`test_a_reported_count_counts_the_rows_it_names.py` both drive it, and
`test_api_v2_regressions.py` uses `peek=true` directly. Re-proving that here would be cost without
coverage. The gap those tests cannot see is the TOOL SURFACE: whether an agent can reach the
parameter at all, and whether the sentence it is given is true.

READ FROM THE DECLARED SCHEMAS, not from prose about them, using the same two harvesters the
transport-parity gate uses.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from service.tests.test_both_transports_declare_the_same_tool_surface import sse_tool_parameters
from service.tests.tool_schemas import tool_parameters

REPO = Path(__file__).resolve().parents[2]
STDIO_INBOX = REPO / "mcp" / "stdio" / "inbox-tools.mjs"
SSE_INBOX = REPO / "service" / "sse" / "inbox_tools.py"


def stdio_description() -> str:
    """The second argument of `server.tool("comms_inbox", ...)` -- the text an agent receives."""
    text = STDIO_INBOX.read_text(encoding="utf-8")
    start = text.index('server.tool(\n    "comms_inbox"')
    window = text[start:start + 1200]
    # Everything between the tool name and the schema object is the description, as a run of
    # concatenated string literals.
    body = window[window.index('"comms_inbox"') + len('"comms_inbox"'):]
    body = body[:body.index("\n    {")]
    return " ".join(re.findall(r'"([^"]*)"', body))


def sse_description() -> str:
    """`comms_inbox`'s docstring, which is what FastMCP hands the model."""
    text = SSE_INBOX.read_text(encoding="utf-8")
    start = text.index("async def comms_inbox")
    window = text[start:]
    opening = window.index('"""')
    closing = window.index('"""', opening + 3)
    return window[opening + 3:closing]


class TheInboxToolCanDoWhatItSaysTests(unittest.TestCase):
    # -- controls on the instruments ---------------------------------------------------------

    def test_the_harvesters_see_a_real_tool(self):
        """POSITIVE CONTROL. Harvesters that returned nothing would make every assertion vacuous."""
        stdio, sse = tool_parameters(), sse_tool_parameters()
        self.assertIn("comms_inbox", stdio, "the stdio harvester did not find comms_inbox at all")
        self.assertIn("comms_inbox", sse, "the SSE harvester did not find comms_inbox at all")
        self.assertIn("mode", stdio["comms_inbox"], "the stdio harvester missed a parameter that is there")
        self.assertIn("mode", sse["comms_inbox"], "the SSE harvester missed a parameter that is there")

    def test_the_harvesters_exclude_something_absent(self):
        """NEGATIVE CONTROL, paired with the above."""
        self.assertNotIn("zzNoSuchParam", tool_parameters().get("comms_inbox", set()))
        self.assertNotIn("zzNoSuchParam", sse_tool_parameters().get("comms_inbox", set()))

    def test_the_descriptions_are_actually_read(self):
        """POSITIVE CONTROL on the description readers, which are the fiddliest part of this file."""
        for name, text in (("stdio", stdio_description()), ("sse", sse_description())):
            self.assertIn("inbox", text.lower(), f"the {name} description reader returned {text!r}")
            self.assertGreater(len(text), 60, f"the {name} description reader returned {text!r}")

    # -- the gate ------------------------------------------------------------------------------

    def test_both_transports_expose_peek(self):
        """The capability existed in the route and no tool could reach it."""
        for name, params in (("stdio", tool_parameters()), ("sse", sse_tool_parameters())):
            self.assertIn(
                "peek", params.get("comms_inbox", set()),
                f"{name}'s comms_inbox cannot ask for a non-consuming read, so an agent that wants "
                "to triage without destroying its unread state has no way to say so",
            )

    def test_neither_description_calls_headers_preview_only(self):
        """The exact false promise, pinned so it cannot come back.

        `mode=headers` chooses whether BODIES are returned. It has never had anything to do with
        whether a message is consumed, and describing it as preview-only invited the one action that
        loses the state the agent was protecting.
        """
        for name, text in (("stdio", stdio_description()), ("sse", sse_description())):
            self.assertNotIn(
                "preview-only", text.lower(),
                f"{name}'s comms_inbox is describing mode=headers as preview-only again. It is not: "
                "the route marks the returned messages read unless `peek` is set.",
            )

    def test_a_description_that_mentions_headers_also_names_peek(self):
        """The positive form, which is what actually keeps the sentence true.

        Banning one phrase only stops one wording. What has to hold is that whenever the description
        explains `mode=headers`, it also says which parameter preserves unread state -- otherwise the
        next rewrite reintroduces the same wrong implication in different words.
        """
        for name, text in (("stdio", stdio_description()), ("sse", sse_description())):
            if "headers" in text.lower():
                self.assertIn(
                    "peek", text.lower(),
                    f"{name}'s comms_inbox explains mode=headers without naming peek, so a reader is "
                    "left to infer that headers alone is non-consuming -- which is the defect",
                )

    def test_neither_client_sends_a_falsy_peek(self):
        """The trap the route documents, and the reason both clients set the key only when true.

        `peek` is a STRING on the route and the gate is `bool(peek)`, so ANY non-empty value counts:
        sending `peek=false` reads as PEEK. Omitting the key is the only way to say no. A client that
        forwarded a boolean directly would turn "do not peek" into "peek".
        """
        for name, path in (("stdio", STDIO_INBOX), ("sse", SSE_INBOX)):
            text = path.read_text(encoding="utf-8")
            for bad in ('"peek", String(', '"peek", str(', '"peek"] = str(peek)', '"peek": peek'):
                self.assertNotIn(
                    bad, text,
                    f"{name} forwards peek's value instead of setting the key only when true; "
                    "`peek=false` would then read as peek on the route",
                )
