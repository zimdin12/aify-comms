"""`ConnectionManager` can address a single agent, and nothing has ever connected as one.

WHAT THE CODE OFFERS. `/ws` accepts an optional `?agent_id=`; when present, `connect()` files the
socket under that id, and `notify_agent(agent_id, event, data)` sends to it. Three product call sites
use it -- a new direct message, a new channel message, and a dispatch request -- each of which reads
like the recipient is being pushed a notification.

WHAT ACTUALLY HAPPENS. Measured 2026-08-26 across the repo: the dashboard is the only client, it
connects to `/ws` with NO query parameter, and nothing anywhere connects with `agent_id`. So
`_agents` is permanently empty, `notify_agent` returns without sending, `online_agents()` is always
the empty set, and `active_count()` has no product caller at all.

NOTHING IS BROKEN BY IT, which is why it survived. Delivery to an agent runs over the dispatch claim
loop on HTTP, not over this socket; the notification was an optimisation that never had a consumer.
The cost is a false belief -- three call sites that appear to notify somebody -- and a well-tested
dead half: fourteen assertions across the manager's own suite prove behaviour no product code reaches.

WHY A GATE RATHER THAN A DELETION. Removing product code is the operator's call, and the half may yet
earn its keep: an agent-side socket would make dispatch delivery push rather than poll. This records
the fact instead, and fails the day it stops being true -- either because a client appears (the three
call sites are suddenly live and want reviewing) or because a caller appears for a method that has
none. A fact nobody has written down is rediscovered at full price.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
#: Directories that are not this project's product code: dependencies, vendored assets, test support
#: and the captured `before-*` snapshots kept as fixtures.
PRUNE = {"node_modules", ".git", "__pycache__", ".venv", "venv", "tests", "fixtures", "vendor",
         ".pytest_cache", "data"}
SUFFIXES = {".py", ".js", ".mjs", ".html"}

#: A socket URL that carries an agent id. Deliberately loose about the surrounding syntax and strict
#: about the two things that matter: it is a websocket path and it names an agent.
_AGENT_SOCKET_RE = re.compile(r"/ws[^\"'`\s]*agent_id")
#: Any websocket connection at all, so the scan can prove it is capable of finding one.
_ANY_SOCKET_RE = re.compile(r"new WebSocket\(|websocket_endpoint|\"/ws\"|'/ws'|`\$\{[^`]*\}/ws`")

#: The three product call sites, by file. Named so a fourth arriving is visible rather than folded
#: into a count -- and so removing one is a deliberate edit here rather than a silent drift.
NOTIFY_AGENT_CALL_SITES = {
    "service/routers/channel_send.py",
    "service/routers/dispatch_messages/dispatch.py",
    "service/routers/dispatch_messages/messages.py",
}


def _product_files() -> list[Path]:
    out = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if PRUNE & set(path.relative_to(REPO).parts):
            continue
        out.append(path)
    return sorted(out)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


class AgentAddressedWebsocketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = _product_files()
        cls.texts = {p.relative_to(REPO).as_posix(): _read(p) for p in cls.files}

    def test_the_scan_can_find_a_socket_at_all(self):
        """Positive control. A scan that matches nothing reports "no agent client" identically to one
        that is broken, and the whole finding here IS a zero."""
        self.assertGreater(len(self.files), 200, f"only {len(self.files)} product files walked")
        with_sockets = [name for name, text in self.texts.items() if _ANY_SOCKET_RE.search(text)]
        self.assertIn("service/new_dashboard/realtime-socket.mjs", with_sockets,
                      "the dashboard's own socket was not found; the scan is broken")
        self.assertIn("service/main.py", with_sockets, "the /ws endpoint was not found")

    def test_the_scan_can_say_ABSENT(self):
        """Negative control, in the same run as the zero it is defending."""
        for text in self.texts.values():
            self.assertNotIn("zzNoSuchSocketIdentifierZz", text)

    def test_nothing_connects_as_an_agent(self):
        offenders = [
            f"{name}:{text[:m.start()].count(chr(10)) + 1}"
            for name, text in self.texts.items()
            for m in [_AGENT_SOCKET_RE.search(text)] if m
        ]
        self.assertEqual(offenders, [], (
            "something now connects to /ws with an agent id, so the agent-addressed half of "
            "ConnectionManager is LIVE for the first time:\n  " + "\n  ".join(offenders)
            + "\nThat is not a failure -- it is the change this test exists to catch. Re-read the "
            "three notify_agent call sites: they were written for a consumer that did not exist, and "
            "what they send has never been received by anything."
        ))

    def test_notify_agent_is_called_from_exactly_the_recorded_places(self):
        """The producers, held to a named set. A fourth call site arriving means someone else believes
        this delivers; a missing one means the belief was corrected somewhere and not here."""
        # PYTHON ONLY, and the reason is a false positive this test found on its first run:
        # `realtime-dispositions.mjs` reads the SERVICE'S OWN SOURCE to learn the broadcast names, so
        # it contains the string `notify_agent(` while calling nothing. A mention is not a call, and a
        # scan that cannot tell them apart would have made this set grow every time a tool learned to
        # read the service.
        callers = {
            name for name, text in self.texts.items()
            if name.endswith(".py") and "notify_agent(" in text and not name.endswith("service/ws.py")
        }
        self.assertEqual(callers, NOTIFY_AGENT_CALL_SITES)

    def test_online_agents_still_has_no_product_caller(self):
        """The other half of the pair, and the one still unused.

        `active_count()` was in this test until 2026-08-26 and came out DELIBERATELY: it now answers
        `sockets` on /health, which is asserted below. `online_agents()` remains what it was -- a
        method with a suite and no consumer -- because its answer is always the empty set while
        nothing connects as an agent, so serving it anywhere would report a fact rather than a state.
        """
        callers = sorted(
            name for name, text in self.texts.items()
            if name.endswith(".py") and "online_agents()" in text and not name.endswith("service/ws.py")
        )
        self.assertEqual(callers, [], f"online_agents() now has a product caller: {callers}")

    def test_active_count_is_served_on_health(self):
        """The half that was worth wiring, pinned so it is not quietly dropped again.

        It is the denominator for every claim about the broadcast path: a fan-out cost means nothing
        without the number of sockets it fans out to, and this review could not size that cost because
        the number was unobtainable without opening a browser.
        """
        health = self.texts.get("service/routers/health.py", "")
        self.assertIn("active_count()", health, "/health no longer reports the socket count")
        self.assertIn('payload["sockets"]', health)


if __name__ == "__main__":
    unittest.main()
