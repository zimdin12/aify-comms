// comms_listen asks for its messages unmarked and marks each one read once it holds it (v0.7.4).
//
// `/listen` marked what it returned inside its own commit, so a caller that disconnected after that
// commit had messages marked read that no reader held. The route's side is proven through the app
// (service/tests/test_listen_long_poll.py, markRead=false). This pins the bridge's side: the tool's
// listen URL opts out, and every received message is marked by id. The tool runs only against a live
// service (IS_REMOTE), so the registration's source is read.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const src = readFileSync(new URL("../inbox-tools.mjs", import.meta.url), "utf8");
const start = src.indexOf('"comms_listen"');
const body = src.slice(start, src.indexOf("server.tool(", start + 1) > 0 ? src.indexOf("server.tool(", start + 1) : undefined);

test("the listen URL asks the route not to mark what it returns", () => {
  assert.ok(start > 0, "comms_listen's registration was not found; this reader is stale");
  assert.match(body, /\/listen\?timeout=\$\{maxWait\}&markRead=false/);
});

test("every received message is marked read by id, after it is held", () => {
  const mark = body.indexOf("/read`, { agentId }");
  assert.ok(mark > 0, "no mark-read call");
  assert.match(body, /r\.messages\.map\(\(m\) => httpCall\("POST", `\/messages\/\$\{encodeURIComponent\(m\.id\)\}\/read`/);
  assert.ok(mark > body.indexOf("const r = await res.json()"), "marked before the response was held");
});
