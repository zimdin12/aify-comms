"""v0.4 ntfy relay — the contract, tested clause by clause.

`docs/V0.4_SPEC.md` exists because review refused my first phrasing, "fire-and-forget with a bounded
timeout", on the grounds that it permits awaiting an HTTP call on the message-send path. Each test
below names the clause it holds and what would ship broken without it.

The agreement half (C7) reads `service/contracts/operator_notify_cases.json`, the same file
`notify.test.mjs` reads — audit finding 2 was one predicate living in two languages where I fixed
one and believed I was done.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import unittest
from pathlib import Path

from service import ntfy
from service.tests._source import code_only

CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "contracts" / "operator_notify_cases.json").read_text(
        encoding="utf-8"
    )
)

URL = "https://ntfy.example.test/aify-secret-topic-9f3a"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


# ── C7: does this event belong to the operator ───────────────────────────────────────
class QualifierAgreementTests(unittest.TestCase):
    def test_every_shared_case(self):
        for case in CASES["shared"]:
            with self.subTest(case["name"]):
                self.assertEqual(
                    ntfy.qualifies(case["event"], case["data"], channel_joined=case["channelJoined"]),
                    case["expected"],
                )

    def test_the_allowed_differences_are_what_python_actually_does(self):
        """These cases must NOT match the JS side. Pinning the Python half here means a change that
        quietly aligns the two — discarding the server's authoritative membership — fails a test
        instead of passing as 'consistency'."""
        for case in CASES["allowedDifferences"]:
            with self.subTest(case["name"]):
                # `channelJoined: null` means "the server asks the database"; for this case the
                # database says joined, which is the information the browser does not have.
                self.assertEqual(
                    ntfy.qualifies(case["event"], case["data"], channel_joined=True),
                    case["python"],
                )
                self.assertNotEqual(case["python"], case["javascript"],
                                    "an allowed difference that is not a difference is a stale exemption")

    def test_an_unknown_channel_membership_stays_closed(self):
        """The server should never be unsure — it reads channel_members at the send site — so None
        means the caller forgot to pass it. Closed is the only safe reading of a caller bug."""
        self.assertFalse(
            ntfy.qualifies("channel_message", {"channel": "sand-castle"}, channel_joined=None)
        )


# ── C4b: the shape that stops the bug coming back ────────────────────────────────────
class EnqueueShapeTests(unittest.TestCase):
    def test_the_enqueue_is_not_awaitable(self):
        """An `async def` here would eventually acquire an `await` at a call site and put the
        network back on the request path — silently, because it would still work."""
        self.assertFalse(inspect.iscoroutinefunction(ntfy.NtfyRelay.enqueue))
        self.assertFalse(inspect.iscoroutinefunction(ntfy.notify_operator))

    def test_enqueue_never_raises_even_on_garbage(self):
        relay = ntfy.NtfyRelay(URL)
        for event, data in [("message_sent", None), (None, None), ("message_sent", {"to": 17})]:
            self.assertIsInstance(relay.enqueue(event, data), str)


# ── C5 / C5b: coalescing happens before the queue; disabled touches nothing ───────────
class CoalesceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.relay = ntfy.NtfyRelay(URL, now=self.clock)
        self.msg = {"from": "sc-manager", "to": ["dashboard"], "subject": "gate 3"}

    def test_a_burst_collapses_to_one_queued_alert(self):
        self.assertEqual(self.relay.enqueue("message_sent", self.msg), "queued")
        for _ in range(5):
            self.assertEqual(self.relay.enqueue("message_sent", self.msg), "coalesced")
        self.assertEqual(self.relay.health()["queueDepth"], 1,
                         "coalescing must happen BEFORE the queue, or a burst eats queue slots")

    def test_a_new_message_after_the_window_gets_through(self):
        self.relay.enqueue("message_sent", self.msg)
        self.clock.advance(ntfy.COALESCE_WINDOW_SECONDS + 1)
        self.assertEqual(self.relay.enqueue("message_sent", self.msg), "queued")

    def test_the_key_is_sender_and_subject_not_message_id(self):
        """Keying on the id would coalesce nothing during exactly the ping-pong burst this exists
        to collapse — every message has a different id."""
        a = dict(self.msg, id="m1")
        b = dict(self.msg, id="m2")
        self.assertEqual(self.relay.enqueue("message_sent", a), "queued")
        self.assertEqual(self.relay.enqueue("message_sent", b), "coalesced")

    def test_a_different_subject_is_a_different_alert(self):
        self.relay.enqueue("message_sent", self.msg)
        other = dict(self.msg, subject="gate 4")
        self.assertEqual(self.relay.enqueue("message_sent", other), "queued")

    def test_the_coalesce_map_is_pruned(self):
        for i in range(50):
            self.relay.enqueue("message_sent", dict(self.msg, subject=f"s{i}"))
            self.clock.advance(ntfy.COALESCE_WINDOW_SECONDS * 11)
        self.assertLessEqual(len(self.relay._last_fired), 2,
                             "a long-lived process must not grow this map without bound")

    def test_disabled_leaves_no_state_behind(self):
        off = ntfy.NtfyRelay("", now=self.clock)
        self.assertEqual(off.enqueue("message_sent", self.msg), "disabled")
        self.assertEqual(off._last_fired, {}, "an 'off' feature that mutates state is a bug report")
        self.assertEqual(off.health()["queueDepth"], 0)

    def test_fleet_chatter_is_not_queued(self):
        self.assertEqual(
            self.relay.enqueue("message_sent", {"from": "a", "to": ["sc-coder"], "subject": "x"}),
            "not-for-operator",
        )
        self.assertEqual(self.relay.health()["queueDepth"], 0)


# ── C2: the bound sheds, it does not raise ───────────────────────────────────────────
class BoundedQueueTests(unittest.TestCase):
    def test_a_full_queue_drops_and_never_raises(self):
        relay = ntfy.NtfyRelay(URL, maxsize=3)
        results = [
            relay.enqueue("message_sent", {"from": "a", "to": ["dashboard"], "subject": f"s{i}"})
            for i in range(6)
        ]
        self.assertEqual(results[:3], ["queued"] * 3)
        self.assertEqual(results[3:], ["dropped-full"] * 3)
        self.assertEqual(relay.health()["droppedFull"], 3, "a shed alert must never be silent")


# ── C3 / C4: the worker owns the network, failures are dropped not retried ───────────
class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_worker_posts_and_records_success(self):
        relay = ntfy.NtfyRelay(URL)
        posted = []

        async def fake_post(item):
            posted.append(item)
            return True

        relay._post = fake_post
        relay.start()
        relay.enqueue("message_sent", {"from": "sc-manager", "to": ["dashboard"], "subject": "hi"})
        await asyncio.wait_for(relay._queue.join(), timeout=2)
        await relay.stop()
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["title"], "sc-manager → you")
        self.assertEqual(relay.health()["sent"], 1)
        self.assertIsNotNone(relay.health()["lastSuccessAt"])

    async def test_a_failing_post_is_counted_and_NOT_retried(self):
        """A retry storm against a third-party host while the fleet is busy is worse than a missed
        buzz about a message that is already delivered and visible in the dashboard."""
        relay = ntfy.NtfyRelay(URL)
        attempts = []

        async def boom(item):
            attempts.append(item)
            raise RuntimeError(f"connect failed to {URL}")

        relay._post = boom
        relay.start()
        relay.enqueue("message_sent", {"from": "a", "to": ["dashboard"], "subject": "hi"})
        await asyncio.wait_for(relay._queue.join(), timeout=2)
        await asyncio.sleep(0.05)
        await relay.stop()
        self.assertEqual(len(attempts), 1, "exactly one attempt — no retry")
        self.assertEqual(relay.health()["sendFailures"], 1)

    async def test_one_failure_does_not_stop_the_worker(self):
        relay = ntfy.NtfyRelay(URL)
        seen = []

        async def flaky(item):
            seen.append(item["title"])
            if len(seen) == 1:
                raise RuntimeError("nope")
            return True

        relay._post = flaky
        relay.start()
        relay.enqueue("message_sent", {"from": "a", "to": ["dashboard"], "subject": "one"})
        relay.enqueue("message_sent", {"from": "b", "to": ["dashboard"], "subject": "two"})
        await asyncio.wait_for(relay._queue.join(), timeout=2)
        await relay.stop()
        self.assertEqual(len(seen), 2, "a dead worker after one bad POST would silently end alerts")

    async def test_the_exception_object_is_never_logged(self):
        """C6. httpx puts the request URL in str(exc), so the obvious `logger.warning(..., exc)`
        writes the credential to disk exactly when something is going wrong."""
        relay = ntfy.NtfyRelay(URL)

        async def boom(item):
            raise RuntimeError(f"connection refused: {URL}")

        relay._post = boom
        relay.start()
        relay.enqueue("message_sent", {"from": "a", "to": ["dashboard"], "subject": "hi"})
        with self.assertLogs("service.ntfy", level="WARNING") as captured:
            await asyncio.wait_for(relay._queue.join(), timeout=2)
            await asyncio.sleep(0.05)
        await relay.stop()
        blob = "\n".join(captured.output)
        self.assertNotIn(URL, blob)
        self.assertNotIn("aify-secret-topic-9f3a", blob)
        self.assertIn("RuntimeError", blob, "the class is useful and safe; the object is not")

    async def test_health_reports_a_wedged_worker(self):
        """A failure counter cannot see this: a worker that stops draining produces NO failures,
        the queue just fills until the bound sheds. Review's catch."""
        relay = ntfy.NtfyRelay(URL, maxsize=4)
        relay.start()
        await relay.stop()  # worker gone, feature still "enabled"
        for i in range(6):
            relay.enqueue("message_sent", {"from": "a", "to": ["dashboard"], "subject": f"s{i}"})
        h = relay.health()
        self.assertFalse(h["workerAlive"])
        self.assertEqual(h["queueDepth"], 4)
        self.assertEqual(h["droppedFull"], 2)
        self.assertEqual(h["sendFailures"], 0, "the wedge is invisible to the failure counter alone")


# ── C6: the URL is a credential ──────────────────────────────────────────────────────
class SecretTests(unittest.TestCase):
    def test_health_never_contains_the_url(self):
        relay = ntfy.NtfyRelay(URL)
        blob = json.dumps(relay.health())
        self.assertNotIn(URL, blob)
        self.assertNotIn("aify-secret-topic-9f3a", blob)
        self.assertNotIn("ntfy.example.test", blob)

    def test_redaction_keeps_identity_without_granting_access(self):
        red = ntfy.redact_url(URL)
        self.assertIn("ntfy.example.test", red)
        self.assertNotIn("aify-secret-topic-9f3a", red)
        self.assertTrue(red.startswith("ntfy:"))

    def test_two_topics_on_one_host_are_distinguishable(self):
        self.assertNotEqual(
            ntfy.redact_url("https://ntfy.sh/topic-a"), ntfy.redact_url("https://ntfy.sh/topic-b")
        )

    def test_redaction_survives_garbage(self):
        self.assertEqual(ntfy.redact_url(""), "")
        self.assertTrue(ntfy.redact_url("not a url").startswith("ntfy:"))

    def test_the_url_comes_from_the_environment_only(self):
        """Never config/service.json — that file is generated, and a settings surface would echo a
        credential back out."""
        src = (Path(__file__).resolve().parents[1] / "ntfy.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv(ENV_VAR', src)
        # Comments stripped: the module explains at length WHY it never reads service.json, and a
        # raw substring search matches that explanation. Twice today a test of mine has asserted
        # against prose instead of code.
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("service.json", code)

    def test_off_by_default(self):
        self.assertFalse(ntfy.NtfyRelay("").enabled)
        self.assertFalse(ntfy.NtfyRelay("   ").enabled)


if __name__ == "__main__":
    unittest.main()


# ── the real network path, against a real server ─────────────────────────────────────
class RealHttpTests(unittest.IsolatedAsyncioTestCase):
    """Everything above stubs `_post`, which proves the queue contract and nothing about the POST.

    This drives the actual `_post` — real httpx, real socket, real HTTP — against a throwaway
    listener on localhost. It is what catches a wrong content type, a header the client rejects, or
    an httpx API that moved, none of which a stub can see. Deliberately NOT pointed at a third-party
    host: verifying our own code must not publish anything to someone else's service.
    """

    async def test_a_real_post_carries_title_and_body(self):
        import asyncio as aio

        received = {}

        async def handle(reader, writer):
            data = await reader.read(65536)
            text = data.decode("utf-8", "replace")
            head, _, body = text.partition("\r\n\r\n")
            received["headers"] = head
            received["body"] = body
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
            writer.close()

        server = await aio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            relay = ntfy.NtfyRelay(f"http://127.0.0.1:{port}/aify-test")
            relay.start()
            relay.enqueue(
                "message_sent",
                {"from": "sc-manager", "to": ["dashboard"], "subject": "gate 3 is red"},
            )
            await aio.wait_for(relay._queue.join(), timeout=5)
            await relay.stop()

        self.assertIn("POST /aify-test", received.get("headers", ""))
        self.assertIn("Title: sc-manager -> you", received.get("headers", ""),
                      "the title is an HTTP header: it must be encodable, and readable")
        self.assertIn("gate 3 is red", received.get("body", ""))
        self.assertEqual(relay.health()["sent"], 1)
        self.assertEqual(relay.health()["sendFailures"], 0)

    async def test_a_refused_connection_is_counted_not_raised(self):
        """The realistic outage: ntfy host down. It must not escape the worker or stop the drain."""
        import asyncio as aio

        # Port 1 on loopback refuses immediately on every platform we run on.
        relay = ntfy.NtfyRelay("http://127.0.0.1:1/aify-test")
        relay.start()
        relay.enqueue("message_sent", {"from": "a", "to": ["dashboard"], "subject": "hi"})
        await aio.wait_for(relay._queue.join(), timeout=10)
        await aio.sleep(0.05)
        await relay.stop()
        self.assertEqual(relay.health()["sendFailures"], 1)
        self.assertEqual(relay.health()["sent"], 0)


# ── header safety, which only a real socket revealed ─────────────────────────────────
class HeaderSafetyTests(unittest.TestCase):
    """Every stubbed test passed while the real POST raised UnicodeEncodeError on our OWN default
    title — `build_alert` renders "sc-manager → you" and U+2192 is not latin-1. The first live
    message would have been counted as a send failure and dropped without a retry."""

    def test_the_default_title_is_encodable(self):
        title, _ = ntfy.build_alert("message_sent", {"from": "sc-manager", "subject": "hi"})
        ntfy.header_safe(title).encode("latin-1")  # must not raise

    def test_a_non_ascii_agent_name_cannot_break_the_post(self):
        """Agent and channel names are operator-chosen free text. Sanitising only our own arrow
        would leave the same crash one Cyrillic name away."""
        title, _ = ntfy.build_alert("message_sent", {"from": "агент-один", "subject": "x"})
        safe = ntfy.header_safe(title)
        safe.encode("latin-1")
        self.assertTrue(safe)

    def test_a_newline_in_a_name_is_not_header_injection(self):
        """A CRLF in a header value is not an encoding problem, it is an injected header."""
        crlf = chr(13) + chr(10)
        for hostile in ["evil" + chr(10) + "X-Injected: 1", "evil" + crlf + "X-Injected: 1"]:
            safe = ntfy.header_safe(hostile)
            self.assertNotIn(chr(10), safe)
            self.assertNotIn(chr(13), safe)

    def test_a_title_that_sanitises_to_nothing_still_has_one(self):
        self.assertEqual(ntfy.header_safe("→→→"), "->->->")
        self.assertEqual(ntfy.header_safe("日本語"), "aify-comms")
        self.assertEqual(ntfy.header_safe(""), "aify-comms")

    def test_the_BODY_keeps_its_unicode(self):
        """Only the header is constrained. The message text itself must reach the phone intact."""
        _, body = ntfy.build_alert("channel_message", {"channel": "c", "from": "a", "body": "héllo 🎉"})
        self.assertIn("héllo 🎉", body)


class HealthEndpointBlastRadiusTests(unittest.TestCase):
    """/health is the CONTAINER'S healthcheck. Phone alerts must not be able to fail it.

    Found reviewing my own change: `docker-compose.yml` runs `curl -f .../health` as the healthcheck,
    and I made that response depend on the ntfy relay. If the relay could raise there, a broken
    optional feature would mark the whole service unhealthy and Docker would restart a container
    serving the fleet perfectly well.

    ntfy is advisory in every other respect — it sheds on a full queue, drops on failure, and never
    blocks a send. It has to be advisory here too.
    """

    def test_a_raising_relay_does_not_break_the_healthcheck(self):
        import asyncio as aio

        from service.routers import health as health_router

        original = ntfy.get_relay
        ntfy.get_relay = lambda: (_ for _ in ()).throw(RuntimeError("relay exploded"))
        try:
            payload = aio.run(health_router.health())
        finally:
            ntfy.get_relay = original

        self.assertEqual(payload["status"], "healthy",
                         "a broken phone-alert relay must never make the service look unhealthy")
        self.assertEqual(payload["ntfy"], {"enabled": None, "error": "unavailable"},
                         "and it must say it could not answer, not report a healthy-looking zero state")

    def test_the_normal_block_is_still_reported(self):
        import asyncio as aio

        from service.routers import health as health_router

        ntfy.reset_relay_for_tests("")
        payload = aio.run(health_router.health())
        self.assertEqual(payload["status"], "healthy")
        self.assertIs(payload["ntfy"]["enabled"], False)
        self.assertIn("queueDepth", payload["ntfy"])
