"""The v1 -> v2 migration, which nothing had ever run.

`service/export_v1.py` and `service/import_v2.py` were the ONLY two of the service's 214 modules
with no function executed by the suite — measured by tracing call events through a full pytest run,
because coverage.py is not installed here and adding a dependency is a reviewer's call, not mine.

They are a one-shot pair: `scripts/migrate-v1-to-v2.sh` reads the v1 JSON volume into a bundle and
writes it into a fresh SQLite database. Being one-shot is exactly the argument FOR testing them
rather than against it. A migration runs once, on data the operator cannot re-create, in a session
where the old format is about to be deleted — the least recoverable moment in the product, and the
one place a silent drop is permanent. The route gate made the same call about the data-repair
endpoints: rarely run, mutating, and exactly where nobody looks.

TESTED AS A ROUND TRIP, not as two units. What matters is that data put into a v1 layout comes back
out of the v2 database, and a per-function test of either half can pass while the pair disagrees
about a field name — which is the failure this shape of code actually has. The two halves are also
asserted where they must disagree: a binary artifact is exported with its bytes flagged and
DELIBERATELY skipped on import, with the file left for a manual copy.

THE DEDUPLICATION IS THE SUBTLE PART. A v1 channel message sits in every member's inbox, so the
bundle carries N copies of one message. Import keeps the first and turns the rest into READ RECEIPTS
— because in v2 a channel message is one row plus a receipt per reader. Getting that wrong does not
error; it silently multiplies every channel message by its member count.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from service.export_v1 import export_v1
from service.import_v2 import import_v2

MESSAGE_ID = "m-1"
CHANNEL_MESSAGE_ID = "m-chan"


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class V1MigrationRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name) / "data"
        self.db_path = Path(self._tmp.name) / "aify.db"
        self._seed_v1()

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_v1(self) -> None:
        _write(self.data / "agents.json", {"agents": {
            "lc-coder": {"role": "coder", "name": "Coder", "cwd": "/repo", "model": "opus",
                         "instructions": "be brief", "status": "idle",
                         "registeredAt": "2026-01-01T00:00:00Z", "lastSeen": "2026-01-02T00:00:00Z"},
            "lc-tester": {"role": "tester"},
        }})
        # A direct message, unread, in its addressee's inbox.
        _write(self.data / "inbox" / "lc-tester" / f"{MESSAGE_ID}.json", {
            "id": MESSAGE_ID, "from": "lc-coder", "type": "request", "subject": "please test",
            "body": "the branch is ready", "priority": "high", "timestamp": 1700000000,
        })
        # ONE channel message, sitting in BOTH members' inboxes — the v1 shape that becomes one row
        # plus receipts in v2. Read by one of them.
        for agent, filename in (("lc-coder", f"{CHANNEL_MESSAGE_ID}.read.json"),
                                ("lc-tester", f"{CHANNEL_MESSAGE_ID}.json")):
            _write(self.data / "inbox" / agent / filename, {
                "id": CHANNEL_MESSAGE_ID, "from": "lc-coder", "source": "channel",
                "channel": "general", "subject": "#general", "body": "standup in five",
                "timestamp": 1700000100, "readAt": "2026-01-03T00:00:00Z",
            })
        _write(self.data / "channels" / "general.json", {
            "name": "general", "description": "everything", "createdBy": "lc-coder",
            "createdAt": "2026-01-01T00:00:00Z", "members": ["lc-coder", "lc-tester"],
            "messages": [{"id": "m-sys", "from": "_system", "body": "channel created",
                          "timestamp": 1700000050}],
        })
        shared = self.data / "shared"
        shared.mkdir(parents=True, exist_ok=True)
        (shared / "notes.md").write_text("# notes\nline two\n", encoding="utf-8")
        _write(shared / "notes.md.meta.json", {
            "from": "lc-coder", "description": "the notes", "sharedAt": "2026-01-04T00:00:00Z",
        })
        (shared / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")
        _write(self.data / "settings.json", {"managed_via_wrapper": ["codex"], "retention_days": 30})

    def _migrate(self) -> dict:
        bundle = export_v1(self.data)
        asyncio.run(import_v2(bundle, self.db_path))
        return bundle

    def _rows(self, sql: str, params: tuple = ()):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    # ── export: what the bundle carries ──────────────────────────────────────────────────────

    def test_the_bundle_carries_every_section_of_the_v1_volume(self):
        bundle = export_v1(self.data)
        self.assertEqual(bundle["version"], "v1")
        self.assertEqual(sorted(bundle["agents"]), ["lc-coder", "lc-tester"])
        self.assertEqual(len(bundle["messages"]), 3, "one direct plus the channel copy per inbox")
        self.assertEqual([c["name"] for c in bundle["channels"]], ["general"])
        self.assertEqual(sorted(a["name"] for a in bundle["shared"]), ["logo.png", "notes.md"])
        self.assertEqual(bundle["settings"]["retention_days"], 30)

    def test_the_inbox_a_message_was_found_in_becomes_its_addressee(self):
        """v1 stores a message under the agent's directory and nowhere in the message itself, so the
        DIRECTORY is the only record of who it was for. Losing it would orphan every message."""
        bundle = export_v1(self.data)
        direct = [m for m in bundle["messages"] if m["id"] == MESSAGE_ID]
        self.assertEqual([m["_to"] for m in direct], ["lc-tester"])

    def test_a_read_message_is_recognised_by_its_FILENAME(self):
        """`.read.json` is v1's entire read-receipt mechanism. It is a suffix on a filename, which is
        the kind of encoding that survives a migration only if someone remembers it exists."""
        bundle = export_v1(self.data)
        by_agent = {(m["id"], m["_to"]): m["_read"] for m in bundle["messages"]}
        self.assertIs(by_agent[(CHANNEL_MESSAGE_ID, "lc-coder")], True)
        self.assertIs(by_agent[(CHANNEL_MESSAGE_ID, "lc-tester")], False)

    def test_a_binary_artifact_is_flagged_rather_than_mangled(self):
        """Reading it as text would raise or corrupt it. It is marked and its content left out, so
        the import can say what it skipped instead of writing a broken row."""
        bundle = export_v1(self.data)
        binary = next(a for a in bundle["shared"] if a["name"] == "logo.png")
        self.assertIs(binary["is_binary"], True)
        self.assertIsNone(binary["content"])
        self.assertGreater(binary["size"], 0)

    def test_an_empty_volume_exports_an_empty_bundle_rather_than_failing(self):
        """A migration pointed at the wrong directory must produce nothing, not a traceback — the
        operator then sees an empty import instead of a half-written database."""
        empty = Path(self._tmp.name) / "nothing"
        empty.mkdir()
        bundle = export_v1(empty)
        self.assertEqual(bundle["agents"], {})
        self.assertEqual(bundle["messages"], [])
        self.assertEqual(bundle["channels"], [])
        self.assertEqual(bundle["shared"], [])
        self.assertEqual(bundle["settings"], {})

    def test_a_corrupt_message_file_is_skipped_not_fatal(self):
        """One unreadable file must not abandon the rest of the migration. Pinned because the
        alternative — failing the whole run — is the reading a `try/except: continue` invites
        someone to "clean up"."""
        (self.data / "inbox" / "lc-tester" / "broken.json").write_text("{not json", encoding="utf-8")
        bundle = export_v1(self.data)
        self.assertEqual(len(bundle["messages"]), 3)

    # ── import: what lands in v2 ─────────────────────────────────────────────────────────────

    def test_agents_survive_with_their_fields(self):
        self._migrate()
        rows = {r["id"]: r for r in self._rows("SELECT * FROM agents")}
        self.assertEqual(sorted(rows), ["lc-coder", "lc-tester"])
        self.assertEqual(rows["lc-coder"]["role"], "coder")
        self.assertEqual(rows["lc-coder"]["cwd"], "/repo")
        self.assertEqual(rows["lc-coder"]["instructions"], "be brief")
        self.assertEqual(rows["lc-coder"]["last_seen"], "2026-01-02T00:00:00Z")

    def test_a_direct_message_keeps_its_addressee_and_its_fields(self):
        self._migrate()
        row = self._rows("SELECT * FROM messages WHERE id = ?", (MESSAGE_ID,))[0]
        self.assertEqual(row["from_agent"], "lc-coder")
        self.assertEqual(row["to_agent"], "lc-tester")
        self.assertEqual(row["subject"], "please test")
        self.assertEqual(row["priority"], "high")

    def test_a_channel_message_in_two_inboxes_becomes_ONE_row(self):
        """One row per channel message, whatever the member count.

        WHAT ENFORCES THIS IS THE PRIMARY KEY, not the `seen_ids` map that appears to. Verified by
        mutation: deleting the dedup branch leaves this outcome unchanged, because `messages.id` is
        the primary key and every insert is `OR IGNORE`. So `seen_ids` is an optimisation, not a
        guard — recorded here because the code reads like the guard, and the next person to change
        it deserves to know which line is load-bearing before they trust it.
        """
        self._migrate()
        rows = self._rows("SELECT * FROM messages WHERE id = ?", (CHANNEL_MESSAGE_ID,))
        self.assertEqual(len(rows), 1, "one channel message became several rows")

    def test_a_read_copy_becomes_a_RECEIPT_for_the_agent_who_read_it(self):
        """The information in the duplicate is not thrown away: in v2 "this agent has seen it" is a
        receipt row, which is what a duplicate inbox copy meant in v1. Asserted per READER — the
        copy is what carries the identity, so a migration that kept one row and dropped the receipts
        would silently mark a whole team's history unread."""
        self._migrate()
        receipts = self._rows(
            "SELECT agent_id FROM read_receipts WHERE message_id = ?", (CHANNEL_MESSAGE_ID,),
        )
        self.assertEqual([r["agent_id"] for r in receipts], ["lc-coder"])

    def test_EVERY_reader_of_a_channel_message_gets_their_own_receipt(self):
        """Both members having read it must produce two receipts, not one. This is the half the
        primary key cannot cover for: receipts are keyed by (message, agent), so dropping the write
        on either branch loses a reader."""
        read_copy = self.data / "inbox" / "lc-tester" / f"{CHANNEL_MESSAGE_ID}.json"
        payload = json.loads(read_copy.read_text(encoding="utf-8"))
        read_copy.unlink()
        _write(self.data / "inbox" / "lc-tester" / f"{CHANNEL_MESSAGE_ID}.read.json", payload)
        self._migrate()
        receipts = self._rows(
            "SELECT agent_id FROM read_receipts WHERE message_id = ? ORDER BY agent_id",
            (CHANNEL_MESSAGE_ID,),
        )
        self.assertEqual([r["agent_id"] for r in receipts], ["lc-coder", "lc-tester"])

    def test_an_unread_message_gets_no_receipt(self):
        self._migrate()
        self.assertEqual(
            self._rows("SELECT * FROM read_receipts WHERE message_id = ?", (MESSAGE_ID,)), [],
        )

    def test_channels_arrive_with_their_members_and_system_messages(self):
        self._migrate()
        self.assertEqual(
            [r["name"] for r in self._rows("SELECT * FROM channels")], ["general"],
        )
        self.assertEqual(
            sorted(r["agent_id"] for r in self._rows("SELECT * FROM channel_members")),
            ["lc-coder", "lc-tester"],
        )
        system = self._rows("SELECT * FROM messages WHERE id = 'm-sys'")[0]
        self.assertEqual(system["from_agent"], "_system")
        self.assertEqual(system["channel"], "general")

    def test_a_text_artifact_arrives_with_its_content_and_a_binary_one_is_skipped(self):
        """The one place export and import deliberately disagree: the bundle carries the binary's
        metadata, the import refuses to write it, and the operator is told to copy the file. A row
        with empty content would look like a successful migration of an empty file."""
        self._migrate()
        rows = {r["name"]: r for r in self._rows("SELECT * FROM shared_artifacts")}
        self.assertEqual(sorted(rows), ["notes.md"])
        self.assertIn("line two", rows["notes.md"]["content"])
        self.assertEqual(rows["notes.md"]["from_agent"], "lc-coder")

    def test_settings_survive_as_json_values(self):
        self._migrate()
        rows = {r["key"]: r["value"] for r in self._rows("SELECT * FROM settings")}
        self.assertEqual(json.loads(rows["retention_days"]), 30)
        self.assertEqual(json.loads(rows["managed_via_wrapper"]), ["codex"])

    def test_importing_the_same_bundle_twice_changes_nothing(self):
        """A migration that fails halfway is re-run by hand; if the second run duplicated everything
        the operator's fix would make it worse. Every insert is OR IGNORE / OR REPLACE — asserted by
        counting, not by reading the SQL."""
        self._migrate()
        before = {
            table: self._rows(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
            for table in ("agents", "messages", "channels", "channel_members",
                          "read_receipts", "shared_artifacts", "settings")
        }
        asyncio.run(import_v2(export_v1(self.data), self.db_path))
        after = {
            table: self._rows(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] for table in before
        }
        self.assertEqual(after, before)

    def test_the_import_writes_no_row_the_schema_would_have_refused(self):
        """The import turns foreign keys OFF so it can insert in any order, which means it CAN write
        an orphan — a message whose channel does not exist, a member of no channel. Nothing would
        complain at write time; the service would fail later, on a cascade that finds nothing.

        `PRAGMA foreign_key_check` is the only assertion that actually covers this. My first version
        of this test read `PRAGMA foreign_keys` on a fresh connection and asserted 0 — which is true
        of every SQLite file ever created, because the pragma is per-connection and not stored. It
        was named after the property and proved nothing.
        """
        self._migrate()
        conn = sqlite3.connect(str(self.db_path))
        try:
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()
        self.assertEqual(
            violations, [], f"the migration left {len(violations)} row(s) the schema forbids",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
