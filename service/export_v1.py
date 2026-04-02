"""
Export v1 JSON file data to a single JSON bundle for import into v2 SQLite.

Usage:
    python -m service.export_v1 /data [output.json]

Reads from the v1 Docker volume:
    /data/agents.json
    /data/inbox/{agent_id}/*.json
    /data/channels/*.json
    /data/shared/* + *.meta.json
    /data/settings.json
"""
import json
import sys
from pathlib import Path


def export_v1(data_dir: Path) -> dict:
    bundle = {"version": "v1", "agents": {}, "messages": [], "channels": [], "shared": [], "settings": {}}

    # Agents
    agents_file = data_dir / "agents.json"
    if agents_file.exists():
        bundle["agents"] = json.loads(agents_file.read_text(encoding="utf-8")).get("agents", {})

    # Messages (all inboxes)
    inbox_dir = data_dir / "inbox"
    if inbox_dir.exists():
        for agent_dir in inbox_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            agent_id = agent_dir.name
            for f in sorted(agent_dir.glob("*.json")):
                try:
                    msg = json.loads(f.read_text(encoding="utf-8"))
                    msg["_to"] = agent_id
                    msg["_read"] = f.name.endswith(".read.json")
                    bundle["messages"].append(msg)
                except Exception:
                    continue

    # Channels
    channels_dir = data_dir / "channels"
    if channels_dir.exists():
        for f in channels_dir.glob("*.json"):
            try:
                ch = json.loads(f.read_text(encoding="utf-8"))
                bundle["channels"].append(ch)
            except Exception:
                continue

    # Shared artifacts
    shared_dir = data_dir / "shared"
    if shared_dir.exists():
        for f in shared_dir.iterdir():
            if f.name.endswith(".meta.json") or f.is_dir():
                continue
            meta_file = shared_dir / f"{f.name}.meta.json"
            meta = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            try:
                content = f.read_text(encoding="utf-8")
                is_binary = False
            except UnicodeDecodeError:
                content = None
                is_binary = True
            bundle["shared"].append({
                **meta,
                "name": f.name,
                "content": content,
                "is_binary": is_binary,
                "size": f.stat().st_size,
            })

    # Settings
    settings_file = data_dir / "settings.json"
    if settings_file.exists():
        try:
            bundle["settings"] = json.loads(settings_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    return bundle


if __name__ == "__main__":
    data_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("v1-export.json")
    bundle = export_v1(data_dir)
    out_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Exported: {len(bundle['agents'])} agents, {len(bundle['messages'])} messages, "
          f"{len(bundle['channels'])} channels, {len(bundle['shared'])} artifacts")
