"""The readers' one way into the operational view, after the 0.9.8 switch (plan 2026-10-20-s4-hard-switch, SW5).

`operational_view` is pure over holders and messaging facts. This module gathers those facts for one agent and hands
back its `View`, so every reader asks the same question the same way:
  - the claim gate, the send-time queue, the reply-overdue reminder (B21) and the stranded guard (B26) read only
    eligibility and busy, which come from the mirror alone: `eligibility_view` needs no database;
  - the word, its last-known note and row 5's promotion read the whole view: `view_for` adds comms' own messaging
    facts (the active run, the operator's stop of an undefined agent, a proven-absent wake handle).

The binding is taken once per question, so one evaluation reads one frozen mirror and one clock.
"""
from datetime import datetime, timedelta

from service.api_core import operational_view as ov
from service.api_core import partial_status_shadow as shadow
from service.api_core.report_age import MISSED_REPORTS, report_age
from service.clock import ISO_SECONDS


def _placement(agent_row):
    try:
        return str(agent_row['machine_id'] or '').strip(), str(agent_row['id'] or '').strip()
    except Exception:
        return '', ''


def _holders(agent_row, binding):
    """The agent's holders on its registered machine. No row, no machine or no id is all unknown, never an empty
    population: an agent comms cannot place cannot be read as held by nobody."""
    machine, agent_id = _placement(agent_row)
    if not machine or not agent_id:
        return ov.Holders((), True)
    return ov.holders_of(binding, machine, agent_id)


def eligibility_view(agent_row, binding=None, run_occupied=False):
    """The view with no messaging facts but the run occupancy a caller already read: enough for every delivery reader,
    which ask only eligibility, busy and (the reminder) whether a run is claimed or running."""
    return ov.comms_view(_holders(agent_row, binding or shadow.bind()), ov.Messaging(run_occupied=bool(run_occupied)))


async def messaging_for(db, agent_row, dispatch_state=None):
    """comms' own facts about the agent, as the word's D8 rows and row 5 read them. `dispatch_state` is the caller's
    when it holds one (a status-cache entry carries the active run); only a stopped agent costs the definition read."""
    from service.api_core.definition_guard import defined_on
    from service.api_core.dispatch_state import _get_dispatch_state_for_agent
    from service.api_core.liveness import _agent_wake_mode
    from service.api_core.manual_status import _MANUAL_STATUSES

    agent_id = str(agent_row['id'])
    if dispatch_state is None:
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
    active = (dispatch_state or {}).get('activeRun') or {}
    stopped = str(agent_row['status'] or '').strip().lower() in _MANUAL_STATUSES
    return ov.Messaging(
        run_occupied=bool((dispatch_state or {}).get('hasActiveRun')),
        run_running=active.get('status') == 'running',
        operator_stopped=stopped and not await defined_on(db, agent_id),  # D8 row 1: a defined agent's stop is env's
        wake_handle_absent=str(_agent_wake_mode(agent_row) or '').endswith('-missing-handle'),
    )


def dispatch_state_of(cache):
    """The run facts a status-cache entry already carries, in `_get_dispatch_state_for_agent`'s shape, so the word
    costs no second read of dispatch_runs."""
    run_id = str(cache.get('active_run_id') or '')
    return {'hasActiveRun': bool(run_id), 'activeRun': {'status': cache.get('active_run_status') or ''} if run_id else None}


async def view_for(db, agent_row, dispatch_state=None, binding=None):
    """The whole view: the word, its last-known note, eligibility, busy and the delivery hold."""
    messaging = await messaging_for(db, agent_row, dispatch_state)
    return ov.comms_view(_holders(agent_row, binding or shadow.bind()), messaging)


#: Why a queued run to an undefined agent comms cannot read waits, as the send's answer says it (SW5 review R3, the
#: operator's rule of 2026-10-16: the sender is told what happened to the wake).
HELD_RECORDLESS = ('no aify-env holds a record of it and no host defines it, so nothing will start it (D8); the run '
                   'waits queued until an aify-env reports it')
HELD_NOT_CURRENT = ("aify-env's record of it is not current and no host defines it, so nothing will start it (D8); "
                    'the run waits queued until its record is current again')


async def held_reasons(db, agent_ids):
    """{agent id: why its run waits} for every recipient whose run is queued with nothing that will start it: comms
    cannot read its eligibility and no host defines it. A defined one is cold-started by its send (page 1), so it is
    not listed; neither is one aify-env knows."""
    from service.api_core.definition_guard import defined_on

    binding = shadow.bind()
    held = {}
    for agent_id in agent_ids:
        row = await (await db.execute('SELECT * FROM agents WHERE id = ?', (agent_id,))).fetchone()
        if row is None:
            continue
        holders = _holders(row, binding)
        if ov.comms_view(holders, ov.Messaging()).eligibility == 'known' or await defined_on(db, agent_id):
            continue
        held[agent_id] = HELD_NOT_CURRENT if holders.holders else HELD_RECORDLESS
    return held


def last_known_api(view):
    """A7's `agent.lastKnown`: the word comms showed for the last record it applied and that record's application time,
    only beside `unknown`; None otherwise."""
    if view.status != 'unknown' or not view.last_known:
        return None
    word, applied_at = view.last_known
    return {'status': word, 'appliedAt': applied_at}


def note_of(view):
    """The status note beside a switched word: why it is unknown, or nothing to add."""
    if view.status != 'unknown':
        return ''
    if view.last_known:
        word, applied_at = view.last_known
        return f"aify-env's state for this agent is not current; last known {word} at {applied_at}"
    return 'aify-env has no current record of this agent'


async def served_note(db, agent_row, view):
    """The note beside the view's word, the same on the poll and the push: the operator's own note beside their stop,
    comms' reason beside `misconfigured`, why an `unknown` is unknown, and otherwise nothing. No note comes from the
    legacy cascade (0.9.9 S3a): it used to survive wherever the legacy word happened to equal the view's."""
    from service.api_core.manual_status import _MANUAL_STATUSES
    from service.api_core.records import _row_status_note

    manual = str(agent_row['status'] or '').strip().lower()
    if view.status == manual and manual in _MANUAL_STATUSES:
        return _row_status_note(agent_row)
    if view.status == 'misconfigured':
        return await misconfigured_note(db, agent_row)
    return note_of(view)


async def misconfigured_note(db, agent_row):
    """Beside aify-env's `misconfigured`, the note comms has always built: why no start can reach this agent (an
    unlaunchable runtime, or a workspace outside the folders its environment starts in). aify-env publishes the word
    with no reason on the wire; comms holds both facts, the definition's workspace and the environment's advertised
    roots, so the note is comms' own (0.9.2 fix C). It is read only for this word."""
    defect = ''
    if db is not None:
        from service.api_core.liveness import _agent_config_defect

        try:
            defect = await _agent_config_defect(db, agent_row, str(agent_row['session_mode'] or '').strip() or 'managed')
        except Exception:
            defect = ''
    return f"Cannot be started: {defect}." if defect else ''


async def word_into(db, agent_row, cache, dispatch_state=None):
    """Put the view's word into a status-cache entry (`status_entry`): the status, its note, A7's last-known note,
    and a refresh time no later than the moment a fresh holder goes stale."""
    binding = shadow.bind()
    if dispatch_state is None and 'active_run_id' in cache:
        dispatch_state = dispatch_state_of(cache)
    view = await view_for(db, agent_row, dispatch_state, binding)
    cache['reason'] = await served_note(db, agent_row, view)
    cache['status'] = view.status
    cache['last_known'] = last_known_api(view)
    until = fresh_until(agent_row, binding)
    if until and (not cache.get('refresh_after') or until < cache['refresh_after']):
        cache['refresh_after'] = until
    return cache


def _plus_ms(iso, ms):
    start = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    return (start + timedelta(milliseconds=ms)).strftime(ISO_SECONDS)


def fresh_until(agent_row, binding):
    """Next contact expiry on the agent's machine, or None. A cached word must recheck both loss of a fresh holder
    and yielding of an unreadable silent peer; neither transition requires another publication."""
    machine, _ = _placement(agent_row)
    if binding.loss or binding.view is None or not machine:
        return None
    ends = []
    for (mid, _), publisher in binding.view.items():
        # Even a publisher with no readable row can hold this agent unknown. When its contact expires it may
        # yield to a live holder, so a cached unknown must recheck at that boundary too.
        if mid != machine:
            continue
        if report_age(publisher, binding.at) == 'fresh':
            received_at, every_ms = publisher.report
            try:
                ends.append(_plus_ms(received_at, MISSED_REPORTS * every_ms))
            except (AttributeError, TypeError, ValueError, OverflowError):
                return binding.at  # an unreadable clock re-reads at once rather than serving it as fresh
    return min(ends) if ends else None


def changed_machines(before, after):
    """The machines whose knowledge a publication changed (SW5 review R2): every machine with a publisher that appeared,
    left or changed between two mirror views, its report pair included. A publisher's readability decides every agent on
    its machine, held by it or not (an unreadable publisher could hold any of them unseen), so the machine is the unit.
    None when either view could not be read: every machine."""
    if before is None or after is None:
        return None
    return {key[0] for key in set(before) | set(after) if before.get(key) is not after.get(key)}


async def expire_machines(db, machines):
    """The cached words of every agent registered on these machines are re-derived on their next read; every cached word
    when the machines cannot be told (None). Returns the agent ids it expired."""
    from service.reconcilers.status_cache import _LIVE_STATE_CACHE

    if machines is None:
        ids = list(_LIVE_STATE_CACHE)
        expire_words(ids)
        return ids
    machines = sorted(m for m in machines if m)
    if not machines:
        return []
    marks = ','.join('?' * len(machines))
    rows = await (await db.execute(
        f"SELECT id FROM agents WHERE TRIM(COALESCE(machine_id, '')) IN ({marks})", machines)).fetchall()
    ids = [str(row['id']) for row in rows]
    expire_words(ids)
    return ids


def expire_words(agent_ids):
    """A publication changed what these agents read: their cached words are re-derived on the next read."""
    from service.reconcilers.status_cache import _live_state_expire

    for agent_id in agent_ids:
        try:
            _live_state_expire(agent_id)
        except Exception:
            pass
