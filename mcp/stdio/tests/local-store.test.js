// The local-mode filesystem store's layout.
//
// With no `AIFY_SERVER_URL`, the bridge IS the backing store: agents, inboxes and shared artifacts are
// files on disk under these paths. Forty readers across `server.js` depended on them and none of it was
// reachable from a test, because `server.js` is the bin entry point and nothing imports it.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { AGENTS_FILE, INBOX_DIR, MESSAGES_DIR, SHARED_DIR } from "../local-store.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STDIO = path.resolve(HERE, "..");
const LEAF = path.join(STDIO, "local-store.mjs");

test("this module must stay directly in mcp/stdio, or every local agent loses its messages", () => {
  // NOT housekeeping. `MESSAGES_DIR` defaults to `.messages` beside the module that computes it, and it
  // computed the right directory inside `server.js` only because this file is server.js's neighbour.
  // Moved into a subdirectory, the default would silently become `mcp/stdio/<subdir>/.messages`: no
  // error, no missing file, just every local-mode agent reading an empty store while its real inbox
  // sits in the old one. This asserts the one property the relocation rests on.
  assert.ok(
    readdirSync(STDIO).includes("local-store.mjs"),
    "local-store.mjs must sit directly in mcp/stdio, beside server.js",
  );
  assert.equal(
    path.resolve(path.dirname(LEAF)), path.resolve(STDIO),
    "if this file moves, MESSAGES_DIR's default moves with it and the store is orphaned",
  );
});

test("the default store sits beside the bridge, not beside the caller's cwd", () => {
  // The second half of the same property, and the one that actually matters in production: agents are
  // launched from their own project directories, and all of them must reach the SAME store.
  //
  // My first version compared MESSAGES_DIR against `process.cwd()` in-process and failed — the bridge
  // suite runs FROM mcp/stdio, so the two are legitimately equal here. That assertion could only ever
  // have passed by accident of where the runner stood. Proving cwd-independence requires actually
  // standing somewhere else.
  assert.equal(path.resolve(MESSAGES_DIR), path.resolve(STDIO, ".messages"));
  const elsewhere = path.resolve(STDIO, "..", "..");
  const fromElsewhere = execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "import { MESSAGES_DIR } from " + JSON.stringify(pathToFileURL(LEAF).href)
      + "; process.stdout.write(MESSAGES_DIR);"],
    { cwd: elsewhere, env: { ...process.env, CLAUDE_MCP_MESSAGES_DIR: "" }, encoding: "utf-8" },
  );
  assert.notEqual(path.resolve(elsewhere), path.resolve(STDIO), "the two directories must really differ");
  assert.equal(
    path.resolve(fromElsewhere), path.resolve(STDIO, ".messages"),
    "an agent launched from another directory must still reach the bridge's own store",
  );
});

test("the three sub-paths hang off MESSAGES_DIR and are distinct", () => {
  assert.equal(AGENTS_FILE, path.join(MESSAGES_DIR, "agents.json"));
  assert.equal(INBOX_DIR, path.join(MESSAGES_DIR, "inbox"));
  assert.equal(SHARED_DIR, path.join(MESSAGES_DIR, "shared"));
  // Distinctness matters: two of these collapsing would make one store overwrite another, and the
  // symptom would be missing messages rather than an error.
  assert.equal(new Set([AGENTS_FILE, INBOX_DIR, SHARED_DIR]).size, 3);
});

test("CLAUDE_MCP_MESSAGES_DIR relocates the whole store, sub-paths included", () => {
  // Resolved at module load, so an in-process assertion could never observe the override — it is fixed
  // by however THIS process started. A child process is the only way to see both cases.
  const read = (env) => JSON.parse(execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "import { MESSAGES_DIR, AGENTS_FILE, INBOX_DIR, SHARED_DIR } from "
      + JSON.stringify(pathToFileURL(LEAF).href)
      + "; process.stdout.write(JSON.stringify({ MESSAGES_DIR, AGENTS_FILE, INBOX_DIR, SHARED_DIR }));"],
    { env: { ...process.env, CLAUDE_MCP_MESSAGES_DIR: "", ...env }, encoding: "utf-8" },
  ));

  const custom = path.join(path.resolve(STDIO, ".."), "custom-store-for-test");
  const moved = read({ CLAUDE_MCP_MESSAGES_DIR: custom });
  assert.equal(moved.MESSAGES_DIR, custom, "the override must win over the default");
  assert.equal(moved.INBOX_DIR, path.join(custom, "inbox"), "the inbox must follow the override");
  assert.equal(moved.SHARED_DIR, path.join(custom, "shared"), "shared artifacts must follow it too");
  assert.equal(moved.AGENTS_FILE, path.join(custom, "agents.json"), "the registry must follow it too");

  // And without it, back to the neighbour default — the anti-vacuity half.
  assert.equal(read({}).MESSAGES_DIR, path.resolve(STDIO, ".messages"));
});

test("server.js declares none of the four — exactly one owner", () => {
  // A leftover `const MESSAGES_DIR` would shadow the import and keep working, until the two derivations
  // disagreed and half the bridge wrote to a different directory than the other half.
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of ["MESSAGES_DIR", "AGENTS_FILE", "INBOX_DIR", "SHARED_DIR"]) {
    assert.doesNotMatch(src, new RegExp(`^(?:const|let|var)\\s+${name}\\b`, "m"), `${name} must be imported`);
  }
  assert.match(src, /(?<![\w.])MESSAGES_DIR(?![\w])/, "server.js is still expected to READ them");
  // The bootstrap that creates the directories deliberately stayed behind: a module that writes to disk
  // on import cannot be imported by a test.
  assert.match(src, /mkdirSync/, "the directory bootstrap belongs to startup, not to this leaf");
});

test("importing this module touches the filesystem NOT AT ALL", () => {
  // Asserted empirically, and it had to be. The first two versions were regexes over the source: one
  // forbade the words and matched the header comment explaining why the bootstrap stayed behind; the
  // second forbade the CALLS, which was correct until the five accessors moved in and made `fs` calls
  // legitimately present. A source regex cannot tell "calls fs when invoked" from "calls fs on import",
  // and that distinction is the entire property.
  //
  // So: point the module at a directory that does not exist, import it in a child process, and check
  // the directory still does not exist. Nothing else proves an import is inert.
  const ghost = path.join(os.tmpdir(), `aify-store-must-not-exist-${process.pid}`);
  rmSync(ghost, { recursive: true, force: true });
  assert.equal(existsSync(ghost), false, "the probe directory must be absent before the import");

  execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "const m = await import(" + JSON.stringify(pathToFileURL(LEAF).href) + ");"
      + " if (typeof m.readAgents !== 'function') { throw new Error('module did not load'); }"],
    { env: { ...process.env, CLAUDE_MCP_MESSAGES_DIR: ghost }, encoding: "utf-8" },
  );

  assert.equal(
    existsSync(ghost), false,
    "importing the store module created its directory — a module that writes on import cannot be imported by a test",
  );
});

test("no module-level mutable state", () => {
  assert.doesNotMatch(readFileSync(LEAF, "utf-8"), /^let\s/m);
});

// ── The five accessors ───────────────────────────────────────────────────────
//
// These ARE the local-mode backing store. Every one of them was unreachable from a test until v0.5.4.

test("the agents registry round-trips, and a missing or corrupt file reads as empty", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "aify-agents-"));
  const run = (script) => execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "const { readAgents, writeAgents, AGENTS_FILE } = await import("
      + JSON.stringify(pathToFileURL(LEAF).href) + ");"
      + "const fs = await import('node:fs');" + script],
    { env: { ...process.env, CLAUDE_MCP_MESSAGES_DIR: dir }, encoding: "utf-8" },
  );
  try {
    // Absent file: the normal first-run case, not a fault.
    assert.equal(run("process.stdout.write(JSON.stringify(readAgents()));"), '{"agents":{}}');
    // Round-trip.
    assert.equal(
      run("writeAgents({ agents: { a: { role: 'coder' } } });"
        + "process.stdout.write(JSON.stringify(readAgents().agents.a));"),
      '{"role":"coder"}',
    );
    // Corrupt file reads as empty too — deliberate, and the reason a caller cannot distinguish a
    // damaged registry from a fresh one. Pinned so the choice stays visible.
    assert.equal(
      run("fs.writeFileSync(AGENTS_FILE, '{not json');"
        + "process.stdout.write(JSON.stringify(readAgents()));"),
      '{"agents":{}}',
    );
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("a delivered message lands unread, reads back, and marking it read is not a delete", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "aify-inbox-"));
  const out = execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "const { deliverMessage, readInbox, markAsRead } = await import("
      + JSON.stringify(pathToFileURL(LEAF).href) + ");"
      + "deliverMessage('agent-b', { from: 'agent-a', subject: 's', body: 'hello' });"
      + "const unread = readInbox('agent-b');"
      + "markAsRead('agent-b', unread);"
      + "process.stdout.write(JSON.stringify({"
      + "  unread: unread.length, body: unread[0]?.body, stamped: !!unread[0]?.timestamp,"
      + "  afterUnread: readInbox('agent-b').length,"
      + "  afterRead: readInbox('agent-b', 'read').length,"
      + "  afterAll: readInbox('agent-b', 'all').length,"
      + "  emptyForStranger: readInbox('nobody').length,"
      + "}));"],
    { env: { ...process.env, CLAUDE_MCP_MESSAGES_DIR: dir }, encoding: "utf-8" },
  );
  try {
    const r = JSON.parse(out);
    assert.equal(r.unread, 1, "a delivered message must arrive unread");
    assert.equal(r.body, "hello", "…with its body intact");
    assert.equal(r.stamped, true, "the store stamps a delivery time");
    assert.equal(r.afterUnread, 0, "once read it must leave the unread view");
    assert.equal(r.afterRead, 1, "…and appear in the read view");
    assert.equal(r.afterAll, 1, "marking read must RENAME, not delete — the message still exists");
    assert.equal(r.emptyForStranger, 0, "an agent with no inbox reads empty rather than throwing");
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
