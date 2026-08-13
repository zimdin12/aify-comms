// The local-mode filesystem store's layout.
//
// With no `AIFY_SERVER_URL`, the bridge IS the backing store: agents, inboxes and shared artifacts are
// files on disk under these paths. Forty readers across `server.js` depended on them and none of it was
// reachable from a test, because `server.js` is the bin entry point and nothing imports it.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
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

test("the leaf performs no I/O at import — it only computes paths", () => {
  // Asserted as CALLS, not as the bare words — the first version matched the header comment explaining
  // why the `mkdirSync` bootstrap stayed behind. Second time this session that a negative proof punished
  // the documentation of the invariant it protects.
  const src = readFileSync(LEAF, "utf-8");
  for (const fn of ["mkdirSync", "writeFileSync", "readFileSync", "readdirSync", "appendFileSync"]) {
    assert.doesNotMatch(src, new RegExp(`\\b${fn}\\s*\\(`), `${fn}() must not run when this module loads`);
  }
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
});
