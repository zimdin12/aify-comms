// The shared-artifact tools, executed rather than scanned.
//
// `comms_share`, `comms_read`, `comms_files`. Each has TWO implementations — a multipart POST in remote
// mode, a file under `SHARED_DIR` with a `.meta.json` sidecar in local mode — and they must agree on
// what a caller sees. Until v0.5.4 all of it lived in `server.js`, the bin entry point, which nothing
// imports, so neither path was reachable from a test.
//
// This file drives the LOCAL path, which is the one with no service to stand in for it and the one
// where a wrong answer means a lost file. The remote path's wire format is pinned separately by
// `share-multipart-integrity.test.js`, which reconstructs the body byte for byte.

import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Local mode, into a scratch store: no server URL, and CLAUDE_MCP_MESSAGES_DIR relocates the whole
// layout so nothing here touches the developer's real `.messages`.
const STORE = mkdtempSync(path.join(os.tmpdir(), "aify-artifacts-"));
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";
process.env.CLAUDE_MCP_MESSAGES_DIR = STORE;

const { registerArtifactTools } = await import("../artifact-tools.mjs");
const { SHARED_DIR } = await import("../local-store.mjs");
const { z } = await import("zod");

const tools = new Map();
registerArtifactTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);

// `comms_share`'s local branch writes into SHARED_DIR; the directory bootstrap lives in server.js, which
// is not loaded here, so the test creates it the way startup would.
import { mkdirSync } from "node:fs";
mkdirSync(SHARED_DIR, { recursive: true });

const text = (res) => res.content[0].text;

test("the wrapper registers exactly the three artifact tools", () => {
  assert.deepEqual([...tools.keys()].sort(), ["comms_files", "comms_read", "comms_share"]);
  for (const [name, tool] of tools) {
    assert.equal(typeof tool.handler, "function", `${name} must have a handler`);
    assert.ok(tool.description.length > 10, `${name} must describe itself`);
  }
});

test("the scratch store is really in use — nothing here writes to the real one", () => {
  // Anti-vacuity for every assertion below: if the env override had not taken effect, these tests would
  // be reading and writing the developer's actual artifact directory and passing just the same.
  assert.ok(SHARED_DIR.startsWith(STORE), `SHARED_DIR should be under the scratch store, got ${SHARED_DIR}`);
});

test("a shared artifact round-trips: written, listed, and read back verbatim", () => {
  const body = "line one\nline two\n";
  const shared = tools.get("comms_share").handler({ name: "notes.txt", content: body, from: "agent-a" });
  return Promise.resolve(shared).then(async (res) => {
    assert.ok(!res.isError, `share failed: ${text(res)}`);
    // On disk under the name given, not a mangled or prefixed one.
    assert.equal(readFileSync(path.join(SHARED_DIR, "notes.txt"), "utf-8"), body);

    const listed = text(await tools.get("comms_files").handler({}));
    assert.match(listed, /notes\.txt/, "a shared file must appear in the listing");

    const readBack = await tools.get("comms_read").handler({ name: "notes.txt" });
    assert.ok(!readBack.isError, `read failed: ${text(readBack)}`);
    assert.match(text(readBack), /line one/, "the content must come back");
    assert.match(text(readBack), /line two/, "…all of it, not just the first line");
  });
});

test("the sidecar records who shared it, and the artifact itself stays clean", async () => {
  await tools.get("comms_share").handler({
    name: "meta-check.txt", content: "payload", from: "agent-b", description: "a description",
  });
  const sidecar = JSON.parse(readFileSync(path.join(SHARED_DIR, "meta-check.txt.meta.json"), "utf-8"));
  assert.equal(sidecar.from, "agent-b", "the sidecar must record the sharer");
  // The metadata must NOT have been mixed into the file — that is the failure that corrupts a shared
  // artifact silently, and it is the local-mode twin of the multipart CRLF bug.
  assert.equal(readFileSync(path.join(SHARED_DIR, "meta-check.txt"), "utf-8"), "payload");
});

test("a name that could escape SHARED_DIR is refused, and writes nothing", async () => {
  // `validateName` is the guard, but what matters here is the OUTCOME: no file outside the store, and
  // an error the caller can act on rather than a silent success.
  const before = readdirSync(SHARED_DIR).length;
  for (const name of ["../escape.txt", "a/b.txt", "..", ".hidden"]) {
    const res = await tools.get("comms_share").handler({ name, content: "x", from: "agent-a" });
    assert.equal(res.isError, true, `${name} must be rejected by share`);
    const read = await tools.get("comms_read").handler({ name });
    assert.equal(read.isError, true, `${name} must be rejected by read`);
  }
  assert.equal(readdirSync(SHARED_DIR).length, before, "a rejected share must not have written anything");
});

test("reading an artifact that does not exist says so instead of throwing", async () => {
  const res = await tools.get("comms_read").handler({ name: "no-such-file.txt" });
  assert.equal(res.isError, true);
  assert.ok(!/undefined|\[object Object\]/.test(text(res)), `leaked a placeholder: ${text(res)}`);
});

test("the listing hides sidecars and survives an artifact whose metadata is missing or corrupt", async () => {
  // Both happen in practice: a file copied in by hand has no sidecar, and a half-written one is invalid
  // JSON. Either must degrade to a listing without that detail, never to a failed listing.
  writeFileSync(path.join(SHARED_DIR, "orphan.txt"), "no sidecar here");
  writeFileSync(path.join(SHARED_DIR, "broken.txt"), "content");
  writeFileSync(path.join(SHARED_DIR, "broken.txt.meta.json"), "{not json");

  const listed = text(await tools.get("comms_files").handler({}));
  assert.match(listed, /orphan\.txt/, "a file with no sidecar must still be listed");
  assert.match(listed, /broken\.txt/, "a file with corrupt metadata must still be listed");
  assert.ok(!/meta\.json/.test(listed), "sidecars are bookkeeping and must not appear as artifacts");
  assert.ok(!/undefined|NaN|\[object Object\]/.test(listed), `leaked a placeholder: ${listed}`);
});

test("the module exports only its owner surface, and kept no state", () => {
  const src = readFileSync(path.join(STDIO, "artifact-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
  assert.equal(
    (src.match(/^export /gm) || []).length, 1,
    "a group leaf exports its wrapper and nothing it merely happens to contain",
  );
});

test("server.js kept none of the three — exactly one owner", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of ["comms_share", "comms_read", "comms_files"]) {
    assert.doesNotMatch(src, new RegExp(`server\\.tool\\(\\s*\\n?\\s*"${name}"`), `${name} still in server.js`);
  }
  assert.match(src, /registerArtifactTools\(server, z\);/, "server.js must still CALL the wrapper");
});

process.on("exit", () => { try { rmSync(STORE, { recursive: true, force: true }); } catch { /* best effort */ } });
