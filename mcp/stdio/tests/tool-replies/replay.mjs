// Replays the comms tool-reply fixtures against the JS tools, in service mode (0.9.11 slice 2).
//
// A fixture case is {name, tool, args, routes} written by hand, plus {requests, text, isError} recorded from
// these JS tools by `record.mjs`. The service's Python copy must make the same requests and render the same
// text (`service/tests/test_tool_replies_match_the_js.py`); while Codex still runs the JS tools, the JS must
// keep producing what was recorded (`tool-replies.test.js`). One engine serves both, so the copies are judged
// by one rule.
//
// The JS runs as `server.js` runs it: the real McpServer with every tool registered, reached through the
// SDK's in-memory transport, so schema defaults and validation apply. The service is a loopback HTTP server
// that answers from the case's `routes` and records every request.
//
// The tool modules read the endpoint and the caller from the environment at import, so `jsToolClient` sets
// them before importing, and one process serves one caller: every case is called as CALLER.

import http from "node:http";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const CASES_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "cases");
export const CALLER = "agent-a";

/** A request path with its query parameters sorted, so the two languages' encodings compare equal. */
export function normalizePath(raw) {
  const url = new URL(raw, "http://fixture");
  const pairs = [...url.searchParams.entries()].sort(([a, x], [b, y]) => (a < b ? -1 : a > b ? 1 : x < y ? -1 : x > y ? 1 : 0));
  const query = new URLSearchParams(pairs).toString();
  return `${url.pathname.replace(/^\/api\/v1/, "")}${query ? `?${query}` : ""}`;
}

/** A request body as recorded: a send's `clientNonce` is random per call, so only its presence is kept. */
export function normalizeBody(raw) {
  if (!raw) return null;
  const body = JSON.parse(raw);
  if (body && typeof body.clientNonce === "string" && body.clientNonce) body.clientNonce = "<nonce>";
  return body;
}

/** Every case file, in a stable order. */
export function loadCases() {
  return readdirSync(CASES_DIR).filter((name) => name.endsWith(".json")).sort()
    .flatMap((file) => JSON.parse(readFileSync(path.join(CASES_DIR, file), "utf8")).map((testCase) => ({ file, ...testCase })));
}

/** The loopback service. `serve(routes)` swaps the answers and clears the record; `requests` reads it. */
export async function startService() {
  let routes = {};
  let requests = [];
  const server = http.createServer((req, res) => {
    let raw = "";
    req.on("data", (chunk) => { raw += chunk; });
    req.on("end", () => {
      const key = `${req.method} ${normalizePath(req.url)}`;
      requests.push({ method: req.method, path: normalizePath(req.url), body: normalizeBody(raw) });
      const answer = Object.hasOwn(routes, key) ? routes[key] : { status: 404, body: { detail: `no fixture route for ${key}` } };
      res.writeHead(answer.status ?? 200, { "content-type": "application/json" });
      res.end(JSON.stringify(answer.body ?? {}));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  server.unref();
  return {
    url: `http://127.0.0.1:${server.address().port}`,
    serve(next) { routes = next || {}; requests = []; },
    get requests() { return requests; },
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

/** An MCP client of the JS tools, configured for `serviceUrl` and CALLER. Call once per process. */
export async function jsToolClient(serviceUrl) {
  const home = mkdtempSync(path.join(os.tmpdir(), "aify-tool-replies-"));
  Object.assign(process.env, {
    AIFY_SERVER_URL: serviceUrl, CLAUDE_MCP_SERVER_URL: "", AIFY_API_KEY: "fixture-key", AIFY_AGENT_ID: CALLER,
    HOME: home, USERPROFILE: home, AIFY_HOME: path.join(home, ".aify-comms"),
  });
  const { McpServer } = await import("@modelcontextprotocol/sdk/server/mcp.js");
  const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
  const { InMemoryTransport } = await import("@modelcontextprotocol/sdk/inMemory.js");
  const { z } = await import("zod");
  const { registerAllTools } = await import("../../register-tools.mjs");
  const server = new McpServer({ name: "aify-comms-fixtures", version: "0" });
  registerAllTools(server, z, { ensureDispatchLoop: () => {} });
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "fixtures", version: "0" });
  await Promise.all([server.connect(serverSide), client.connect(clientSide)]);
  return client;
}

/**
 * Run one case: what the tool asked the service, and what it told the agent. A request no route answers
 * fails the case: two copies calling the same wrong path would agree on the fixture's 404 and pass.
 */
export async function runCase(client, service, testCase) {
  service.serve(testCase.routes);
  const result = await client.callTool({ name: testCase.tool, arguments: testCase.args || {} });
  const unrouted = service.requests.map((r) => `${r.method} ${r.path}`).filter((key) => !Object.hasOwn(testCase.routes || {}, key));
  if (unrouted.length) throw new Error(`${testCase.name}: no route answers ${unrouted.join(", ")}`);
  return {
    requests: service.requests,
    text: (result.content || []).map((part) => part.text ?? "").join("\n"),
    isError: Boolean(result.isError),
  };
}
