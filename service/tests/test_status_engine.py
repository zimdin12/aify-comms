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
