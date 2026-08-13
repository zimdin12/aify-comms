// Where the bridge keeps messages when there is no service to talk to.
//
// In local mode — no `AIFY_SERVER_URL` — the bridge is its own backing store: agents, inboxes and
// shared artifacts are files on disk. These four paths are that store's layout. v0.5.4 layer 0 of the
// server.js decomposition; they had 40 readers between them and no owner, which made them look like a
// dependency of whichever tool group was being measured at the time.
//
// THE DEFAULT PATH IS RELATIVE TO THIS FILE, AND THAT IS LOAD-BEARING. With no
// `CLAUDE_MCP_MESSAGES_DIR` set, `MESSAGES_DIR` resolves to `.messages` beside the module that computes
// it. It computed the same directory in `server.js` because this file is its neighbour — both sit
// directly in `mcp/stdio/`. Move this file into a subdirectory and every local-mode agent silently
// starts reading an empty store while its real messages sit in the old one: no error, no missing file,
// just an agent that has forgotten everything. `local-store.test.js` asserts the location for that
// reason, and that assertion is not housekeeping.
//
// THE `mkdirSync` BOOTSTRAP DELIBERATELY DID NOT COME ALONG. It stays in `server.js`. Creating
// directories is an action taken at startup, not a fact about where they are, and a module that writes
// to the filesystem when imported cannot be imported by a test — which is the whole reason these
// definitions were unreachable before.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import path from "path";

export const MESSAGES_DIR =
  process.env.CLAUDE_MCP_MESSAGES_DIR ||
  path.join(
    path.dirname(
      decodeURIComponent(new URL(import.meta.url).pathname).replace(/^\/([A-Z]:)/, "$1")
    ),
    ".messages"
  );
export const AGENTS_FILE = path.join(MESSAGES_DIR, "agents.json");
export const INBOX_DIR = path.join(MESSAGES_DIR, "inbox");
export const SHARED_DIR = path.join(MESSAGES_DIR, "shared");

// ── Reading and writing that store ───────────────────────────────────────────
//
// The five accessors that ARE the local-mode backing store's API. They joined the paths here in v0.5.4
// because they are the same subject: a module that says where the store is but not how to read it
// leaves its only real consumer — the tool groups — importing two modules to do one thing.
//
// NOTHING HERE RUNS AT IMPORT. Each touches the filesystem only when called. That is the property the
// tests assert empirically, by importing this module in a child process pointed at a directory that
// does not exist and checking it still does not exist afterwards. The `mkdirSync` bootstrap that runs
// at startup deliberately stayed in `server.js` for the same reason.
//
// THEY SWALLOW ERRORS AND RETURN EMPTY, WHICH IS DELIBERATE AND WORTH KNOWING. A missing or corrupt
// agents file reads as `{ agents: {} }`, and an unreadable inbox as `[]`. A first run has no store at
// all, so "absent" is the normal case rather than a fault — but it does mean a corrupt file is
// indistinguishable from an empty one to every caller.

import { randomUUID } from "crypto";
import fs from "fs";

export function readAgents() {
  try {
    return JSON.parse(fs.readFileSync(AGENTS_FILE, "utf-8"));
  } catch {
    return { agents: {} };
  }
}

export function writeAgents(data) {
  fs.writeFileSync(AGENTS_FILE, JSON.stringify(data, null, 2));
}

export function readInbox(agentId, filter = "unread") {
  const dir = path.join(INBOX_DIR, agentId);
  fs.mkdirSync(dir, { recursive: true });
  try {
    let files = fs.readdirSync(dir).filter((f) => f.endsWith(".json")).sort().reverse();
    if (filter === "unread") files = files.filter((f) => !f.endsWith(".read.json"));
    else if (filter === "read") files = files.filter((f) => f.endsWith(".read.json"));
    return files.map((f) => {
      const msg = JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8"));
      msg._file = f;
      msg._read = f.endsWith(".read.json");
      return msg;
    });
  } catch {
    return [];
  }
}

export function markAsRead(agentId, messages) {
  const dir = path.join(INBOX_DIR, agentId);
  for (const m of messages) {
    if (m._read) continue;
    const oldPath = path.join(dir, m._file);
    const newPath = path.join(dir, m._file.replace(/\.json$/, ".read.json"));
    try { fs.renameSync(oldPath, newPath); } catch { /* race or already renamed */ }
  }
}

export function deliverMessage(toAgentId, message) {
  const dir = path.join(INBOX_DIR, toAgentId);
  fs.mkdirSync(dir, { recursive: true });
  const filename = `${Date.now()}-${randomUUID().slice(0, 8)}.json`;
  fs.writeFileSync(
    path.join(dir, filename),
    JSON.stringify({ ...message, timestamp: Date.now() })
  );
}
