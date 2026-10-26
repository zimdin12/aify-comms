// A bridge that re-registers after a 404 rebuilds its config from this machine, never from the redacted record.
//
// The service serves agent records with credentials redacted (688d8525), and the dispatch loop caches what it is
// served. On a non-tombstoned 404 the bridge re-registered from that cache, so `token=[redacted]` became the stored
// gateway credential, and the operator's gateway read handed back the marker (review of 688d8525, reproduced through
// the real route). The hermes gateway URL comes from this process's environment, as at startup.
//
// A REAL SERVICE on 127.0.0.2, and the gateway variable, both set before the import: they resolve at module load.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

const POSTS = [];
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    if (req.method === "POST") POSTS.push(JSON.parse(body));
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
  });
});
const PORT = await new Promise((resolve) => { SERVER.listen(0, "127.0.0.2", () => resolve(SERVER.address().port)); });
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.CLAUDE_MCP_SERVER_URL = `http://127.0.0.2:${PORT}`;
const LIVE = "ws://127.0.0.1:9341/api/ws?token=live-launcher-token";
process.env.AIFY_HERMES_GATEWAY_URL = LIVE;
delete process.env.AIFY_TERMINAL_ID;

const { reregisterAgentFromState } = await import("../auto-registration.mjs");
const { REDACTION_MARKER } = await import("../api-exposure.mjs");

test.after(() => SERVER.close());

const served = (runtime, runtimeConfig) => ({ info: { role: "coder", runtime, sessionMode: "resident", cwd: "/w", runtimeConfig } });

test("a hermes bridge posts the gateway its launcher gave it, not the served marker", async () => {
  POSTS.length = 0;
  await reregisterAgentFromState("hermes-a", served("hermes", {
    gatewayUrl: `ws://127.0.0.1:9341/api/ws?token=${REDACTION_MARKER}`, effort: "high" }));
  assert.equal(POSTS.length, 1, "control: the recovery posted");
  assert.equal(POSTS[0].runtimeConfig.gatewayUrl, LIVE);
  assert.equal(POSTS[0].runtimeConfig.effort, "high", "a setting the record carries survives");
});

test("any other value still carrying the marker is left out rather than stored", async () => {
  POSTS.length = 0;
  await reregisterAgentFromState("generic-a", served("generic", {
    statusUrl: `http://127.0.0.1:1/?key=${REDACTION_MARKER}`, nested: { auth: `Bearer ${REDACTION_MARKER}` }, effort: "low" }));
  assert.deepEqual(POSTS[0].runtimeConfig, { effort: "low" });
  assert.ok(!JSON.stringify(POSTS[0]).includes(REDACTION_MARKER), "the marker reached the service");
});
