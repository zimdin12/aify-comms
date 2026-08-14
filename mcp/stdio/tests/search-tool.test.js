// `comms_search`, executed rather than scanned.
//
// THE DEFECT THIS TOOL WAS RESHAPED TO PREVENT is not "search misses things". It is that an empty result
// was being read as "no such message exists" when messages had not been searched AT ALL — omitting
// `agentId` searches shared artifacts only. An empty answer then licensed work that had already been
// ruled out: the tool failed OPEN. So every response must report what it actually searched.
//
// Until v0.5.4 this lived in `server.js`, the bin entry point, which nothing imports, so none of that
// was reachable from a test. The only guard was a regex in `transport-parity.test.js` asserting the
// strings "searched" and "NOT searched" appear in both transports' source — which cannot tell whether
// they reach a caller.

import assert from "node:assert/strict";
import test from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const STORE = mkdtempSync(path.join(os.tmpdir(), "aify-search-"));
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";
process.env.CLAUDE_MCP_MESSAGES_DIR = STORE;

const { registerSearchTool } = await import("../search-tool.mjs");
const { SHARED_DIR, deliverMessage, MESSAGES_DIR } = await import("../local-store.mjs");
const { z } = await import("zod");

const tools = new Map();
registerSearchTool(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const search = tools.get("comms_search");
const text = (res) => res.content[0].text;

mkdirSync(SHARED_DIR, { recursive: true });

test("the scratch store is really in use", () => {
  assert.ok(MESSAGES_DIR.startsWith(STORE), `expected the scratch store, got ${MESSAGES_DIR}`);
});

test("the tool registers, and its own description warns about the omitted-agentId trap", () => {
  assert.ok(search, "comms_search must be registered");
  // The warning has to reach the model CHOOSING the tool, not only the reader of its output — by then
  // the wrong conclusion has already been formed.
  assert.match(search.description, /NOT searched/i, "the description must warn that messages can be skipped");
  assert.match(
    search.schema.agentId.description, /OMIT AND MESSAGES ARE NOT SEARCHED/i,
    "the agentId field itself must say what omitting it costs",
  );
});

test("a match in the agent's own messages is found and rendered", async () => {
  deliverMessage("agent-b", {
    id: "m1", from: "agent-a", subject: "deploy the widget", body: "please deploy it tonight",
  });
  const res = await search.handler({ agentId: "agent-b", query: "widget" });
  assert.ok(!res.isError, `search failed: ${text(res)}`);
  assert.match(text(res), /m1|deploy the widget/, "the matching message must be reported");
});

test("search covers the BODY, not only the subject", async () => {
  // Both are in the haystack, and a caller looking for a phrase they remember from the body is the
  // common case. Asserting only the subject would let a body-only regression pass.
  const res = await search.handler({ agentId: "agent-b", query: "tonight" });
  assert.match(text(res), /deploy the widget/, "a term appearing only in the body must still match");
});

test("a shared artifact matches on its name, its description, AND its contents", async () => {
  writeFileSync(path.join(SHARED_DIR, "runbook.md"), "the incantation is xyzzy\n");
  writeFileSync(path.join(SHARED_DIR, "runbook.md.meta.json"),
    JSON.stringify({ from: "agent-a", description: "how to restart the thing" }));

  assert.match(text(await search.handler({ query: "runbook" })), /runbook\.md/, "by name");
  assert.match(text(await search.handler({ query: "restart the thing" })), /runbook\.md/, "by description");
  assert.match(text(await search.handler({ query: "xyzzy" })), /runbook\.md/, "by content");
});

test("DEFECT, PINNED NOT FIXED: in LOCAL mode an empty result still reads as proof of absence", async () => {
  // FOUND BY THIS EXTRACTION, and it is the tool's own founding defect surviving in one of its two
  // branches. Omitting `agentId` searches shared artifacts ONLY. The REMOTE branch says so — it appends
  // `(searched: …)` and a `NOT searched:` warning. The LOCAL branch returns a bare
  // `No results for "<query>".` with no scope note at all, so an empty answer is indistinguishable from
  // "messages were never looked at" — which is exactly what let an empty result license work that had
  // already been ruled out. The tool fails OPEN here.
  //
  // WHY NO TEST CAUGHT IT: `transport-parity.test.js` asserts the strings "searched" and "NOT searched"
  // appear in each transport's SOURCE. The remote branch supplies both, so the file-level assertion is
  // satisfied while one of the two code paths has neither. That is the same half-fix its own header
  // comment records for comms_search ("I fixed the stdio renderer, believed I was done, and found the
  // SSE copy afterwards") — one layer further down.
  //
  // This is a structural slice, so the behaviour is PINNED rather than corrected, and reported as its own
  // packet. Change these assertions only as part of that fix.
  const res = await search.handler({ query: "definitely-not-present-anywhere" });
  assert.equal(
    text(res), 'No results for "definitely-not-present-anywhere".',
    "current LOCAL behaviour: no scope note. When the fix lands, this assertion is what must change.",
  );

  // The remote branch's shape is asserted here too, so the pair cannot silently converge on the WRONG
  // one. If someone 'simplifies' the remote renderer to match local, this fails.
  const src = readFileSync(path.join(STDIO, "search-tool.mjs"), "utf-8");
  assert.match(src, /searched: \$\{r\.searched\.join/, "the remote branch must keep reporting what it searched");
  assert.match(src, /NOT searched/, "…and must keep warning about what it skipped");
});

test("scope=shared does not search messages, and scope=inbox does not search artifacts", async () => {
  // Each scope must actually restrict. A scope that is accepted and then ignored is worse than a
  // rejected one, because the caller believes they narrowed the search.
  const sharedOnly = text(await search.handler({ agentId: "agent-b", query: "widget", scope: "shared" }));
  assert.doesNotMatch(sharedOnly, /deploy the widget/, "scope=shared must not return a message hit");

  const inboxOnly = text(await search.handler({ agentId: "agent-b", query: "runbook", scope: "inbox" }));
  assert.doesNotMatch(inboxOnly, /runbook\.md/, "scope=inbox must not return an artifact hit");
});

test("the result count is capped and the truncation is declared", async () => {
  for (let i = 0; i < 8; i++) {
    deliverMessage("agent-cap", { id: `c${i}`, from: "agent-a", subject: `cap ${i}`, body: "capsearch" });
  }
  const limited = text(await search.handler({ agentId: "agent-cap", query: "capsearch", limit: 3 }));
  assert.match(limited, /8 total, showing 3/, "a truncated result must say how much it left out");
});

test("an unreadable or binary artifact does not fail the whole search", async () => {
  // A shared directory accumulates whatever agents put in it. One unreadable file must degrade to
  // "not a content match", never to a failed search that hides every other hit.
  writeFileSync(path.join(SHARED_DIR, "blob.bin"), Buffer.from([0x00, 0xff, 0xfe, 0x00]));
  const res = await search.handler({ query: "runbook" });
  assert.ok(!res.isError, `a binary neighbour broke the search: ${text(res)}`);
  assert.match(text(res), /runbook\.md/, "the real hit must still be reported");
});

test("the module exports only its owner surface and kept no state", () => {
  const src = readFileSync(path.join(STDIO, "search-tool.mjs"), "utf-8");
  assert.equal((src.match(/^export /gm) || []).length, 1, "one export: the wrapper");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
});

test("server.js kept none of it — exactly one owner", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  assert.doesNotMatch(src, /server\.tool\(\s*\n?\s*"comms_search"/, "comms_search still registered in server.js");
  // Moved with the registration list to `register-tools.mjs` in v0.5.4.
  const reg = readFileSync(path.join(STDIO, "register-tools.mjs"), "utf-8");
  assert.match(reg, /registerSearchTool\(server, z\);/, "the registrar must still CALL the wrapper");
});

process.on("exit", () => { try { rmSync(STORE, { recursive: true, force: true }); } catch { /* best effort */ } });
