// The terminal-control claim pass, tested by CALLING it against a real HTTP server.
//
// Extracted from server.js in v0.5.4, where nothing could reach it. This is the console path: it claims
// one terminal control at a time and starts, stops, writes to or reaps a terminal.
//
// NOTHING HERE STARTS A TERMINAL. Every case ends at or before the workspace check, so no PTY is
// spawned and no process is killed by this suite — which matters more than usual here, because the
// branches past that point reap processes by pid.
//
// Fake service on 127.0.0.2, `AIFY_SERVER_URL` set BEFORE the import.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";

const REQUESTS = [];
let CONTROL = null;
let CLAIM_STATUS = 200;
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    if (req.url.includes("/claim") && CLAIM_STATUS !== 200) {
      res.writeHead(CLAIM_STATUS, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "nope" }));
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(req.url.includes("/claim") ? { controls: CONTROL ? [CONTROL] : [] } : {}));
  });
});
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
const { runTerminalControlPass } = await import("../terminal-control-loop.mjs");

test.after(() => SERVER.close());

const deps = (environment) => ({
  CLAIM_OPTS: {},
  CLAIM_WAIT_MS: 0,
  effectiveEnvironmentPayload: () => environment,
  extractTerminalSessionHandle: () => "",
});

function scenario(control, { status = 200 } = {}) {
  REQUESTS.length = 0;
  CONTROL = control;
  CLAIM_STATUS = status;
}

const ENV = { id: "env-1", cwdRoots: ["C:/work"] };

test("it claims terminal controls for THIS environment", async () => {
  scenario(null);
  await runTerminalControlPass(deps(ENV));
  const claim = REQUESTS.find((r) => r.url.includes("/claim"));
  assert.ok(claim, "a claim must be attempted");
  assert.equal(JSON.parse(claim.body).environmentId, "env-1");
});

test("NO CONTROL costs exactly one request — this runs every 800ms forever", async () => {
  // The tightest loop in the bridge. An idle poll that PATCHed anything would be thousands of writes
  // an hour against the operator's own service.
  scenario(null);
  await runTerminalControlPass(deps(ENV));
  const patches = REQUESTS.filter((r) => r.method === "PATCH");
  assert.deepEqual(patches, [], "an idle poll must not PATCH");
});

test("A START OUTSIDE THE DECLARED ROOTS IS REFUSED — a terminal is a shell on this machine", async () => {
  // The console gives whoever holds the dashboard a live shell. The workspace check is what confines it
  // to the roots this environment advertised, and the request originates elsewhere.
  scenario({ id: "tc-1", action: "start", terminalId: "t1", workspace: "C:/somewhere-else", runtime: "claude" });
  await runTerminalControlPass(deps(ENV));
  const patch = REQUESTS.find((r) => r.method === "PATCH");
  assert.ok(patch, "the control must be resolved rather than left claimed");
  const sent = JSON.parse(patch.body);
  assert.equal(sent.status, "failed");
  assert.match(String(sent.error), /root/i, "the refusal must say why");
});

test("A CLAIM ERROR IS NOT SWALLOWED HERE — the loop's catch owns it", async () => {
  // Unlike the spawn pass, this one has NO try/catch around the claim: an HTTP error rejects out of the
  // pass and `runTerminalControlLoop`'s own catch records it. Asserted as it BEHAVES because the two
  // passes differing is easy to mistake for an oversight in whichever one you read second — and moving
  // the handling would change which failures the claim tracker counts.
  scenario(null, { status: 404 });
  await assert.rejects(() => runTerminalControlPass(deps(ENV)), /404/);

  scenario(null, { status: 500 });
  await assert.rejects(() => runTerminalControlPass(deps(ENV)));
});
