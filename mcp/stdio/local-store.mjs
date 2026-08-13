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
