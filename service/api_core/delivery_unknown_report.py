"""D-UNKNOWN's measurement (s2c-owners step 6b): which origin left busy's delivery turn unknown, over one cohort of
closed windows. Diagnostic and temporary: deleted with the shadow (S5). Read-only: it never closes, restarts or writes.

THE COHORT starts at a pinned window's OPENING (the prefix before its first flush is in it) and runs through windows
with consecutive ids, each opening where the last closed, to the first one CLOSED at or after that opening plus
COHORT_HOURS. Its closed windows' committed counts are the whole population: there is no pending tail and no fixed
sample, and no bound on how long the run takes. The run is PENDING until that terminal close exists, and VOID when a
member is missing or expired, open before the terminal, closed 'flush-lost' (a failed tick drained counts that never
landed), 'service-restart' or 'version-change', or off the chain. Publication-loss closes stay in and are listed.

THE ANSWER, per busy variant at the claim site: each origin's share of 'unavailable:delivery-turn'. One origin above
half picks a branch; a fault, binding-lost, not-eligible or unclassified majority, a broken sum or a zero denominator
picks none. These comms-side filters do not show that no same-build aify-env restart happened.
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from service import clock

COHORT_HOURS = 6
VOID_CLOSES = ('flush-lost', 'service-restart', 'version-change')
SITE = 'claim'
BRANCHES = {'never-known': 'bootstrap-knowledge-contract-question', 'withheld': 'multi-holder-with-the-B5-authority',
            'lost': 'reset-investigation', 'ambiguous-closed': 'unblock-or-late-end-question'}
DELIVERY_UNKNOWN = 'unavailable:delivery-turn'
PREFIX, FAULT = 'deliveryUnknown:', 'deliveryUnknownFault'


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def cohort(windows, first_id, hours=COHORT_HOURS):
    """PURE. `windows` maps id to {openedAt, closedAt, closeReason}. (status, reason, member ids)."""
    first = windows.get(first_id)
    if first is None:
        return 'void', f'window {first_id} missing or expired', []
    end = _time(first['openedAt']) + timedelta(hours=hours)
    members, previous = [], None
    while True:
        window_id = first_id + len(members)
        window = windows.get(window_id)
        if window is None:
            return 'void', f'window {window_id} missing or expired', members
        if previous is not None and window['openedAt'] != previous['closedAt']:
            return 'void', f'window {window_id} does not open where {window_id - 1} closed', members
        members.append(window_id)
        if window['closedAt'] is None:
            return 'pending', f'window {window_id} is open', members
        if window['closeReason'] in VOID_CLOSES:
            return 'void', f"window {window_id} closed '{window['closeReason']}'", members
        if _time(window['closedAt']) >= end:
            return 'complete', None, members
        previous = window


def explain(counts):
    """PURE. `counts` is (window, variant, site, name, count) for busy over the members. (per variant/site totals,
    the strata whose explanations do not sum to their unknowns)."""
    totals = defaultdict(Counter)
    strata = defaultdict(lambda: [0, 0])
    for window, variant, site, name, count in counts:
        if name == DELIVERY_UNKNOWN:
            totals[(variant, site)][DELIVERY_UNKNOWN] += count
            strata[(window, variant, site)][0] += count
        elif name.startswith(PREFIX) or name == FAULT:
            totals[(variant, site)][name] += count
            strata[(window, variant, site)][1] += count
    broken = sorted(f'{w}/{v}/{s}: {u} unknown, {e} explained' for (w, v, s), (u, e) in strata.items() if u != e)
    return totals, broken


def decide(total):
    """PURE. One variant's totals at the site: (origin shares, decision)."""
    denominator = total.get(DELIVERY_UNKNOWN, 0)
    origins = Counter()
    for name, count in total.items():
        if name == FAULT:
            origins['fault'] += count
        elif name.startswith(PREFIX):
            origins[name[len(PREFIX):].split(':')[0]] += count
    if not denominator:
        return dict(origins), 'no-denominator'
    leader = max(origins, key=origins.get, default=None)
    if leader is None or origins[leader] * 2 <= denominator:
        return dict(origins), 'no-majority'
    return dict(origins), BRANCHES.get(leader, f'no-causal-choice ({leader} majority)')


def read_report(db, first_id, read_at):
    """The measurement, read in ONE read transaction on an autocommit connection, so an expiry between the window
    read and the count read cannot drop a member's counts and leave its survivors to decide."""
    db.execute('BEGIN')
    try:
        return _read(db, first_id, read_at)
    finally:
        db.execute('ROLLBACK')


def read_database(path, first_id, read_at):
    """The CLI's read: a fresh read-only, autocommit connection, closed after."""
    connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True, isolation_level=None)
    try:
        return read_report(connection, first_id, read_at)
    finally:
        connection.close()


def _read(db, first_id, read_at):
    rows = db.execute('SELECT id, comms_build, opened_at, closed_at, close_reason FROM status_shadow_windows '
                      'WHERE id >= ? ORDER BY id', (first_id,)).fetchall()
    windows = {r[0]: dict(commsBuild=r[1], openedAt=r[2], closedAt=r[3], closeReason=r[4]) for r in rows}
    status, reason, members = cohort(windows, first_id)
    out = dict(readAt=read_at, firstWindow=first_id, status=status, reason=reason,
               windows=[dict(id=m, **windows[m]) for m in members if m in windows])
    if status != 'complete':
        return out
    marks = ','.join('?' * len(members))
    counts = db.execute('SELECT window_id, variant, source, name, count FROM status_shadow_window_counts '
                        f"WHERE owner = 'busy' AND window_id IN ({marks})", members).fetchall()
    totals, broken = explain(counts)
    out.update(hours=(_time(windows[members[-1]]['closedAt']) - _time(windows[first_id]['openedAt'])).total_seconds()
               / 3600, sumBroken=broken, publicationCloses=[m for m in members if 'publication' in
                                                           (windows[m]['closeReason'] or '')],
               totals={f'{v}/{s}': dict(t) for (v, s), t in sorted(totals.items())})
    out['decisions'] = {} if broken else {v: dict(zip(('origins', 'decision'), decide(t)))
                                          for (v, s), t in sorted(totals.items()) if s == SITE}
    return out


if __name__ == '__main__':
    print(json.dumps(read_database(sys.argv[1], int(sys.argv[2]), clock.now()), indent=2))
