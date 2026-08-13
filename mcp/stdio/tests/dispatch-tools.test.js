// The dispatch tool group, executed rather than scanned.
//
// Five tools — `comms_dispatch`, `comms_run_status`, `comms_contracts`, `comms_run_interrupt`,
// `comms_interrupt` — and the two helpers only they use. Until v0.5.4 all of it was inside `server.js`,
// the bin entry point, which nothing imports: the only way to check any of it was to regex the source,
// and a regex cannot fail on wrong logic. This file registers the real tools on a fake MCP server and
// calls the real handlers.
//
// `comms_contracts` has its own file (`comms-contracts-defaults.test.js`) for its two defaults, and
// `comms_interrupt`'s console behaviour is asserted in `console-tools.test.js` where the rest of the
// console surface lives. What is here is the group: what it registers, what it refuses, and the two
// helpers' output.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// No server URL: IS_REMOTE resolves false, which is the branch every one of these tools guards on.
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const { commsInterruptHandler, registerDispatchTools, summarizeContract } =
  await import("../dispatch-tools.mjs");
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

test("summarizeContract renders state, route, age and subject", () => {
  const line = summarizeContract({
    from: "agent-a", targetAgentId: "agent-b", state: "missing_reply",
    subject: "deploy the thing", ageMinutes: 42,
  });
  assert.match(line, /MISSING REPLY/, "the underscore state must read as words, upper-cased");
  assert.match(line, /agent-a -> agent-b/);
  assert.match(line, /42m/, "an age under an hour is shown in minutes");
  assert.match(line, /deploy the thing/);
});

test("summarizeContract switches to hours past 60 minutes, and never prints a placeholder", () => {
  assert.match(summarizeContract({ ageMinutes: 90 }), /1\.5h/);
  assert.match(summarizeContract({ ageMinutes: 60 }), /1h/, "exactly 60 minutes is already hours");
  assert.match(summarizeContract({ ageMinutes: 59 }), /59m/, "the minute below the boundary stays minutes");
  // Degenerate inputs. An operator reading "undefined -> NaN" learns nothing about their fleet, and
  // this is a function whose entire output is read by a human.
  for (const contract of [{}, { ageMinutes: "" }, { ageMinutes: null }, { ageMinutes: "abc" }, { ageMinutes: -5 }]) {
    const line = summarizeContract(contract);
    assert.ok(!/undefined|NaN|\[object Object\]/.test(line), `leaked a placeholder: ${line}`);
  }
  assert.match(summarizeContract({}), /\(no subject\)/, "a contract with no subject says so");
  assert.match(summarizeContract({}), /SENT/, "state falls back to sent");
});

test("summarizeContract truncates a long answer preview rather than dumping it", () => {
  const line = summarizeContract({ resultPreview: "x".repeat(500) });
  assert.match(line, /answer: x+/);
  assert.ok(line.length < 300, `an unbounded preview would flood the caller: ${line.length} chars`);
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
