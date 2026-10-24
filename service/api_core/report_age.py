"""How long since comms last heard from an aify-env, judged two ways (decision 2, DECISIONS.md: "aify-env announces its
reporting interval; comms derives 'not reporting' from it").

  - `report_age` is the rule: a publisher is in contact while fewer than three of the intervals it announced have passed
    since comms accepted its last report. It reads only the publisher's report pair, never its data time, so an
    `unavailable` report keeps contact without making data fresh. No announcement is `not-reporting`: no guessed number.
  - `legacy_receipt_age` is today's fixed 180 s on the data time, kept under its own name for the readers SW5 moves to
    `report_age`; SW7 deletes it.

Both are pure: a clock in, a class out.
"""
from datetime import datetime

#: "Three missed reports": the protocol's policy, here and nowhere else. That three intervals cover a quiet host's
#: roughly two-interval gap between full reports is read from aify-env's code, not measured.
MISSED_REPORTS = 3
#: Whole milliseconds from 1 s to 1 h, shared by the ingest validator and the saved-contact reader.
REPORT_EVERY_MS_BOUNDS = (1_000, 3_600_000)
#: Today's fixed receipt limit, for the legacy readers only.
LEGACY_RECEIPT_LIMIT_S = 180


def _elapsed_s(since, at):
    """Seconds from `since` to `at` (ISO timestamps with a zone), or the class that says why there is no answer."""
    try:
        start = datetime.fromisoformat(since.replace('Z', '+00:00'))
        end = datetime.fromisoformat(at.replace('Z', '+00:00'))
        if start.tzinfo is None or end.tzinfo is None:
            return 'invalid-clock'
        seconds = (end - start).total_seconds()
        return 'future' if seconds < 0 else seconds
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 'invalid-clock'


def legacy_receipt_age(data_applied_at, at):
    """'fresh' while less than LEGACY_RECEIPT_LIMIT_S since the data was applied, else 'stale', 'future' or
    'invalid-clock'."""
    seconds = _elapsed_s(data_applied_at, at)
    if isinstance(seconds, str):
        return seconds
    return 'fresh' if seconds < LEGACY_RECEIPT_LIMIT_S else 'stale'


def is_report_interval(value):
    low, high = REPORT_EVERY_MS_BOUNDS
    return type(value) is int and low <= value <= high


def report_age(publisher, at):
    """'fresh' while fewer than MISSED_REPORTS announced intervals have passed since comms accepted the publisher's last
    report; 'stale' from then; 'not-reporting' when it never announced an interval, or its last report withdrew it;
    'future' or 'invalid-clock' as the clock dictates."""
    pair = getattr(publisher, 'report', None)
    if not isinstance(pair, tuple) or len(pair) != 2 or not is_report_interval(pair[1]):
        return 'not-reporting'
    received_at, every_ms = pair
    seconds = _elapsed_s(received_at, at)
    if isinstance(seconds, str):
        return seconds
    return 'fresh' if seconds * 1000 < MISSED_REPORTS * every_ms else 'stale'
