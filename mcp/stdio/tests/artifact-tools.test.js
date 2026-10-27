// The shared-artifact tools, executed rather than scanned.
//
// `comms_share`, `comms_read`, `comms_files`, `comms_unshare`. The upload's wire format is pinned by
// `share-multipart-integrity.test.js`, which reconstructs the body byte for byte.
// What each tool DOES is held by the reply fixtures (tool-replies.test.js, and the service's Python copy
// against the same cases). Local mode, the file store these tests used to drive, was deleted in 0.9.11.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// No service: nothing here may reach one.
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const { registerArtifactTools } = await import("../artifact-tools.mjs");
const { z } = await import("zod");

const tools = new Map();
registerArtifactTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);

// `comms_share`'s local branch writes into SHARED_DIR; the directory bootstrap lives in server.js, which
// is not loaded here, so the test creates it the way startup would.

const text = (res) => res.content[0].text;

test("the wrapper registers exactly the four artifact tools", () => {
  // `comms_unshare` joined them 2026-08-18. Until then there was NO per-item delete, so an agent
  // wanting to remove one artifact had only `comms_clear(target="shared")` — which wipes every
  // artifact on the hub for every team. The endpoint existed and had no ownership check either;
  // both were fixed together, because adding the tool alone would have opened the hole.
  assert.deepEqual([...tools.keys()].sort(),
    ["comms_files", "comms_read", "comms_share", "comms_unshare"]);
  for (const [name, tool] of tools) {
    assert.equal(typeof tool.handler, "function", `${name} must have a handler`);
    assert.ok(tool.description.length > 10, `${name} must describe itself`);
  }
});

test("the module exports only its owner surface, and kept no state", () => {
  const src = readFileSync(path.join(STDIO, "artifact-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
  assert.equal(
    (src.match(/^export /gm) || []).length, 1,
    "a group leaf exports its wrapper and nothing it merely happens to contain",
  );
});

test("with no service URL, share and read say so instead of throwing on a relative URL", async () => {
  // They fetch directly (multipart, raw bytes), past httpCall's refusal (review of 12e9cf37).
  const { NO_SERVICE_URL } = await import("../aify-service-endpoint.mjs");
  for (const [name, args] of [["comms_share", { from: "a", name: "n.txt", content: "x" }], ["comms_read", { name: "n.txt" }]]) {
    const res = await tools.get(name).handler(args);
    assert.equal(res.isError, true, name);
    assert.equal(res.content[0].text, NO_SERVICE_URL, name);
  }
});
