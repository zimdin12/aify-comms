"""The ntfy call SITES, not the relay — the contract can be satisfied by the module and broken here.

`test_ntfy_relay.py` proves the relay honours C1–C7. That says nothing about how it is called, and
every clause in the contract is defeatable at the call site:

    C1   enqueue before `db.commit()`  -> a phone buzzes for a message that rolled back
    C4b  `await notify_operator(...)`  -> the network is back on the request path, silently, and it
                                          still works, which is why only a test catches it
    C7   gated on `if ws:`             -> the alert fires ONLY when a dashboard is connected, i.e.
                                          never in the one situation the feature exists for

The last one is the interesting failure. Both send handlers already had a `if ws:` block right after
the commit, and putting the enqueue inside it would look completely reasonable in review — it sits
with the other notification code. It would also mean the operator's phone stays silent whenever no
browser tab is open, which is the entire use case.

Source-shape assertions, deliberately. The alternative is standing up the app, a database, a
websocket manager and an ntfy stub to observe an ordering that is plainly visible in ten lines of
source — and a passing integration test would still not prove the enqueue is outside the `if ws:`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from service.tests._source import code_only

API = Path(__file__).resolve().parents[1] / "routers" / "api_v2.py"


def _source() -> str:
    """CODE ONLY, and that is not a detail. The call sites carry comments explaining why the enqueue
    sits outside `if ws:` and after the commit — so the ordering assertions below, run against the
    raw text, match the explanation instead of the code. That happened four times on 2026-08-11
    before the filter went into `_source.py`, once in this very file: `rfind("if ws:")` found the
    words "if ws:` below" in a comment four lines above the real gate, and the test failed on
    prose."""
    return code_only(API.read_text(encoding="utf-8", errors="replace"))


def _call_sites() -> list[int]:
    src = _source()
    return [m.start() for m in re.finditer(r"^\s*notify_operator\(", src, re.MULTILINE)]


class SendPathWiringTests(unittest.TestCase):
    def setUp(self):
        self.src = _source()
        self.sites = _call_sites()

    def test_both_send_paths_notify(self):
        """Direct messages and channel messages. Losing one is the half-fixed shape audit finding 2
        described — `comms_search` repaired in one transport and believed done."""
        self.assertEqual(len(self.sites), 2, f"expected exactly 2 call sites, found {len(self.sites)}")

    def test_no_call_site_is_awaited(self):
        """C4b. An `await` here would put an HTTP call back on the message-send path and still
        appear to work, which is precisely why it needs a test rather than review attention."""
        self.assertNotIn("await notify_operator", self.src)

    def test_every_call_site_is_after_a_commit(self):
        """C1. Searches backwards from each call for the nearest `db.commit()` and the nearest
        `db.execute(` — if a write is closer than the commit, the enqueue is inside the transaction.
        """
        for at in self.sites:
            before = self.src[:at]
            commit_at = before.rfind("await db.commit()")
            self.assertGreater(commit_at, -1, "call site has no preceding commit at all")
            gap = before[commit_at:]
            self.assertNotIn("await db.execute(", gap,
                             "a write happens between the commit and the enqueue — not post-commit")

    def test_no_call_site_is_inside_the_websocket_gate(self):
        """The failure that would look most reasonable in review, and would silently remove the
        whole point: `if ws:` means a dashboard is connected, and the mobile alert exists for when
        one is not."""
        for at in self.sites:
            before = self.src[:at]
            gate_at = before.rfind("if ws:")
            commit_at = before.rfind("await db.commit()")
            self.assertLess(gate_at, commit_at,
                            "notify_operator sits under `if ws:` — it would only fire when a "
                            "dashboard is already open, which is the case that needs no alert")

    def test_the_channel_site_passes_authoritative_membership(self):
        """C7. Without `channel_joined` the qualifier fails closed and channel alerts never fire —
        a silent no-op rather than an error."""
        at = self.src.index('notify_operator(\n            "channel_message"')
        call = self.src[at : at + 400]
        self.assertIn('channel_joined=("dashboard" in members)', call,
                      "membership must come from the loaded member list, not be omitted or guessed")

    def test_the_import_is_the_sync_helper(self):
        self.assertIn("from service.ntfy import notify_operator", self.src)


class LifespanWiringTests(unittest.TestCase):
    """A relay with no running worker queues alerts nobody ever sends."""

    def setUp(self):
        self.src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")

    def test_the_worker_is_started_and_stopped(self):
        self.assertIn("ntfy_relay.start()", self.src)
        self.assertIn("await ntfy_relay.stop()", self.src)

    def test_shutdown_runs_in_the_finally_block(self):
        """Cancelled on the way out even when the app is going down badly — otherwise a reload
        leaks a task per restart."""
        after_yield = self.src[self.src.index("        yield") :]
        self.assertIn("finally:", after_yield)
        self.assertLess(after_yield.index("finally:"), after_yield.index("await ntfy_relay.stop()"))

    def test_startup_never_logs_the_raw_url(self):
        """C6, at the one place a URL is most tempting to print."""
        self.assertIn("ntfy_relay.redacted", self.src)
        self.assertNotIn("AIFY_NTFY_URL", code_only(self.src),
                         "main.py must not read the credential at all")


class HealthWiringTests(unittest.TestCase):
    def test_health_exposes_the_relay_block(self):
        src = (Path(__file__).resolve().parents[1] / "routers" / "health.py").read_text(encoding="utf-8")
        self.assertIn('"ntfy": get_relay().health()', src)


if __name__ == "__main__":
    unittest.main()
