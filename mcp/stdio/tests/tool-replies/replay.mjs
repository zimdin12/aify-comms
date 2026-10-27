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
//
// ERROR CASES ANSWER 4xx. The bridge's `httpCall` retries a GET, PATCH or DELETE that gets a 5xx, which is
// its network policy, not the tool's; the service calls its own API over loopback and does not retry. A
// 5xx case would record the retry count, not the tool.

import http from "node:http";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const CASES_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "cases");
export const CALLER = "agent-a";

/**
 * A request path under the API prefix, with its query parameters sorted, so the two languages' encodings compare
 * equal. A path outside the prefix keeps a marker, so no route answers it: the real service would not either.
 */
export function normalizePath(raw) {
  const url = new URL(raw, "http://fixture");
  const pairs = [...url.searchParams.entries()].sort(([a, x], [b, y]) => (a < b ? -1 : a > b ? 1 : x < y ? -1 : x > y ? 1 : 0));
  const query = new URLSearchParams(pairs).toString();
  const path = url.pathname.startsWith("/api/v1/") ? url.pathname.slice("/api/v1".length) : `<outside /api/v1>${url.pathname}`;
  return `${path}${query ? `?${query}` : ""}`;
}

const seenNonces = new Set();

/**
 * A request body as recorded: JSON, or a form as `{form: {...}}`. A send's `clientNonce` is random per call, so a
 * new one is kept as `<nonce>` and one seen before as `<repeated nonce>`: the service replays a repeated nonce as the
 * earlier message, so a constant one would turn every later send into a replay.
 */
export function normalizeBody(raw, contentType = "") {
  if (!raw) return null;
  if (/x-www-form-urlencoded/i.test(contentType)) return { form: Object.fromEntries(new URLSearchParams(raw)) };
  const body = JSON.parse(raw);
  if (body && typeof body.clientNonce === "string" && body.clientNonce) {
    const nonce = body.clientNonce;
    body.clientNonce = seenNonces.has(nonce) ? "<repeated nonce>" : "<nonce>";
    seenNonces.add(nonce);
  }
  return body;
}

/** Every case file, in a stable order. */
export function loadCases() {
  return readdirSync(CASES_DIR).filter((name) => name.endsWith(".json")).sort()
    .flatMap((file) => JSON.parse(readFileSync(path.join(CASES_DIR, file), "utf8")).map((testCase) => ({ file, ...testCase })));
}

/**
 * A route body with `<now-3h>`-style strings made into times, so a reply that states an age (a stale bridge's
 * "3h ago") reads the same on every run. Units s, m, h; `<now>` alone is the present.
 */
export function withTimes(value, now = Date.now()) {
  if (typeof value === "string") {
    const match = /^<now(?:-(\d+)([smh]))?>$/.exec(value);
    if (!match) return value;
    const ago = match[1] ? Number(match[1]) * { s: 1e3, m: 6e4, h: 36e5 }[match[2]] : 0;
    return new Date(now - ago).toISOString();
  }
  if (Array.isArray(value)) return value.map((item) => withTimes(item, now));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, withTimes(v, now)]));
  return value;
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
      requests.push({ method: req.method, path: normalizePath(req.url), body: normalizeBody(raw, req.headers["content-type"]) });
      const answer = Object.hasOwn(routes, key) ? routes[key] : { status: 404, body: { detail: `no fixture route for ${key}` } };
      res.writeHead(answer.status ?? 200, { "content-type": "application/json" });
      res.end(JSON.stringify(withTimes(answer.body ?? {})));
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

export const SCHEMAS_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), "schemas.json");

/** Every value a schema allows, wherever zod or pydantic put the `enum`. */
function enumValues(node) {
  if (!node || typeof node !== "object") return [];
  return [...(Array.isArray(node.enum) ? node.enum : []), ...Object.values(node).flatMap((child) => enumValues(child))];
}

/** What the tools' public schemas promise a caller: the required arguments and each argument's allowed values. */
export async function toolSchemas(client) {
  const { tools } = await client.listTools();
  return Object.fromEntries(tools.map((tool) => {
    const properties = tool.inputSchema?.properties || {};
    const enums = Object.fromEntries(Object.entries(properties)
      .map(([name, schema]) => [name, [...new Set(enumValues(schema))].sort()]).filter(([, values]) => values.length));
    return [tool.name, { required: [...(tool.inputSchema?.required || [])].sort(), enums }];
  }).sort(([a], [b]) => (a < b ? -1 : 1)));
}

/**
 * Run one case: what the tool asked the service, and what it told the agent. A request no route answers
 * fails the case: two copies calling the same wrong path would agree on the fixture's 404 and pass.
 */
export async function runCase(client, service, testCase) {
  service.serve(testCase.routes);
  const result = await client.callTool({ name: testCase.tool, arguments: testCase.args || {} });
  const asked = service.requests.map((r) => `${r.method} ${r.path}`);
  const unrouted = asked.filter((key) => !Object.hasOwn(testCase.routes || {}, key));
  if (unrouted.length) throw new Error(`${testCase.name}: no route answers ${unrouted.join(", ")}`);
  // EXHAUSTED: a route the tool never asked for is a call the case expected and did not happen.
  const unused = Object.keys(testCase.routes || {}).filter((key) => !asked.includes(key));
  if (unused.length) throw new Error(`${testCase.name}: the tool never asked ${unused.join(", ")}`);
  return {
    requests: service.requests,
    text: (result.content || []).map((part) => part.text ?? "").join("\n"),
    isError: Boolean(result.isError),
  };
}
