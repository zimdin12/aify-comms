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

import { agentHeartbeatFields } from "../agent-heartbeat.mjs";
import { BRIDGE_INSTANCE_ID } from "../bridge-instance.mjs";
import { isUsedInBridge } from "./bridge-sources.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "agent-heartbeat.mjs")).href;

test("every beat carries the bridge id that attributes it", () => {
  // The field the supersede logic keys on. Without it the service cannot tell which bridge is beating, and
  // a beat from an unattributable process is indistinguishable from no beat at all.
  const f = agentHeartbeatFields({});
  assert.equal(f.bridgeId, BRIDGE_INSTANCE_ID, "the beat must carry THIS process's instance id");
  assert.ok(f.bridgeId, "…and it must be non-empty");
});

test("machineId prefers the agent's own record over this host", () => {
  // A managed worker's beat describes THAT worker, which may not be on this machine. Falling back to the
  // local machine id is right when the record does not say; overriding the record would misattribute it.
  assert.equal(agentHeartbeatFields({ info: { machineId: "box-9" } }).machineId, "box-9");
  const fallback = agentHeartbeatFields({}).machineId;
  assert.ok(fallback, "with no record it must still report a machine, not empty");
  assert.notEqual(fallback, "box-9");
  // snake_case is NOT read here — only `info.machineId`. Pinned because the service sends both spellings
  // elsewhere and a future edit might assume symmetry.
  assert.equal(agentHeartbeatFields({ info: { machine_id: "box-8" } }).machineId, fallback,
    "current behaviour: only camelCase machineId is read from the record");
});

test("an unexpanded ${...} terminal id is blanked, not reported", () => {
  // Same placeholder hazard as everywhere else in this bridge: a config that writes `${AIFY_TERMINAL_ID}`
  // unexpanded hands over a truthy literal. Reported as a terminal id it would attribute the beat to a
  // terminal that does not exist.
  const prev = process.env.AIFY_TERMINAL_ID;
  try {
    process.env.AIFY_TERMINAL_ID = "${AIFY_TERMINAL_ID}";
    assert.equal("terminalId" in agentHeartbeatFields({}), false, "a placeholder must not become a terminal id");
    process.env.AIFY_TERMINAL_ID = "   ";
    assert.equal("terminalId" in agentHeartbeatFields({}), false, "…nor must blanks: an empty id matches no worker");
    process.env.AIFY_TERMINAL_ID = "term_real";
    assert.equal(agentHeartbeatFields({}).terminalId, "term_real");
    delete process.env.AIFY_TERMINAL_ID;
    assert.equal(agentHeartbeatFields({ info: { terminalId: "from-record" } }).terminalId, "from-record",
      "with no env value the agent's own record supplies it");
  } finally {
    if (prev === undefined) delete process.env.AIFY_TERMINAL_ID; else process.env.AIFY_TERMINAL_ID = prev;
  }
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
    await m.reportAgentHeartbeat(${argsJs});
    srv.close();
    process.stdout.write(JSON.stringify(received));
  `;
  return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script],
    { env: { ...sealedChildEnv(), AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "" },
      encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] }));
}

test("the owner holds no state and reaches only owned leaves", () => {
  const src = readFileSync(path.join(STDIO, "agent-heartbeat.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, [
    "./aify-service-endpoint.mjs",
    "./bridge-instance.mjs",
    "./launch-identity.mjs",
    "./machine-id.mjs",
  ]);
});

test("reportAgentHeartbeat posts who and where for the named agent, and never a turn", () => {
  // Since 0.9.9 S3c no beat describes a turn: the turn is aify-env's, and comms ignored `turnBusy` from S2a.
  const got = beatVia("'agent-b', { info: { machineId: 'box-2' } }");
  assert.equal(got.length, 1, "exactly one beat");
  const [beat] = got;
  assert.equal(beat.method, "POST");
  assert.match(beat.url, /\/agents\/agent-b\/heartbeat$/, "…for the agent it was called with");
  assert.equal(beat.body.machineId, "box-2", "the base fields are merged in");
  assert.ok(beat.body.bridgeId, "…including the attribution every beat carries");
  assert.deepEqual(Object.keys(beat.body).filter((k) => k.startsWith("turn")), [], "no turn field on the wire");
});

test("the bridge still beats", () => {
  // A second declaration of reportAgentHeartbeat is gated bridge-wide by each-name-has-one-owner.test.js.
  // BRIDGE-WIDE: the caller moved to `dispatch-loop.mjs` with the dispatch pass in v0.5.4.
  assert.equal(isUsedInBridge("reportAgentHeartbeat"), true, "the bridge must still beat");
});
