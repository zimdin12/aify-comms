// The dispatch tool group, executed rather than scanned.
//
// Five tools — `comms_dispatch`, `comms_run_status`, `comms_contracts`, `comms_run_interrupt`,
// `comms_interrupt` — and the two helpers only they use. Until v0.5.4 all of it was inside `server.js`,
// the bin entry point, which nothing imports: the only way to check any of it was to regex the source,
// and a regex cannot fail on wrong logic. This file registers the real tools on a fake MCP server and
// calls the real handlers.
//
// Three files cover this group, split by subject rather than by file of origin:
//   * here — what the wrapper registers, what it refuses, and its export surface;
//   * `comms-contracts-defaults.test.js` — the contracts defaults AND the private `summarizeContract`
//     rendering, both driven through the real handler against a loopback server;
//   * `console-tools.test.js` — `comms_interrupt`'s console behaviour, beside the rest of that surface.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// No server URL: IS_REMOTE resolves false, which is the branch every one of these tools guards on.
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

// Two exports, and the module has exactly two by design. `summarizeContract` is group-private and is
// proven through `comms_contracts` in `comms-contracts-defaults.test.js` — a test is not a consumer
// that justifies widening a module's API.
const { commsInterruptHandler, registerDispatchTools } = await import("../dispatch-tools.mjs");
const { z } = await import("zod");

function register() {
  const tools = new Map();
  registerDispatchTools(
    { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
    z,
  );
  return tools;
}

const EXPECTED = [
  "comms_dispatch", "comms_run_status", "comms_contracts", "comms_run_interrupt", "comms_interrupt",
];

test("the wrapper registers exactly the five dispatch tools", () => {
  assert.deepEqual([...register().keys()].sort(), [...EXPECTED].sort());
});

test("registration is an effect of CALLING the wrapper, never of importing the module", () => {
  // If the module registered at import time it would fire on any import, including this test's, and
  // the group could not be exercised without a real MCP transport. Two independent calls must also
  // produce two independent registrations — a shared or memoised server would couple callers.
  const a = register();
  const b = register();
  assert.equal(a.size, 5);
  assert.equal(b.size, 5);
  assert.notEqual(a.get("comms_dispatch"), b.get("comms_dispatch"), "each call registers afresh");
});

test("every tool arrives with a description and a usable schema", () => {
  // An MCP tool with no description is invisible to the model that has to choose it; a missing schema
  // means arguments are never validated. Both are silent at registration time.
  for (const [name, tool] of register()) {
    assert.equal(typeof tool.description, "string", `${name} must have a description`);
    assert.ok(tool.description.length > 30, `${name}'s description is too short to be useful`);
    assert.equal(typeof tool.handler, "function", `${name} must have a handler`);
    assert.equal(typeof tool.schema, "object", `${name} must declare a schema`);
    for (const [field, shape] of Object.entries(tool.schema)) {
      assert.equal(typeof shape?.parse, "function", `${name}.${field} is not a zod schema`);
    }
  }
});

test("in local mode every dispatch tool refuses instead of half-working", () => {
  // These all need the HTTP service. The failure that matters is not an exception — it is a tool that
  // returns something plausible-looking while having reached nothing.
  return Promise.all([...register()].map(async ([name, tool]) => {
    const res = await tool.handler({ agentId: "a", runId: "r", to: "b", subject: "s", body: "x" });
    assert.equal(res.isError, true, `${name} must report an error in local mode`);
    assert.match(res.content[0].text, /remote|server mode/i, `${name} should say WHY it refused`);
  }));
});

test("commsInterruptHandler refuses in local mode without calling anything", async () => {
  let called = false;
  const res = await commsInterruptHandler(
    { agentId: "a" },
    { httpCall: async () => { called = true; return { ok: true }; } },
  );
  assert.equal(res.isError, true);
  assert.equal(called, false, "it must not reach the service before checking the mode");
});

test("the module exports its owner surface and nothing it merely happens to contain", async () => {
  // The reviewer's rule for group leaves: export `registerDispatchTools`, plus only helpers with a real
  // consumer outside the group. `commsInterruptHandler` qualifies — `console-tools.test.js` asserts its
  // console behaviour, and it was already exported before the move. `summarizeContract` does not, and
  // briefly was exported here anyway; that is the drift this asserts against.
  const mod = await import("../dispatch-tools.mjs");
  assert.deepEqual(
    Object.keys(mod).sort(), ["commsInterruptHandler", "registerDispatchTools"],
    "a group leaf's API should not grow just because a test found something convenient to import",
  );
});

test("neither comms_register nor runDispatchLoop came along", () => {
  // The negative proof the reviewer required. Those two carry a 53-function closure between them and
  // were excluded before this slice began; the risk is that a later edit quietly pulls one in.
  //
  // Asserted as CALLS and REGISTRATIONS, not as the bare words. The first version of this test
  // forbade the identifier anywhere in the file and failed on the header comment that explains why
  // these two are excluded — a check that punishes documenting the invariant it protects.
  const src = readFileSync(path.join(STDIO, "dispatch-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /(?<![\w.])runDispatchLoop\s*\(/, "runDispatchLoop must not be CALLED here");
  assert.doesNotMatch(src, /import\b[^;]*runDispatchLoop/, "runDispatchLoop must not be imported here");
  assert.doesNotMatch(src, /server\.tool\(\s*\n?\s*"comms_register"/, "comms_register must not be registered here");
  // And it must not have acquired module state on the way — that is what would make the two
  // independent registrations above start sharing.
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
});

test("server.js kept none of it — exactly one owner", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of EXPECTED) {
    assert.doesNotMatch(
      src, new RegExp(`server\\.tool\\(\\s*\\n?\\s*"${name}"`),
      `${name} is still registered in server.js as well — two registrations, one name`,
    );
  }
  for (const helper of ["commsInterruptHandler", "summarizeContract"]) {
    assert.doesNotMatch(src, new RegExp(`function\\s+${helper}\\b`), `${helper} must not be redeclared`);
  }
  assert.match(src, /registerDispatchTools\(server, z\);/, "server.js must still CALL the wrapper");
});
