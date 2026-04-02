"""
Import v1 export bundle into v2 SQLite database.

Usage:
    python -m service.import_v2 export.json [aify.db]
"""
import json
import sys
import asyncio
from pathlib import Path
from service.db import init_db, get_db


async def import_v2(bundle: dict, db_path: Path):
    await init_db(db_path)
    db = await get_db()
    await db.execute("PRAGMA foreign_keys=OFF")

    try:
        # Agents
        for agent_id, info in bundle.get("agents", {}).items():
            await db.execute(
                "INSERT OR REPLACE INTO agents (id, role, name, cwd, model, instructions, status, registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                (agent_id, info.get("role", ""), info.get("name", agent_id), info.get("cwd", ""),
                 info.get("model", ""), info.get("instructions", ""), info.get("status", "idle"),
                 info.get("registeredAt", ""), info.get("lastSeen", ""))
            )

        # Deduplicate messages by ID — channel messages appear in multiple inboxes
        seen_ids = {}  # msg_id -> True
        for msg in bundle.get("messages", []):
            msg_id = msg.get("id", "")
            to_agent = msg.get("_to", "")
            is_read = msg.get("_read", False)
            source = msg.get("source", "direct")
            channel = msg.get("channel", "")

            if msg_id in seen_ids:
                # Already inserted — just add read receipt if read
                if is_read:
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        (msg_id, to_agent, msg.get("readAt", ""))
                    )
                continue

            seen_ids[msg_id] = True
            await db.execute(
                "INSERT OR IGNORE INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, priority, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (msg_id, msg.get("from", ""), to_agent, channel, source,
                 msg.get("type", "info"), msg.get("subject", ""), msg.get("body", ""),
                 msg.get("priority", "normal"), msg.get("inReplyTo"), msg.get("timestamp", 0))
            )
            if is_read:
                await db.execute(
                    "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                    (msg_id, to_agent, msg.get("readAt", ""))
                )

        # Channels
        for ch in bundle.get("channels", []):
            await db.execute(
                "INSERT OR IGNORE INTO channels (name, description, created_by, created_at) VALUES (?,?,?,?)",
                (ch["name"], ch.get("description", ""), ch.get("createdBy", ""), ch.get("createdAt", ""))
            )
            for member in ch.get("members", []):
                await db.execute(
                    "INSERT OR IGNORE INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
                    (ch["name"], member, ch.get("createdAt", ""))
                )
            # Import channel-only messages (not in any inbox)
            for msg in ch.get("messages", []):
                if msg.get("from") == "_system":
                    msg_id = msg.get("id", "")
                    if msg_id not in seen_ids:
                        seen_ids[msg_id] = True
                        await db.execute(
                            "INSERT OR IGNORE INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
                            (msg_id, "_system", ch["name"], "channel", "info", f"#{ch['name']}", msg.get("body", ""), msg.get("timestamp", 0))
                        )
                    continue
                msg_id = msg.get("id", "")
                if msg_id not in seen_ids:
                    seen_ids[msg_id] = True
                    await db.execute(
                        "INSERT OR IGNORE INTO messages (id, from_agent, channel, source, type, subject, body, priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                        (msg_id, msg.get("from", ""), ch["name"], "channel",
                         msg.get("type", "info"), f"#{ch['name']}", msg.get("body", ""),
                         msg.get("priority", "normal"), msg.get("timestamp", 0))
                    )

        # Shared artifacts (text only — binary needs manual file copy)
        for art in bundle.get("shared", []):
            if art.get("is_binary"):
                print(f"  SKIP binary artifact: {art['name']} (copy file manually)")
                continue
            await db.execute(
                "INSERT OR IGNORE INTO shared_artifacts (name, from_agent, description, content, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (art["name"], art.get("from", ""), art.get("description", ""),
                 art.get("content", ""), art.get("size", 0), 0, art.get("sharedAt", ""))
            )

        # Settings
        for key, value in bundle.get("settings", {}).items():
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (key, json.dumps(value))
            )

        await db.execute("PRAGMA foreign_keys=ON")
        await db.commit()
    finally:
        await db.close()


if __name__ == "__main__":
    bundle_path = Path(sys.argv[1])
    db_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("aify.db")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    asyncio.run(import_v2(bundle, db_path))
    print(f"Imported into {db_path}")
