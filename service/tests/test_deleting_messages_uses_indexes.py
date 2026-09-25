"""Every statement the message-delete path runs finds its rows by index.

`_delete_messages_by_ids` nulls four message-id columns on dispatch_runs/dispatch_controls per chunk of
250, under the single writer lock, on every rotation, clear, unsend and channel delete. None of the four
was indexed, so each chunk was four full-table scans of a table that is never pruned for live agents
(v0.7 scan A4). Checked by EXPLAIN QUERY PLAN on a database built by the real init_db.
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from service.db import init_db

STATEMENTS = [
    "UPDATE messages SET in_reply_to = NULL WHERE in_reply_to IN (?)",
    "UPDATE dispatch_runs SET message_id = NULL WHERE message_id IN (?)",
    "UPDATE dispatch_runs SET in_reply_to = NULL WHERE in_reply_to IN (?)",
    "UPDATE dispatch_runs SET result_message_id = NULL WHERE result_message_id IN (?)",
    "UPDATE dispatch_controls SET source_message_id = '' WHERE source_message_id IN (?)",
    "DELETE FROM read_receipts WHERE message_id IN (?)",
]


class DeletingMessagesUsesIndexesTests(unittest.TestCase):
    def test_no_statement_scans_its_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.db"
            asyncio.run(init_db(path))
            con = sqlite3.connect(path)
            try:
                for sql in STATEMENTS:
                    with self.subTest(sql=sql):
                        plan = " | ".join(row[-1] for row in con.execute("EXPLAIN QUERY PLAN " + sql, ("m1",)))
                        self.assertIn("USING", plan, f"{sql} -> {plan}")
                        self.assertNotRegex(plan, r"^SCAN \w+$|\| SCAN \w+$", f"{sql} -> {plan}")
            finally:
                con.close()
