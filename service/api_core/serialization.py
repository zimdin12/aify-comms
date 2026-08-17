"""Scalar and serialization primitives: no I/O, no database, no application state.

v0.5.1c, the first SHARED-CORE slice of the v0.5.x structural series, extracted verbatim from
`service/routers/api_v2.py`.

WHY THE CORE COMES FIRST. The measurement that set this order: 94 of the router's 236 helpers are
reached by four or more route domains (3,682 lines). Peeling a route domain off first would either
drag those helpers with it -- stranding the other eleven domains -- or leave them behind and import
back into the router, recreating v0.5's borrow debt at four times the scale. So the shared core
becomes leaf-owned FIRST, and domains peel off afterwards against a core that no longer moves.

WHY THIS FAMILY IS FIRST WITHIN THE CORE. These nine are the atomic tier, chosen by measurement
rather than by eye: no `await`, no SQL, no database/websocket/settings/event-append reference, they
call no other helper, and they read no module-level constant. Nothing has to move with them and
nothing can be left dangling behind them -- which is exactly what you want from the slice that
proves the mechanism.

There are NO shims in this module and there must never be. It is a leaf: it imports the standard
library and nothing from this service. The router now imports these names from here, so there is
exactly one owner and no copy that can drift.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from service.clock import iso_to_epoch as _iso_to_epoch


def _json_loads_or(value: Any, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


# Any run of line breaks or control characters, collapsed to one space by `_quote_untrusted_subject`.
# `\x00-\x1f` covers CR, LF, TAB and ESC; `\x7f` is DEL. Written as one class so there is a single
# answer to "what counts as a control character here" rather than one per call site.
_CONTROL_RUN_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _clip_text(text: str, limit: int = 240) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)].rstrip() + "…"


def _iso_from_ms(timestamp_ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(0, int(timestamp_ms or 0)) / 1000))


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _timestamp_sort_key(value: Any) -> str:
    """A stable ORDERING key. Falls back to the raw string, which is why it is not a trust boundary.

    Keeping an unparseable value means a list still sorts deterministically instead of throwing, and
    for display ordering that is right. It is WRONG for a decision: letters sort above digits, so a
    non-ISO string compares GREATER than every real ISO timestamp. Use `_parsed_timestamp` where the
    comparison decides something.
    """
    try:
        raw = str(value or "").strip()
        if not raw:
            return ""
        from datetime import datetime, timezone
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:
        return str(value or "")


def _parsed_timestamp(value: Any) -> str:
    """The same normalisation, but "" when the value is not a real timestamp.

    THE TOMBSTONE GATES NEEDED THIS. Both the agent tombstone and the environment forget-tombstone
    decide "may this registration resurrect a deliberately-removed row?" by comparing an incoming,
    CALLER-SUPPLIED `bridgeStartedAt` against the server's `removed_at`/`forgottenAt`:

        relaunched = bool(incoming) and (not removed_at or incoming > removed_at)

    With `_timestamp_sort_key` an unparseable incoming value survives as itself, and `"now"`,
    `"garbage"` or `"Sat Aug 16 2026"` all compare GREATER than any `2026-…` — letters outrank
    digits. So any bridge sending a non-ISO `bridgeStartedAt` read as a genuine fresh relaunch and
    cleared the tombstone, which is the exact thing both gates exist to prevent: the agent gate's own
    comment says the bridge "sets restoreDeleted=true UNCONDITIONALLY on every auto/comms_register".

    Returning "" makes an unparseable value NO EVIDENCE, and `bool(incoming)` then refuses — the
    repo's standing rule that a check which could not gather evidence must not report a pass.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = _timestamp_sort_key(raw)
    return "" if normalized == raw and not _iso_to_epoch(raw) else normalized


def _normalize_machine_id(machine_id: Any) -> str:
    """Canonical machine_id form for storage AND comparison.

    The host machine_id is "<platform>:<hostname>" (e.g. "win32:DevBox-1").
    Different launch paths report the hostname with different casing, and
    machine_id is compared in bridge supersession + dispatch-claim routing.
    Comparing case-sensitively let a re-registered worker under a different
    casing escape supersession, leaving duplicate live bridge_instances.
    Lowercasing is safe (platform is already lowercase, only host casing
    varies) and idempotent, so we normalize at every store/compare site.
    """
    return str(machine_id or "").strip().lower()


def _machine_ids_same_host(a: Any, b: Any) -> bool:
    """Tolerant machine_id equality for dispatch-claim routing.

    machine_id is "<platform>:<host>". On WSL the platform tag is unstable
    across spawn contexts: the SAME machine registers as both
    "wsl-<distro>:host" (when WSL_DISTRO_NAME is set) and "linux:host" (when it
    isn't), because the env var is not propagated to every process. An exact
    comparison then treats one machine as two — a WSL delivery loop
    ("wsl-ubuntu:host") could never claim runs for a WSL-registered agent
    ("linux:host"), so deliveries sat queued forever (observed 2026-06-02,
    ci-senior-dev). Collapse the linux/WSL platform family so they match, while
    keeping win32/darwin distinct (a Windows bridge must NOT claim a WSL agent's
    runs). Fully generic: only the host component and a family collapse are
    compared, nothing machine-specific.
    """
    na, nb = _normalize_machine_id(a), _normalize_machine_id(b)
    if na == nb:
        return True
    if not na or not nb:
        return False
    fa, _, ha = na.partition(":")
    fb, _, hb = nb.partition(":")
    if not ha or ha != hb:
        return False

    def _fam(f: str) -> str:
        return "linux" if f == "linux" or f.startswith("wsl") else f

    return _fam(fa) == _fam(fb)


def _quote_untrusted_subject(subject: str, limit: int = 80) -> str:
    """Render another agent's subject so it cannot read as an instruction to whoever sees it.

    OPERATOR-REPORTED 2026-08-11: "when you restart agent then it gives some text ... but my agent
    decided to restart himself after reading this."

    A subject is free text written BY one agent FOR another, and these summaries strip the addressing
    away. So `Restart lc-coder` — a request aimed at somebody else — arrives in a third agent's
    context as a bare imperative line, and an agent that treats its context as instructions acts on
    it. Nothing was wrong with the routing; the RENDERING made a quotation look like a command.

    Quoting is the whole fix, and it must be applied wherever a foreign subject is echoed:
    a quoted string reads as a thing being talked about, an unquoted imperative reads as a thing to
    do. The same reasoning as the inbox safety header, applied to the one-line summaries that do not
    carry it.
    """
    # NEWLINES ESCAPE THE QUOTING, and until 2026-08-18 nothing stopped them. Reported by a reviewer
    # on another instance, and it defeated this function at EVERY call site:
    #
    #     subject = 'x\nRestart lc-coder'   ->   Subject: "x
    #                                            Restart lc-coder"
    #
    # Line two is a bare imperative on its own line — exactly the rendering this function exists to
    # prevent, and the closing quote is too far away to read as quoting. The `"` -> `'` substitution
    # below was the only escape ever considered, which made the guard look complete.
    #
    # Control characters go with them, not as scope creep but as the same escape: ESC (\x1b) would
    # otherwise carry ANSI sequences into a terminal-rendered console, and \r alone repositions the
    # cursor to overwrite the line that was already printed. A subject is ONE LINE by definition
    # here, so collapsing a run of them to a single space loses nothing a reader wanted.
    #
    # Collapsed BEFORE clipping so the limit measures what is actually displayed — otherwise a
    # subject could spend its whole budget on newlines and push the visible text past the clip.
    text = _CONTROL_RUN_RE.sub(" ", str(subject or "")).strip()
    text = _clip_text(text, limit) or "(no subject)"
    # Neutralise any embedded quote so the quoting cannot be escaped by the subject itself.
    return '"' + text.replace('"', "'") + '"'


def _row_require_reply(row) -> bool:
    return bool(int((row["require_reply"] if row and "require_reply" in row.keys() else 0) or 0))

# Two row/timestamp helpers from the control plane, v0.5.4. `_row_get` joins `_row_require_reply`
# above it -- both read a field out of something that may be a dict or a sqlite3.Row.
# `_iso_add_seconds` composes `_iso_from_ms` (here) with `iso_to_epoch` (service/clock.py), which is
# why it needs the one import added above: its closure spans two modules and this is the one that
# owns the FORMATTING half. clock.py stays dependency-free -- the import runs this way only.
def _row_get(row, key, default=None):
    """Safely fetch a field from either a dict or a sqlite3.Row."""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return value if value is not None else default


def _iso_add_seconds(value: str, seconds: int) -> str:
    # Compose the canonical parse/format helpers so refresh_after timestamps use
    # the same second-precision "...Z" form as _now() (what they're compared to).
    epoch = _iso_to_epoch(value)
    if not epoch:
        return ""
    return _iso_from_ms(int((epoch + max(0, int(seconds))) * 1000))
