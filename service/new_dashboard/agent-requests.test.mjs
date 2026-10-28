// The History drawer's requests section: what the host did with each request, read back.

import assert from "node:assert/strict";
import { test } from "node:test";

import { REQUEST_ROWS, loadAgentRequests, requestRows, requestsSection } from "./agent-requests.mjs";

const life = (action, at, extra = {}) => ({ action, status: "done", outcome: "", requestedBy: "dashboard", createdAt: at, ...extra });
const def = (patch, at, extra = {}) => ({ patch, status: "pending", outcome: "", requestedBy: "dashboard", createdAt: at, ...extra });

test("BOTH KINDS IN ONE LIST, newest first, in the drawer's words", () => {
  const rows = requestRows(
    [life("stop", "2026-10-28T01:00:00Z"), life("kill", "2026-10-28T03:00:00Z"), life("release", "2026-10-28T04:00:00Z")],
    [def({ model: "opus", role: "lead" }, "2026-10-28T02:00:00Z")]);
  assert.deepEqual(rows.map((r) => r.what), ["release hold", "stop", "edit model, role", "hold"]);
});

test("AN OUTCOME IS SHOWN, so a refusal reads as one and not as queued", () => {
  const html = requestsSection(requestRows([], [def({ harness: "codex" }, "t", { status: "refused", outcome: "launcher codex is not installed" })]));
  assert.match(html, /request-refused/);
  assert.match(html, /launcher codex is not installed/);
});

test("ONLY THE NEWEST ROWS, and a definition removal is named", () => {
  const many = Array.from({ length: REQUEST_ROWS + 5 }, (_, i) => life("start", `2026-10-28T00:${String(i).padStart(2, "0")}:00Z`));
  assert.equal(requestRows(many).length, REQUEST_ROWS);
  assert.equal(requestRows([], [def({ remove: true }, "t")])[0].what, "remove definition");
});

test("A LIST THAT CANNOT BE READ IS NAMED, and the other is still shown", async () => {
  const ask = async (path) => {
    if (path.includes("lifecycle")) return { requests: [life("kill", "t")] };
    throw new Error("HTTP 500");
  };
  const html = await loadAgentRequests("coder", ask);
  assert.match(html, /Could not read its definition requests: HTTP 500/);
  assert.match(html, /<strong>stop<\/strong>/);
  const none = await loadAgentRequests("coder", async () => ({ requests: [] }));
  assert.match(none, /None asked\./);
  assert.doesNotMatch(none, /Could not read/);
});
