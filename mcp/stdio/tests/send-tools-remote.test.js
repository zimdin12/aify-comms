// comms_send's REMOTE branch, through the real tool handler against a local fake service (review of
// 13decd49). send-tools.test.js seals the local branch; this is the branch every agent actually runs, so the
// answer the formatter sees is the answer httpCall parsed, not a hand-built object.
import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

let REPLY = {};
const POSTS = [];
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    POSTS.push({ method: req.method, url: req.url, body });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(REPLY));
  });
});
const PORT = await new Promise((resolve) => SERVER.listen(0, "127.0.0.2", () => resolve(SERVER.address().port)));
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.CLAUDE_MCP_SERVER_URL = `http://127.0.0.2:${PORT}`;
const { registerSendTools } = await import("../send-tools.mjs");
const { IS_REMOTE } = await import("../aify-service-endpoint.mjs");
const { z } = await import("zod");

test.after(() => SERVER.close());

const tools = new Map();
registerSendTools({ tool: (name, description, schema, handler) => tools.set(name, handler) }, z);
const send = async (reply) => {
  REPLY = reply;
  const res = await tools.get("comms_send")({ from: "me", to: "peer", type: "info", subject: "s", body: "b" });
  return res.content[0].text;
};

test("the fixture really is remote and reaches the fake service", async () => {
  assert.equal(IS_REMOTE, true);
  POSTS.length = 0;
  await send({ ok: true, messageId: "m1", recipients: ["peer"], dispatchRuns: [], notStarted: [] });
  assert.equal(POSTS.length, 1);
  assert.match(POSTS[0].url, /\/messages\/send$/);
});

test("a present but unusable message id is incomplete, never 'Stored ()' or 'Stored ([object Object])'", async () => {
  for (const messageId of [[], {}, "   ", 7]) {
    const out = await send({ ok: true, messageId, recipients: ["peer"], dispatchRuns: [], consoleDeliveries: [], notStarted: [] });
    assert.match(out, /^The service's answer was incomplete \(no usable message id\)/, `${JSON.stringify(messageId)}: ${out}`);
    assert.doesNotMatch(out, /Stored \(/);
  }
});

test("CONTROLS: a string id is stored with the wake unknown; an absent id is incomplete", async () => {
  assert.match(await send({ ok: true, messageId: "m-7", recipients: ["peer"], dispatchRuns: [], notStarted: [] }),
    /^Stored \(m-7\)\. Whether a wake happened is not known from this answer\./);
  assert.match(await send({ ok: true, recipients: ["peer"], dispatchRuns: [], notStarted: [] }),
    /^The service's answer was incomplete \(no usable message id\)/);
});

test("a replay with an unusable id is incomplete too, not 'Already sent ()'", async () => {
  const out = await send({ ok: true, replayed: true, messageId: {}, recipients: [], dispatchRuns: [], notStarted: [] });
  assert.match(out, /^The service's answer was incomplete \(no usable message id\)/);
});
