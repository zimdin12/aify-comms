"""Which `environments` rows a terminal-control sweep may still act on.

`_environment_actionable_sql` is a SQL fragment, and it is tested here by RUNNING it against sqlite
rather than by asserting its text. A string comparison would pin the spelling and prove nothing about
the semantics; every case below is a row going in and a verdict coming out.

THE INCIDENT IT ENCODES (reviewer finding N7, 2026-07-26): the sweeps asked `status = 'online'` while
the stop-REQUEST path asked `_environment_effective_status(...) in {"online", "degraded"}`. Two halves
of one feature answering the same question differently, so a degraded environment's stop was left
PENDING by the request path and then FAILED by the sweep — the PTY survived, the session was already
marked ended, and Start was free to spawn a SECOND worker onto it.

Three rules come out of that, and they pull against each other:

  * `degraded` IS actionable. A degraded bridge is reduced-capability, not dead, and it keeps
    heartbeating.
  * Both heartbeat statuses AGE. Raw `status='online'` never aged, so a silently-dead bridge kept its
    controls pending forever. Ageing is what preserves the accumulation bound once `degraded` is
    admitted — admitting it without ageing would have traded one leak for a bigger one.
  * `offline` / `forgotten` / `disabled` are DECISIONS, not observations, and are never revived by a
    fresh timestamp.

And the degenerate timestamps fail toward TRUSTING the stored status rather than inventing a failure,
which is what `_environment_effective_status` does when `fromisoformat` raises. Comparison is on the
canonical 19-character prefix so a legacy `...:00.123456Z` stamp compares correctly against a
`...:00Z` cutoff — mixed-width timestamps compared lexically is its own recorded defect class.
"""
from __future__ import annotations

import sqlite3

import pytest

from service.reconcilers.terminal_controls import _environment_actionable_sql

CUTOFF = "2026-08-16T12:00:00Z"
FRESH = "2026-08-16T12:00:30Z"
OLD = "2026-08-16T11:59:00Z"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE environments (id TEXT PRIMARY KEY, status TEXT, last_seen TEXT)")
    try:
        yield conn
    finally:
        conn.close()


def actionable(db, status, last_seen) -> bool:
    """Insert one row and ask the fragment about it, exactly as a sweep would."""
    db.execute("DELETE FROM environments")
    db.execute("INSERT INTO environments (id, status, last_seen) VALUES (?, ?, ?)", ("env-1", status, last_seen))
    sql = f"SELECT COUNT(*) FROM environments WHERE {_environment_actionable_sql()}"
    return db.execute(sql, (CUTOFF,)).fetchone()[0] == 1


# ── the two heartbeat statuses ───────────────────────────────────────────────────────────────
def test_a_fresh_online_environment_is_actionable(db):
    assert actionable(db, "online", FRESH) is True


def test_a_fresh_DEGRADED_environment_is_actionable(db):
    """THE N7 FINDING. A degraded bridge is reduced-capability, not dead — excluding it is what left
    a stop pending, then failed it, and let Start spawn a second worker onto a surviving PTY."""
    assert actionable(db, "degraded", FRESH) is True


def test_both_heartbeat_statuses_AGE_out(db):
    """Admitting `degraded` without ageing would trade one leak for a bigger one: a silently-dead
    bridge would keep its controls pending indefinitely."""
    assert actionable(db, "online", OLD) is False
    assert actionable(db, "degraded", OLD) is False


def test_the_cutoff_boundary_is_inclusive(db):
    """`>=` — a row stamped exactly at the cutoff is still inside the window rather than aged out on
    a tie."""
    assert actionable(db, "online", CUTOFF) is True


# ── the decided statuses are never revived ───────────────────────────────────────────────────
@pytest.mark.parametrize("status", ["offline", "forgotten", "disabled"])
def test_a_decided_status_is_never_actionable_however_fresh(status):
    """These are DECISIONS, not observations. A heartbeat arriving afterwards must not undo one."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE environments (id TEXT PRIMARY KEY, status TEXT, last_seen TEXT)")
    try:
        assert actionable(conn, status, FRESH) is False
        assert actionable(conn, status, None) is False, "not even a null stamp revives it"
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["", None, "ONLINE", "Online", "unknown-status"])
def test_an_unrecognised_status_is_not_actionable(db, status):
    """The IN list is exact and case-sensitive, matching how the value is written. A status this
    sweep does not recognise is not one it may act on — the safe direction for a reaper."""
    assert actionable(db, status, FRESH) is False


# ── undatable stamps trust the stored status ─────────────────────────────────────────────────
@pytest.mark.parametrize("last_seen", [None, "", "   ", "not a timestamp", "2026-08-16", "yesterday"])
def test_an_undatable_last_seen_falls_back_to_trusting_the_status(db, last_seen):
    """An absent, empty or malformed stamp is NOT datable, so the row is judged on its status alone
    rather than being aged out on a comparison that cannot be made. This is the same choice
    `_environment_effective_status` makes when `fromisoformat` raises, and the class that produced
    the future-timestamp strands is exactly the one this avoids."""
    assert actionable(db, "online", last_seen) is True
    assert actionable(db, "degraded", last_seen) is True
    assert actionable(db, "offline", last_seen) is False, "an unreadable stamp still does not revive a decision"


def test_a_legacy_fractional_stamp_compares_on_the_canonical_prefix(db):
    """`substr(..., 1, 19)` on BOTH sides. A `...:00.123456Z` stamp is 26 characters and would
    compare wrongly against a 20-character cutoff if either side were left whole — comparing
    mixed-width timestamps lexically is its own recorded defect class."""
    assert actionable(db, "online", "2026-08-16T12:00:30.123456Z") is True
    assert actionable(db, "online", "2026-08-16T11:59:00.123456Z") is False


def test_a_stamp_with_no_zone_suffix_is_still_datable(db):
    """The GLOB matches the first 19 characters, so the trailing `Z` is not required — a stamp
    written without one is compared, not waved through."""
    assert actionable(db, "online", "2026-08-16T12:00:30") is True
    assert actionable(db, "online", "2026-08-16T11:59:00") is False


# ── the fragment's shape ─────────────────────────────────────────────────────────────────────
def test_it_binds_exactly_one_parameter(db):
    """The docstring promises ONE `?`, and callers compose this into larger statements where a second
    placeholder would silently consume the next argument."""
    assert _environment_actionable_sql().count("?") == 1


def test_it_composes_into_a_larger_query_with_other_conditions(db):
    """How it is actually used: ANDed with the sweep's own predicates, so it must be a self-contained
    boolean expression rather than a fragment needing its own parentheses at the call site."""
    db.execute("DELETE FROM environments")
    db.execute(
        "INSERT INTO environments (id, status, last_seen) VALUES (?, ?, ?)", ("env-keep", "degraded", FRESH)
    )
    db.execute(
        "INSERT INTO environments (id, status, last_seen) VALUES (?, ?, ?)", ("env-drop", "offline", FRESH)
    )
    rows = db.execute(
        f"SELECT id FROM environments WHERE {_environment_actionable_sql()} AND environments.id LIKE 'env-%'",
        (CUTOFF,),
    ).fetchall()
    assert [r[0] for r in rows] == ["env-keep"]
