// What this bridge says about an agent when it beats.
//
// The heartbeat is how the service knows an agent is alive and what it is doing. `bridgeId` attributes the
// beat to THIS process rather than the one it superseded; `machineId` and `terminalId` are how a beat is
// matched to a live worker. A beat missing its bridge id is not a beat with less detail — it is one the
// service cannot attribute, which reads as a bridge that has gone quiet.
//
// None of it was reachable from a test: it lived in `server.js`, the bin entry point, which nothing imports.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  baseAgentHeartbeatFields,
  currentTurnHeartbeatFields,
  reportTurnBusy,
} from "../agent-heartbeat.mjs";
import { BRIDGE_INSTANCE_ID } from "../bridge-instance.mjs";
import { declaringModules, isUsedInBridge } from "./bridge-sources.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "agent-heartbeat.mjs")).href;

test("every beat carries the bridge id that attributes it", () => {
  // The field the supersede logic keys on. Without it the service cannot tell which bridge is beating, and
  // a beat from an unattributable process is indistinguishable from no beat at all.
  const f = baseAgentHeartbeatFields({});
  assert.equal(f.bridgeId, BRIDGE_INSTANCE_ID, "the beat must carry THIS process's instance id");
  assert.ok(f.bridgeId, "…and it must be non-empty");
});

test("machineId prefers the agent's own record over this host", () => {
  // A managed worker's beat describes THAT worker, which may not be on this machine. Falling back to the
  // local machine id is right when the record does not say; overriding the record would misattribute it.
  assert.equal(baseAgentHeartbeatFields({ info: { machineId: "box-9" } }).machineId, "box-9");
  const fallback = baseAgentHeartbeatFields({}).machineId;
  assert.ok(fallback, "with no record it must still report a machine, not empty");
  assert.notEqual(fallback, "box-9");
  // snake_case is NOT read here — only `info.machineId`. Pinned because the service sends both spellings
  // elsewhere and a future edit might assume symmetry.
  assert.equal(baseAgentHeartbeatFields({ info: { machine_id: "box-8" } }).machineId, fallback,
    "current behaviour: only camelCase machineId is read from the record");
});

test("an unexpanded ${...} terminal id is blanked, not reported", () => {
  // Same placeholder hazard as everywhere else in this bridge: a config that writes `${AIFY_TERMINAL_ID}`
  // unexpanded hands over a truthy literal. Reported as a terminal id it would attribute the beat to a
  // terminal that does not exist.
  const prev = process.env.AIFY_TERMINAL_ID;
  try {
    process.env.AIFY_TERMINAL_ID = "${AIFY_TERMINAL_ID}";
    assert.equal(baseAgentHeartbeatFields({}).terminalId, "", "a placeholder must not become a terminal id");
    process.env.AIFY_TERMINAL_ID = "term_real";
    assert.equal(baseAgentHeartbeatFields({}).terminalId, "term_real");
    delete process.env.AIFY_TERMINAL_ID;
    assert.equal(baseAgentHeartbeatFields({ info: { terminalId: "from-record" } }).terminalId, "from-record",
      "with no env value the agent's own record supplies it");
  } finally {
    if (prev === undefined) delete process.env.AIFY_TERMINAL_ID; else process.env.AIFY_TERMINAL_ID = prev;
  }
});

test("currentTurnHeartbeatFields describes a turn only when there IS one", () => {
  // Two shapes, and the difference is what the service reads as "this agent is mid-turn". Emitting the
  // active-run shape with no run would assert work that is not happening.
  const idle = currentTurnHeartbeatFields({}, null);
  const busy = currentTurnHeartbeatFields({}, { id: "run-1", subject: "s" });
  assert.notDeepEqual(idle, busy, "an idle beat and a mid-turn beat must not be the same payload");
  assert.equal(idle.bridgeId, BRIDGE_INSTANCE_ID, "…and both carry the attribution");
  assert.equal(busy.bridgeId, BRIDGE_INSTANCE_ID);
});

// Post a real beat and report what the server received. THE SERVER RUNS INSIDE THE CHILD, ON 127.0.0.2,
// and both of those are safety requirements rather than style.
//
// WHAT WENT WRONG THE FIRST TIME, recorded because it nearly wrote to production. I hosted the fake server
// in the TEST process and drove the child with `execFileSync`, which blocks the parent's event loop — so the
// parent could not accept the child's connection. `httpCall` retried, then failed over, and the fallback it
// chose was `http://127.0.0.1:8800`: the operator's LIVE aify-comms. The beat was rejected only because the
// agent id did not exist ("FOREIGN KEY constraint failed"); a real id would have written to the live
// database from a unit test.
//
// Two independent fixes, either of which would have been enough:
//   * the server runs in the CHILD, so nothing blocks it;
//   * it binds 127.0.0.2, and `defaultFallbackServerUrls` only adds the real service when the primary is
//     exactly 127.0.0.1 or localhost — so there is no fallback path to production at all.
function beatVia(argsJs) {
  const script = `
    import http from "node:http";
    const received = [];
    const srv = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        received.push({ method: req.method, url: req.url, body: body ? JSON.parse(body) : null });
        res.writeHead(200, { "content-type": "application/json" });
        res.end("{}");
      });
    });
    await new Promise((r) => srv.listen(0, "127.0.0.2", r));
    process.env.AIFY_SERVER_URL = "http://127.0.0.2:" + srv.address().port;
    process.env.CLAUDE_MCP_SERVER_URL = "";
    const m = await import(${JSON.stringify(LEAF)});
    await m.reportTurnBusy(${argsJs});
    srv.close();
    process.stdout.write(JSON.stringify(received));
  `;
  return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script],
    { env: { ...sealedChildEnv(), AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "" },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] }));
}

test("reportTurnBusy puts the busy flag, run id and runtime ON THE WIRE", () => {
  // This payload is what holds an agent at `working` through a long turn. A dropped field reads as an agent
  // that stopped working, which is the symptom this heartbeat was added to fix.
  const got = beatVia("'agent-a', { info: { machineId: 'box-1' } }, { busy: true, runId: 'run-7', runtime: 'codex' }");
  assert.equal(got.length, 1, "exactly one beat must be posted");
  const [beat] = got;
  assert.equal(beat.method, "POST");
  assert.match(beat.url, /\/agents\/agent-a\/heartbeat$/, "it must beat for the named agent");
  assert.equal(beat.body.turnBusy, true, "the busy flag is the whole point of the beat");
  assert.equal(beat.body.turnRunId, "run-7", "the run must be identified");
  assert.equal(beat.body.turnRuntime, "codex", "…and the runtime, since status differs per runtime");
  assert.equal(beat.body.machineId, "box-1", "the base fields must be merged in, not replaced");
  assert.ok(beat.body.bridgeId, "…including the attribution");
});

test("reportTurnBusy can report NOT busy — the clear, not only the set", () => {
  // A heartbeat that could only assert `working` would never release an agent. `busy: false` is how a turn
  // ends on this path, and the coercion matters: the field must be a boolean, not the caller's argument.
  const [beat] = beatVia("'agent-a', {}, { busy: 0 }");
  assert.equal(beat.body.turnBusy, false, "a falsy busy must send boolean false, not 0");

  // EMPTY OPTIONALS ARE OMITTED, not sent as empty strings. Read from `agentHeartbeatPayload` after I
  // asserted `""` and got `undefined` — it does `if (runId) body.turnRunId = runId`. That is the better
  // contract and worth pinning: the service can distinguish "no run" from "a run with an empty id", which it
  // could not if the field were always present.
  assert.equal("turnRunId" in beat.body, false, "an absent run id must be OMITTED, not sent empty");
  assert.equal("turnRuntime" in beat.body, false, "…and likewise the runtime");
  // What is always present, because attribution is not optional.
  assert.ok("bridgeId" in beat.body, "every beat carries its bridge id");
  assert.ok("machineId" in beat.body, "…and its machine id");
});

test("BOTH turn-busy functions in this bridge are DIFFERENT functions with the same name", () => {
  // A collision worth asserting rather than commenting. `hermes-run-reporting.mjs` exports
  // `reportTurnBusy(httpCall, agentId, {...})`; this module exports
  // `reportTurnBusy(agentId, state, {...})`. Importing the wrong one type-checks nowhere and fails at
  // runtime with a confusing shape, so the arities are pinned.
  // FOUR modules define this name, not two — I assumed two and the scan corrected me. `claude-channel.js`
  // and `hermes-channel.js` have their own as well. That makes the collision worse than the header said, and
  // worth pinning as an inventory: a fifth appearing should be a decision, not a surprise.
  const owners = declaringModules("reportTurnBusy").map((o) => o.file).sort();
  assert.deepEqual(owners,
    ["agent-heartbeat.mjs", "claude-channel.js", "hermes-channel.js", "hermes-run-reporting.mjs"],
    "the reportTurnBusy inventory changed — check the collision note in agent-heartbeat.mjs");
  assert.equal(reportTurnBusy.length, 1, "this one takes (agentId, state = {}, opts = {}) → arity 1");
  const hermesSrc = readFileSync(path.join(STDIO, "hermes-run-reporting.mjs"), "utf-8");
  assert.match(hermesSrc, /reportTurnBusy\(httpCall, agentId/, "the hermes one takes httpCall FIRST");
});

test("exactly one module declares each of the three, and the bridge still calls them", () => {
  for (const name of ["baseAgentHeartbeatFields", "currentTurnHeartbeatFields"]) {
    assert.deepEqual(
      declaringModules(name), [{ file: "agent-heartbeat.mjs", kind: "function" }],
      `${name} must be declared exactly once, by its owner`,
    );
  }
  const server = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  assert.doesNotMatch(server, /^(?:async\s+)?function\s+(baseAgentHeartbeatFields|currentTurnHeartbeatFields|reportTurnBusy)\b/m,
    "none may be redeclared in server.js");
  // BRIDGE-WIDE: the caller moved to `dispatch-loop.mjs` with the dispatch pass in v0.5.4. The
  // no-redeclaration check above still names server.js, which is right — that one is about server.js
  // specifically not holding a second copy.
  assert.equal(isUsedInBridge("reportTurnBusy"), true, "the bridge must still report turn-busy");
});

test("the owner holds no state and reaches only owned leaves", () => {
  const src = readFileSync(path.join(STDIO, "agent-heartbeat.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, [
    "./aify-service-endpoint.mjs",
    "./bridge-instance.mjs",
    "./launch-identity.mjs",
    "./runtimes.js",
    "./turn-busy.js",
  ]);
});

// Same child-hosted, 127.0.0.2-bound fixture as `beatVia`, for the three-argument current-turn reporter.
function beatVia2(argsJs) {
  const script = `
    import http from "node:http";
    const received = [];
    const srv = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        received.push({ method: req.method, url: req.url, body: body ? JSON.parse(body) : null });
        res.writeHead(200, { "content-type": "application/json" });
        res.end("{}");
      });
    });
    await new Promise((r) => srv.listen(0, "127.0.0.2", r));
    process.env.AIFY_SERVER_URL = "http://127.0.0.2:" + srv.address().port;
    process.env.CLAUDE_MCP_SERVER_URL = "";
    const m = await import(${JSON.stringify(LEAF)});
    await m.reportAgentHeartbeat(${argsJs});
    srv.close();
    process.stdout.write(JSON.stringify(received));
  `;
  return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script],
    { env: { ...sealedChildEnv(), AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "" },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] }));
}

test("reportAgentHeartbeat posts the CURRENT-TURN payload for the named agent", () => {
  // The other beat on this module: `reportTurnBusy` announces a turn starting or ending, this one is the
  // periodic "still here, and here is what I am doing" that carries `currentTurnHeartbeatFields`. It joined
  // this module in v0.5.4 because both halves of it already lived here — the payload above and the same
  // `httpCall` transport — so in `server.js` it was a four-line wrapper importing its own content back.
  const got = beatVia2("'agent-b', { info: { machineId: 'box-2' } }, { id: 'run-9', subject: 's' }");
  assert.equal(got.length, 1, "exactly one beat");
  const [beat] = got;
  assert.equal(beat.method, "POST");
  assert.match(beat.url, /\/agents\/agent-b\/heartbeat$/, "…for the agent it was called with");
  assert.equal(beat.body.machineId, "box-2", "the base fields are merged in");
  assert.ok(beat.body.bridgeId, "…including the attribution every beat carries");
});

test("an idle beat and a mid-turn beat are different payloads on the wire", () => {
  // The distinction the service reads as "this agent is mid-turn". Asserted through the real POST rather
  // than by comparing the field builders, because the wire is what the service actually sees.
  const idle = beatVia2("'agent-b', {}, null")[0];
  const busy = beatVia2("'agent-b', {}, { id: 'run-9' }")[0];
  assert.notDeepEqual(idle.body, busy.body, "an idle beat must not look like a mid-turn one");
});

test("exactly one module declares reportAgentHeartbeat, and the bridge still beats", () => {
  assert.deepEqual(declaringModules("reportAgentHeartbeat"),
    [{ file: "agent-heartbeat.mjs", kind: "function" }]);
  const server = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  // BRIDGE-WIDE: the caller moved to `dispatch-loop.mjs` with the dispatch pass in v0.5.4.
  assert.equal(isUsedInBridge("reportAgentHeartbeat"), true, "the bridge must still beat");
  assert.doesNotMatch(server, /^async function reportAgentHeartbeat/m, "…and must not re-declare it");
});
