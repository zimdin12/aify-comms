// A refused start-up registration retries every 30 s; once the process has begun to leave, none may start.
//
// Review of 9f6e1a90: a registration that lands after the leaving process reported itself gone makes the gone
// process the agent's owner. `resident-duties.mjs` hands the registrar `active: () => not yet leaving`, and the
// retry asks it. The real registrar against a loopback stand-in that refuses as a live predecessor does; the 30 s
// are mock time.

import assert from "node:assert/strict";
import http from "node:http";
import { mock, test } from "node:test";

let posts = 0;
const server = http.createServer((req, res) => {
  req.resume();
  req.on("end", () => {
    if (req.method === "POST" && req.url === "/api/v1/agents") posts += 1;
    res.writeHead(409, { "content-type": "application/json" });
    res.end(JSON.stringify({ detail: "agent 'retry-witness' already has a LIVE resident bridge; pass force=true" }));
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
// Read once, at import, by the endpoint and identity modules: set before the registrar is imported.
Object.assign(process.env, {
  AIFY_SERVER_URL: `http://127.0.0.1:${server.address().port}`, CLAUDE_MCP_SERVER_URL: "",
  AIFY_AGENT_ID: "retry-witness", AIFY_RUNTIME: "claude-code", AIFY_SESSION_MODE: "resident",
});
const { makeAutoRegister } = await import("../auto-registration.mjs");

async function postsAfterThirtySeconds(active) {
  posts = 0;
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    await makeAutoRegister({ ensureDispatchLoop: () => {}, active })();
    assert.equal(posts, 1, "the first registration did not reach the stand-in");
    mock.timers.tick(30_000);
  } finally {
    mock.timers.reset();
  }
  await new Promise((resolve) => setTimeout(resolve, 500));
  return posts;
}

test("a process that has begun to leave starts no retry", async () => {
  assert.equal(await postsAfterThirtySeconds(() => false), 1);
});

test("control: a process still running retries after 30 s", async () => {
  assert.equal(await postsAfterThirtySeconds(() => true), 2);
});

test.after(() => new Promise((resolve) => server.close(resolve)));
