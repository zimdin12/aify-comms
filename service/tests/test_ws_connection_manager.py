"""The real WebSocket manager, which every other test replaces with a double.

`broadcast`, `notify_agent` and `active_count` were three of the 71 service functions the suite never
entered — not because they are unimportant, but because `service/tests/_base.py` installs `DummyWS`
in `app.state.ws_manager` for every request test. That is the right call for those tests and it
leaves the real class with no coverage at all: everything the dashboard sees live goes through this
file and nothing has ever run it.

THE SNAPSHOT ITERATION IS THE REASON THIS MATTERS. `broadcast` iterates `list(self._connections)`
rather than the list itself, and the comment records why (bughunt 2026-07-03): a concurrent
`disconnect()` does an in-place `list.remove` during an `await send_text`, shifting the list under an
index-based iterator and silently SKIPPING a live client. At ~40 terminal_output frames a second that
is a sequence gap, which the dashboard shows as a transient scrambled console. It is reproducible
here in one test, deterministically, because the fake socket controls exactly when the removal lands.

NO REAL SOCKETS. The fakes record what they were sent and can be told to fail; `connect` only awaits
`accept`, so nothing here binds a port or touches the running service.
"""

from __future__ import annotations

import asyncio
import time
import json
import unittest

from service.ws import ConnectionManager


class FakeWebSocket:
    """Records sends. `fail_after` makes it raise like a client that went away mid-broadcast."""

    def __init__(self, name: str, *, fail_after: int | None = None, on_send=None):
        self.name = name
        self.sent: list[str] = []
        self.accepted = False
        self._fail_after = fail_after
        self._on_send = on_send

    async def accept(self):
        self.accepted = True

    async def send_text(self, text: str):
        if self._on_send is not None:
            await self._on_send(self)
        if self._fail_after is not None and len(self.sent) >= self._fail_after:
            raise RuntimeError(f"{self.name} is gone")
        self.sent.append(text)

    def events(self) -> list[str]:
        return [json.loads(raw)["event"] for raw in self.sent]


def run(coro):
    return asyncio.run(coro)


class ConnectionManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = ConnectionManager()

    # ── connect / disconnect bookkeeping ─────────────────────────────────────────────────────

    def test_connecting_accepts_the_socket_and_counts_it(self):
        ws = FakeWebSocket("a")
        run(self.manager.connect(ws))
        self.assertTrue(ws.accepted, "a socket that is never accepted never receives anything")
        self.assertEqual(self.manager.active_count(), 1)

    def test_an_agent_socket_is_reachable_by_id(self):
        ws = FakeWebSocket("a")
        run(self.manager.connect(ws, "lc-coder"))
        self.assertEqual(self.manager.online_agents(), {"lc-coder"})

    def test_a_connection_with_no_agent_id_is_counted_but_anonymous(self):
        """The dashboard connects without an agent id. It must receive broadcasts and must not
        appear as an online agent."""
        run(self.manager.connect(FakeWebSocket("dash")))
        self.assertEqual(self.manager.active_count(), 1)
        self.assertEqual(self.manager.online_agents(), set())

    def test_disconnect_clears_BOTH_the_list_and_the_agent_map(self):
        """Leaving a stale entry in either one is a different bug: a dead socket in the list gets
        written to on every broadcast, and a dead socket in the map makes an offline agent look
        online forever."""
        ws = FakeWebSocket("a")
        run(self.manager.connect(ws, "lc-coder"))
        self.manager.disconnect(ws)
        self.assertEqual(self.manager.active_count(), 0)
        self.assertEqual(self.manager.online_agents(), set())

    def test_disconnecting_a_socket_that_was_never_connected_is_harmless(self):
        """It is called from the failure path of a send, which can race a real disconnect."""
        self.manager.disconnect(FakeWebSocket("stranger"))
        self.assertEqual(self.manager.active_count(), 0)

    def test_one_socket_registered_under_two_ids_is_cleared_from_both(self):
        ws = FakeWebSocket("a")
        run(self.manager.connect(ws, "lc-one"))
        run(self.manager.connect(ws, "lc-two"))
        self.manager.disconnect(ws)
        self.assertEqual(self.manager.online_agents(), set())

    # ── broadcast ────────────────────────────────────────────────────────────────────────────

    def test_a_broadcast_reaches_every_connection_in_the_documented_envelope(self):
        a, b = FakeWebSocket("a"), FakeWebSocket("b")
        run(self.manager.connect(a))
        run(self.manager.connect(b, "lc-coder"))
        run(self.manager.broadcast("terminal_output", {"terminalId": "t-1"}))
        for ws in (a, b):
            self.assertEqual(len(ws.sent), 1, f"{ws.name} missed the broadcast")
            self.assertEqual(
                json.loads(ws.sent[0]),
                {"event": "terminal_output", "data": {"terminalId": "t-1"}},
            )

    def test_a_broadcast_with_no_data_still_carries_an_object(self):
        """`data or {}` — a client reading `data.something` off a null would throw, and the
        dashboard reads exactly that on every event."""
        ws = FakeWebSocket("a")
        run(self.manager.connect(ws))
        run(self.manager.broadcast("agent_removed"))
        self.assertEqual(json.loads(ws.sent[0]), {"event": "agent_removed", "data": {}})

    def test_a_DEAD_client_does_not_swallow_the_broadcast_for_the_others(self):
        """One browser tab closing must not cost every other client the event."""
        dead = FakeWebSocket("dead", fail_after=0)
        alive_before = FakeWebSocket("before")
        alive_after = FakeWebSocket("after")
        for ws in (alive_before, dead, alive_after):
            run(self.manager.connect(ws))
        run(self.manager.broadcast("agent_status", {"agentId": "lc-coder"}))
        self.assertEqual(alive_before.events(), ["agent_status"])
        self.assertEqual(alive_after.events(), ["agent_status"], "a dead client stopped the sweep")
        self.assertEqual(dead.sent, [])

    def test_a_dead_client_is_disconnected_rather_than_written_to_forever(self):
        dead = FakeWebSocket("dead", fail_after=0)
        alive = FakeWebSocket("alive")
        run(self.manager.connect(dead, "lc-gone"))
        run(self.manager.connect(alive))
        run(self.manager.broadcast("agent_status"))
        self.assertEqual(self.manager.active_count(), 1, "the dead socket kept its slot")
        self.assertEqual(self.manager.online_agents(), set(), "…and its agent still read as online")

    def test_a_disconnect_DURING_a_broadcast_does_not_skip_a_live_client(self):
        """THE 2026-07-03 INCIDENT, reproduced deterministically.

        WHICH client is removed decides whether the bug shows. A list iterator holds an INDEX: while
        it is on element 0, removing a LATER element shifts nothing it has yet to reach, and the
        sweep completes — my first version removed the second client and passed against the buggy
        code. Removing an element at or before the cursor is what shifts the tail left under the
        loop, so the socket that disconnects here is the one currently being sent to, which is
        exactly the real case: `await send_text` suspends and that client's own handler reaps it.

        Iterating the live list then skips the SECOND client entirely. At ~40 terminal_output frames
        a second that skip is a sequence gap, which the dashboard renders as a scrambled console.
        """
        second = FakeWebSocket("second")
        third = FakeWebSocket("third")

        async def evict_self(sender):
            self.manager.disconnect(sender)

        first = FakeWebSocket("first", on_send=evict_self)
        for ws in (first, second, third):
            run(self.manager.connect(ws))
        run(self.manager.broadcast("terminal_output", {"seq": 1}))
        self.assertEqual(second.events(), ["terminal_output"],
                         "a live client was skipped when the one being sent to disconnected")
        self.assertEqual(third.events(), ["terminal_output"])

    def test_broadcasting_with_no_connections_is_a_no_op(self):
        run(self.manager.broadcast("agent_status", {"agentId": "lc-coder"}))
        self.assertEqual(self.manager.active_count(), 0)

    # ── notify_agent ─────────────────────────────────────────────────────────────────────────

    def test_notify_agent_reaches_only_that_agent(self):
        """It is used for per-agent wake-ups. Delivering to the wrong socket would wake an agent
        for someone else's message."""
        # The TARGET is registered SECOND on purpose: notifying the first-registered socket is what a
        # lookup that ignores the id degrades to, and a test that targeted the first agent would pass
        # against exactly that. Verified by mutation.
        other = FakeWebSocket("other")
        target = FakeWebSocket("target")
        run(self.manager.connect(other, "lc-tester"))
        run(self.manager.connect(target, "lc-coder"))
        run(self.manager.notify_agent("lc-coder", "message", {"id": "m-1"}))
        self.assertEqual(json.loads(target.sent[0]), {"event": "message", "data": {"id": "m-1"}})
        self.assertEqual(other.sent, [], "another agent was woken for someone else's message")

    def test_notifying_an_agent_that_is_not_connected_is_silent(self):
        """Every send path calls this; an agent being offline is the normal case, not an error."""
        run(self.manager.notify_agent("lc-nobody", "message", {"id": "m-1"}))

    def test_a_failed_notify_disconnects_the_agent(self):
        dead = FakeWebSocket("dead", fail_after=0)
        run(self.manager.connect(dead, "lc-coder"))
        run(self.manager.notify_agent("lc-coder", "message"))
        self.assertEqual(self.manager.online_agents(), set())
        self.assertEqual(self.manager.active_count(), 0)

    def test_notify_with_no_data_carries_an_object_too(self):
        ws = FakeWebSocket("a")
        run(self.manager.connect(ws, "lc-coder"))
        run(self.manager.notify_agent("lc-coder", "dispatch_queued"))
        self.assertEqual(json.loads(ws.sent[0]), {"event": "dispatch_queued", "data": {}})


class BroadcastIsConcurrentTests(unittest.TestCase):
    """The fan-out costs the SLOWEST client, not the sum of all of them.

    `broadcast` awaited each `send_text` in a loop, so every client waited for the one in front of it
    and the CALLER waited for all of them -- and every caller is an HTTP request handler on a
    single-worker event loop. Measured before the change, three runs, median:

        clients x 20ms each      1      2      4      8
        broadcast took        30.8   62.2  124.0  248.2  ms

    Exactly linear. On `POST /terminals/{id}/output`, which runs at roughly 40 a second per live
    terminal, that is the ingest path paying for the slowest browser tab. After: 31.0 ms at every size.

    THE BOUNDS BELOW ARE GENEROUS ON PURPOSE. Windows' timer floor is ~15ms, so a 50ms sleep measures
    nearer 60; a test that budgets less than the work costs is the flake this repo has already paid for
    twice. What separates sum from max here is a FACTOR of eight, so the assertion sits in the middle
    of that gap rather than close to either end.
    """

    SLOW = 0.05
    CLIENTS = 8

    def _manager_with_slow_clients(self, count, delay):
        manager = ConnectionManager()
        async def sleep_first(_ws):
            await asyncio.sleep(delay)
        clients = [FakeWebSocket(f"slow-{i}", on_send=sleep_first) for i in range(count)]
        for client in clients:
            manager._connections.append(client)
        return manager, clients

    def test_eight_slow_clients_cost_about_one_slow_client(self):
        manager, clients = self._manager_with_slow_clients(self.CLIENTS, self.SLOW)
        started = time.perf_counter()
        run(manager.broadcast("terminal_output", {"terminalId": "t-1"}))
        elapsed = time.perf_counter() - started

        self.assertTrue(all(len(c.sent) == 1 for c in clients), "a client was skipped")
        sequential = self.CLIENTS * self.SLOW
        self.assertLess(
            elapsed, sequential / 2,
            f"{self.CLIENTS} clients that each take {self.SLOW * 1000:.0f}ms took {elapsed * 1000:.0f}ms; "
            "sequentially they would take about "
            f"{sequential * 1000:.0f}ms, so the fan-out is still one-at-a-time",
        )

    def test_a_slow_client_does_not_hold_up_a_fast_one(self):
        """The shape that matters in production: ONE stalled tab among healthy ones."""
        manager = ConnectionManager()
        finished = {}

        async def slow(ws):
            await asyncio.sleep(self.SLOW)
            finished[ws.name] = time.perf_counter()

        async def fast(ws):
            finished[ws.name] = time.perf_counter()

        stalled = FakeWebSocket("stalled", on_send=slow)
        healthy = FakeWebSocket("healthy", on_send=fast)
        # The stalled one FIRST, so a sequential loop would make the healthy one wait behind it.
        manager._connections.extend([stalled, healthy])

        started = time.perf_counter()
        run(manager.broadcast("agent_status", {"agentId": "a"}))

        self.assertIn("healthy", finished, "the healthy client never received the frame")
        self.assertLess(
            finished["healthy"] - started, self.SLOW / 2,
            "the healthy client waited behind the stalled one",
        )

    def test_a_client_that_dies_mid_fanout_is_still_the_one_disconnected(self):
        """The gather results are matched back to the snapshot POSITIONALLY. Getting that mapping
        wrong would disconnect a healthy client and keep the dead one -- silently, because both
        outcomes look like 'one client went away'."""
        manager = ConnectionManager()
        async def sleep_first(_ws):
            await asyncio.sleep(0.01)
        good_a = FakeWebSocket("good-a", on_send=sleep_first)
        broken = FakeWebSocket("broken", fail_after=0, on_send=sleep_first)
        good_b = FakeWebSocket("good-b", on_send=sleep_first)
        for client in (good_a, broken, good_b):
            manager._connections.append(client)

        run(manager.broadcast("agent_status", {"agentId": "a"}))

        self.assertEqual(manager.active_count(), 2, "the wrong number of clients was dropped")
        self.assertNotIn(broken, manager._connections, "the dead client kept its slot")
        self.assertIn(good_a, manager._connections, "a healthy client was disconnected")
        self.assertIn(good_b, manager._connections, "a healthy client was disconnected")
        self.assertEqual(len(good_a.sent), 1)
        self.assertEqual(len(good_b.sent), 1)

    def test_no_connections_is_a_no_op(self):
        manager = ConnectionManager()
        run(manager.broadcast("agent_status", {"agentId": "a"}))
        self.assertEqual(manager.active_count(), 0)
