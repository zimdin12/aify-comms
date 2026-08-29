"""The one route that deletes rows nobody asked it about, asked of the query itself.

`POST /api/v1/messages/cleanup/orphan-unread` deletes "unread inbox messages addressed to removed
agents". It was one of exactly TWO real routes in the service that no test exercised — measured
against `create_app()`: 127 method+path routes, 7 unmentioned, 5 of them favicons and the oauth
redirect.

An untested cleanup endpoint is a different risk from an untested read. This one is a single WHERE
clause over two LEFT JOINs, and every condition in it is the only thing standing between a
maintenance click and deleted history. A mock would agree with whatever I believed, so these run
against real sqlite — the same reasoning `test_orphaned_runs_query.py` states for the reaper.

THE CLAUSE IS IMPORTED, NOT COPIED. It was a string literal inside the handler, so a test could only
reach it by calling the route or by re-typing it; the second is a fork that agrees with itself
forever. It is now `_ORPHAN_UNREAD_WHERE`.

THE CONDITION THAT WOULD HURT MOST is `m.to_agent IS NOT NULL`. `channel_send.py` inserts a channel
message TWICE: one broadcast row with no `to_agent`, plus one fan-out row per member WITH it. For
every broadcast row `a.id IS NULL` is trivially true — there is no agent to join to — so dropping
that condition turns this endpoint into "delete every unread channel broadcast in the database".
That is the test I would want to exist before anyone edits this query, and it did not.
"""

from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from service.api_core.message_store import _delete_messages_where, _select_message_ids
from service.routers.dispatch_messages.message_removal import _ORPHAN_UNREAD_WHERE

#: Only the columns the clause touches, plus what `_delete_messages_by_ids` rewrites on the way
#: through — it clears reply links in `messages`, `dispatch_runs` and `dispatch_controls`, so those
#: tables have to exist or the delete raises rather than returning a count.
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_tombstones (
    agent_id TEXT PRIMARY KEY,
    removed_at TEXT NOT NULL,
    removed_by TEXT DEFAULT '',
    bridge_id TEXT DEFAULT '',
    reason TEXT DEFAULT ''
);
CREATE TABLE messages (
    id TEXT PRIMARY KEY, from_agent TEXT, to_agent TEXT, channel TEXT, source TEXT,
    type TEXT, subject TEXT, body TEXT, in_reply_to TEXT, timestamp TEXT
);
CREATE TABLE agents (id TEXT PRIMARY KEY);
CREATE TABLE read_receipts (message_id TEXT, agent_id TEXT);
CREATE TABLE dispatch_runs (
    id TEXT PRIMARY KEY, message_id TEXT, in_reply_to TEXT, result_message_id TEXT
);
CREATE TABLE dispatch_controls (id TEXT PRIMARY KEY, source_message_id TEXT);
"""


async def _seed(db):
    await db.executescript(SCHEMA)
    # One LIVE agent, and one that has been REMOVED. `remove_agent` calls `_tombstone_agent`
    # unconditionally and then deletes the row, so a removal always leaves BOTH marks -- and from
    # 2026-08-29 the tombstone is what "orphan" means. Seeding only the absence, which this fixture
    # did, is a state the product cannot produce: it is also the state of `dashboard`, which was
    # never an agent and had 1,792 unread messages the old predicate would have deleted.
    await db.execute("INSERT INTO agents (id) VALUES ('live-agent')")
    await db.execute(
        "INSERT INTO agent_tombstones (agent_id, removed_at, removed_by, reason)"
        " VALUES ('gone-agent', '2026-08-29T00:00:00Z', 'dashboard', 'test')"
    )
    rows = [
        # id                  to_agent        channel     source
        ("orphan-unread",     "gone-agent",   None,       "direct"),
        ("orphan-read",       "gone-agent",   None,       "direct"),
        ("live-unread",       "live-agent",   None,       "direct"),
        ("channel-broadcast", None,           "general",  "channel"),
        ("channel-fanout",    "gone-agent",   "general",  "channel"),
    ]
    for message_id, to_agent, channel, source in rows:
        await db.execute(
            "INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (message_id, "sender", to_agent, channel, source, "info", "s", "b"),
        )
    # `orphan-read` was read before its agent went away, so it is history rather than an orphan.
    await db.execute("INSERT INTO read_receipts (message_id, agent_id) VALUES ('orphan-read', 'gone-agent')")
    await db.commit()


def _run(coro):
    return asyncio.run(coro)


async def _surviving_ids() -> list[str]:
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await _seed(db)
        deleted = await _delete_messages_where(db, _ORPHAN_UNREAD_WHERE)
        cursor = await db.execute("SELECT id FROM messages ORDER BY id")
        remaining = [row["id"] for row in await cursor.fetchall()]
        return deleted, remaining


async def _selected_ids(where: str) -> list[str]:
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await _seed(db)
        return sorted(await _select_message_ids(db, where))


class OrphanUnreadCleanupTests(unittest.TestCase):
    def test_it_deletes_exactly_the_orphaned_unread_messages(self):
        deleted, remaining = _run(_surviving_ids())
        self.assertEqual(deleted, 2, "both messages to the removed agent that nobody read")
        self.assertEqual(
            remaining, ["channel-broadcast", "live-unread", "orphan-read"],
            "a channel broadcast, a live agent's mail and an already-read message must survive",
        )

    def test_a_channel_BROADCAST_is_never_deleted(self):
        """A broadcast row has no `to_agent`, and it must never be selected.

        THE MUTATION THAT USED TO LIVE HERE NO LONGER FIRES, and that is a real change rather than a
        weakened test. Against the old `a.id IS NULL` predicate, dropping `to_agent IS NOT NULL`
        selected every unread broadcast in the database -- a missing agent row is trivially true when
        there is no recipient at all -- so that condition was the only thing standing in the way.
        The predicate now asks for an `agent_tombstones` row matching `m.to_agent`, and a NULL
        recipient matches no tombstone either, so the broadcast is excluded twice over.

        The condition stays: it says what the query means, and a future predicate that stops being
        NULL-safe would need it. What is asserted is the OUTCOME, which is what mattered."""
        selected = _run(_selected_ids(_ORPHAN_UNREAD_WHERE))
        self.assertNotIn("channel-broadcast", selected)
        self.assertIn("m.to_agent IS NOT NULL", _ORPHAN_UNREAD_WHERE,
                      "the recipient guard is gone; nothing states that a broadcast is not an orphan")

    def test_a_live_agents_message_is_never_deleted(self):
        """And the mutation that DOES still fire, run here rather than argued: drop the removal test
        and a live agent's unread message is selected for deletion."""
        selected = _run(_selected_ids(_ORPHAN_UNREAD_WHERE))
        self.assertNotIn("live-unread", selected)
        without_guard = _ORPHAN_UNREAD_WHERE.replace(
            " AND EXISTS (SELECT 1 FROM agent_tombstones t WHERE t.agent_id = m.to_agent)", "")
        self.assertNotEqual(without_guard, _ORPHAN_UNREAD_WHERE,
                            "the removal test is not in the predicate; this mutation removed nothing")
        self.assertIn("live-unread", _run(_selected_ids(without_guard)))

    def test_an_already_read_message_is_never_deleted(self):
        selected = _run(_selected_ids(_ORPHAN_UNREAD_WHERE))
        self.assertNotIn("orphan-read", selected)
        without_guard = _ORPHAN_UNREAD_WHERE.replace(" AND r.message_id IS NULL", "")
        self.assertIn("orphan-read", _run(_selected_ids(without_guard)))

    def test_a_channel_FAN_OUT_row_to_a_removed_agent_IS_deleted(self):
        """The other half of the channel story, so the broadcast rule does not read as "channel
        messages are exempt". A fan-out row is addressed to one member and is ordinary inbox mail;
        when that member is gone and never read it, it is exactly what this endpoint is for."""
        self.assertIn("channel-fanout", _run(_selected_ids(_ORPHAN_UNREAD_WHERE)))

    def test_the_clause_is_the_one_the_route_uses(self):
        """This file drives `_ORPHAN_UNREAD_WHERE` directly, so it must be what the handler passes.

        A source read, and the honest form: the call is one expression inside an async handler that
        needs a live database and a ws manager. It proves the route uses this clause — not what the
        clause then does, which is every test above.
        """
        import pathlib

        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "routers" / "dispatch_messages" / "message_removal.py").read_text(encoding="utf-8")
        self.assertIn("_delete_messages_where(db, _ORPHAN_UNREAD_WHERE)", source)
