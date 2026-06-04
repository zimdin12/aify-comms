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
