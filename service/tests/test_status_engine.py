import sqlite3, tempfile, os, asyncio
from service.db import init_db

def test_agent_status_state_table_exists():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        asyncio.run(init_db(path))
        conn = sqlite3.connect(path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_status_state)")]
        conn.close()
        assert set(["agent_id", "status", "in_turn", "awaiting_input",
                    "last_event", "last_event_at", "updated_at"]).issubset(set(cols))
    finally:
        os.remove(path)


from service.status_engine import StatusInputs, derive

def _inp(**kw):
    base = dict(mode="managed", alive=True, in_turn=False, awaiting_input=False,
                worker_present=True, env_reachable=True, disabled=False,
                bridge_stale=False, has_live_session=True, idle_too_long=False)
    base.update(kw); return StatusInputs(**base)

def test_working_when_in_turn():
    assert derive(_inp(in_turn=True)) == "working"

def test_blocked_when_in_turn_and_awaiting_input():
    assert derive(_inp(in_turn=True, awaiting_input=True)) == "blocked"

def test_managed_online_when_alive_worker_present():
    assert derive(_inp()) == "online"

def test_managed_idle_when_quiet_too_long():
    assert derive(_inp(idle_too_long=True)) == "idle"


def test_managed_reachable_env_dead_worker_is_available_not_offline():
    # ed44b60: managed agent, env online, worker died -> available (lazy-autostart)
    assert derive(_inp(worker_present=False, alive=False, env_reachable=True)) == "available"

def test_managed_unreachable_env_is_offline():
    assert derive(_inp(worker_present=False, env_reachable=False)) == "offline"

def test_managed_claude_live_sidecar_no_console_is_available():
    # status-F1: managed online REQUIRES worker_present (console AND sidecar);
    # caller sets worker_present=False for the headless-orphan case.
    assert derive(_inp(worker_present=False, alive=True, env_reachable=True)) == "available"

def test_hermes_working_while_delivering_is_working():
    # #172: a turn in flight reads working even though it's "online"-ish underneath
    assert derive(_inp(mode="managed", in_turn=True, worker_present=True)) == "working"

def test_resident_stale_bridge_is_stale():
    assert derive(_inp(mode="resident", alive=False, bridge_stale=True, has_live_session=True)) == "stale"

def test_resident_no_live_session_is_offline():
    assert derive(_inp(mode="resident", alive=False, has_live_session=False, bridge_stale=False)) == "offline"

def test_resident_live_session_is_online():
    assert derive(_inp(mode="resident", alive=True, has_live_session=True)) == "online"

def test_disabled_always_stopped():
    assert derive(_inp(disabled=True, in_turn=True)) == "stopped"


from service.status_engine import apply_event, EVENT_KINDS

def test_turn_start_sets_in_turn_then_turn_end_clears():
    s = {"in_turn": 0, "awaiting_input": 0, "turn_run_id": ""}
    s = apply_event(s, {"kind": "turn_start", "runId": "r1"})
    assert s["in_turn"] == 1 and s["turn_run_id"] == "r1"
    s = apply_event(s, {"kind": "turn_end", "runId": "r1"})
    assert s["in_turn"] == 0

def test_blocked_event_sets_awaiting_input():
    s = apply_event({"in_turn": 1, "awaiting_input": 0, "turn_run_id": ""}, {"kind": "blocked"})
    assert s["awaiting_input"] == 1

def test_unknown_event_is_noop():
    s = {"in_turn": 1, "awaiting_input": 0, "turn_run_id": ""}
    assert apply_event(dict(s), {"kind": "nonsense"}) == s
