#!/usr/bin/env node
// `registerConsoleTools` — the two tool declarations, and the one field a caller cannot supply.
//
// The export ratchet listed it as named by no test. Its siblings in `console-tools.mjs` were already
// covered: `console-tools.test.js` drives both handlers with an injected `httpCall`. What had nothing
// was the REGISTRATION — the schemas a model is given, and the wiring between the declaration and the
// handler.
//
// THE ATTRIBUTION IS THE PART THAT MATTERS. Console input is audited, and the audit's `from` is set by
// the registration rather than by the caller: `{ ...args, from: AIFY_AGENT_ID }` spreads the model's
// arguments FIRST and then overwrites `from`. So a tool call claiming to be somebody else is recorded
// as the process that actually made it. That ordering is one keystroke from being reversed, and
// reversed it would let any caller write its own name into the audit of a recovery-only lever.
//
// A FAKE SERVER captures the registrations. `server.tool(name, description, schema, callback)` is the
// whole of what this function touches, so a recorder is enough and no MCP transport is involved.
//
// The env vars are set BEFORE the import: `IS_REMOTE` and `AIFY_AGENT_ID` are both resolved at module
// load, once per process — the trap `console-tools.test.js` records for the same reason.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

// A REAL service on 127.0.0.2, because the registration's callbacks call the handlers WITHOUT
// injecting `httpCall` — production wiring, and the only place the merged arguments become
// observable. Started before the import: `IS_REMOTE` and `AIFY_AGENT_ID` are both resolved at module
// load, once per process.
const REQUESTS = [];
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, terminalId: "vterm_1", controlId: "ctl_1", live: false }));
  });
});
const PORT = await new Promise((resolve) => {
  SERVER.listen(0, "127.0.0.2", () => resolve(SERVER.address().port));
});

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;

// The modules read `CLAUDE_MCP_SERVER_URL || AIFY_SERVER_URL` — the LEGACY name WINS, and a

// live wrapper environment exports it. Setting only the new name left the fake below unused.

process.env.CLAUDE_MCP_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_AGENT_ID = "registration-test-agent";

const { CONSOLE_INPUT_TOOL_DESCRIPTION, registerConsoleTools } =
  await import("../console-tools.mjs");
const { AIFY_AGENT_ID } = await import("../launch-identity.mjs");

test.after(() => SERVER.close());

// A stand-in for zod that records what each field was asked to be. Only the calls the schemas below
// actually make are implemented; anything else throws, so a schema that grew a new constraint fails
// here instead of silently recording nothing.
function fakeZod() {
  const spec = (kind) => {
    const self = {
      kind,
      constraints: [],
      optional() { self.constraints.push("optional"); return self; },
      describe(text) { self.description = text; return self; },
      int() { self.constraints.push("int"); return self; },
      min(n) { self.constraints.push(`min:${n}`); return self; },
      max(n) { self.constraints.push(`max:${n}`); return self; },
    };
    return self;
  };
  return {
    string: () => spec("string"),
    number: () => spec("number"),
    boolean: () => spec("boolean"),
  };
}

function register() {
  const registered = [];
  const server = {
    tool(name, description, schema, callback) {
      registered.push({ name, description, schema, callback });
    },
  };
  registerConsoleTools(server, fakeZod());
  return registered;
}

function byName(name) {
  const found = register().find((t) => t.name === name);
  assert.ok(found, `${name} was not registered`);
  return found;
}

test("exactly the two console tools are registered", () => {
  // A census, not a spot check: a third tool appearing in this group is a decision, and one
  // disappearing is a capability an agent silently loses.
  assert.deepEqual(register().map((t) => t.name), ["comms_console_tail", "comms_console_input"]);
});

test("the INPUT tool uses the shared danger description, not a copy of it", () => {
  // Identity, so the long warning cannot drift from the constant the tests assert about. That
  // description is the only thing standing between a model and interrupting somebody mid-turn.
  assert.equal(byName("comms_console_input").description, CONSOLE_INPUT_TOOL_DESCRIPTION);
});

test("the TAIL tool's description says it works on a DEAD worker", () => {
  // The 2026-08-07 case: the cause sat in the terminal row for 2.5 hours while an operator relayed it
  // by hand, because nothing told the agent this tool could read a dead worker's last output.
  const description = byName("comms_console_tail").description;
  assert.match(description, /DEAD worker/);
  assert.match(description, /NOT LIVE/);
  assert.match(description, /why/i);
});

test("both tools require an agentId", () => {
  for (const name of ["comms_console_tail", "comms_console_input"]) {
    const schema = byName(name).schema;
    assert.equal(schema.agentId.kind, "string", name);
    assert.ok(!schema.agentId.constraints.includes("optional"), `${name}: agentId is optional`);
  }
});

test("the LINE COUNT is bounded in the schema as well as in the handler", () => {
  // The handler clamps to 1..200 itself, so this bound is the model-facing half of a duplicated
  // rule. Both halves are asserted, here and in `console-tools.test.js`, because a schema that
  // allowed 10000 would let a model ask for a payload the handler then silently truncates.
  const lines = byName("comms_console_tail").schema.lines;
  assert.equal(lines.kind, "number");
  assert.ok(lines.constraints.includes("int"), lines.constraints.join(","));
  assert.ok(lines.constraints.includes("min:1"), lines.constraints.join(","));
  assert.ok(lines.constraints.includes("max:200"), lines.constraints.join(","));
  assert.ok(lines.constraints.includes("optional"), "lines must be optional — the default is 40");
});

test("the input tool's TEXT and ENTER are optional", () => {
  // "Empty string plus enter=true sends just Enter" is a documented use, so requiring `text` would
  // remove the bare-Enter recovery the description talks about.
  const schema = byName("comms_console_input").schema;
  assert.ok(schema.text.constraints.includes("optional"));
  assert.ok(schema.enter.constraints.includes("optional"));
  assert.equal(schema.enter.kind, "boolean");
});

test("the enter field WARNS that it only attempts a submit", () => {
  // The C8 finding lives in two places by design: the tool description and this field's own text. A
  // model reading the argument list and not the preamble still has to see it.
  assert.match(byName("comms_console_input").schema.enter.description, /ATTEMPTS/);
});

test("the input registration STAMPS the caller's identity", async () => {
  // The audit property, driven through the real callback and read off the request the service
  // received. My first version of this test built `{ ...args, from: AIFY_AGENT_ID }` in the test and
  // asserted THAT — which proves the spread operator works and nothing about the registration.
  REQUESTS.length = 0;
  await byName("comms_console_input").callback({ agentId: "target", text: "hi" });
  const posted = REQUESTS.filter((r) => r.method === "POST");
  assert.equal(posted.length, 1, `expected one POST, got ${posted.length}`);
  assert.equal(JSON.parse(posted[0].body).from, AIFY_AGENT_ID);
});

test("a CALLER CANNOT OVERRIDE the recorded identity", async () => {
  // `{ ...args, from: AIFY_AGENT_ID }` — args first, `from` last. Reverse those two and any caller
  // could write somebody else's name into the audit of a recovery-only lever.
  REQUESTS.length = 0;
  await byName("comms_console_input").callback({
    agentId: "target", text: "hi", from: "somebody-else",
  });
  const body = JSON.parse(REQUESTS.filter((r) => r.method === "POST")[0].body);
  assert.equal(body.from, AIFY_AGENT_ID);
  assert.notEqual(body.from, "somebody-else");
});

test("the input callback reaches the agent's own console endpoint", async () => {
  REQUESTS.length = 0;
  await byName("comms_console_input").callback({ agentId: "tar get", text: "hi" });
  const posted = REQUESTS.filter((r) => r.method === "POST")[0];
  assert.equal(posted.url, "/api/v1/agents/tar%20get/console/input");
});

test("ENTER defaults to true through the registration", async () => {
  // The handler's default, reached via the registration rather than called directly: a bare recovery
  // Enter is the documented use, and a default of false would make every such call a no-op.
  REQUESTS.length = 0;
  await byName("comms_console_input").callback({ agentId: "target", text: "hi" });
  assert.equal(JSON.parse(REQUESTS.filter((r) => r.method === "POST")[0].body).enter, true);
});

test("the TAIL callback passes the model's arguments through to the endpoint", async () => {
  REQUESTS.length = 0;
  await byName("comms_console_tail").callback({ agentId: "target", lines: 7 });
  const got = REQUESTS.filter((r) => r.method === "GET")[0];
  assert.equal(got.url, "/api/v1/agents/target/console?lines=7");
});

test("the tail callback does NOT stamp an identity — it is a read", async () => {
  // Only the write is audited. Sending an identity on a read would put the caller's name into a
  // request that changes nothing, and the two tools' shapes should differ where their risk differs.
  REQUESTS.length = 0;
  await byName("comms_console_tail").callback({ agentId: "target" });
  const got = REQUESTS.filter((r) => r.method === "GET")[0];
  assert.ok(!got.url.includes("from="), got.url);
  assert.equal(got.body, "");
});
