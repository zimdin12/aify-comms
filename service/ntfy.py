"""Mobile alerts via ntfy — one outbound POST, kept entirely off the message-send path.

v0.4. The operator asked to hear their phone when an agent messages them. v0.3 shipped the desktop
half (a browser Notification from the dashboard's existing socket, no server change at all); this is
the half that works with no tab open and the laptop asleep.

THE CONTRACT IS THE POINT, and it is `docs/V0.4_SPEC.md`. Review refused to let any code start on my
first phrasing — "fire-and-forget with a bounded timeout" — because that sentence permits awaiting
an HTTP call on a path carrying 3,883 messages a fortnight. What that produced:

    C1  enqueue AFTER commit, beside the existing broadcasts
    C2  bounded queue, drop on full, never raise into the caller
    C3  the worker owns the network AND the timeout
    C4  failure logged, never retried; the relay's own health is observable
    C4b the enqueue is a plain `def` — an `async def` gets an `await` in front of it eventually
    C5  coalescing happens BEFORE the queue, or a burst sheds real alerts
    C6  the URL is a credential
    C7  only what the operator was actually sent

WHY THIS IS ITS OWN MODULE. Same reason as `terminal_diagnostics.py` and `status_engine.py`: logic
that lives inside a 23k-line router is only reachable through the app, so it can only fail in
production. Every decision here — does this qualify, is it a duplicate, is the queue full — is a
branch worth failing a test on.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# The operator's inbox id. Messages TO this are the operator's own mail; everything else on the wire
# is fleet traffic we can see but were not sent. Mirrors notify.mjs's OPERATOR_RECIPIENT.
OPERATOR_RECIPIENT = "dashboard"

# Mirrors notify.mjs. Everything else on the socket is fleet mechanics.
NOTIFIABLE_EVENTS = frozenset({"message_sent", "channel_message"})

# Same window and reasoning as the desktop side: measured cadence in an active thread is ~2-4
# minutes per message, so this collapses a burst while letting a genuinely new message through.
COALESCE_WINDOW_SECONDS = 90.0

# C2. At ~277 messages/day fleet-wide, of which operator-addressed is a small fraction, a queue that
# reaches this depth means the worker is wedged — and the right response then is to shed, not grow.
QUEUE_MAXSIZE = 256

# C3. The worker's own bound. Nothing on a request path ever waits on this.
POST_TIMEOUT_SECONDS = 5.0

ENV_VAR = "AIFY_NTFY_URL"


def _recipients(data: dict[str, Any]) -> list[str]:
    to = (data or {}).get("to")
    if isinstance(to, str):
        return [to.strip().lower()]
    if isinstance(to, (list, tuple, set)):
        return [str(t or "").strip().lower() for t in to]
    return []


def qualifies(event: str, data: dict[str, Any], *, channel_joined: Optional[bool] = None) -> bool:
    """Is this event addressed at the OPERATOR, as opposed to fleet chatter?

    C7. This duplicates `notify.mjs`'s `isForOperator`, which is exactly the shape audit finding 2
    called out (`comms_search` in two languages, one fixed, believed done). The agreement is held by
    `service/contracts/operator_notify_cases.json`, which both suites read.

    `channel_joined` is the ONE deliberate asymmetry, and it is authority rather than drift. The
    browser fails closed when membership is unknown because its channel list loads asynchronously
    and it can see channels it never joined. The server reads `channel_members` at the send site, so
    it is never unsure — `None` here means "the caller did not tell us", which for the server is a
    programming error rather than a runtime unknown, and is therefore also treated as closed.
    """
    if event not in NOTIFIABLE_EVENTS:
        return False
    if event == "channel_message":
        if not str((data or {}).get("channel") or "").strip():
            return False
        return bool(channel_joined)
    return OPERATOR_RECIPIENT in _recipients(data or {})


def coalesce_key(event: str, data: dict[str, Any]) -> str:
    """Keyed on sender + subject, NOT message id.

    The id is unique per message, so keying on it would coalesce nothing during exactly the
    two-agent ping-pong burst this exists to collapse. Same key as notify.mjs.
    """
    d = data or {}
    who = str(d.get("from") or d.get("channel") or "?").strip().lower()
    what = str(d.get("subject") or "").strip().lower()
    return f"{event}|{who}|{what}"


def build_alert(event: str, data: dict[str, Any]) -> tuple[str, str]:
    """(title, body) for the push. Deliberately the same shape as the desktop notification."""
    d = data or {}
    sender = str(d.get("from") or "agent").strip()
    if event == "channel_message":
        return (
            f"#{str(d.get('channel') or 'channel').strip()} — {sender}",
            str(d.get("body") or "").strip()[:180] or "(no body)",
        )
    return (f"{sender} → you", str(d.get("subject") or "").strip()[:180] or "(no subject)")


def header_safe(value: str) -> str:
    """An HTTP header value, from arbitrary text.

    FOUND BY A REAL SOCKET, and it could not have been found any other way. Every queue-contract
    test stubs `_post`, so they all passed while the actual POST raised `UnicodeEncodeError` on our
    own default title: `build_alert` renders "sc-manager → you", and U+2192 is not encodable in the
    latin-1 that HTTP headers use. The first live message would have failed, been counted as a send
    failure, and dropped — silently, since C4 does not retry.

    Agent names and channel names are operator-chosen free text, so sanitising only our own arrow
    would leave the same crash one Cyrillic agent name away. Everything unencodable is transliterated
    or dropped here instead. The BODY is untouched — it goes as UTF-8 bytes and needs no such care,
    which is why the message text itself survives intact.
    """
    text = str(value or "")
    for fancy, plain in (("→", "->"), ("—", "-"), ("–", "-"), ("’", "'"), ("“", '"'), ("”", '"')):
        text = text.replace(fancy, plain)
    cleaned = text.encode("ascii", "ignore").decode("ascii")
    # Header values cannot contain CR/LF at all — a newline in an agent name would be a header
    # injection, not merely an encoding problem.
    cleaned = cleaned.replace("\r", " ").replace("\n", " ").strip()
    return cleaned or "aify-comms"


def redact_url(url: str) -> str:
    """C6. Anyone holding the topic URL can READ every notification and publish to it, so it is a
    credential and never appears anywhere in full — not in health, not in a log, not in an error.

    Keeps enough to tell two configurations apart (host + a hash of the topic) and nothing that
    grants access."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        host = parts.netloc or "?"
        topic = (parts.path or "").strip("/") or "?"
    except Exception:
        return "ntfy:<unparseable>"
    digest = hashlib.sha256(topic.encode("utf-8", "replace")).hexdigest()[:8]
    return f"ntfy:{host}/{digest}"


class NtfyRelay:
    """Bounded queue in front of a single background POST worker.

    In-memory on purpose. A missed alert about a message that is already persisted, delivered and
    visible in the dashboard costs nothing worth a table, a migration and a drain-on-restart path.

    Correct ONLY under single-worker uvicorn — the same hard constraint `_LIVE_STATE_CACHE` already
    imposes. With `--workers > 1` each process would keep its own queue and coalesce map, so a burst
    would notify once per worker.
    """

    def __init__(self, url: str = "", *, maxsize: int = QUEUE_MAXSIZE, now=time.monotonic):
        self._url = str(url or "").strip()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._last_fired: dict[str, float] = {}
        self._now = now
        self._task: Optional[asyncio.Task] = None
        self.dropped_full = 0
        self.coalesced = 0
        self.sent = 0
        self.send_failures = 0
        self.last_success_at: Optional[str] = None
        self.last_failure_at: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    @property
    def redacted(self) -> str:
        """The ONLY form of the URL that may leave this object. There is deliberately no accessor
        for the raw value: a caller that cannot get it cannot log it."""
        return redact_url(self._url)

    # ── C1/C2/C4b/C5: the send path's entire contact with this feature ───────────────
    #
    # PLAIN `def`, and that is load-bearing (C4b). An `async def` here would eventually acquire an
    # `await` at a call site and silently reintroduce the very bug the contract exists to prevent —
    # it would still work, just with the network back on the request path. A sync function cannot be
    # awaited by accident. `test_the_enqueue_is_not_awaitable` pins it.
    def enqueue(self, event: str, data: dict[str, Any], *, channel_joined: Optional[bool] = None) -> str:
        """Returns WHY, not a bool: when no alert appears, "why" is the only useful question.

        Never raises. A failure to notify must never affect the message being reported.
        """
        try:
            # C5b — disabled leaves no state behind at all, not even a coalesce entry.
            if not self.enabled:
                return "disabled"
            if not qualifies(event, data, channel_joined=channel_joined):
                return "not-for-operator"

            # C5 — coalesce BEFORE the queue. Worker-side collapsing would still let a burst eat
            # queue slots and push the bound toward shedding alerts that matter.
            key = coalesce_key(event, data)
            t = self._now()
            previous = self._last_fired.get(key)
            if previous is not None and (t - previous) < COALESCE_WINDOW_SECONDS:
                self.coalesced += 1
                return "coalesced"
            self._last_fired[key] = t
            self._prune(t)

            title, body = build_alert(event, data)
            try:
                self._queue.put_nowait({"title": title, "body": body, "key": key})
            except asyncio.QueueFull:
                # C2 — shed. A phone buzz is advisory; the message it describes is primary and has
                # already been committed. `droppedFull` on /health keeps this from being silent.
                self.dropped_full += 1
                return "dropped-full"
            return "queued"
        except Exception:  # pragma: no cover - the whole point is that nothing escapes
            logger.warning("ntfy enqueue failed internally; alert dropped", exc_info=False)
            return "error"

    def _prune(self, t: float) -> None:
        cutoff = COALESCE_WINDOW_SECONDS * 10
        for k in [k for k, ts in self._last_fired.items() if t - ts > cutoff]:
            del self._last_fired[k]

    # ── C3/C4: the worker owns the network and the timeout ───────────────────────────
    async def _post(self, item: dict[str, str]) -> bool:
        import httpx

        async with httpx.AsyncClient(timeout=POST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                self._url,
                # The BODY is bytes and carries UTF-8 fine — an agent writing in any language, or
                # an emoji in a subject, reaches the phone intact.
                content=item["body"].encode("utf-8"),
                # The TITLE is an HTTP HEADER, and headers are not UTF-8. See header_safe: this
                # blew up on our own default title the first time it hit a real socket.
                headers={"Title": header_safe(item["title"]), "Priority": "default"},
            )
        return 200 <= response.status_code < 300

    async def _drain_once(self, item: dict[str, str]) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            ok = await self._post(item)
        except Exception as exc:
            # C6 — the EXCEPTION OBJECT is not safe to log. httpx and friends put the request URL
            # in str(exc), so the obvious `logger.warning("...: %s", exc)` writes the credential to
            # disk at exactly the moment something is going wrong. Class name only.
            self.send_failures += 1
            self.last_failure_at = stamp
            logger.warning("ntfy post failed (%s) for %s", type(exc).__name__, redact_url(self._url))
            return
        if ok:
            self.sent += 1
            self.last_success_at = stamp
        else:
            self.send_failures += 1
            self.last_failure_at = stamp
            logger.warning("ntfy post rejected for %s", redact_url(self._url))

    async def run_worker(self) -> None:
        """Drains until cancelled. C4: a failed alert is a missed alert — no retry, no backoff."""
        while True:
            item = await self._queue.get()
            try:
                await self._drain_once(item)
            finally:
                self._queue.task_done()

    def start(self, loop=None) -> None:
        if not self.enabled or self._task is not None:
            return
        # `get_running_loop`, not `get_event_loop`. The latter is deprecated and, from 3.12, RAISES
        # when there is no running loop — so on a newer interpreter a start() from outside the
        # lifespan would blow up instead of no-opping. The explicit fallback keeps that a quiet
        # no-op: a relay with no worker still reports `workerAlive: false` on /health, which is the
        # honest outcome, rather than taking the service down at startup over phone alerts.
        try:
            running = loop or asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("ntfy worker not started: no running event loop")
            return
        self._task = running.create_task(self.run_worker())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # ── C4: the relay's own health ───────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        """A failure counter alone cannot see a WEDGED worker — it produces no failures at all, the
        queue just fills until the bound starts shedding. So liveness and depth are reported too,
        and drops are named rather than silent.

        Carries no URL in any form. `test_health_never_contains_the_url` asserts it.
        """
        task = self._task
        return {
            "enabled": self.enabled,
            "workerAlive": bool(task is not None and not task.done()),
            "queueDepth": self._queue.qsize(),
            "queueMax": self._queue.maxsize,
            "sent": self.sent,
            "coalesced": self.coalesced,
            "droppedFull": self.dropped_full,
            "sendFailures": self.send_failures,
            "lastSuccessAt": self.last_success_at,
            "lastFailureAt": self.last_failure_at,
        }


# One relay per process. Single-worker uvicorn is a hard constraint here for the same reason it is
# for `_LIVE_STATE_CACHE`.
_RELAY: Optional[NtfyRelay] = None


def get_relay() -> NtfyRelay:
    global _RELAY
    if _RELAY is None:
        # C6 — read from the environment ONLY. Never `config/service.json`, which is generated and
        # would put a credential where a settings surface can echo it back.
        _RELAY = NtfyRelay(os.getenv(ENV_VAR, ""))
    return _RELAY


def reset_relay_for_tests(url: str = "") -> NtfyRelay:
    global _RELAY
    _RELAY = NtfyRelay(url)
    return _RELAY


def notify_operator(event: str, data: dict[str, Any], *, channel_joined: Optional[bool] = None) -> str:
    """The one call site shape the send handlers use. Sync, never raises. See NtfyRelay.enqueue."""
    return get_relay().enqueue(event, data, channel_joined=channel_joined)
