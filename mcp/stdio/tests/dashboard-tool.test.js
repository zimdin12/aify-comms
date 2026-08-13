// The dashboard tool, executed rather than scanned.
//
// In remote mode it opens the service's dashboard URL. In local mode there is no service, so it GENERATES
// an HTML view from the filesystem store — agents, inboxes and shared artifacts assembled on the spot. That
// generated page is most of the code and had no test: `server.js` is the bin entry point and nothing imports
// it, so nothing had ever checked that the page it writes is well-formed or contains what it claims.
//
// `open: false` is used throughout. These tests must never launch a browser, and the tool's own parameter is
// the supported way to say so — no stubbing required.

import assert from "node:assert/strict";
import test from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { STDIO_DIR, toolSources } from "./bridge-sources.mjs";

const STORE = mkdtempSync(path.join(os.tmpdir(), "aify-dashboard-"));
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";
process.env.CLAUDE_MCP_MESSAGES_DIR = STORE;

const dashboard = await import("../dashboard-tool.mjs");
const { writeAgents, deliverMessage, SHARED_DIR, MESSAGES_DIR } = await import("../local-store.mjs");
const { z } = await import("zod");

const tools = new Map();
dashboard.registerDashboardTool(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const tool = tools.get("comms_dashboard");
const text = (res) => res.content[0].text;

mkdirSync(SHARED_DIR, { recursive: true });

test("the scratch store is really in use", () => {
  assert.ok(MESSAGES_DIR.startsWith(STORE), `expected the scratch store, got ${MESSAGES_DIR}`);
});

test("the tool registers, exports only the wrapper, and can be told not to open a browser", () => {
  assert.ok(tool, "comms_dashboard must be registered");
  assert.deepEqual(Object.keys(dashboard).sort(), ["registerDashboardTool"]);
  assert.ok(tool.schema.open, "the `open` parameter must exist — it is how a caller declines the launch");
});

test("local mode writes a real HTML file and reports where it put it", async () => {
  writeAgents({ agents: { "agent-a": { role: "coder", runtime: "codex", status: "idle" } } });
  const res = await tool.handler({ open: false });
  assert.ok(!res.isError, `dashboard failed: ${text(res)}`);

  // The response must name the file, or a caller has been told something happened with no way to find it.
  const match = text(res).match(/([A-Za-z]:\\[^\s"']+\.html|\/[^\s"']+\.html)/);
  assert.ok(match, `the response must name the generated file: ${text(res)}`);
  const html = readFileSync(match[1], "utf-8");

  assert.match(html, /<html/i, "it must actually be HTML");
  assert.match(html, /<\/html>/i, "…and complete, not truncated mid-write");
  assert.match(html, /agent-a/, "…and contain the fleet it was generated from");
});

test("the generated page reflects the store it was built from, not a cached one", async () => {
  // The failure this catches: a page written once and reused. An operator refreshing after a change would
  // see the old fleet and conclude nothing had happened.
  writeAgents({ agents: { "agent-first": { role: "coder", runtime: "codex" } } });
  const first = await tool.handler({ open: false });
  const firstPath = text(first).match(/([A-Za-z]:\\[^\s"']+\.html|\/[^\s"']+\.html)/)[1];
  assert.match(readFileSync(firstPath, "utf-8"), /agent-first/);

  writeAgents({ agents: { "agent-second": { role: "tester", runtime: "pi" } } });
  await tool.handler({ open: false });
  const regenerated = readFileSync(firstPath, "utf-8");
  assert.match(regenerated, /agent-second/, "the regenerated page must show the new fleet");
  assert.doesNotMatch(regenerated, /agent-first/, "…and not the one that is gone");
});

test("shared artifacts and inbox counts appear, and a broken sidecar does not stop the page", async () => {
  // A shared directory accumulates whatever agents put in it. One unreadable file must degrade to a page
  // missing that detail, never to no page at all — the whole point of a fallback view is that it works when
  // things are already wrong.
  writeAgents({ agents: { "agent-a": { role: "coder", runtime: "codex" } } });
  deliverMessage("agent-a", { id: "m1", from: "agent-b", subject: "hello", body: "body" });
  writeFileSync(path.join(SHARED_DIR, "notes.txt"), "content");
  writeFileSync(path.join(SHARED_DIR, "notes.txt.meta.json"), "{not json");

  const res = await tool.handler({ open: false });
  assert.ok(!res.isError, `a corrupt sidecar broke the page: ${text(res)}`);
  const html = readFileSync(text(res).match(/([A-Za-z]:\\[^\s"']+\.html|\/[^\s"']+\.html)/)[1], "utf-8");
  assert.match(html, /notes\.txt/, "the artifact must be listed despite its unreadable metadata");
  assert.ok(!/undefined|NaN|\[object Object\]/.test(html), "the page must not render a placeholder to a human");
});

test("open:false really does not launch anything", () => {
  // Asserted structurally, because the alternative is launching a browser during the suite. The launch is
  // guarded on the parameter in BOTH branches — remote and local — and a guard present in only one is the
  // failure that would surprise whoever runs this on a machine with a service configured.
  // Counted first and it was wrong: 4 `open !== false` occurrences against 2 launches, because the other
  // two make the "Opened in browser." claim conditional. Equality was the wrong relation. What matters is
  // CONTAINMENT — every launch sits inside a guard — so this checks each launch line against the line above
  // it rather than comparing totals.
  const lines = readFileSync(path.join(STDIO_DIR, "dashboard-tool.mjs"), "utf-8").split("\n");
  const launches = lines.map((line, i) => [i, line]).filter(([, line]) => /spawn\(openCmd/.test(line));
  assert.ok(launches.length >= 1, "the tool does launch a browser by default");
  for (const [i] of launches) {
    assert.match(
      lines[i - 1], /if \(open !== false\)/,
      `the launch on line ${i + 1} must be directly guarded by the open parameter`,
    );
  }
  assert.equal(launches.length, 2, "both modes launch — remote opens a URL, local opens the generated file");

  // And the CLAIM must be conditional too. A response saying "Opened in browser." when nothing was opened
  // is misinformation, which is this repo's recurring failure mode rather than a crash.
  const claims = lines.filter((line) => /Opened in browser/.test(line));
  assert.equal(claims.length, 2, "both modes say so when they open");
  for (const line of claims) {
    assert.match(line, /open !== false/, "…and only when they actually did");
  }
});

test("it is registered exactly once across the whole bridge", () => {
  const registering = toolSources().filter(([, src]) =>
    /server\.tool\(\s*\n?\s*"comms_dashboard"/.test(src));
  assert.equal(registering.length, 1, `registered by ${registering.map(([f]) => f).join(", ")}`);
  assert.equal(registering[0][0], "dashboard-tool.mjs");
});

test("the module kept no state and reaches only owned leaves plus node builtins", () => {
  const src = readFileSync(path.join(STDIO_DIR, "dashboard-tool.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, ["./aify-service-endpoint.mjs", "./local-store.mjs", "child_process", "fs", "path"]);
});

process.on("exit", () => { try { rmSync(STORE, { recursive: true, force: true }); } catch { /* best effort */ } });
